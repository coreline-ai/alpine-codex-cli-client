from __future__ import annotations

import hashlib
import gzip
import io
import importlib.util
import json
import sys
import tempfile
import tarfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "python_package_pack", ROOT / "scripts/python_package_pack.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def tar_gzip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, value in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(value))
    return gzip.compress(output.getvalue(), mtime=0)


def alpine_package(
    name: str = "python3",
    version: str = "3.12.0-r0",
    architecture: str = "aarch64",
) -> bytes:
    signature = tar_gzip({".SIGN.RSA.fixture.rsa.pub": b"fixture-signature"})
    control = tar_gzip(
        {
            ".PKGINFO": (
                f"pkgname = {name}\npkgver = {version}\narch = {architecture}\n"
            ).encode()
        }
    )
    data = tar_gzip({"usr/bin/python3": b"fixture"})
    return signature + control + data


class PythonPackagePackTest(unittest.TestCase):
    def make_pack(self, root: Path, *, production: bool = True) -> Path:
        package = alpine_package()
        sbom = json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}).encode()
        (root / "packages").mkdir(parents=True)
        (root / "packages/python3-3.12.0-r0.apk").write_bytes(package)
        (root / "sbom.spdx.json").write_bytes(sbom)
        lock = {
            "schema": 1,
            "pack_id": "fixture-alpine-python3",
            "alpine_version": "3.21.3",
            "architecture": "aarch64",
            "production": production,
            "packages": [
                {
                    "file": "packages/python3-3.12.0-r0.apk",
                    "name": "python3",
                    "version": "3.12.0-r0",
                    "size": len(package),
                    "sha256": digest(package),
                }
            ],
            "sbom": {
                "file": "sbom.spdx.json",
                "size": len(sbom),
                "sha256": digest(sbom),
            },
        }
        (root / "python-pack.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        return root

    def test_valid_fixture_and_asset_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_pack(root / "source")
            output = root / "assets"
            lock = MODULE.validate_pack(source)
            self.assertEqual("python3", lock.packages[0].name)
            status = MODULE.prepare_assets(source, output)
            self.assertTrue(status["available"])
            self.assertEqual(lock, MODULE.validate_asset_pack(output, require_production=True))

    def test_missing_source_generates_unavailable_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "assets"
            status = MODULE.prepare_assets(root / "missing", output)
            self.assertFalse(status["available"])
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_asset_pack(output, require_production=True)

    def test_production_rejects_fixture_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source", production=False)
            MODULE.validate_pack(source)
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source, require_production=True)

    def test_hash_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            (source / "packages/python3-3.12.0-r0.apk").write_bytes(b"mutated")
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

    def test_missing_and_extra_packages_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            expected = source / "packages/python3-3.12.0-r0.apk"
            expected.unlink()
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)
            expected.write_bytes(alpine_package())
            (source / "packages/extra.apk").write_bytes(b"extra")
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

    def test_path_escape_and_duplicate_name_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            lock_path = source / "python-pack.lock.json"
            lock = json.loads(lock_path.read_text())
            escaped = dict(lock["packages"][0])
            escaped["file"] = "../python3.apk"
            lock["packages"] = [escaped]
            lock_path.write_text(json.dumps(lock))
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

            source = self.make_pack(Path(directory) / "second")
            lock_path = source / "python-pack.lock.json"
            lock = json.loads(lock_path.read_text())
            duplicate = dict(lock["packages"][0])
            duplicate["file"] = "packages/python3-copy.apk"
            (source / duplicate["file"]).write_bytes(b"copy")
            duplicate["size"] = 4
            duplicate["sha256"] = digest(b"copy")
            lock["packages"].append(duplicate)
            lock_path.write_text(json.dumps(lock))
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

    def test_symlink_package_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_pack(root / "source")
            package = source / "packages/python3-3.12.0-r0.apk"
            outside = root / "outside.apk"
            outside.write_bytes(package.read_bytes())
            package.unlink()
            package.symlink_to(outside)
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

    def test_pkginfo_identity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            package = alpine_package(name="not-python")
            path = source / "packages/python3-3.12.0-r0.apk"
            path.write_bytes(package)
            lock_path = source / "python-pack.lock.json"
            lock = json.loads(lock_path.read_text())
            lock["packages"][0]["size"] = len(package)
            lock["packages"][0]["sha256"] = digest(package)
            lock_path.write_text(json.dumps(lock))
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)

    def test_noarch_dependency_is_accepted_but_foreign_architecture_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            path = source / "packages/python3-3.12.0-r0.apk"
            lock_path = source / "python-pack.lock.json"
            lock = json.loads(lock_path.read_text())

            noarch = alpine_package(architecture="noarch")
            path.write_bytes(noarch)
            lock["packages"][0]["size"] = len(noarch)
            lock["packages"][0]["sha256"] = digest(noarch)
            lock_path.write_text(json.dumps(lock))
            MODULE.validate_pack(source, require_production=True)

            foreign = alpine_package(architecture="x86_64")
            path.write_bytes(foreign)
            lock["packages"][0]["size"] = len(foreign)
            lock["packages"][0]["sha256"] = digest(foreign)
            lock_path.write_text(json.dumps(lock))
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source, require_production=True)

    def test_unsigned_package_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_pack(Path(directory) / "source")
            package = (
                tar_gzip({"not-a-signature": b"fixture"})
                + tar_gzip({".PKGINFO": b"pkgname = python3\npkgver = 3.12.0-r0\narch = aarch64\n"})
                + tar_gzip({"usr/bin/python3": b"fixture"})
            )
            path = source / "packages/python3-3.12.0-r0.apk"
            path.write_bytes(package)
            lock_path = source / "python-pack.lock.json"
            lock = json.loads(lock_path.read_text())
            lock["packages"][0]["size"] = len(package)
            lock["packages"][0]["sha256"] = digest(package)
            lock_path.write_text(json.dumps(lock))
            with self.assertRaises(MODULE.PythonPackagePackError):
                MODULE.validate_pack(source)


if __name__ == "__main__":
    unittest.main()
