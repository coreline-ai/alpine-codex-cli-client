from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.offline_rootfs_builder import build_rootfs, staging_tree_sha256
from scripts.runtime_supply_chain import (
    SupplyChainError,
    package_inventory_sha256,
    parse_apk_installed_database,
    read_rootfs_inventory,
    sha256,
)


def package_record(name: str, version: str, license_id: str = "MIT") -> str:
    return "\n".join(
        (
            f"P:{name}",
            f"V:{version}",
            "A:aarch64",
            f"L:{license_id}",
            f"o:{name}",
            f"c:{'b' * 40}",
            f"U:https://example.invalid/{name}",
            f"C:Q1-{name}-checksum",
        )
    )


def make_staging(root: Path) -> str:
    database = "\n\n".join(
        (
            package_record("musl", "1.2.6-r0"),
            package_record("python3", "3.12.11-r0", "Python-2.0"),
        )
    )
    for relative in ("etc/apk", "lib/apk/db", "usr/bin", "usr/lib"):
        (root / relative).mkdir(parents=True, mode=0o755, exist_ok=True)
    (root / "etc/alpine-release").write_text("3.22.1\n")
    (root / "etc/apk/repositories").write_text("# offline runtime: repositories disabled\n")
    (root / "lib/apk/db/installed").write_text(database)
    python = root / "usr/bin/python3.12"
    python.write_bytes(b"offline-python-fixture")
    python.chmod(0o755)
    (root / "usr/bin/python3").symlink_to("python3.12")
    return database


def write_lock(root: Path, staging: Path, sources: Path, database: str) -> Path:
    source = sources / "reviewed-packages.lock"
    source.write_bytes(b"reviewed local package set")
    packages = parse_apk_installed_database(database)
    document = {
        "schema_version": 1,
        "alpine_version": "3.22.1",
        "apk_architecture": "aarch64",
        "package_inventory_sha256": package_inventory_sha256(packages),
        "staging_tree_sha256": staging_tree_sha256(staging),
        "python_package_version": "3.12.11-r0",
        "source_date_epoch": 0,
        "source_inputs": [
            {
                "path": source.name,
                "sha256": sha256(source),
                "size": source.stat().st_size,
            }
        ],
    }
    path = root / "package-lock.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


class OfflineRootfsBuilderTest(unittest.TestCase):
    def test_build_is_deterministic_offline_and_contains_python_provenance(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            staging = root / "staging"
            sources = root / "sources"
            staging.mkdir()
            sources.mkdir()
            database = make_staging(staging)
            lock = write_lock(root, staging, sources, database)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            result = build_rootfs(staging, lock, sources, first)
            build_rootfs(staging, lock, sources, second)
            self.assertEqual(sha256(first), sha256(second))
            self.assertEqual(result["output_sha256"], sha256(first))
            inventory = read_rootfs_inventory(first)
            self.assertEqual("3.22.1", inventory.alpine_version)
            self.assertTrue(inventory.python_prebundled)
            self.assertEqual(["musl", "python3"], [item.name for item in inventory.packages])
            import tarfile

            with tarfile.open(first, "r:gz") as archive:
                provenance = archive.extractfile(
                    "./usr/share/alpine-codex/runtime-provenance.json"
                )
                self.assertIsNotNone(provenance)
                payload = json.loads(provenance.read())
            self.assertEqual(result["staging_tree_sha256"], payload["staging_tree_sha256"])

    def test_enabled_repository_and_staging_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            staging = root / "staging"
            sources = root / "sources"
            staging.mkdir()
            sources.mkdir()
            database = make_staging(staging)
            lock = write_lock(root, staging, sources, database)
            (staging / "etc/apk/repositories").write_text(
                "https://dl-cdn.alpinelinux.org/alpine/latest-stable/main\n"
            )
            with self.assertRaises(SupplyChainError):
                build_rootfs(staging, lock, sources, root / "networked.tar.gz")
            (staging / "etc/apk/repositories").write_text(
                "# offline runtime: repositories disabled\n"
            )
            (staging / "usr/lib/unreviewed").write_text("drift")
            with self.assertRaises(SupplyChainError):
                build_rootfs(staging, lock, sources, root / "drift.tar.gz")

    def test_escaping_symlink_and_output_inside_staging_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            staging = root / "staging"
            sources = root / "sources"
            staging.mkdir()
            sources.mkdir()
            database = make_staging(staging)
            lock = write_lock(root, staging, sources, database)
            with self.assertRaises(SupplyChainError):
                build_rootfs(staging, lock, sources, staging / "rootfs.tar.gz")
            os.symlink("../../../outside", staging / "usr/bin/escape")
            with self.assertRaises(SupplyChainError):
                build_rootfs(staging, lock, sources, root / "escape.tar.gz")


if __name__ == "__main__":
    unittest.main()
