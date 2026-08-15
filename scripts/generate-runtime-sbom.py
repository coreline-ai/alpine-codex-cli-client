#!/usr/bin/env python3
"""Generate the deterministic package-level SPDX document for the locked Alpine runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_supply_chain import (
    SupplyChainError,
    build_spdx_document,
    load_lock,
    read_rootfs_inventory,
    sha256,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        project_root = args.project_root.resolve()
        lock = load_lock(project_root / "alpine-runtime-pack-bundled/runtime-lock.json")
        rootfs = project_root / lock["artifacts"]["rootfs"]["path"]
        document = build_spdx_document(read_rootfs_inventory(rootfs), lock, sha256(rootfs))
        output = args.output.resolve()
        if project_root != output and project_root not in output.parents:
            raise SupplyChainError("SBOM output must remain inside the project")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (KeyError, OSError, SupplyChainError) as error:
        print(f"runtime SBOM generation: FAIL ({error})")
        return 1
    print(f"runtime package-level SBOM: PASS ({len(document['packages']) - 3} APK packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
