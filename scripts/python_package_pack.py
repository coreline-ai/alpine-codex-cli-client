#!/usr/bin/env python3
"""Validate and prepare an APK-contained Alpine Python package pack.

This module is deliberately standard-library only. It never downloads packages and accepts only
an already-local, hash-locked production input directory.
"""

from __future__ import annotations

import argparse
import io
import hashlib
import json
import os
import re
import shutil
import tarfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


LOCK_NAME = "python-pack.lock.json"
STATUS_NAME = "pack-status.json"
ASSET_DIRECTORY = "alpine-python-pack"
MAX_PACKAGES = 128
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_METADATA_MEMBER_BYTES = 8 * 1024 * 1024
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]*")
SAFE_PACK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class PythonPackagePackError(ValueError):
    """Raised when a local package pack is unsafe, incomplete, or not reproducible."""


@dataclass(frozen=True)
class PackageEntry:
    file: str
    name: str
    version: str
    size: int
    sha256: str


@dataclass(frozen=True)
class FileEntry:
    file: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PythonPackagePackLock:
    pack_id: str
    alpine_version: str
    architecture: str
    production: bool
    packages: tuple[PackageEntry, ...]
    sbom: FileEntry


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PythonPackagePackError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise PythonPackagePackError(f"{label} must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PythonPackagePackError(f"{label} fields are not exact")


def _require_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PythonPackagePackError(f"{label} is invalid")
    return value


def _require_string(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PythonPackagePackError(f"{label} is invalid")
    return value


def _require_regular_file(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise PythonPackagePackError(f"{label} path is unsafe")
    path = root.joinpath(*pure.parts)
    try:
        if path.is_symlink() or not path.is_file():
            raise PythonPackagePackError(f"{label} is missing or not a regular file")
        if root.resolve() not in path.resolve().parents:
            raise PythonPackagePackError(f"{label} escapes the pack directory")
    except OSError as error:
        raise PythonPackagePackError(f"{label} cannot be inspected") from error
    return path


def _parse_file_entry(value: Any, label: str, expected_file: str | None = None) -> FileEntry:
    if not isinstance(value, dict):
        raise PythonPackagePackError(f"{label} must be an object")
    _require_exact_keys(value, {"file", "size", "sha256"}, label)
    file_name = value["file"]
    if not isinstance(file_name, str) or (expected_file is not None and file_name != expected_file):
        raise PythonPackagePackError(f"{label} file is invalid")
    return FileEntry(
        file=file_name,
        size=_require_int(value["size"], f"{label} size", 1, MAX_PACKAGE_BYTES),
        sha256=_require_string(value["sha256"], SHA256, f"{label} SHA-256"),
    )


def _package_metadata(path: Path) -> dict[str, str]:
    """Read the signed Alpine v2 APK metadata members without executing package scripts."""
    try:
        remaining = path.read_bytes()
    except OSError as error:
        raise PythonPackagePackError("Alpine package cannot be read") from error
    members: list[bytes] = []
    for _ in range(2):
        if not remaining.startswith(b"\x1f\x8b"):
            raise PythonPackagePackError("Alpine package gzip member is missing")
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            member = decompressor.decompress(remaining, MAX_METADATA_MEMBER_BYTES + 1)
        except zlib.error as error:
            raise PythonPackagePackError("Alpine package gzip member is invalid") from error
        if not decompressor.eof or len(member) > MAX_METADATA_MEMBER_BYTES:
            raise PythonPackagePackError("Alpine package metadata member is too large")
        members.append(member)
        remaining = decompressor.unused_data
    if not remaining.startswith(b"\x1f\x8b"):
        raise PythonPackagePackError("Alpine package data member is missing")

    try:
        with tarfile.open(fileobj=io.BytesIO(members[0]), mode="r:") as signature_tar:
            signatures = [
                item
                for item in signature_tar.getmembers()
                if item.isfile() and item.name.startswith(".SIGN.RSA.")
            ]
            if len(signatures) != 1 or not 1 <= signatures[0].size <= 64 * 1024:
                raise PythonPackagePackError("Alpine package signature member is invalid")
        with tarfile.open(fileobj=io.BytesIO(members[1]), mode="r:") as control_tar:
            pkginfo = control_tar.getmember(".PKGINFO")
            if not pkginfo.isfile() or not 1 <= pkginfo.size <= 1024 * 1024:
                raise PythonPackagePackError("Alpine .PKGINFO is invalid")
            extracted = control_tar.extractfile(pkginfo)
            if extracted is None:
                raise PythonPackagePackError("Alpine .PKGINFO is unavailable")
            text = extracted.read().decode("utf-8")
    except (KeyError, tarfile.TarError, UnicodeDecodeError, OSError) as error:
        raise PythonPackagePackError("Alpine package metadata tar is invalid") from error
    metadata: dict[str, str] = {}
    for raw_line in text.splitlines():
        if " = " not in raw_line:
            continue
        key, value = raw_line.split(" = ", 1)
        if key in {"pkgname", "pkgver", "arch"}:
            if key in metadata or not value:
                raise PythonPackagePackError("Alpine .PKGINFO identity is invalid")
            metadata[key] = value
    if set(metadata) != {"pkgname", "pkgver", "arch"}:
        raise PythonPackagePackError("Alpine .PKGINFO identity is incomplete")
    return metadata


def read_lock(root: Path) -> PythonPackagePackLock:
    lock_path = _require_regular_file(root, LOCK_NAME, "Python package lock")
    value = _load_json(lock_path, "Python package lock")
    _require_exact_keys(
        value,
        {"schema", "pack_id", "alpine_version", "architecture", "production", "packages", "sbom"},
        "Python package lock",
    )
    if value["schema"] != 1 or isinstance(value["schema"], bool):
        raise PythonPackagePackError("Python package lock schema is unsupported")
    pack_id = _require_string(value["pack_id"], SAFE_PACK_ID, "pack ID")
    alpine_version = _require_string(value["alpine_version"], SAFE_VERSION, "Alpine version")
    if value["architecture"] != "aarch64":
        raise PythonPackagePackError("Python package architecture must be aarch64")
    if not isinstance(value["production"], bool):
        raise PythonPackagePackError("production marker must be boolean")
    packages_value = value["packages"]
    if not isinstance(packages_value, list) or not 1 <= len(packages_value) <= MAX_PACKAGES:
        raise PythonPackagePackError("package list size is invalid")
    packages: list[PackageEntry] = []
    for index, item in enumerate(packages_value):
        label = f"package[{index}]"
        if not isinstance(item, dict):
            raise PythonPackagePackError(f"{label} must be an object")
        _require_exact_keys(item, {"file", "name", "version", "size", "sha256"}, label)
        file_name = item["file"]
        if not isinstance(file_name, str):
            raise PythonPackagePackError(f"{label} file is invalid")
        pure = PurePosixPath(file_name)
        if (
            len(pure.parts) != 2
            or pure.parts[0] != "packages"
            or not pure.parts[1].endswith(".apk")
            or SAFE_COMPONENT.fullmatch(pure.parts[1]) is None
        ):
            raise PythonPackagePackError(f"{label} file is unsafe")
        packages.append(
            PackageEntry(
                file=file_name,
                name=_require_string(item["name"], SAFE_COMPONENT, f"{label} name"),
                version=_require_string(item["version"], SAFE_VERSION, f"{label} version"),
                size=_require_int(item["size"], f"{label} size", 1, MAX_PACKAGE_BYTES),
                sha256=_require_string(item["sha256"], SHA256, f"{label} SHA-256"),
            )
        )
    if len({item.file for item in packages}) != len(packages):
        raise PythonPackagePackError("package file is duplicated")
    if len({item.name for item in packages}) != len(packages):
        raise PythonPackagePackError("package name is duplicated")
    if "python3" not in {item.name for item in packages}:
        raise PythonPackagePackError("python3 package is missing")
    if sum(item.size for item in packages) > MAX_TOTAL_BYTES:
        raise PythonPackagePackError("package pack is too large")
    return PythonPackagePackLock(
        pack_id=pack_id,
        alpine_version=alpine_version,
        architecture="aarch64",
        production=value["production"],
        packages=tuple(packages),
        sbom=_parse_file_entry(value["sbom"], "SBOM", expected_file="sbom.spdx.json"),
    )


def validate_pack(root: Path, *, require_production: bool = False) -> PythonPackagePackLock:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise PythonPackagePackError("Python package pack directory is unavailable")
    lock = read_lock(root)
    if require_production and not lock.production:
        raise PythonPackagePackError("test fixture cannot be used for production")

    expected_root = {LOCK_NAME, lock.sbom.file, "packages"}
    try:
        actual_root = {entry.name for entry in root.iterdir()}
    except OSError as error:
        raise PythonPackagePackError("Python package pack directory cannot be listed") from error
    if actual_root != expected_root:
        raise PythonPackagePackError("Python package pack has unexpected root entries")
    packages_dir = root / "packages"
    if packages_dir.is_symlink() or not packages_dir.is_dir():
        raise PythonPackagePackError("packages directory is invalid")
    expected_package_names = {PurePosixPath(item.file).name for item in lock.packages}
    actual_package_names = {entry.name for entry in packages_dir.iterdir()}
    if actual_package_names != expected_package_names:
        raise PythonPackagePackError("package lock coverage mismatch")

    for item in (*lock.packages, lock.sbom):
        path = _require_regular_file(root, item.file, item.file)
        if path.stat().st_size != item.size or sha256_file(path) != item.sha256:
            raise PythonPackagePackError(f"locked file mismatch: {item.file}")
    for item in lock.packages:
        metadata = _package_metadata(root / item.file)
        if (
            metadata["pkgname"] != item.name
            or metadata["pkgver"] != item.version
            or metadata["arch"] not in {lock.architecture, "noarch"}
        ):
            raise PythonPackagePackError(f"Alpine package identity mismatch: {item.file}")

    sbom = _load_json(root / lock.sbom.file, "Python package SBOM")
    if sbom.get("spdxVersion") != "SPDX-2.3" or not isinstance(sbom.get("packages"), list):
        raise PythonPackagePackError("Python package SBOM is not SPDX-2.3")
    return lock


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_assets(source: Path, output_root: Path) -> dict[str, Any]:
    """Create one deterministic generated asset directory without any network fallback."""
    output_root = output_root.resolve()
    if output_root.exists():
        if output_root.is_symlink() or not output_root.is_dir():
            raise PythonPackagePackError("generated asset output is unsafe")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    if not source.exists():
        status = {"schema": 1, "available": False, "reason": "local_pack_not_provided"}
        _write_json(output_root / STATUS_NAME, status)
        return status

    lock = validate_pack(source, require_production=True)
    shutil.copy2(source / LOCK_NAME, output_root / LOCK_NAME)
    shutil.copy2(source / lock.sbom.file, output_root / lock.sbom.file)
    package_output = output_root / "packages"
    package_output.mkdir()
    for item in lock.packages:
        shutil.copy2(source / item.file, output_root / item.file)
    status = {
        "schema": 1,
        "available": True,
        "pack_id": lock.pack_id,
        "lock_sha256": sha256_file(output_root / LOCK_NAME),
        "package_count": len(lock.packages),
        "production": True,
    }
    _write_json(output_root / STATUS_NAME, status)
    validate_asset_pack(output_root, require_production=True)
    return status


def validate_asset_pack(root: Path, *, require_production: bool = False) -> PythonPackagePackLock:
    status = _load_json(root / STATUS_NAME, "Python package pack status")
    if status.get("schema") != 1 or status.get("available") is not True:
        raise PythonPackagePackError("Python package assets are unavailable")
    temporary = root.parent / f".{root.name}.validation-{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    try:
        for entry in root.iterdir():
            if entry.name == STATUS_NAME:
                continue
            destination = temporary / entry.name
            if entry.is_dir() and not entry.is_symlink():
                shutil.copytree(entry, destination)
            elif entry.is_file() and not entry.is_symlink():
                shutil.copy2(entry, destination)
            else:
                raise PythonPackagePackError("Python package assets contain an unsafe entry")
        lock = validate_pack(temporary, require_production=require_production)
        if (
            status.get("production") is not True
            or status.get("pack_id") != lock.pack_id
            or status.get("package_count") != len(lock.packages)
            or status.get("lock_sha256") != sha256_file(temporary / LOCK_NAME)
        ):
            raise PythonPackagePackError("Python package status does not match the lock")
        return lock
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--prepare", action="store_true")
    operation.add_argument("--verify-source", action="store_true")
    operation.add_argument("--verify-assets", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-production", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.prepare:
            if arguments.source is None or arguments.output is None:
                raise PythonPackagePackError("prepare requires source and output")
            status = prepare_assets(arguments.source, arguments.output)
            print(
                "Python package assets: "
                + (f"READY ({status['package_count']} packages)" if status["available"] else "UNAVAILABLE")
            )
        elif arguments.verify_source:
            if arguments.source is None:
                raise PythonPackagePackError("source verification requires source")
            lock = validate_pack(arguments.source, require_production=arguments.require_production)
            print(f"Python package source: PASS ({len(lock.packages)} packages)")
        else:
            if arguments.output is None:
                raise PythonPackagePackError("asset verification requires output")
            lock = validate_asset_pack(arguments.output, require_production=arguments.require_production)
            print(f"Python package assets: PASS ({len(lock.packages)} packages)")
    except (OSError, PythonPackagePackError) as error:
        print(f"Python package pack: FAIL ({error})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
