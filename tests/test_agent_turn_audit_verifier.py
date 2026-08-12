"""Credential-free tests for the content-free Samsung turn audit verifier."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts/verify-agent-turn-audit.py"


def audit_line(
    *,
    outcome: str = "done",
    visible: int = 2,
    cancel: int = 0,
    retry: str = "none",
    attempts: int = 0,
    retry_max: int = 0,
    profile: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
) -> str:
    return (
        f"agent=grok outcome={outcome} prompt_dispatch=1 visible_delta={visible} "
        f"terminal=1 cancel={cancel} retry={retry} retry_attempts={attempts} "
        f"retry_max={retry_max} profile_tool={profile[0]} profile_subagent={profile[1]} "
        f"profile_mcp={profile[2]} profile_filesystem={profile[3]} "
        f"profile_terminal={profile[4]}\n"
    )


class AgentTurnAuditVerifierTest(unittest.TestCase):
    def run_verifier(self, mode: str, value: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, SCRIPT.as_posix(), "--mode", mode],
            cwd=PROJECT_ROOT,
            input=value,
            text=True,
            capture_output=True,
        )

    def test_chat_accepts_no_retry_and_bounded_pre_output_retry(self) -> None:
        for value in (
            audit_line(),
            audit_line(retry="pre_output", attempts=1, retry_max=3),
        ):
            result = self.run_verifier("chat", value)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("PASS", result.stdout)

    def test_stop_requires_one_cancel_and_error_terminal(self) -> None:
        result = self.run_verifier("stop", audit_line(outcome="error", visible=0, cancel=1))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("cancel=1", result.stdout)

    def test_rejects_profile_activity_and_non_g1_retry(self) -> None:
        values = (
            audit_line(profile=(1, 0, 0, 0, 0)),
            audit_line(retry="post_output", attempts=1, retry_max=3),
            audit_line(visible=0),
            audit_line(cancel=1),
        )
        for value in values:
            with self.subTest(value=value):
                self.assertNotEqual(0, self.run_verifier("chat", value).returncode)

    def test_rejects_extra_line_or_private_suffix(self) -> None:
        for value in (
            audit_line() + audit_line(),
            audit_line().rstrip() + " private@example.invalid\n",
            "not-an-audit-line\n",
        ):
            with self.subTest(value=value):
                self.assertNotEqual(0, self.run_verifier("chat", value).returncode)
