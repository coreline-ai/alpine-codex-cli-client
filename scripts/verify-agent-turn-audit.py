#!/usr/bin/env python3
"""Verify one content-free Grok terminal audit line from stdin without retaining it."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys


MAX_INPUT_BYTES = 4096
MAX_STREAM_EVENTS = 128
MAX_RETRY_ATTEMPTS = 32
AUDIT_PATTERN = re.compile(
    r"agent=grok outcome=(done|error) "
    r"prompt_dispatch=([0-9]{1,3}) "
    r"visible_delta=([0-9]{1,3}) "
    r"terminal=([0-9]{1,3}) "
    r"cancel=([0-9]{1,3}) "
    r"retry=(none|pre_output|post_output|strict_blocked|exhausted|auth_failed|failed) "
    r"retry_attempts=([0-9]{1,3}) "
    r"retry_max=([0-9]{1,3}) "
    r"profile_tool=([0-9]{1,3}) "
    r"profile_subagent=([0-9]{1,3}) "
    r"profile_mcp=([0-9]{1,3}) "
    r"profile_filesystem=([0-9]{1,3}) "
    r"profile_terminal=([0-9]{1,3})"
)


@dataclass(frozen=True)
class TurnAudit:
    outcome: str
    prompt_dispatch: int
    visible_delta: int
    terminal: int
    cancel: int
    retry: str
    retry_attempts: int
    retry_max: int
    profile_counts: tuple[int, int, int, int, int]


def parse_audit(raw: bytes) -> TurnAudit:
    if not raw or len(raw) > MAX_INPUT_BYTES or b"\x00" in raw:
        raise ValueError("audit input invalid")
    try:
        lines = [line.strip() for line in raw.decode("utf-8", errors="strict").splitlines() if line.strip()]
    except UnicodeDecodeError as error:
        raise ValueError("audit input invalid") from error
    if len(lines) != 1:
        raise ValueError("exactly one audit line required")
    match = AUDIT_PATTERN.fullmatch(lines[0])
    if match is None:
        raise ValueError("audit line shape invalid")
    values = match.groups()
    numbers = tuple(int(value) for value in values[1:5] + values[6:])
    return TurnAudit(
        outcome=values[0],
        prompt_dispatch=numbers[0],
        visible_delta=numbers[1],
        terminal=numbers[2],
        cancel=numbers[3],
        retry=values[5],
        retry_attempts=numbers[4],
        retry_max=numbers[5],
        profile_counts=numbers[6:11],
    )


def verify_audit(value: TurnAudit, mode: str) -> None:
    expected_outcome = "done" if mode == "chat" else "error"
    expected_cancel = 0 if mode == "chat" else 1
    if (
        value.outcome != expected_outcome
        or value.prompt_dispatch != 1
        or value.terminal != 1
        or value.cancel != expected_cancel
        or value.visible_delta > MAX_STREAM_EVENTS
        or any(value.profile_counts)
    ):
        raise ValueError("turn audit invariant failed")
    if mode == "chat" and value.visible_delta < 1:
        raise ValueError("chat emitted no visible stream delta")
    if value.retry == "none":
        if value.retry_attempts != 0 or value.retry_max != 0:
            raise ValueError("retry count invalid")
    elif value.retry == "pre_output":
        if not 1 <= value.retry_attempts <= value.retry_max <= MAX_RETRY_ATTEMPTS:
            raise ValueError("pre-output retry count invalid")
    else:
        raise ValueError("retry classification forbidden for successful E2E")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("chat", "stop"), required=True)
    args = parser.parse_args()
    try:
        value = parse_audit(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        verify_audit(value, args.mode)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(
        f"Grok {args.mode} audit: PASS dispatch=1 terminal=1 cancel={value.cancel} "
        f"retry={value.retry} profile=clean"
    )


if __name__ == "__main__":
    main()
