#!/usr/bin/env python3
"""Reject likely OAuth/account/chat material in explicitly supplied redacted evidence files."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


PATTERNS = {
    "oauth-url": re.compile(
        r"https://(?:[a-z0-9-]+\.)?(?:x\.ai|openai\.com|chatgpt\.com)/(?:[^\s?#]+)?\?[^\s]+",
        re.I,
    ),
    "device-code": re.compile(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4}){1,3}\b"),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "credential-field": re.compile(
        r'''(?i)(?:["']?(?:access_token|refresh_token|id_token|bearer|user_code|device_code)["']?)\s*[:=]\s*\S+'''
    ),
    "raw-chat-field": re.compile(r'''(?i)["'](?:prompt|response|message_content)["']\s*:'''),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        if not path.is_file():
            raise SystemExit("evidence file unavailable")
        text = path.read_text(encoding="utf-8", errors="strict")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                raise SystemExit(f"sensitive evidence violation: {label} ({path.name})")
    print("sensitive evidence scan: PASS")


if __name__ == "__main__":
    main()
