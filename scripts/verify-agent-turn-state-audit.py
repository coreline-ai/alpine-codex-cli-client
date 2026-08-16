#!/usr/bin/env python3
"""Verify the two content-free Grok Stop lifecycle checkpoints from stdin."""

from __future__ import annotations

import sys


MAX_INPUT_BYTES = 1024
STARTED = "agent=grok state=started request_bound=1"
STOPPED = "agent=grok state=stop_requested dispatched=1"


def verify(raw: bytes) -> None:
    if not raw or len(raw) > MAX_INPUT_BYTES or b"\x00" in raw:
        raise ValueError("state audit input invalid")
    try:
        lines = [line.strip() for line in raw.decode("utf-8", errors="strict").splitlines() if line.strip()]
    except UnicodeDecodeError as error:
        raise ValueError("state audit input invalid") from error
    if lines != [STARTED, STOPPED]:
        raise ValueError("exact started/stop_requested sequence required")


def main() -> None:
    try:
        verify(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print("Grok stop state audit: PASS started=1 stop_requested=1")


if __name__ == "__main__":
    main()
