from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.gradle_supply_chain import (
    GradleSupplyChainError,
    is_dynamic_version,
    verify_project,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def copy_fixture(destination: Path) -> Path:
    for relative in (
        "settings.gradle.kts",
        "build.gradle.kts",
        "gradle.properties",
        "settings-gradle.lockfile",
        "gradle/libs.versions.toml",
        "gradle/wrapper/gradle-wrapper.properties",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    settings = (PROJECT_ROOT / "settings.gradle.kts").read_text()
    for line in settings.splitlines():
        if not line.startswith('include(":'):
            continue
        module = line.split('"')[1][1:]
        target = destination / module / "gradle.lockfile"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / module / "gradle.lockfile", target)
        source_build = PROJECT_ROOT / module / "build.gradle.kts"
        if source_build.is_file():
            shutil.copy2(source_build, target.parent / "build.gradle.kts")
    return destination


class GradleSupplyChainTest(unittest.TestCase):
    def test_current_project_has_locks_and_cache_isolation(self):
        result = verify_project(PROJECT_ROOT)
        self.assertEqual(14, result["module_lock_count"])
        self.assertGreater(result["locked_component_count"], 100)
        self.assertTrue(result["build_cache_disabled"])
        self.assertTrue(result["release_configurations_locked"])

    def test_dynamic_version_detection(self):
        for version in ("1.+", "latest.release", "2.0-SNAPSHOT", "[1,2)"):
            self.assertTrue(is_dynamic_version(version))
        self.assertFalse(is_dynamic_version("2.2.21"))

    def test_dynamic_catalog_and_missing_lock_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            catalog = fixture / "gradle/libs.versions.toml"
            catalog.write_text(catalog.read_text().replace('kotlin = "2.2.21"', 'kotlin = "2.+"'))
            with self.assertRaises(GradleSupplyChainError):
                verify_project(fixture)
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            (fixture / "app/gradle.lockfile").unlink()
            with self.assertRaises(GradleSupplyChainError):
                verify_project(fixture)

    def test_cache_reenable_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            properties = fixture / "gradle.properties"
            properties.write_text(
                properties.read_text().replace("org.gradle.caching=false", "org.gradle.caching=true")
            )
            with self.assertRaises(GradleSupplyChainError):
                verify_project(fixture)

    def test_missing_release_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            lock = fixture / "app/gradle.lockfile"
            lock.write_text(lock.read_text().replace("releaseCompileClasspath,", ""))
            with self.assertRaises(GradleSupplyChainError):
                verify_project(fixture)


if __name__ == "__main__":
    unittest.main()
