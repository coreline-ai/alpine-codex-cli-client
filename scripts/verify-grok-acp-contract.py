#!/usr/bin/env python3
"""Verify the pinned Grok ACP allowlist and absence of blocked wire methods."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


EXPECTED_FORBIDDEN = {
    "_x.ai/auth/getBearerToken",
    "_x.ai/getApiKey",
    "_x.ai/setApiKey",
    "_x.ai/futureUnknown",
}
EXPECTED_PUBLIC_SUPERVISOR_METHODS = {
    "add_notification_listener",
    "auth_info",
    "authenticate",
    "cancel_auth",
    "cancel_session",
    "close_session",
    "get_auth_url",
    "list_models",
    "load_session",
    "logout",
    "new_session",
    "prompt",
    "resume_session",
    "set_session_model",
    "start",
    "stop",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def enum_values(tree, class_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            values = set()
            for child in node.body:
                if isinstance(child, ast.Assign) and isinstance(child.value, ast.Constant):
                    if isinstance(child.value.value, str):
                        values.add(child.value.value)
            return values
    raise SystemExit(f"missing enum: {class_name}")


def public_methods(tree, class_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
                and not any(
                    isinstance(decorator, ast.Name) and decorator.id == "property"
                    for decorator in child.decorator_list
                )
            }
    raise SystemExit(f"missing class: {class_name}")


def verify(root):
    fixture = json.loads((root / "tests/fixtures/grok-acp-v1.0.0.json").read_text())
    if fixture.get("version") != "1.0.0" or fixture.get("schemaVersion") != 1:
        raise SystemExit("Grok ACP fixture version invalid")
    forbidden = set(fixture.get("forbiddenRequestMethods", []))
    if forbidden != EXPECTED_FORBIDDEN:
        raise SystemExit("Grok ACP forbidden list drift")

    contract_path = root / "codex_gateway/grok_acp/contract.py"
    process_path = root / "codex_gateway/grok_acp/process.py"
    contract_tree = ast.parse(contract_path.read_text(), contract_path.as_posix())
    process_tree = ast.parse(process_path.read_text(), process_path.as_posix())
    process_text = process_path.read_text()
    entrypoint_text = (root / "codex_gateway/agent_gateway.py").read_text()
    allowed = enum_values(contract_tree, "_RequestMethod")
    if allowed != set(fixture.get("allowedRequestMethods", [])):
        raise SystemExit("Grok ACP request allowlist drift")
    if any(method.startswith("x.ai/") for method in allowed):
        raise SystemExit("unprefixed Grok extension method reached the wire allowlist")
    if allowed & forbidden:
        raise SystemExit("forbidden Grok ACP method is allowlisted")
    if public_methods(process_tree, "GrokAcpSupervisor") != EXPECTED_PUBLIC_SUPERVISOR_METHODS:
        raise SystemExit("Grok supervisor public surface drift")
    for required in (
        '"close_fds": True',
        '"cwd": self._spec.working_directory',
        "POST_INITIALIZE_STABILITY_SECONDS",
        "_RequestMethod.AUTH_INFO",
        "_spawn_owner_loop",
        'name="grok-acp-spawn-owner"',
    ):
        if required not in process_text:
            raise SystemExit("Grok production spawn hardening drift")
    if "os.umask(CHILD_UMASK)" not in entrypoint_text:
        raise SystemExit("Gateway private process umask drift")

    shipping = tuple((root / "codex_gateway/grok_acp").glob("*.py"))
    for path in shipping:
        text = path.read_text()
        forbidden_source_spellings = forbidden | {
            method[1:] for method in forbidden if method.startswith("_")
        }
        for method in forbidden_source_spellings:
            if method in text:
                raise SystemExit(f"forbidden Grok ACP method shipped in {path.name}")
        if "preexec_fn" in text:
            raise SystemExit(f"thread-unsafe Grok child preexec hook shipped in {path.name}")
        if path == process_path and "umask=" in text:
            raise SystemExit("per-child Grok umask disables the production spawn contract")
    print("Grok ACP contract: PASS")


if __name__ == "__main__":
    verify(parse_args().project_root.resolve())
