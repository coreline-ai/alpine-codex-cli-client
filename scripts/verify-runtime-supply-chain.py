#!/usr/bin/env python3
"""Fail closed when the bundled runtime or package SBOM drifts."""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime_supply_chain import SupplyChainError, verify_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    try:
        result = verify_project(args.project_root)
    except (KeyError, OSError, SupplyChainError) as error:
        print(f"runtime supply-chain integrity: FAIL ({error})")
        return 1
    print(
        "runtime supply-chain integrity: PASS "
        f"(packages={result['package_count']}, python_prebundled="
        f"{str(result['python_prebundled']).lower()}, "
        f"vulnerability_db_complete="
        f"{str(result['vulnerability_database_complete']).lower()}, "
        f"recorded_high_critical={result['blocked_finding_count']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
