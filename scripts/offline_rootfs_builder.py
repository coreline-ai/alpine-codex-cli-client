#!/usr/bin/env python3
"""Deterministically repack a reviewed, fully offline Alpine rootfs staging tree."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
from typing import Any

try:
    from .runtime_supply_chain import (
        MAX_EXTRACTED_ROOTFS_BYTES,
        MAX_ROOTFS_MEMBER_BYTES,
        MAX_TAR_ENTRIES,
        SupplyChainError,
        package_inventory_sha256,
        parse_apk_installed_database,
        sha256,
    )
except ImportError:  # Direct script execution keeps scripts/ on sys.path.
    from runtime_supply_chain import (
        MAX_EXTRACTED_ROOTFS_BYTES,
        MAX_ROOTFS_MEMBER_BYTES,
        MAX_TAR_ENTRIES,
        SupplyChainError,
        package_inventory_sha256,
        parse_apk_installed_database,
        sha256,
    )


PROVENANCE_PATH = "usr/share/alpine-codex/runtime-provenance.json"
MAX_PACKAGE_LOCK_BYTES = 4 * 1024 * 1024


def _safe_relative_path(path: str) -> PurePosixPath:
    value = PurePosixPath(path)
    if (
        value.is_absolute()
        or not value.parts
        or ".." in value.parts
        or not 1 <= len(path) <= 4_096
        or any(ord(character) < 0x20 for character in path)
    ):
        raise SupplyChainError("offline rootfs lock contains an unsafe path")
    return value


def _safe_symlink(entry: PurePosixPath, target_name: str) -> bool:
    if not target_name or "\x00" in target_name:
        return False
    parts: list[str] = [] if target_name.startswith("/") else list(entry.parent.parts)
    for part in PurePosixPath(target_name).parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if not parts:
                return False
            parts.pop()
        else:
            parts.append(part)
    return bool(parts)


def _walk_tree(root: Path) -> list[tuple[PurePosixPath, Path, os.stat_result]]:
    if not root.is_dir() or root.is_symlink():
        raise SupplyChainError("offline rootfs staging root is not a real directory")
    entries: list[tuple[PurePosixPath, Path, os.stat_result]] = []

    def visit(directory: Path, relative: PurePosixPath) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: os.fsencode(item.name))
        except OSError as error:
            raise SupplyChainError("offline rootfs staging tree cannot be read") from error
        for child in children:
            if (
                not child.name
                or child.name in {".", ".."}
                or any(ord(character) < 0x20 for character in child.name)
            ):
                raise SupplyChainError("offline rootfs staging entry name is invalid")
            child_relative = relative / child.name
            if child_relative.as_posix() == PROVENANCE_PATH:
                raise SupplyChainError("offline rootfs staging tree predefines provenance")
            child_path = Path(child.path)
            metadata = child.stat(follow_symlinks=False)
            mode = metadata.st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                raise SupplyChainError("offline rootfs staging tree contains a special file")
            if mode & (stat.S_ISUID | stat.S_ISGID):
                raise SupplyChainError("offline rootfs staging tree contains setuid/setgid bits")
            if stat.S_ISREG(mode):
                if metadata.st_nlink != 1:
                    raise SupplyChainError("offline rootfs staging tree contains a hard-linked file")
                if metadata.st_size < 0 or metadata.st_size > MAX_ROOTFS_MEMBER_BYTES:
                    raise SupplyChainError("offline rootfs staging file is oversized")
                if mode & stat.S_IWOTH:
                    raise SupplyChainError("offline rootfs staging file is world writable")
            if stat.S_ISDIR(mode) and mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
                raise SupplyChainError("offline rootfs world-writable directory lacks sticky bit")
            if stat.S_ISLNK(mode) and not _safe_symlink(
                child_relative, os.readlink(child_path)
            ):
                raise SupplyChainError("offline rootfs staging symlink escapes the root")
            entries.append((child_relative, child_path, metadata))
            if len(entries) > MAX_TAR_ENTRIES:
                raise SupplyChainError("offline rootfs staging tree contains too many entries")
            if stat.S_ISDIR(mode):
                visit(child_path, child_relative)

    visit(root, PurePosixPath())
    total = sum(metadata.st_size for _, _, metadata in entries if stat.S_ISREG(metadata.st_mode))
    if total > MAX_EXTRACTED_ROOTFS_BYTES:
        raise SupplyChainError("offline rootfs staging tree is oversized")
    return entries


def staging_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for relative, path, metadata in _walk_tree(root):
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            kind = "dir"
            content = ""
            size = 0
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            content = os.readlink(path)
            size = 0
        else:
            kind = "file"
            content = sha256(path)
            size = metadata.st_size
        row = json.dumps(
            {
                "content": content,
                "kind": kind,
                "mode": mode,
                "path": relative.as_posix(),
                "size": size,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest.update(len(row).to_bytes(8, "big"))
        digest.update(row)
    return digest.hexdigest()


def load_package_lock(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PACKAGE_LOCK_BYTES:
        raise SupplyChainError("offline rootfs package lock is missing or oversized")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SupplyChainError("offline rootfs package lock cannot be read") from error
    if document.get("schema_version") != 1:
        raise SupplyChainError("offline rootfs package lock schema is unsupported")
    return document


def _validate_source_inputs(lock: dict[str, Any], artifacts_root: Path) -> None:
    inputs = lock.get("source_inputs")
    if not isinstance(inputs, list) or not inputs or len(inputs) > 4_096:
        raise SupplyChainError("offline rootfs source input lock is incomplete")
    if not artifacts_root.is_dir() or artifacts_root.is_symlink():
        raise SupplyChainError("offline rootfs source artifact root is not a real directory")
    root = artifacts_root.resolve()
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict):
            raise SupplyChainError("offline rootfs source input is invalid")
        relative = _safe_relative_path(str(item.get("path", "")))
        key = relative.as_posix()
        if key in seen:
            raise SupplyChainError("offline rootfs source input is duplicated")
        seen.add(key)
        candidate = root / Path(*relative.parts)
        if candidate.is_symlink():
            raise SupplyChainError("offline rootfs source input cannot be a symlink")
        path = candidate.resolve()
        if root not in path.parents or not path.is_file():
            raise SupplyChainError("offline rootfs source input escapes or is missing")
        expected_size = item.get("size")
        expected_hash = item.get("sha256")
        if not isinstance(expected_size, int) or expected_size <= 0:
            raise SupplyChainError("offline rootfs source input size is invalid")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise SupplyChainError("offline rootfs source input checksum is invalid")
        if path.stat().st_size != expected_size or sha256(path) != expected_hash:
            raise SupplyChainError("offline rootfs source input drift")


def _resolve_staging_executable(staging_root: Path, relative: PurePosixPath) -> Path:
    current = relative
    for _ in range(16):
        path = staging_root / Path(*current.parts)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SupplyChainError("offline rootfs executable is missing") from error
        if stat.S_ISREG(metadata.st_mode):
            if not metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                raise SupplyChainError("offline rootfs executable lacks execute permission")
            return path
        if not stat.S_ISLNK(metadata.st_mode):
            raise SupplyChainError("offline rootfs executable has an invalid type")
        target = os.readlink(path)
        if not _safe_symlink(current, target):
            raise SupplyChainError("offline rootfs executable symlink escapes the root")
        parts: list[str] = [] if target.startswith("/") else list(current.parent.parts)
        for part in PurePosixPath(target).parts:
            if part in {"", ".", "/"}:
                continue
            if part == "..":
                parts.pop()
            else:
                parts.append(part)
        current = PurePosixPath(*parts)
    raise SupplyChainError("offline rootfs executable symlink chain is too deep")


def validate_staging(
    staging_root: Path,
    package_lock: dict[str, Any],
    source_artifacts_root: Path,
) -> dict[str, Any]:
    _validate_source_inputs(package_lock, source_artifacts_root)
    required = {
        "alpine_version": package_lock.get("alpine_version"),
        "apk_architecture": package_lock.get("apk_architecture"),
        "package_inventory_sha256": package_lock.get("package_inventory_sha256"),
        "staging_tree_sha256": package_lock.get("staging_tree_sha256"),
        "python_package_version": package_lock.get("python_package_version"),
    }
    if not all(isinstance(value, str) and value for value in required.values()):
        raise SupplyChainError("offline rootfs package lock metadata is incomplete")
    source_date_epoch = package_lock.get("source_date_epoch")
    if not isinstance(source_date_epoch, int) or not 0 <= source_date_epoch <= 2_147_483_647:
        raise SupplyChainError("offline rootfs source date epoch is invalid")

    release_file = staging_root / "etc/alpine-release"
    database_file = staging_root / "lib/apk/db/installed"
    repositories_file = staging_root / "etc/apk/repositories"
    python_file = staging_root / "usr/bin/python3"
    for path in (release_file, database_file, repositories_file):
        if not path.is_file() or path.is_symlink():
            raise SupplyChainError("offline rootfs required metadata file is missing")
    _resolve_staging_executable(staging_root, PurePosixPath("usr/bin/python3"))
    try:
        alpine_version = release_file.read_text(encoding="ascii").strip()
        database = database_file.read_text(encoding="utf-8")
        repositories = repositories_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SupplyChainError("offline rootfs required metadata cannot be read") from error
    if alpine_version != required["alpine_version"]:
        raise SupplyChainError("offline rootfs Alpine version drift")
    if any(line.strip() and not line.lstrip().startswith("#") for line in repositories.splitlines()):
        raise SupplyChainError("offline rootfs contains an enabled network repository")
    packages = parse_apk_installed_database(database)
    if {package.architecture for package in packages} != {required["apk_architecture"]}:
        raise SupplyChainError("offline rootfs package architecture drift")
    if package_inventory_sha256(packages) != required["package_inventory_sha256"]:
        raise SupplyChainError("offline rootfs package inventory drift")
    python_versions = [package.version for package in packages if package.name == "python3"]
    if python_versions != [required["python_package_version"]]:
        raise SupplyChainError("offline rootfs Python package lock drift")
    tree_hash = staging_tree_sha256(staging_root)
    if tree_hash != required["staging_tree_sha256"]:
        raise SupplyChainError("offline rootfs staging tree drift")
    return {
        "alpine_version": alpine_version,
        "apk_architecture": required["apk_architecture"],
        "package_count": len(packages),
        "package_inventory_sha256": required["package_inventory_sha256"],
        "python_package_version": required["python_package_version"],
        "source_date_epoch": source_date_epoch,
        "source_inputs": package_lock["source_inputs"],
        "staging_tree_sha256": tree_hash,
    }


def _tar_info(
    relative: PurePosixPath,
    metadata: os.stat_result,
    source_date_epoch: int,
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(f"./{relative.as_posix()}")
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mode = stat.S_IMODE(metadata.st_mode)
    info.mtime = source_date_epoch
    if stat.S_ISDIR(metadata.st_mode):
        info.type = tarfile.DIRTYPE
        info.size = 0
    elif stat.S_ISLNK(metadata.st_mode):
        info.type = tarfile.SYMTYPE
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.size = metadata.st_size
    return info


def build_rootfs(
    staging_root: Path,
    package_lock_path: Path,
    source_artifacts_root: Path,
    output: Path,
) -> dict[str, Any]:
    package_lock = load_package_lock(package_lock_path)
    provenance = validate_staging(staging_root, package_lock, source_artifacts_root)
    entries = _walk_tree(staging_root)
    payload = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode()
    staging_root = staging_root.resolve()
    output_parent = output.parent.resolve()
    if output_parent == staging_root or staging_root in output_parent.parents:
        raise SupplyChainError("offline rootfs output cannot be inside the staging tree")
    output_parent.mkdir(parents=True, exist_ok=True)
    output = output_parent / output.name
    if output.is_symlink():
        raise SupplyChainError("offline rootfs output cannot replace a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output_parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                    for relative, path, metadata in entries:
                        info = _tar_info(relative, metadata, provenance["source_date_epoch"])
                        if info.issym():
                            info.linkname = os.readlink(path)
                            archive.addfile(info)
                        elif info.isfile():
                            with path.open("rb") as source:
                                archive.addfile(info, source)
                        else:
                            archive.addfile(info)
                    info = tarfile.TarInfo(f"./{PROVENANCE_PATH}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mode = 0o444
                    info.mtime = provenance["source_date_epoch"]
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            raw.flush()
            os.fsync(raw.fileno())
        if staging_tree_sha256(staging_root) != provenance["staging_tree_sha256"]:
            raise SupplyChainError("offline rootfs staging tree changed during build")
        _validate_source_inputs(package_lock, source_artifacts_root)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {**provenance, "output_sha256": sha256(output), "output_size": output.stat().st_size}
