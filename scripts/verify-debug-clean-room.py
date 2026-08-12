#!/usr/bin/env python3
"""Checks the debug source, tracked files, and optional APK for forbidden auth paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


SOURCE_ROOTS = (
    "app/src/main",
    "app/src/debug",
    "codex-runtime-bridge/src/main",
    "codex_gateway",
    "codex-cli-pack",
    "grok-cli-pack",
    "codex-gateway-pack-bundled",
)
TEXT_SUFFIXES = {".kt", ".kts", ".java", ".py", ".xml", ".json"}
FORBIDDEN_TEXT = {
    "api-key-path": re.compile(r"(?i)(?:openai|provider|codex)?[_-]?api[_-]?key"),
    "oauth-client-id": re.compile(r"(?i)(?:openai|oauth|codex)[_-]?client[_-]?id|clientId"),
    "cli-fingerprint": re.compile(r"(?i)(?:cli|codex)[_-]?fingerprint"),
    "private-key": re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    "provider-direct-endpoint": re.compile(r"(?i)(?:api|platform|chat)\\.openai\\.com"),
    "bearer-auth-header": re.compile(r"(?i)authorization\\s*:\\s*bearer"),
}
FORBIDDEN_APK_BYTES = tuple(
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
        "authorization: bearer",
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apk", type=Path)
    return parser.parse_args()


def fail(label: str, path: Path) -> None:
    raise SystemExit(f"clean-room violation: {label} ({path.as_posix()})")


def source_files(project_root: Path):
    for relative in SOURCE_ROOTS:
        root = project_root / relative
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in TEXT_SUFFIXES:
                    yield path
    for path in project_root.glob("*.gradle.kts"):
        yield path
    yield project_root / "app/build.gradle.kts"


def check_source(project_root: Path) -> None:
    seen: set[Path] = set()
    for path in source_files(project_root):
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_TEXT.items():
            if pattern.search(text):
                fail(label, path.relative_to(project_root))
    gradle_files = [path for path in project_root.rglob("*.gradle.kts") if "/build/" not in path.as_posix()]
    signing = re.compile(r"\b(signingConfigs?|storeFile|storePassword|keyAlias|keyPassword)\b")
    explicit_release = re.compile(r"\b(?:getByName|named|create|register)\s*\(\s*\"(?:release|assembleRelease|bundleRelease)")
    for path in gradle_files:
        text = path.read_text(encoding="utf-8")
        if signing.search(text):
            fail("release-signing", path.relative_to(project_root))
        if explicit_release.search(text):
            fail("release-artifact-route", path.relative_to(project_root))


def check_tracked_files(project_root: Path) -> None:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=project_root)
    prohibited_suffixes = (".apk", ".aab", ".tar.gz", ".tar.xz", ".tar.zst")
    prohibited_parts = ("/build/", "/.gradle/", "/captures/", "/logs/", "/credentials/", "/.codex/", "/rootfs-expanded/")
    for encoded in output.split(b"\0"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8")
        normalized = f"/{relative.lower()}"
        if normalized.endswith(prohibited_suffixes) or any(part in normalized for part in prohibited_parts):
            fail("tracked-generated-or-sensitive-artifact", Path(relative))
        if "codex-cli" in normalized and normalized.endswith("/codex"):
            fail("tracked-codex-cli-binary", Path(relative))
        if "grok-cli" in normalized and normalized.endswith("/grok"):
            fail("tracked-grok-cli-binary", Path(relative))


def read_locked_clis(project_root: Path) -> dict[str, tuple[int, str]]:
    values: dict[str, tuple[int, str]] = {}
    for asset_path, lock_relative in (
        ("assets/codex-cli/codex", "codex-cli-pack/codex-cli.lock.json"),
        ("assets/grok-cli/grok", "grok-cli-pack/grok-cli.lock.json"),
    ):
        try:
            lock = json.loads((project_root / lock_relative).read_text(encoding="utf-8"))
            binary_size = lock["binary_size"]
            binary_sha256 = lock["binary_sha256"]
        except (KeyError, OSError, json.JSONDecodeError) as error:
            raise SystemExit("debug CLI lock is unavailable for APK scan") from error
        if (
            not isinstance(binary_size, int)
            or binary_size <= 0
            or not isinstance(binary_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", binary_sha256)
        ):
            raise SystemExit("debug CLI lock is invalid for APK scan")
        values[asset_path] = (binary_size, binary_sha256)
    return values


def check_apk(project_root: Path, apk_path: Path) -> None:
    if not apk_path.is_file():
        raise SystemExit("debug APK is unavailable for clean-room scan")
    found_inventory = False
    locked_clis = read_locked_clis(project_root)
    found_locked_clis: set[str] = set()
    with zipfile.ZipFile(apk_path) as archive:
        for entry in archive.infolist():
            if entry.filename == "assets/META-INF/alpine-codex/debug-component-inventory.json":
                found_inventory = True
            if entry.filename in locked_clis:
                locked_cli_size, locked_cli_sha256 = locked_clis[entry.filename]
                digest = hashlib.sha256()
                with archive.open(entry) as source:
                    while block := source.read(64 * 1024):
                        digest.update(block)
                if entry.file_size != locked_cli_size or digest.hexdigest() != locked_cli_sha256:
                    fail("unlocked-cli-binary-in-apk", Path(entry.filename))
                found_locked_clis.add(entry.filename)
                continue
            tail = b""
            with archive.open(entry) as source:
                while True:
                    block = source.read(64 * 1024)
                    if not block:
                        break
                    lowered = (tail + block).lower()
                    if any(token in lowered for token in FORBIDDEN_APK_BYTES):
                        fail("forbidden-auth-or-provider-byte-in-apk", Path(entry.filename))
                    tail = lowered[-64:]
    if not found_inventory:
        raise SystemExit("debug component inventory asset is missing from APK")
    if found_locked_clis != set(locked_clis):
        raise SystemExit("locked CLI asset is missing from APK")


def verify(project_root: Path, apk: Path | None) -> None:
    check_source(project_root)
    check_tracked_files(project_root)
    if apk is not None:
        check_apk(project_root, apk)
    print("debug clean-room scan: PASS")


if __name__ == "__main__":
    args = parse_args()
    verify(args.project_root.resolve(), args.apk.resolve() if args.apk else None)
