#!/usr/bin/env python3
"""Verifies the pinned Grok CLI lock, static AArch64 ELF, APK asset, and Git exclusion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import zipfile


class VerificationError(ValueError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_lock(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError("grok_lock_unavailable") from error
    if not isinstance(value, dict):
        raise VerificationError("grok_lock_invalid")
    verify_lock(value)
    return value


def verify_lock(value: dict[str, object]) -> None:
    string_fields = (
        "version",
        "target",
        "source_url",
        "artifact_name",
        "binary_name",
        "binary_sha256",
        "version_output",
        "source_repository",
        "source_repository_commit",
        "source_revision",
        "license",
    )
    if any(not isinstance(value.get(name), str) or not value[name] for name in string_fields):
        raise VerificationError("grok_lock_field_invalid")
    version = value["version"]
    artifact_name = value["artifact_name"]
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise VerificationError("grok_version_invalid")
    if value["target"] != "linux-aarch64-static":
        raise VerificationError("grok_target_invalid")
    if artifact_name != f"grok-{version}-linux-aarch64":
        raise VerificationError("grok_artifact_name_invalid")
    if value["source_url"] != f"https://x.ai/cli/{artifact_name}":
        raise VerificationError("grok_source_url_invalid")
    if value["binary_name"] != "grok":
        raise VerificationError("grok_binary_name_invalid")
    if not isinstance(value.get("binary_size"), int) or value["binary_size"] <= 0:
        raise VerificationError("grok_size_invalid")
    if re.fullmatch(r"[0-9a-f]{64}", value["binary_sha256"]) is None:
        raise VerificationError("grok_sha_invalid")
    if re.fullmatch(rf"grok {re.escape(version)} \([0-9a-f]{{10}}\)", value["version_output"]) is None:
        raise VerificationError("grok_version_output_invalid")
    if value["source_repository"] != "https://github.com/xai-org/grok-build":
        raise VerificationError("grok_repository_invalid")
    for name in ("source_repository_commit", "source_revision"):
        if re.fullmatch(r"[0-9a-f]{40}", value[name]) is None:
            raise VerificationError("grok_revision_invalid")
    if value["license"] != "Apache-2.0":
        raise VerificationError("grok_license_invalid")


def verify_binary(path: Path, lock: dict[str, object]) -> None:
    if not path.is_file() or path.stat().st_size != lock["binary_size"]:
        raise VerificationError("grok_binary_size_mismatch")
    if sha256(path) != lock["binary_sha256"]:
        raise VerificationError("grok_binary_sha_mismatch")
    with path.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:4] != b"\x7fELF":
            raise VerificationError("grok_binary_not_elf")
        if header[4] != 2 or header[5] != 1:
            raise VerificationError("grok_binary_not_elf64_le")
        if struct.unpack_from("<H", header, 18)[0] != 183:
            raise VerificationError("grok_binary_not_aarch64")
        program_offset = struct.unpack_from("<Q", header, 32)[0]
        program_size = struct.unpack_from("<H", header, 54)[0]
        program_count = struct.unpack_from("<H", header, 56)[0]
        if program_offset < 64 or program_size < 56 or program_count not in range(1, 257):
            raise VerificationError("grok_program_headers_invalid")
        if program_offset + program_size * program_count > path.stat().st_size:
            raise VerificationError("grok_program_headers_truncated")
        for index in range(program_count):
            source.seek(program_offset + program_size * index)
            raw_type = source.read(4)
            if len(raw_type) != 4:
                raise VerificationError("grok_program_headers_truncated")
            if struct.unpack("<I", raw_type)[0] == 3:
                raise VerificationError("grok_dynamic_interpreter_forbidden")


def verify_apk(path: Path, lock: dict[str, object]) -> None:
    if not path.is_file():
        raise VerificationError("debug_apk_unavailable")
    asset_name = "assets/grok-cli/grok"
    with zipfile.ZipFile(path) as archive:
        try:
            info = archive.getinfo(asset_name)
        except KeyError as error:
            raise VerificationError("grok_apk_asset_missing") from error
        if info.file_size != lock["binary_size"]:
            raise VerificationError("grok_apk_asset_size_mismatch")
        digest = hashlib.sha256()
        with archive.open(info) as source:
            for block in iter(lambda: source.read(64 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != lock["binary_sha256"]:
            raise VerificationError("grok_apk_asset_sha_mismatch")


def verify_git_exclusion(project_root: Path, lock: dict[str, object]) -> None:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=project_root).split(b"\0")
    history = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=project_root).splitlines()
    artifact_name = lock["artifact_name"]

    def forbidden(path: str) -> bool:
        normalized = "/" + path.lower().lstrip("/")
        return (
            Path(path).name == artifact_name
            or normalized.endswith("/grok-cli-pack/src/main/assets/grok")
            or normalized.endswith("/grok-cli-pack/src/debug/assets/grok")
            or "/grok-cli-pack/build/" in normalized
            or normalized.endswith((".apk", ".aab"))
        )

    for encoded in tracked:
        if encoded and forbidden(encoded.decode("utf-8")):
            raise VerificationError("grok_binary_tracked")
    for line in history:
        fields = line.decode("utf-8", errors="replace").split(" ", 1)
        if len(fields) == 2 and forbidden(fields[1]):
            raise VerificationError("grok_binary_present_in_history")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--apk", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = args.project_root.resolve()
    lock = read_lock(project_root / "grok-cli-pack/grok-cli.lock.json")
    verify_git_exclusion(project_root, lock)
    if args.binary is not None:
        verify_binary(args.binary.resolve(), lock)
    if args.apk is not None:
        verify_apk(args.apk.resolve(), lock)
    print("Grok CLI artifact: PASS")


if __name__ == "__main__":
    main()
