#!/usr/bin/env python3
"""Deterministic, dependency-free Alpine runtime inventory and integrity helpers."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


MAX_ROOTFS_BYTES = 256 * 1024 * 1024
MAX_EXTRACTED_ROOTFS_BYTES = 1024 * 1024 * 1024
MAX_ROOTFS_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TAR_ENTRIES = 100_000
MAX_PACKAGE_DATABASE_BYTES = 8 * 1024 * 1024
MAX_PACKAGES = 4_096
MAX_METADATA_BYTES = 64 * 1024
MAX_VULNERABILITY_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_VULNERABILITY_FINDINGS = 4_096
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PACKAGE_NAME_PATTERN = re.compile(r"[A-Za-z0-9+_.-]{1,128}")
VERSION_PATTERN = re.compile(r"[A-Za-z0-9+_.:~%-]{1,192}")
ARCH_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")


class SupplyChainError(ValueError):
    """Raised when locked runtime supply-chain data fails closed."""


@dataclass(frozen=True)
class AlpinePackage:
    name: str
    version: str
    architecture: str
    license_declared: str
    origin: str
    package_commit: str
    source_url: str
    installed_checksum: str


@dataclass(frozen=True)
class RootfsInventory:
    alpine_version: str
    packages: tuple[AlpinePackage, ...]
    python_prebundled: bool


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not 1 <= len(name) <= 4_096
        or any(ord(character) < 0x20 for character in name)
    ):
        raise SupplyChainError("rootfs contains an unsafe archive path")
    normalized = str(path)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", "."}:
        return ""
    return normalized


def _safe_link_target(member_name: str, target_name: str) -> bool:
    if "\x00" in target_name or not target_name:
        return False
    if target_name.startswith("/"):
        resolved = posixpath.normpath(target_name.lstrip("/"))
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(member_name), target_name)
        )
    return resolved not in {"", ".", ".."} and not resolved.startswith("../")


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if not member.isfile() or member.size < 0 or member.size > limit:
        raise SupplyChainError("rootfs metadata member has an invalid type or size")
    handle = archive.extractfile(member)
    if handle is None:
        raise SupplyChainError("rootfs metadata member cannot be read")
    payload = handle.read(limit + 1)
    if len(payload) != member.size or len(payload) > limit:
        raise SupplyChainError("rootfs metadata member length mismatch")
    return payload


def read_rootfs_inventory(rootfs: Path) -> RootfsInventory:
    if not rootfs.is_file():
        raise SupplyChainError("rootfs artifact is missing")
    size = rootfs.stat().st_size
    if size <= 0 or size > MAX_ROOTFS_BYTES:
        raise SupplyChainError("rootfs artifact size is outside the allowed bound")

    package_database: bytes | None = None
    alpine_release: bytes | None = None
    python_prebundled = False
    seen_metadata: set[str] = set()
    try:
        with tarfile.open(rootfs, "r:gz") as archive:
            extracted_size = 0
            for count, member in enumerate(archive, start=1):
                if count > MAX_TAR_ENTRIES:
                    raise SupplyChainError("rootfs archive contains too many entries")
                name = _safe_member_name(member.name)
                if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                    raise SupplyChainError("rootfs archive contains a special file")
                if member.size < 0 or member.size > MAX_ROOTFS_MEMBER_BYTES:
                    raise SupplyChainError("rootfs archive member is oversized")
                extracted_size += member.size
                if extracted_size > MAX_EXTRACTED_ROOTFS_BYTES:
                    raise SupplyChainError("rootfs extracted size exceeds the allowed bound")
                if member.issym() or member.islnk():
                    if not _safe_link_target(name, member.linkname):
                        raise SupplyChainError("rootfs contains an unsafe link target")
                if name == "usr/bin/python3" and (member.isfile() or member.issym()):
                    python_prebundled = True
                if name not in {"etc/alpine-release", "lib/apk/db/installed"}:
                    continue
                if name in seen_metadata:
                    raise SupplyChainError("rootfs contains duplicate security metadata")
                seen_metadata.add(name)
                if name == "etc/alpine-release":
                    alpine_release = _read_member(archive, member, MAX_METADATA_BYTES)
                else:
                    package_database = _read_member(
                        archive, member, MAX_PACKAGE_DATABASE_BYTES
                    )
    except (tarfile.TarError, OSError, UnicodeError) as error:
        raise SupplyChainError("rootfs archive cannot be inspected") from error

    if alpine_release is None or package_database is None:
        raise SupplyChainError("rootfs Alpine release or package database is missing")
    try:
        version = alpine_release.decode("ascii", errors="strict").strip()
        database = package_database.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise SupplyChainError("rootfs security metadata is not valid text") from error
    if not VERSION_PATTERN.fullmatch(version):
        raise SupplyChainError("rootfs Alpine release is invalid")
    packages = parse_apk_installed_database(database)
    return RootfsInventory(version, packages, python_prebundled)


def _bounded_field(values: dict[str, str], key: str, maximum: int = 512) -> str:
    value = values.get(key, "")
    if not value or len(value) > maximum or any(ord(character) < 0x20 for character in value):
        raise SupplyChainError("Alpine package database has invalid required fields")
    return value


def parse_apk_installed_database(database: str) -> tuple[AlpinePackage, ...]:
    if len(database.encode("utf-8")) > MAX_PACKAGE_DATABASE_BYTES or "\x00" in database:
        raise SupplyChainError("Alpine package database is oversized or malformed")
    packages: list[AlpinePackage] = []
    seen: set[tuple[str, str]] = set()
    for paragraph in re.split(r"\n\s*\n", database.strip()):
        values: dict[str, str] = {}
        for line in paragraph.splitlines():
            if len(line) >= 3 and line[1] == ":" and line[0] in "PV ALocUC".replace(" ", ""):
                values.setdefault(line[0], line[2:])
        if "P" not in values:
            continue
        name = _bounded_field(values, "P", 128)
        version = _bounded_field(values, "V", 192)
        architecture = _bounded_field(values, "A", 32)
        license_declared = _bounded_field(values, "L", 512)
        origin = _bounded_field(values, "o", 128)
        package_commit = _bounded_field(values, "c", 64)
        source_url = _bounded_field(values, "U", 512)
        installed_checksum = _bounded_field(values, "C", 128)
        if not PACKAGE_NAME_PATTERN.fullmatch(name):
            raise SupplyChainError("Alpine package name is invalid")
        if not VERSION_PATTERN.fullmatch(version) or not ARCH_PATTERN.fullmatch(architecture):
            raise SupplyChainError("Alpine package version or architecture is invalid")
        if not PACKAGE_NAME_PATTERN.fullmatch(origin):
            raise SupplyChainError("Alpine package origin is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", package_commit):
            raise SupplyChainError("Alpine package source revision is invalid")
        key = (name, architecture)
        if key in seen:
            raise SupplyChainError("Alpine package database contains a duplicate package")
        seen.add(key)
        packages.append(
            AlpinePackage(
                name=name,
                version=version,
                architecture=architecture,
                license_declared=license_declared,
                origin=origin,
                package_commit=package_commit,
                source_url=source_url,
                installed_checksum=installed_checksum,
            )
        )
        if len(packages) > MAX_PACKAGES:
            raise SupplyChainError("Alpine package database contains too many packages")
    if not packages:
        raise SupplyChainError("Alpine package database contains no packages")
    return tuple(sorted(packages, key=lambda item: (item.name, item.architecture)))


def load_lock(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupplyChainError("runtime lock cannot be read") from error
    if document.get("schema_version") != 1:
        raise SupplyChainError("runtime lock schema is unsupported")
    return document


def package_inventory_sha256(packages: tuple[AlpinePackage, ...]) -> str:
    payload = [
        {
            "architecture": package.architecture,
            "installed_checksum": package.installed_checksum,
            "license_declared": package.license_declared,
            "name": package.name,
            "origin": package.origin,
            "package_commit": package.package_commit,
            "source_url": package.source_url,
            "version": package.version,
        }
        for package in packages
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _artifact_path(project_root: Path, item: dict[str, Any]) -> Path:
    relative = PurePosixPath(str(item.get("path", "")))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SupplyChainError("runtime lock contains an unsafe artifact path")
    path = (project_root / Path(*relative.parts)).resolve()
    root = project_root.resolve()
    if path == root or root not in path.parents:
        raise SupplyChainError("runtime lock artifact escapes the project")
    return path


def verify_vulnerability_snapshot(
    project_root: Path,
    snapshot_path: Path,
    inventory: RootfsInventory,
    rootfs_sha256: str,
) -> dict[str, Any]:
    if snapshot_path.stat().st_size > MAX_VULNERABILITY_SNAPSHOT_BYTES:
        raise SupplyChainError("runtime vulnerability snapshot is oversized")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupplyChainError("runtime vulnerability snapshot cannot be read") from error
    if snapshot.get("schema_version") != 1:
        raise SupplyChainError("runtime vulnerability snapshot schema is unsupported")
    source = snapshot.get("source")
    scope = snapshot.get("scope")
    findings = snapshot.get("findings")
    if not isinstance(source, dict) or not isinstance(scope, dict) or not isinstance(findings, list):
        raise SupplyChainError("runtime vulnerability snapshot shape is invalid")
    if len(findings) > MAX_VULNERABILITY_FINDINGS:
        raise SupplyChainError("runtime vulnerability snapshot contains too many findings")

    source_path = _artifact_path(project_root, {"path": source.get("path", "")})
    source_hash = source.get("sha256")
    if not isinstance(source_hash, str) or not SHA256_PATTERN.fullmatch(source_hash):
        raise SupplyChainError("runtime vulnerability source checksum is invalid")
    if not source_path.is_file() or sha256(source_path) != source_hash:
        raise SupplyChainError("runtime vulnerability source evidence drift")
    if source.get("kind") not in {"local-security-review", "pinned-alpine-secdb", "pinned-osv"}:
        raise SupplyChainError("runtime vulnerability source kind is not allowed")

    complete = scope.get("database_complete")
    if not isinstance(complete, bool):
        raise SupplyChainError("runtime vulnerability completeness decision is missing")
    if scope.get("rootfs_sha256") != rootfs_sha256:
        raise SupplyChainError("runtime vulnerability rootfs scope drift")
    if scope.get("package_inventory_sha256") != package_inventory_sha256(inventory.packages):
        raise SupplyChainError("runtime vulnerability package inventory scope drift")

    installed = {(package.name, package.version) for package in inventory.packages}
    seen: set[tuple[str, str, str]] = set()
    blocked = 0
    unknown = 0
    for finding in findings:
        if not isinstance(finding, dict):
            raise SupplyChainError("runtime vulnerability finding is invalid")
        identifier = finding.get("id")
        package = finding.get("package")
        version = finding.get("installed_version")
        severity = finding.get("severity")
        status = finding.get("status")
        if not isinstance(identifier, str) or not re.fullmatch(
            r"(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9-]{8,}|LOCAL-[A-Z0-9-]{4,})",
            identifier,
        ):
            raise SupplyChainError("runtime vulnerability identifier is invalid")
        if not isinstance(package, str) or not PACKAGE_NAME_PATTERN.fullmatch(package):
            raise SupplyChainError("runtime vulnerability package is invalid")
        if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
            raise SupplyChainError("runtime vulnerability version is invalid")
        if (package, version) not in installed:
            raise SupplyChainError("runtime vulnerability finding does not match the rootfs")
        if severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}:
            raise SupplyChainError("runtime vulnerability severity is invalid")
        if status not in {"AFFECTED", "NOT_AFFECTED", "UNKNOWN"}:
            raise SupplyChainError("runtime vulnerability status is invalid")
        key = (identifier, package, version)
        if key in seen:
            raise SupplyChainError("runtime vulnerability finding is duplicated")
        seen.add(key)
        if status == "AFFECTED" and severity in {"CRITICAL", "HIGH"}:
            blocked += 1
        if status == "UNKNOWN" or severity == "UNKNOWN":
            unknown += 1
    return {
        "database_complete": complete,
        "finding_count": len(findings),
        "blocked_finding_count": blocked,
        "unknown_finding_count": unknown,
    }


def _spdx_id(package: AlpinePackage) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9.-]", "-", package.name)
    suffix = hashlib.sha256(
        f"{package.name}\0{package.version}\0{package.architecture}".encode()
    ).hexdigest()[:12]
    return f"SPDXRef-AlpinePackage-{safe_name}-{suffix}"


def build_spdx_document(
    inventory: RootfsInventory,
    lock: dict[str, Any],
    rootfs_sha256: str,
) -> dict[str, Any]:
    runtime = lock.get("runtime")
    if not isinstance(runtime, dict):
        raise SupplyChainError("runtime lock metadata is missing")
    created = runtime.get("sbom_created")
    if not isinstance(created, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created
    ):
        raise SupplyChainError("runtime lock SBOM timestamp is invalid")
    namespace_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"alpine-runtime:{rootfs_sha256}")
    root_id = "SPDXRef-AlpineRootfs"
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": root_id,
            "name": "Alpine Linux rootfs",
            "versionInfo": inventory.alpine_version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": rootfs_sha256}],
            "primaryPackagePurpose": "OPERATING-SYSTEM",
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_id,
        }
    ]
    for package in inventory.packages:
        package_id = _spdx_id(package)
        purl = (
            "pkg:apk/alpine/"
            f"{quote(package.name, safe='')}@{quote(package.version, safe='')}"
            f"?arch={quote(package.architecture, safe='')}"
        )
        packages.append(
            {
                "SPDXID": package_id,
                "name": package.name,
                "versionInfo": package.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": package.license_declared,
                "copyrightText": "NOASSERTION",
                "sourceInfo": (
                    f"origin={package.origin}; aports={package.package_commit}; "
                    f"upstream={package.source_url}; apk-checksum={package.installed_checksum}"
                ),
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": purl,
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": package_id,
            }
        )

    for key, package_name, package_id in (
        ("proot", "OpenMinis PRoot Android fork", "SPDXRef-PRoot"),
        ("proot_loader", "OpenMinis PRoot loader", "SPDXRef-PRootLoader"),
    ):
        artifact = lock.get("artifacts", {}).get(key)
        if not isinstance(artifact, dict):
            raise SupplyChainError("runtime lock native artifact metadata is missing")
        packages.append(
            {
                "SPDXID": package_id,
                "name": package_name,
                "versionInfo": str(artifact.get("version", "")),
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": str(artifact.get("license", "NOASSERTION")),
                "copyrightText": "NOASSERTION",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": artifact.get("sha256")}
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": package_id,
            }
        )
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "name": f"alpine-runtime-{inventory.alpine_version}-arm64",
        "documentNamespace": f"urn:uuid:{namespace_uuid}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: alpine-codex-runtime-supply-chain-v1"],
        },
        "packages": packages,
        "relationships": relationships,
    }


def verify_project(project_root: Path) -> dict[str, Any]:
    project_root = project_root.resolve()
    lock_path = project_root / "alpine-runtime-pack-bundled/runtime-lock.json"
    lock = load_lock(lock_path)
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "rootfs",
        "proot",
        "proot_loader",
        "sbom",
        "vulnerability_snapshot",
    }:
        raise SupplyChainError("runtime lock artifact set is incomplete")
    resolved: dict[str, Path] = {}
    for key, item in artifacts.items():
        if not isinstance(item, dict):
            raise SupplyChainError("runtime lock artifact entry is invalid")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
            raise SupplyChainError("runtime lock artifact checksum is invalid")
        if not isinstance(expected_size, int) or expected_size <= 0:
            raise SupplyChainError("runtime lock artifact size is invalid")
        path = _artifact_path(project_root, item)
        if not path.is_file() or path.stat().st_size != expected_size:
            raise SupplyChainError("runtime artifact size mismatch")
        if sha256(path) != expected_hash:
            raise SupplyChainError("runtime artifact checksum mismatch")
        resolved[key] = path

    inventory = read_rootfs_inventory(resolved["rootfs"])
    rootfs_lock = lock.get("rootfs")
    if not isinstance(rootfs_lock, dict):
        raise SupplyChainError("runtime lock rootfs metadata is missing")
    if inventory.alpine_version != rootfs_lock.get("alpine_version"):
        raise SupplyChainError("runtime rootfs Alpine version drift")
    architectures = {package.architecture for package in inventory.packages}
    if architectures != {rootfs_lock.get("apk_architecture")}:
        raise SupplyChainError("runtime rootfs package architecture drift")
    if len(inventory.packages) != rootfs_lock.get("package_count"):
        raise SupplyChainError("runtime rootfs package count drift")
    if inventory.python_prebundled is not rootfs_lock.get("python_prebundled"):
        raise SupplyChainError("runtime rootfs Python policy drift")

    expected_sbom = build_spdx_document(
        inventory, lock, artifacts["rootfs"]["sha256"]
    )
    try:
        actual_sbom = json.loads(resolved["sbom"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupplyChainError("runtime SBOM cannot be read") from error
    if actual_sbom != expected_sbom:
        raise SupplyChainError("runtime package-level SBOM drift")

    vulnerability = verify_vulnerability_snapshot(
        project_root,
        resolved["vulnerability_snapshot"],
        inventory,
        artifacts["rootfs"]["sha256"],
    )

    source = (
        project_root
        / "alpine-runtime-pack-bundled/src/main/kotlin/dev/alpine/runtime/pack/bundled/"
        "BundledRuntimeArtifactProvider.kt"
    ).read_text(encoding="utf-8")
    gradle = (project_root / "alpine-runtime-pack-bundled/build.gradle.kts").read_text(
        encoding="utf-8"
    )
    for token in (
        artifacts["rootfs"]["sha256"],
        artifacts["proot"]["sha256"],
        artifacts["proot_loader"]["sha256"],
        artifacts["sbom"]["sha256"],
        str(rootfs_lock["alpine_version"]),
        str(lock["runtime"]["runtime_version"]),
    ):
        if token not in source + "\n" + gradle:
            raise SupplyChainError("runtime Kotlin/Gradle lock projection drift")
    rootfs_size = artifacts["rootfs"]["size"]
    if str(rootfs_size) not in source and f"{rootfs_size:_}" not in source:
        raise SupplyChainError("runtime Kotlin rootfs size projection drift")
    return {
        "package_count": len(inventory.packages),
        "python_prebundled": inventory.python_prebundled,
        "vulnerability_database_complete": vulnerability["database_complete"],
        "blocked_finding_count": vulnerability["blocked_finding_count"],
        "unknown_finding_count": vulnerability["unknown_finding_count"],
    }
