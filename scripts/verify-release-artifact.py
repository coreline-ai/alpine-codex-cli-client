#!/usr/bin/env python3
"""Verifies a signed public release APK/AAB identity, certificate, and locked payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


EXPECTED_APPLICATION_ID = "dev.alpine.codexclient"
EXPECTED_VERSION_CODE = "2"
EXPECTED_VERSION_NAME = "0.2.0"
EXPECTED_INVENTORY_FORMAT = "alpine-codex-component-inventory/v1"
FORBIDDEN_BYTES = tuple(
    value.encode("ascii")
    for value in (
        "openai_api_key",
        "api_key=",
        "oauth_client_id",
        "openai_client_id",
        "cli_fingerprint",
        "codex_fingerprint",
        " private key-----",
        "api.openai.com",
        "platform.openai.com",
        "chat.openai.com",
        "api.x.ai",
        "authorization: bearer",
    )
)


class ReleaseArtifactError(ValueError):
    """Raised when a release artifact is not fit for distribution."""


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, check=True, text=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseArtifactError(f"release tool failed: {Path(command[0]).name}") from error


def _sdk_tool(name: str) -> Path:
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    candidates: list[Path] = []
    sdk_roots = [Path(sdk)] if sdk else [Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"]
    for sdk_root in sdk_roots:
        if name == "apkanalyzer":
            candidates.append(sdk_root / "cmdline-tools/latest/bin/apkanalyzer")
        else:
            build_tools = sdk_root / "build-tools"
            if build_tools.is_dir():
                candidates.extend(
                    directory / name for directory in sorted(build_tools.iterdir(), reverse=True)
                )
    resolved = shutil.which(name)
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ReleaseArtifactError(f"required Android SDK tool is missing: {name}")


def _normalized_certificate(value: str) -> str:
    normalized = value.replace(":", "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ReleaseArtifactError("expected release certificate SHA-256 is invalid")
    return normalized


def _verify_manifest(artifact: Path) -> None:
    analyzer = str(_sdk_tool("apkanalyzer"))
    application_id = _run([analyzer, "manifest", "application-id", str(artifact)]).strip()
    version_code = _run([analyzer, "manifest", "version-code", str(artifact)]).strip()
    version_name = _run([analyzer, "manifest", "version-name", str(artifact)]).strip()
    debuggable = _run([analyzer, "manifest", "debuggable", str(artifact)]).strip().lower()
    manifest = _run([analyzer, "manifest", "print", str(artifact)])
    if application_id != EXPECTED_APPLICATION_ID:
        raise ReleaseArtifactError("release application ID mismatch")
    if version_code != EXPECTED_VERSION_CODE or version_name != EXPECTED_VERSION_NAME:
        raise ReleaseArtifactError("release version mismatch")
    if debuggable != "false" or "android:testOnly" in manifest:
        raise ReleaseArtifactError("release artifact is debuggable or test-only")
    if not re.search(r'android:allowBackup\s*=\s*"false"', manifest):
        raise ReleaseArtifactError("release backup policy is not fail-closed")
    for attribute in ("android:dataExtractionRules", "android:fullBackupContent"):
        if attribute not in manifest:
            raise ReleaseArtifactError(f"release manifest is missing {attribute}")
    if manifest.count("android.intent.action.MAIN") != 1:
        raise ReleaseArtifactError("release launcher identity is ambiguous")


def _verify_apk_signature(artifact: Path, expected: str) -> str:
    signer = str(_sdk_tool("apksigner"))
    output = _run([signer, "verify", "--print-certs", str(artifact)])
    match = re.search(r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F:]+)", output)
    if match is None:
        raise ReleaseArtifactError("release APK certificate is unavailable")
    observed = _normalized_certificate(match.group(1))
    if observed != expected:
        raise ReleaseArtifactError("release APK certificate mismatch")
    if "android debug" in output.lower():
        raise ReleaseArtifactError("release APK uses an Android debug certificate")
    return observed


def _verify_aab_signature(artifact: Path, expected: str) -> str:
    jarsigner = shutil.which("jarsigner")
    keytool = shutil.which("keytool")
    if not jarsigner or not keytool:
        raise ReleaseArtifactError("JDK signing verification tools are missing")
    _run([jarsigner, "-verify", str(artifact)])
    output = _run([keytool, "-J-Duser.language=en", "-printcert", "-jarfile", str(artifact)])
    match = re.search(r"SHA256:\s*([0-9A-Fa-f:]+)", output)
    if match is None:
        raise ReleaseArtifactError("release AAB certificate is unavailable")
    observed = _normalized_certificate(match.group(1))
    if observed != expected:
        raise ReleaseArtifactError("release AAB certificate mismatch")
    if "android debug" in output.lower():
        raise ReleaseArtifactError("release AAB uses an Android debug certificate")
    return observed


def _read_lock(project_root: Path, relative: str) -> tuple[int, str]:
    try:
        lock = json.loads((project_root / relative).read_text(encoding="utf-8"))
        size = lock["binary_size"]
        digest = lock["binary_sha256"]
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError("CLI lock is unavailable") from error
    if (
        not isinstance(size, int)
        or size <= 0
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise ReleaseArtifactError("CLI lock is invalid")
    return size, digest


def _read_bytes(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as error:
        raise ReleaseArtifactError(f"release payload is missing: {name}") from error


def _verify_hash(archive: zipfile.ZipFile, name: str, expected_size: int, expected_hash: str) -> None:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ReleaseArtifactError(f"release payload is missing: {name}") from error
    digest = hashlib.sha256()
    with archive.open(info) as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    if info.file_size != expected_size or digest.hexdigest() != expected_hash:
        raise ReleaseArtifactError(f"release payload hash mismatch: {name}")


def _verify_gateway(archive: zipfile.ZipFile, assets: str) -> None:
    manifest_name = f"{assets}codex-gateway/gateway-manifest.json"
    try:
        manifest = json.loads(_read_bytes(archive, manifest_name).decode("utf-8"))
        entries = manifest["files"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
        raise ReleaseArtifactError("Gateway asset manifest is invalid") from error
    expected_paths: set[str] = set()
    for entry in entries:
        try:
            path = entry["path"]
            size = entry["size"]
            digest = entry["sha256"]
        except (KeyError, TypeError) as error:
            raise ReleaseArtifactError("Gateway asset manifest entry is invalid") from error
        if (
            not isinstance(path, str)
            or not path.startswith("codex_gateway/")
            or not path.endswith(".py")
            or path in expected_paths
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ReleaseArtifactError("Gateway asset manifest entry is invalid")
        expected_paths.add(path)
        _verify_hash(archive, f"{assets}codex-gateway/{path}", size, digest)
    actual_paths = {
        name.removeprefix(f"{assets}codex-gateway/")
        for name in archive.namelist()
        if name.startswith(f"{assets}codex-gateway/codex_gateway/") and name.endswith(".py")
    }
    if not expected_paths or actual_paths != expected_paths:
        raise ReleaseArtifactError("Gateway asset manifest coverage mismatch")


def _verify_python_package_pack(archive: zipfile.ZipFile, assets: str) -> int:
    prefix = f"{assets}alpine-python-pack/"
    try:
        status_bytes = _read_bytes(archive, f"{prefix}pack-status.json")
        status = json.loads(status_bytes.decode("utf-8"))
        lock_bytes = _read_bytes(archive, f"{prefix}python-pack.lock.json")
        lock = json.loads(lock_bytes.decode("utf-8"))
        packages = lock["packages"]
        sbom = lock["sbom"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ReleaseArtifactError("Python package pack metadata is invalid") from error
    if (
        status.get("schema") != 1
        or status.get("available") is not True
        or status.get("production") is not True
        or lock.get("schema") != 1
        or lock.get("production") is not True
        or lock.get("architecture") != "aarch64"
        or status.get("pack_id") != lock.get("pack_id")
        or status.get("lock_sha256") != hashlib.sha256(lock_bytes).hexdigest()
        or not isinstance(packages, list)
        or not 1 <= len(packages) <= 128
        or status.get("package_count") != len(packages)
    ):
        raise ReleaseArtifactError("Python package pack status or lock is invalid")
    expected_paths = {"pack-status.json", "python-pack.lock.json"}
    package_names: set[str] = set()
    component_names: set[str] = set()
    for entry in packages:
        try:
            path = entry["file"]
            name = entry["name"]
            version = entry["version"]
            size = entry["size"]
            digest = entry["sha256"]
        except (KeyError, TypeError) as error:
            raise ReleaseArtifactError("Python package lock entry is invalid") from error
        if (
            not isinstance(path, str)
            or re.fullmatch(r"packages/[A-Za-z0-9][A-Za-z0-9._+~-]*\.apk", path) is None
            or path in package_names
            or not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", name) is None
            or name in component_names
            or not isinstance(version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}", version) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ReleaseArtifactError("Python package lock entry is invalid")
        package_names.add(path)
        component_names.add(name)
        expected_paths.add(path)
        _verify_hash(archive, f"{prefix}{path}", size, digest)
    if "python3" not in component_names:
        raise ReleaseArtifactError("Python package pack does not contain python3")
    try:
        sbom_path = sbom["file"]
        sbom_size = sbom["size"]
        sbom_hash = sbom["sha256"]
    except (KeyError, TypeError) as error:
        raise ReleaseArtifactError("Python package SBOM lock is invalid") from error
    if (
        sbom_path != "sbom.spdx.json"
        or not isinstance(sbom_size, int)
        or isinstance(sbom_size, bool)
        or sbom_size <= 0
        or not isinstance(sbom_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", sbom_hash) is None
    ):
        raise ReleaseArtifactError("Python package SBOM lock is invalid")
    _verify_hash(archive, f"{prefix}{sbom_path}", sbom_size, sbom_hash)
    expected_paths.add(sbom_path)
    actual_paths = {
        name.removeprefix(prefix)
        for name in archive.namelist()
        if name.startswith(prefix) and not name.endswith("/")
    }
    if actual_paths != expected_paths:
        raise ReleaseArtifactError("Python package asset coverage mismatch")
    try:
        sbom_value = json.loads(_read_bytes(archive, f"{prefix}{sbom_path}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError("Python package SBOM is invalid") from error
    if sbom_value.get("spdxVersion") != "SPDX-2.3" or not isinstance(
        sbom_value.get("packages"), list
    ):
        raise ReleaseArtifactError("Python package SBOM is not SPDX-2.3")
    return len(packages)


def verify_payload(project_root: Path, artifact: Path) -> dict[str, int | str]:
    is_aab = artifact.suffix.lower() == ".aab"
    assets = "base/assets/" if is_aab else "assets/"
    native = "base/lib/arm64-v8a/" if is_aab else "lib/arm64-v8a/"
    codex_size, codex_hash = _read_lock(project_root, "codex-cli-pack/codex-cli.lock.json")
    grok_size, grok_hash = _read_lock(project_root, "grok-cli-pack/grok-cli.lock.json")
    with zipfile.ZipFile(artifact) as archive:
        cli_names = {f"{assets}codex-cli/codex", f"{assets}grok-cli/grok"}
        _verify_hash(archive, f"{assets}codex-cli/codex", codex_size, codex_hash)
        _verify_hash(archive, f"{assets}grok-cli/grok", grok_size, grok_hash)

        inventory = json.loads(
            _read_bytes(archive, f"{assets}META-INF/alpine-codex/component-inventory.json").decode("utf-8")
        )
        if (
            inventory.get("format") != EXPECTED_INVENTORY_FORMAT
            or inventory.get("scope") != "debug-secureDebug-release"
            or not inventory.get("components")
            or not inventory.get("sboms")
        ):
            raise ReleaseArtifactError("release component inventory is invalid")
        if "Alpine Python package pack" not in {
            component.get("name") for component in inventory["components"] if isinstance(component, dict)
        } or "alpine-python-pack/sbom.spdx.json" not in {
            sbom.get("path") for sbom in inventory["sboms"] if isinstance(sbom, dict)
        }:
            raise ReleaseArtifactError("release Python package inventory is missing")

        profile_source = project_root / "grok-cli-pack/src/main/assets/grok-profile/chat-only.md"
        lock_source = project_root / "grok-cli-pack/src/main/assets/grok-profile/chat-only.lock.json"
        if _read_bytes(archive, f"{assets}grok-profile/chat-only.md") != profile_source.read_bytes():
            raise ReleaseArtifactError("release Grok profile drift")
        if _read_bytes(archive, f"{assets}grok-profile/chat-only.lock.json") != lock_source.read_bytes():
            raise ReleaseArtifactError("release Grok profile lock drift")

        _verify_gateway(archive, assets)
        python_package_count = _verify_python_package_pack(archive, assets)
        _read_bytes(archive, f"{assets}alpine-minirootfs.tar.gz.asset")
        for library in ("libproot.so", "libproot-loader.so", "libalpine-runtime-pty.so"):
            _read_bytes(archive, f"{native}{library}")

        for info in archive.infolist():
            if info.is_dir() or info.filename in cli_names:
                continue
            tail = b""
            with archive.open(info) as source:
                for block in iter(lambda: source.read(64 * 1024), b""):
                    lowered = (tail + block).lower()
                    if any(token in lowered for token in FORBIDDEN_BYTES):
                        raise ReleaseArtifactError(
                            f"forbidden authentication/provider byte: {info.filename}"
                        )
                    tail = lowered[-64:]
    return {
        "codex_cli_bytes": codex_size,
        "grok_cli_bytes": grok_size,
        "python_packages": python_package_count,
        "format": artifact.suffix[1:],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--expected-certificate-sha256", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    project_root = arguments.project_root.resolve()
    artifact = arguments.artifact.resolve()
    if not artifact.is_file() or artifact.suffix.lower() not in {".apk", ".aab"}:
        raise SystemExit("release artifact: FAIL (signed .apk or .aab is required)")
    try:
        expected_certificate = _normalized_certificate(arguments.expected_certificate_sha256)
        _verify_manifest(artifact)
        if artifact.suffix.lower() == ".apk":
            certificate = _verify_apk_signature(artifact, expected_certificate)
        else:
            certificate = _verify_aab_signature(artifact, expected_certificate)
        result = verify_payload(project_root, artifact)
    except (ReleaseArtifactError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        raise SystemExit(f"release artifact: FAIL ({error})") from error
    print(
        "release artifact: PASS "
        f"(format={result['format']}, application_id={EXPECTED_APPLICATION_ID}, "
        f"certificate_sha256={certificate})"
    )
