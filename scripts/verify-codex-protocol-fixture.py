#!/usr/bin/env python3
"""Fails closed when the pinned app-server surface drifts from the adapter."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def strings_used_as_requests(source: str) -> set[str]:
    return set(
        re.findall(
            r"(?:_supervisor|_require_rpc\(\))\.request\(\s*[\"']([^\"']+)[\"']",
            source,
        )
    )


def verify(project_root: Path) -> None:
    fixture_path = project_root / "tests/fixtures/codex-app-server-rust-v0.147.0.json"
    lock_path = project_root / "codex-cli-pack/codex-cli.lock.json"
    protocol_path = project_root / "codex_gateway/app_server/protocol.py"
    process_path = project_root / "codex_gateway/app_server/process.py"
    gateway_path = project_root / "codex_gateway/gateway.py"

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if fixture.get("fixture_version") != 1:
        raise SystemExit("protocol fixture version is unsupported")
    if fixture.get("codex_cli_version") != lock.get("version"):
        raise SystemExit("protocol fixture CLI version does not match the locked CLI")

    request_methods = strings_used_as_requests(protocol_path.read_text(encoding="utf-8"))
    request_methods.update(strings_used_as_requests(process_path.read_text(encoding="utf-8")))
    initialized = set(re.findall(r'"method":"([^\"]+)"', process_path.read_text(encoding="utf-8")))
    server_notifications = set(
        re.findall(r'if method == "([^\"]+)"', gateway_path.read_text(encoding="utf-8"))
    )

    expected_requests = set(fixture["client_requests"])
    expected_initialized = set(fixture["client_notifications"])
    expected_notifications = set(fixture["server_notifications"])
    if request_methods != expected_requests:
        raise SystemExit("pinned protocol request methods drifted")
    if initialized != expected_initialized:
        raise SystemExit("pinned protocol client notifications drifted")
    if server_notifications != expected_notifications:
        raise SystemExit("pinned protocol server notifications drifted")
    print("Codex protocol fixture: PASS")


if __name__ == "__main__":
    verify(parse_args().project_root.resolve())
