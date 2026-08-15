#!/usr/bin/env python3
"""Verify pinned dependencies and isolated caches without contacting a repository."""

from __future__ import annotations

import argparse
from pathlib import Path

from gradle_supply_chain import GradleSupplyChainError, verify_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        result = verify_project(args.project_root)
    except (OSError, GradleSupplyChainError) as error:
        print(f"Gradle supply-chain policy: FAIL ({error})")
        return 1
    print(
        "Gradle supply-chain policy: PASS "
        f"(module_locks={result['module_lock_count']}, "
        f"components={result['locked_component_count']}, "
        f"cache_disabled={str(result['build_cache_disabled']).lower()}, "
        f"release_locked={str(result['release_configurations_locked']).lower()}, "
        f"wrapper_checksum={str(result['wrapper_checksum_pinned']).lower()}, "
        f"verification_metadata={str(result['dependency_verification_present']).lower()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
