from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest

from codex_gateway.grok_acp.contract import (
    AUTH_METHOD_ID,
    REQUEST_METHODS,
    _RequestMethod,
    parse_initialize_result,
)
from codex_gateway.grok_acp.diagnostics import DiscardingStderrDiagnostics
from codex_gateway.grok_acp.process import (
    GrokAcpSupervisor,
    GrokSupervisorError,
    GrokSupervisorState,
)
from codex_gateway.grok_acp.rpc import (
    AcpPendingLimit,
    AcpProcessLost,
    AcpProtocolError,
    AcpStopped,
    AcpTimeout,
    _AcpMultiplexer,
)


ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fake_grok_acp.py"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "grok-acp-v1.0.0.json"


def make_supervisor(mode="normal", **kwargs):
    return GrokAcpSupervisor._for_test(
        [sys.executable, "-u", FAKE.as_posix(), mode],
        ROOT.as_posix(),
        **kwargs,
    )


class GrokContractTest(unittest.TestCase):
    def test_initialize_normalizes_reordered_fixture_and_discards_broad_capabilities(self):
        fixture = json.loads(FIXTURE_PATH.read_text())
        result = fixture["initializeResult"]
        normalized = parse_initialize_result(result)
        reordered = {key: result[key] for key in reversed(tuple(result))}
        self.assertEqual(normalized, parse_initialize_result(reordered))
        self.assertEqual("1", normalized.protocol_version)
        self.assertEqual(AUTH_METHOD_ID, normalized.auth_method_id)
        self.assertEqual(("model-alpha", "model-beta"), tuple(m.model_id for m in normalized.models))
        self.assertEqual("model-alpha", normalized.current_model_id)
        self.assertTrue(normalized.can_load_session)
        self.assertTrue(normalized.can_resume_session)
        self.assertTrue(normalized.can_close_session)
        self.assertFalse(hasattr(normalized, "mcp_capabilities"))

    def test_contract_is_closed_and_forbidden_strings_never_reach_writer(self):
        fixture = json.loads(FIXTURE_PATH.read_text())
        self.assertEqual(set(fixture["allowedRequestMethods"]), set(REQUEST_METHODS))
        writes = []
        rpc = _AcpMultiplexer(writes.append, generation=1)
        for method in fixture["forbiddenRequestMethods"]:
            with self.assertRaises(ValueError):
                rpc.request(method, {}, 0.01)  # type: ignore[arg-type]
        self.assertEqual([], writes)
        self.assertFalse(hasattr(GrokAcpSupervisor, "request"))


class GrokMultiplexerTest(unittest.TestCase):
    def test_request_ids_are_monotonic_and_late_response_is_rejected(self):
        writes = []
        rpc = _AcpMultiplexer(writes.append, generation=2)
        results = []

        def request(method):
            results.append(rpc.request(method, {}, 1.0))

        first = threading.Thread(target=request, args=(_RequestMethod.MODELS_LIST,))
        first.start()
        while len(writes) != 1:
            time.sleep(0.005)
        rpc.handle_object({"jsonrpc": "2.0", "id": 1, "result": {}}, generation=2)
        first.join(1.0)

        second = threading.Thread(target=request, args=(_RequestMethod.AUTH_INFO,))
        second.start()
        while len(writes) != 2:
            time.sleep(0.005)
        rpc.handle_object({"jsonrpc": "2.0", "id": 2, "result": {}}, generation=2)
        second.join(1.0)
        self.assertEqual([1, 2], [json.loads(payload)["id"] for payload in writes])
        self.assertEqual([{}, {}], results)

        timed_out = _AcpMultiplexer(lambda _: None, generation=3)
        with self.assertRaises(AcpTimeout):
            timed_out.request(_RequestMethod.MODELS_LIST, {}, 0.01)
        with self.assertRaises(AcpProtocolError):
            timed_out.handle_object({"jsonrpc": "2.0", "id": 1, "result": {}}, generation=3)

    def test_monotonic_serialized_request_ids_and_pending_limit(self):
        writes = []
        wrote = threading.Event()

        def writer(payload):
            writes.append(payload)
            wrote.set()

        rpc = _AcpMultiplexer(writer, generation=7, max_pending=1)
        first_error = []

        def first():
            try:
                rpc.request(_RequestMethod.MODELS_LIST, {}, 2.0)
            except Exception as error:
                first_error.append(error)

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(wrote.wait(1.0))
        with self.assertRaises(AcpPendingLimit):
            rpc.request(_RequestMethod.AUTH_INFO, {}, 0.1)
        self.assertEqual(1, len(writes))
        self.assertEqual(1, json.loads(writes[0])["id"])
        rpc.fail_all(AcpStopped())
        thread.join(1.0)
        self.assertIsInstance(first_error[0], AcpStopped)

    def test_generation_mismatch_unknown_notification_and_duplicate_terminal(self):
        rpc = _AcpMultiplexer(lambda _: None, generation=4)
        notifications = []
        rpc.add_notification_listener(notifications.append)
        terminal = {
            "jsonrpc": "2.0",
            "method": "x.ai/session/prompt_complete",
            "params": {"sessionId": "s", "promptId": "p"},
        }
        rpc.handle_object(terminal, generation=3)
        rpc.handle_object({"jsonrpc": "2.0", "method": "x.ai/noise", "params": {}}, 4)
        rpc.handle_object(terminal, generation=4)
        rpc.handle_object(terminal, generation=4)
        self.assertEqual(1, len(notifications))
        self.assertEqual(4, notifications[0].generation)
        self.assertEqual(1, rpc.stale_generation_count)
        self.assertEqual(1, rpc.discarded_notification_count)


class GrokSupervisorTest(unittest.TestCase):
    def tearDown(self):
        supervisor = getattr(self, "supervisor", None)
        if supervisor is not None:
            supervisor.stop()

    def start(self, mode="normal", **kwargs):
        self.supervisor = make_supervisor(mode, **kwargs)
        return self.supervisor.start()

    def test_fragmented_and_combined_lines_initialize(self):
        for mode in ("split", "combined"):
            state = self.start(mode)
            self.assertEqual(AUTH_METHOD_ID, state.auth_method_id)
            self.assertEqual(GrokSupervisorState.READY, self.supervisor.state)
            self.supervisor.stop()

    def test_malformed_invalid_utf8_oversized_unknown_duplicate_and_reverse_request_fail_closed(self):
        for mode in (
            "malformed_json",
            "invalid_utf8",
            "oversized",
            "unknown_id",
            "duplicate_id",
            "reverse_request",
        ):
            self.supervisor = make_supervisor(mode, timeout_scale=0.01)
            with self.assertRaises(GrokSupervisorError):
                self.supervisor.start()
            self.assertEqual(GrokSupervisorState.FAILED, self.supervisor.state, mode)
            self.supervisor.stop()

    def test_timeout_late_response_and_process_crash_are_bounded(self):
        self.start("late_response", timeout_scale=0.01)
        with self.assertRaises(AcpTimeout):
            self.supervisor.list_models()
        self.assertEqual(GrokSupervisorState.FAILED, self.supervisor.state)
        self.supervisor.stop()

        self.start("crash")
        with self.assertRaises(AcpProcessLost):
            self.supervisor.list_models()
        self.assertEqual(GrokSupervisorState.FAILED, self.supervisor.state)

    def test_stderr_content_is_never_retained_across_chunk_boundaries(self):
        self.start("stderr_privacy")
        deadline = time.monotonic() + 1.0
        while not self.supervisor.stderr_diagnostic and time.monotonic() < deadline:
            time.sleep(0.01)
        diagnostic = self.supervisor.stderr_diagnostic
        self.assertTrue(diagnostic.startswith("grok_stderr_redacted:"))
        for fragment in ("https://", "private-value", "@", "synthetic-secret", "do-not-retain"):
            self.assertNotIn(fragment, diagnostic)

        buffer = DiscardingStderrDiagnostics(max_observed_bytes=8)
        buffer.append(b"123456")
        buffer.append(b"789-secret")
        self.assertEqual("grok_stderr_redacted:8:truncated", buffer.snapshot())
        self.assertEqual(0, buffer.retained_content_bytes)

    def test_notification_reorder_and_terminal_duplicate_are_normalized(self):
        self.start("duplicate_terminal")
        notifications = []
        self.supervisor.add_notification_listener(notifications.append)
        result = self.supervisor.prompt("session-1", "fixture prompt")
        self.assertEqual("end_turn", result["stopReason"])
        terminal = [n for n in notifications if n.method == "x.ai/session/prompt_complete"]
        self.assertEqual(1, len(terminal))
        self.assertEqual(sorted(n.sequence for n in notifications), [n.sequence for n in notifications])

    def test_shutdown_cancels_active_request_and_reaps_child(self):
        self.start("hold", timeout_scale=1.0)
        process = self.supervisor._process
        self.assertIsNotNone(process)
        errors = []

        def request():
            try:
                self.supervisor.list_models()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=request)
        thread.start()
        deadline = time.monotonic() + 1.0
        while self.supervisor._require_rpc().pending_count != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(1, self.supervisor._require_rpc().pending_count)
        self.supervisor.stop(timeout_seconds=1.0)
        thread.join(1.0)
        self.assertIsInstance(errors[0], AcpStopped)
        self.assertIsNotNone(process.returncode)
        self.assertEqual(GrokSupervisorState.STOPPED, self.supervisor.state)


if __name__ == "__main__":
    unittest.main()
