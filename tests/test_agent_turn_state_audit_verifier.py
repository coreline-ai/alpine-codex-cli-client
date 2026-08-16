from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "verify-agent-turn-state-audit.py"
SPEC = importlib.util.spec_from_file_location("verify_agent_turn_state_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AgentTurnStateAuditVerifierTest(unittest.TestCase):
    def test_exact_stop_sequence_passes(self) -> None:
        MODULE.verify(
            b"agent=grok state=started request_bound=1\n"
            b"agent=grok state=stop_requested dispatched=1\n"
        )

    def test_duplicate_missing_reordered_and_unbound_sequences_fail(self) -> None:
        invalid = (
            b"agent=grok state=started request_bound=1\n",
            b"agent=grok state=stop_requested dispatched=1\n",
            b"agent=grok state=stop_requested dispatched=1\n"
            b"agent=grok state=started request_bound=1\n",
            b"agent=grok state=started request_bound=1\n"
            b"agent=grok state=stop_requested dispatched=1\n"
            b"agent=grok state=stop_requested dispatched=1\n",
            b"agent=grok state=started request_bound=0\n"
            b"agent=grok state=stop_requested dispatched=1\n",
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    MODULE.verify(payload)

    def test_unknown_fields_binary_and_oversized_input_fail(self) -> None:
        invalid = (
            b"agent=grok state=started request_bound=1 private=value\n"
            b"agent=grok state=stop_requested dispatched=1\n",
            b"agent=grok state=started request_bound=1\x00\n"
            b"agent=grok state=stop_requested dispatched=1\n",
            b"x" * (MODULE.MAX_INPUT_BYTES + 1),
        )
        for payload in invalid:
            with self.subTest(size=len(payload)):
                with self.assertRaises(ValueError):
                    MODULE.verify(payload)


if __name__ == "__main__":
    unittest.main()
