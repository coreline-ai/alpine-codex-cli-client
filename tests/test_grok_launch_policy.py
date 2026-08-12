from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

from codex_gateway.grok_acp.policy import (
    FIXED_COMMAND,
    FIXED_ENVIRONMENT,
    LOCKED_BINARY_SIZE,
    GrokLaunchPolicy,
    GrokPolicyError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_SOURCE = (
    PROJECT_ROOT / "grok-cli-pack/src/debug/assets/grok-profile/chat-only.md"
)


class GrokLaunchPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.allowed = Path(self.temporary.name)
        os.chmod(self.allowed, 0o700)
        self.root = self.allowed / ".alpine-grok"
        self.policy = GrokLaunchPolicy.for_root(self.allowed, self.root)
        for directory in (
            self.root,
            self.policy.home,
            self.policy.staging,
            self.policy.staging / "grok-cli",
            self.policy.profile_directory,
            self.policy.work,
            self.policy.executable.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        self.policy.profile.write_bytes(PROFILE_SOURCE.read_bytes())
        os.chmod(self.policy.profile, 0o600)
        with self.policy.executable.open("wb") as output:
            output.truncate(LOCKED_BINARY_SIZE)
        os.chmod(self.policy.executable, 0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_layout_environment_command_and_permission_probe(self) -> None:
        self.policy.validate(verify_binary_hash=False)
        self.policy.permission_probe()
        self.assertEqual(FIXED_COMMAND, self.policy.command())
        self.assertEqual(FIXED_ENVIRONMENT, self.policy.environment())
        self.assertEqual(set(FIXED_ENVIRONMENT), set(self.policy.environment()))
        self.assertFalse((self.policy.home / ".permission-probe").exists())

    def test_codex_and_grok_roots_are_disjoint(self) -> None:
        codex_root = self.allowed / ".alpine-codex"
        self.assertNotEqual(codex_root, self.policy.root)
        self.assertFalse(codex_root in self.policy.root.parents)
        self.assertFalse(self.policy.root in codex_root.parents)

    def test_wrong_mode_fails_before_launch(self) -> None:
        os.chmod(self.policy.profile, 0o644)
        with self.assertRaisesRegex(GrokPolicyError, "grok_launch_policy_invalid"):
            self.policy.validate(verify_binary_hash=False)

    def test_symlink_fails_before_launch(self) -> None:
        original = self.allowed / "outside-profile"
        original.write_bytes(PROFILE_SOURCE.read_bytes())
        os.chmod(original, 0o600)
        self.policy.profile.unlink()
        self.policy.profile.symlink_to(original)
        with self.assertRaisesRegex(GrokPolicyError, "grok_launch_policy_invalid"):
            self.policy.validate(verify_binary_hash=False)

    def test_profile_hash_mismatch_fails_before_launch(self) -> None:
        data = bytearray(self.policy.profile.read_bytes())
        data[-2] ^= 1
        self.policy.profile.write_bytes(data)
        os.chmod(self.policy.profile, 0o600)
        with self.assertRaisesRegex(GrokPolicyError, "grok_launch_policy_invalid"):
            self.policy.validate(verify_binary_hash=False)

    def test_layout_override_fails_closed(self) -> None:
        changed = replace(self.policy, home=self.allowed / "other-home")
        with self.assertRaisesRegex(GrokPolicyError, "grok_launch_policy_invalid"):
            changed.validate(verify_binary_hash=False)

    def test_environment_has_only_kill_switches_and_no_secret_values(self) -> None:
        values = self.policy.environment()
        self.assertEqual(set(FIXED_ENVIRONMENT), set(values))
        self.assertNotIn("PATH", values)
        self.assertNotIn("GROK_AGENT_SECRET", values)
        self.assertNotIn("HTTP_PROXY", values)
        self.assertNotIn("HTTPS_PROXY", values)
        self.assertTrue(values["GROK_DISABLE_API_KEY_AUTH"] == "true")
        self.assertTrue(values["GROK_SUBAGENTS"] == "0")
        self.assertTrue(values["GROK_TELEMETRY_ENABLED"] == "false")


if __name__ == "__main__":
    unittest.main()
