"""Credential-free regression tests for Phase 7 verification helpers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Phase7VerifierTest(unittest.TestCase):
    def run_script(self, relative: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PROJECT_ROOT / relative), *arguments],
            cwd=PROJECT_ROOT,
            check=True,
            text=True,
            capture_output=True,
        )

    def test_protocol_fixture_matches_locked_adapter_surface(self) -> None:
        result = self.run_script("scripts/verify-codex-protocol-fixture.py")
        self.assertIn("PASS", result.stdout)

    def test_component_inventory_is_deterministic_and_contains_runtime_sbom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            self.run_script("scripts/generate-component-inventory.py", "--output", str(first))
            self.run_script("scripts/generate-component-inventory.py", "--output", str(second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            inventory = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual("alpine-codex-component-inventory/v1", inventory["format"])
            self.assertEqual("debug-secureDebug-release", inventory["scope"])
            self.assertEqual("SPDX-2.3", inventory["sboms"][0]["format"])

    def test_clean_room_source_and_git_scan_passes_without_an_apk(self) -> None:
        result = self.run_script("scripts/verify-debug-clean-room.py")
        self.assertIn("PASS", result.stdout)

    def test_sensitive_evidence_scanner_accepts_counts_and_rejects_grok_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "evidence.txt"
            evidence.write_text(
                "agent=grok authenticated=true user_nodes=1 assistant_nodes=1 terminal_events=1\n",
                encoding="utf-8",
            )
            result = self.run_script("scripts/verify-sensitive-evidence.py", str(evidence))
            self.assertIn("PASS", result.stdout)

            violations = (
                "https://auth.x.ai/device?challenge=synthetic",
                "user_code=SYNTHETIC",
                "person@example.test",
                "'prompt': 'synthetic'",
            )
            for violation in violations:
                with self.subTest(violation=violation):
                    evidence.write_text(violation, encoding="utf-8")
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.run_script("scripts/verify-sensitive-evidence.py", str(evidence))
