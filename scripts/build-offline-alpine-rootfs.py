#!/usr/bin/env python3
"""Build a deterministic Alpine rootfs only from a reviewed local staging tree and locks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from offline_rootfs_builder import build_rootfs
from runtime_supply_chain import SupplyChainError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--package-lock", type=Path, required=True)
    parser.add_argument("--source-artifacts-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_rootfs(
            args.staging_root.resolve(),
            args.package_lock.resolve(),
            args.source_artifacts_root.resolve(),
            args.output.resolve(),
        )
    except (OSError, KeyError, SupplyChainError) as error:
        print(f"offline Alpine rootfs build: FAIL ({error})")
        return 1
    print(
        "offline Alpine rootfs build: PASS "
        + json.dumps(
            {
                "alpine_version": result["alpine_version"],
                "output_sha256": result["output_sha256"],
                "output_size": result["output_size"],
                "package_count": result["package_count"],
                "python_package_version": result["python_package_version"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
