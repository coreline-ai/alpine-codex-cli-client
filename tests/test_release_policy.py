from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from scripts.release_policy import ReleasePolicyError, verify_project


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def copy_fixture(destination: Path) -> Path:
    for relative in (
        "app/build.gradle.kts",
        "codex-cli-pack/build.gradle.kts",
        "grok-cli-pack/build.gradle.kts",
        "codex-gateway-pack-bundled/build.gradle.kts",
        "alpine-python-pack-bundled/build.gradle.kts",
        "app/src/main/java/dev/alpine/codexclient/GatewayPythonBootstrapper.kt",
        "grok-cli-pack/src/main/assets/grok-profile/chat-only.md",
        "grok-cli-pack/src/main/assets/grok-profile/chat-only.lock.json",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, target)
    return destination


class ReleasePolicyTest(unittest.TestCase):
    def test_current_project_has_public_release_boundary(self):
        status = verify_project(PROJECT_ROOT)
        self.assertTrue(status["release_variant"])
        self.assertEqual(4, status["external_signing_inputs"])
        self.assertEqual(4, status["distribution_asset_packs"])

    def test_debug_only_pack_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            build = fixture / "codex-cli-pack/build.gradle.kts"
            build.write_text(
                build.read_text().replace('getByName("main").assets.srcDir', 'getByName("debug").assets.srcDir')
            )
            with mock.patch("scripts.release_policy.subprocess.check_output", return_value=b""):
                with self.assertRaises(ReleasePolicyError):
                    verify_project(fixture)

    def test_hard_coded_password_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            build = fixture / "app/build.gradle.kts"
            build.write_text(build.read_text() + '\nstorePassword = "forbidden"\n')
            with mock.patch("scripts.release_policy.subprocess.check_output", return_value=b""):
                with self.assertRaises(ReleasePolicyError):
                    verify_project(fixture)

    def test_online_python_package_bootstrap_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = copy_fixture(Path(raw))
            bootstrapper = fixture / "app/src/main/java/dev/alpine/codexclient/GatewayPythonBootstrapper.kt"
            bootstrapper.write_text(
                bootstrapper.read_text()
                + '\nval forbidden = RuntimeCommandRequest(executable = "/sbin/apk", '
                + 'arguments = listOf("add", "python3"))\n'
            )
            with mock.patch("scripts.release_policy.subprocess.check_output", return_value=b""):
                with self.assertRaises(ReleasePolicyError):
                    verify_project(fixture)


if __name__ == "__main__":
    unittest.main()
