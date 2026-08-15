#!/usr/bin/env python3
"""CLI wrapper for public Android release policy verification."""

from __future__ import annotations

import argparse
from pathlib import Path

from release_policy import ReleasePolicyError, verify_project


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    try:
        status = verify_project(arguments.project_root)
    except ReleasePolicyError as error:
        raise SystemExit(f"release policy: FAIL ({error})") from error
    print(
        "release policy: PASS "
        f"(signing_inputs={status['external_signing_inputs']}, "
        f"asset_packs={status['distribution_asset_packs']}, "
        "private_keys_tracked=false)"
    )
