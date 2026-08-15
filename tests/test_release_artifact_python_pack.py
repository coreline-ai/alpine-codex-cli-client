from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_release_artifact", ROOT / "scripts/verify-release-artifact.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReleaseArtifactPythonPackTest(unittest.TestCase):
    def make_archive(self, path: Path, *, mutate_package: bool = False, extra: bool = False) -> None:
        package = b"locked-package"
        sbom = json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}).encode()
        lock = {
            "schema": 1,
            "pack_id": "alpine-python3",
            "alpine_version": "3.21.3",
            "architecture": "aarch64",
            "production": True,
            "packages": [
                {
                    "file": "packages/python3-3.12-r0.apk",
                    "name": "python3",
                    "version": "3.12-r0",
                    "size": len(package),
                    "sha256": hashlib.sha256(package).hexdigest(),
                }
            ],
            "sbom": {
                "file": "sbom.spdx.json",
                "size": len(sbom),
                "sha256": hashlib.sha256(sbom).hexdigest(),
            },
        }
        lock_bytes = json.dumps(lock).encode()
        status = {
            "schema": 1,
            "available": True,
            "production": True,
            "pack_id": "alpine-python3",
            "package_count": 1,
            "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        }
        prefix = "assets/alpine-python-pack/"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(prefix + "pack-status.json", json.dumps(status))
            archive.writestr(prefix + "python-pack.lock.json", lock_bytes)
            archive.writestr(prefix + "sbom.spdx.json", sbom)
            archive.writestr(
                prefix + "packages/python3-3.12-r0.apk",
                b"mutated-package" if mutate_package else package,
            )
            if extra:
                archive.writestr(prefix + "packages/extra.apk", b"extra")

    def test_exact_pack_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "fixture.apk"
            self.make_archive(artifact)
            with zipfile.ZipFile(artifact) as archive:
                self.assertEqual(1, MODULE._verify_python_package_pack(archive, "assets/"))

    def test_mutated_or_extra_payload_fails(self) -> None:
        for mutation in ("hash", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                artifact = Path(directory) / "fixture.apk"
                self.make_archive(
                    artifact,
                    mutate_package=mutation == "hash",
                    extra=mutation == "extra",
                )
                with zipfile.ZipFile(artifact) as archive:
                    with self.assertRaises(MODULE.ReleaseArtifactError):
                        MODULE._verify_python_package_pack(archive, "assets/")


if __name__ == "__main__":
    unittest.main()
