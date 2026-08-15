#!/usr/bin/env python3
"""Verify the exact project-owned Grok chat-only profile and optional APK asset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


PROFILE_RELATIVE = Path("grok-cli-pack/src/main/assets/grok-profile/chat-only.md")
LOCK_RELATIVE = Path("grok-cli-pack/src/main/assets/grok-profile/chat-only.lock.json")
APK_PROFILE = "assets/grok-profile/chat-only.md"
APK_LOCK = "assets/grok-profile/chat-only.lock.json"
EXPECTED_PROFILE_NAME = "alpine-chat-only"
EXPECTED_CLI_VERSION = "1.0.0"
EXPECTED_ALLOWED_SENTINEL = ("task",)
EXPECTED_DENIED_TOOLS = ("task", "search_tool", "use_tool")
EXPECTED_TOP_LEVEL_KEYS = {
    "name",
    "description",
    "promptMode",
    "permissionMode",
    "discoverSkills",
    "inheritSkills",
    "agentsMd",
    "skills",
    "tools",
    "disallowedTools",
    "mcpServers",
    "background",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--apk", type=Path)
    return parser.parse_args()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_scalar(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*([^\n]+)$", text)
    if match is None:
        raise SystemExit("Grok profile contract is incomplete")
    return match.group(1).strip()


def parse_list(text: str, key: str) -> tuple[str, ...]:
    match = re.search(rf"(?ms)^{re.escape(key)}:\s*\n((?:  - [^\n]+\n?)+)", text)
    if match is None:
        raise SystemExit("Grok profile list contract is incomplete")
    return tuple(
        line.removeprefix("  - ").strip()
        for line in match.group(1).splitlines()
    )


def verify_bytes(profile: bytes, lock_bytes: bytes) -> dict[str, object]:
    try:
        lock = json.loads(lock_bytes.decode("utf-8"))
        text = profile.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("Grok profile lock is invalid") from error
    expected_keys = {
        "schema_version",
        "profile_name",
        "file_name",
        "size",
        "sha256",
        "grok_cli_version",
    }
    if set(lock) != expected_keys:
        raise SystemExit("Grok profile lock fields changed")
    if (
        lock["schema_version"] != 1
        or lock["profile_name"] != EXPECTED_PROFILE_NAME
        or lock["file_name"] != PROFILE_RELATIVE.name
        or lock["grok_cli_version"] != EXPECTED_CLI_VERSION
        or lock["size"] != len(profile)
        or lock["sha256"] != digest(profile)
    ):
        raise SystemExit("Grok profile hash contract failed")
    if not text.startswith("---\n") or "\n---\n\n" not in text:
        raise SystemExit("Grok profile frontmatter is invalid")
    frontmatter = text.split("\n---\n\n", 1)[0].removeprefix("---\n")
    top_level_keys = re.findall(r"(?m)^([A-Za-z][A-Za-z0-9]*):", frontmatter)
    if len(top_level_keys) != len(set(top_level_keys)) or set(top_level_keys) != EXPECTED_TOP_LEVEL_KEYS:
        raise SystemExit("Grok profile top-level fields changed")
    if parse_scalar(text, "name") != EXPECTED_PROFILE_NAME:
        raise SystemExit("Grok profile name changed")
    expected_scalars = {
        "promptMode": "full",
        "permissionMode": "plan",
        "discoverSkills": "false",
        "inheritSkills": "false",
        "agentsMd": "false",
        "skills": "[]",
        "mcpServers": "[]",
        "background": "false",
    }
    if any(parse_scalar(text, key) != value for key, value in expected_scalars.items()):
        raise SystemExit("Grok profile safety setting changed")
    if parse_list(text, "tools") != EXPECTED_ALLOWED_SENTINEL:
        raise SystemExit("Grok profile sentinel allowlist changed")
    if parse_list(text, "disallowedTools") != EXPECTED_DENIED_TOOLS:
        raise SystemExit("Grok profile special tool denylist changed")
    forbidden = re.compile(
        r"(?im)^\s*-\s*(?:bash|run_terminal_cmd|read_file|search_replace|grep|list_dir|"
        r"web_search|web_fetch|spawn_subagent|mcp__|always-approve|plugin)"
    )
    if forbidden.search(text):
        raise SystemExit("Grok profile exposes a forbidden tool")
    return lock


def verify(project_root: Path, apk_path: Path | None) -> None:
    profile = (project_root / PROFILE_RELATIVE).read_bytes()
    lock_bytes = (project_root / LOCK_RELATIVE).read_bytes()
    verify_bytes(profile, lock_bytes)
    if apk_path is not None:
        with zipfile.ZipFile(apk_path) as archive:
            apk_profile = archive.read(APK_PROFILE)
            apk_lock = archive.read(APK_LOCK)
        if apk_profile != profile or apk_lock != lock_bytes:
            raise SystemExit("Grok profile APK asset differs from source")
        verify_bytes(apk_profile, apk_lock)
    print("Grok chat-only profile: PASS")


if __name__ == "__main__":
    arguments = parse_args()
    verify(
        arguments.project_root.resolve(),
        arguments.apk.resolve() if arguments.apk else None,
    )
