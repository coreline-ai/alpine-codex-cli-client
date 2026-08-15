from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

from scripts.runtime_supply_chain import (
    SupplyChainError,
    build_spdx_document,
    parse_apk_installed_database,
    read_rootfs_inventory,
    sha256,
    verify_vulnerability_snapshot,
    verify_project,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def copy_project_fixture(destination: Path) -> Path:
    lock = json.loads(
        (PROJECT_ROOT / "alpine-runtime-pack-bundled/runtime-lock.json").read_text()
    )
    for item in lock["artifacts"].values():
        relative = Path(item["path"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    for relative in (
        Path("alpine-runtime-pack-bundled/runtime-lock.json"),
        Path("alpine-runtime-pack-bundled/build.gradle.kts"),
        Path(
            "alpine-runtime-pack-bundled/src/main/kotlin/dev/alpine/runtime/pack/bundled/"
            "BundledRuntimeArtifactProvider.kt"
        ),
        Path("security_best_practices_report.md"),
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    return destination


def package_record(name: str = "busybox", version: str = "1.0-r0") -> str:
    return "\n".join(
        (
            f"P:{name}",
            f"V:{version}",
            "A:aarch64",
            "L:GPL-2.0-only",
            f"o:{name}",
            f"c:{'a' * 40}",
            f"U:https://example.invalid/{name}",
            "C:Q1fixed-checksum",
        )
    )


def write_rootfs(
    path: Path,
    database: str,
    *,
    python: bool = False,
    extra_names: tuple[str, ...] = (),
    unsafe_link: bool = False,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload, mode in (
            ("./etc/alpine-release", b"3.22.1\n", 0o644),
            ("./lib/apk/db/installed", database.encode(), 0o600),
        ):
            item = tarfile.TarInfo(name)
            item.size = len(payload)
            item.mode = mode
            archive.addfile(item, io.BytesIO(payload))
        if python:
            item = tarfile.TarInfo("./usr/bin/python3")
            item.size = 2
            item.mode = 0o755
            archive.addfile(item, io.BytesIO(b"#!"))
        for name in extra_names:
            item = tarfile.TarInfo(name)
            item.size = 1
            archive.addfile(item, io.BytesIO(b"x"))
        if unsafe_link:
            item = tarfile.TarInfo("./usr/bin/escape")
            item.type = tarfile.SYMTYPE
            item.linkname = "../../../outside"
            archive.addfile(item)


class RuntimeSupplyChainTest(unittest.TestCase):
    def test_inventory_is_sorted_bounded_and_detects_prebundled_python(self):
        with tempfile.TemporaryDirectory() as raw:
            rootfs = Path(raw) / "rootfs.tar.gz"
            write_rootfs(
                rootfs,
                package_record("zlib") + "\n\n" + package_record("busybox"),
                python=True,
            )
            inventory = read_rootfs_inventory(rootfs)
        self.assertEqual("3.22.1", inventory.alpine_version)
        self.assertEqual(["busybox", "zlib"], [item.name for item in inventory.packages])
        self.assertTrue(inventory.python_prebundled)

    def test_duplicate_package_fails_closed(self):
        database = package_record() + "\n\n" + package_record()
        with self.assertRaises(SupplyChainError):
            parse_apk_installed_database(database)

    def test_missing_license_and_malformed_revision_fail_closed(self):
        with self.assertRaises(SupplyChainError):
            parse_apk_installed_database(package_record().replace("L:GPL-2.0-only\n", ""))
        with self.assertRaises(SupplyChainError):
            parse_apk_installed_database(package_record().replace("a" * 40, "untrusted"))

    def test_unsafe_archive_member_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            rootfs = Path(raw) / "rootfs.tar.gz"
            write_rootfs(rootfs, package_record(), extra_names=("../escape",))
            with self.assertRaises(SupplyChainError):
                read_rootfs_inventory(rootfs)
        with tempfile.TemporaryDirectory() as raw:
            rootfs = Path(raw) / "rootfs.tar.gz"
            write_rootfs(rootfs, package_record(), unsafe_link=True)
            with self.assertRaises(SupplyChainError):
                read_rootfs_inventory(rootfs)

    def test_spdx_contains_each_apk_package_and_native_artifact(self):
        packages = parse_apk_installed_database(
            package_record("busybox") + "\n\n" + package_record("zlib")
        )
        inventory = type("Inventory", (), {})()
        inventory.alpine_version = "3.22.1"
        inventory.packages = packages
        lock = {
            "runtime": {"sbom_created": "2026-08-15T00:00:00Z"},
            "artifacts": {
                "proot": {
                    "version": "abc",
                    "license": "GPL-2.0-or-later",
                    "sha256": "1" * 64,
                },
                "proot_loader": {
                    "version": "abc",
                    "license": "GPL-2.0-or-later",
                    "sha256": "2" * 64,
                },
            },
        }
        document = build_spdx_document(inventory, lock, "3" * 64)
        names = {item["name"] for item in document["packages"]}
        self.assertEqual(
            {
                "Alpine Linux rootfs",
                "busybox",
                "zlib",
                "OpenMinis PRoot Android fork",
                "OpenMinis PRoot loader",
            },
            names,
        )
        self.assertEqual(2, sum(1 for item in document["relationships"] if item["relationshipType"] == "CONTAINS"))

    def test_current_runtime_integrity_and_recorded_inventory_pass(self):
        result = verify_project(PROJECT_ROOT)
        self.assertEqual(15, result["package_count"])
        self.assertFalse(result["python_prebundled"])
        self.assertFalse(result["vulnerability_database_complete"])
        self.assertEqual(2, result["blocked_finding_count"])

    def test_artifact_and_sbom_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_project_fixture(Path(raw))
            rootfs = (
                fixture
                / "alpine-runtime-pack-bundled/src/main/assets/"
                "alpine-minirootfs.tar.gz.asset"
            )
            rootfs.write_bytes(rootfs.read_bytes() + b"tampered")
            with self.assertRaises(SupplyChainError):
                verify_project(fixture)
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_project_fixture(Path(raw))
            sbom = (
                fixture
                / "alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/"
                "sbom.spdx.json"
            )
            sbom.write_text(sbom.read_text() + " ")
            with self.assertRaises(SupplyChainError):
                verify_project(fixture)

    def test_vulnerability_snapshot_scope_and_duplicates_fail_closed(self):
        inventory = read_rootfs_inventory(
            PROJECT_ROOT
            / "alpine-runtime-pack-bundled/src/main/assets/alpine-minirootfs.tar.gz.asset"
        )
        source = PROJECT_ROOT / "security/alpine-vulnerability-snapshot.json"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as raw:
            snapshot = Path(raw) / "snapshot.json"
            document = json.loads(source.read_text())
            document["scope"]["rootfs_sha256"] = "0" * 64
            snapshot.write_text(json.dumps(document))
            with self.assertRaises(SupplyChainError):
                verify_vulnerability_snapshot(
                    PROJECT_ROOT, snapshot, inventory, sha256(
                        PROJECT_ROOT
                        / "alpine-runtime-pack-bundled/src/main/assets/"
                        "alpine-minirootfs.tar.gz.asset"
                    )
                )
            document = json.loads(source.read_text())
            document["findings"].append(document["findings"][0])
            snapshot.write_text(json.dumps(document))
            with self.assertRaises(SupplyChainError):
                verify_vulnerability_snapshot(
                    PROJECT_ROOT,
                    snapshot,
                    inventory,
                    sha256(
                        PROJECT_ROOT
                        / "alpine-runtime-pack-bundled/src/main/assets/"
                        "alpine-minirootfs.tar.gz.asset"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
