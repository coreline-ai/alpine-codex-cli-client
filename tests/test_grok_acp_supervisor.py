from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from codex_gateway.grok_acp.contract import (
    AUTH_METHOD_ID,
    NOTIFICATION_METHODS,
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
        self.assertEqual(set(fixture["allowedNotificationMethods"]), set(NOTIFICATION_METHODS))
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
        self.assertEqual(
            ["_x.ai/models/list", "_x.ai/auth/info"],
            [json.loads(payload)["method"] for payload in writes],
        )
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
            "method": "_x.ai/session/prompt_complete",
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

    def test_official_xai_retry_notification_is_allowlisted_and_bounded(self):
        rpc = _AcpMultiplexer(lambda _: None, generation=5)
        notifications = []
        rpc.add_notification_listener(notifications.append)
        rpc.handle_object(
            {
                "jsonrpc": "2.0",
                "method": "_x.ai/session_notification",
                "params": {
                    "sessionId": "session-1",
                    "update": {
                        "sessionUpdate": "retry_state",
                        "type": "retrying",
                        "attempt": 1,
                        "max_retries": 3,
                        "reason": "fixture",
                    },
                },
            },
            generation=5,
        )
        self.assertEqual(1, len(notifications))
        self.assertEqual("_x.ai/session_notification", notifications[0].method)

    def test_chat_profile_audit_blocks_forbidden_activity_before_prompt_write(self):
        cases = (
            (
                "tool_event_count",
                "session/update",
                {"update": {"sessionUpdate": "tool_call", "toolCallId": "fixture"}},
            ),
            (
                "subagent_event_count",
                "_x.ai/session/update",
                {"update": {"sessionUpdate": "subagent_spawned"}},
            ),
            ("mcp_event_count", "x.ai/mcp/tools_changed", {}),
            ("filesystem_event_count", "x.ai/fs_notify", {}),
            ("terminal_event_count", "x.ai/terminal/output", {}),
            ("filesystem_event_count", "fs/read_text_file", {}),
            ("terminal_event_count", "terminal/create", {}),
        )
        for field, method, params in cases:
            with self.subTest(field=field):
                writes = []
                rpc = _AcpMultiplexer(writes.append, generation=11)
                with self.assertRaises(AcpProtocolError) as error:
                    rpc.handle_object(
                        {"jsonrpc": "2.0", "method": method, "params": params},
                        generation=11,
                    )
                self.assertEqual("grok_chat_profile_violation", error.exception.code)
                self.assertEqual(1, getattr(rpc.profile_audit, field))
                with self.assertRaises(AcpProtocolError):
                    rpc.request(
                        _RequestMethod.SESSION_PROMPT,
                        {"sessionId": "fixture", "prompt": []},
                        0.01,
                        require_clean_profile=True,
                    )
                self.assertEqual([], writes)

        clean_writes = []
        clean = _AcpMultiplexer(clean_writes.append, generation=12)
        clean.handle_object(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "available_commands_update",
                        "availableCommands": ["model"],
                    }
                },
            },
            generation=12,
        )
        self.assertTrue(clean.profile_audit.clean)


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

    def test_fixture_spawn_keeps_closed_descriptors_without_preexec_fn(self):
        real_popen = subprocess.Popen
        with mock.patch(
            "codex_gateway.grok_acp.process.subprocess.Popen",
            wraps=real_popen,
        ) as popen:
            self.start()
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(ROOT.as_posix(), kwargs["cwd"])
        self.assertNotIn("umask", kwargs)
        self.assertNotIn("preexec_fn", kwargs)

    def test_production_spawn_uses_fixed_cwd_closed_fds_and_no_child_callback(self):
        class Policy:
            work = ROOT

            def command(self):
                return (sys.executable, "-u", FAKE.as_posix(), "reject_early_auth")

            def environment(self):
                return dict(os.environ)

            def validate(self):
                return None

            def permission_probe(self):
                return None

        real_popen = subprocess.Popen
        caller_thread = threading.get_ident()
        spawn_threads = []

        def record_popen(*args, **kwargs):
            spawn_threads.append(threading.get_ident())
            return real_popen(*args, **kwargs)

        with (
            mock.patch(
                "codex_gateway.grok_acp.process.GrokLaunchPolicy.production",
                return_value=Policy(),
            ),
            mock.patch(
                "codex_gateway.grok_acp.process.subprocess.Popen",
                side_effect=record_popen,
            ) as popen,
        ):
            self.supervisor = GrokAcpSupervisor()
            self.supervisor.start()
            self.assertEqual({"methodId": None}, self.supervisor.auth_info())
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["close_fds"])
        self.assertEqual(ROOT.as_posix(), kwargs["cwd"])
        self.assertNotIn("umask", kwargs)
        self.assertNotIn("preexec_fn", kwargs)
        self.assertEqual(1, len(spawn_threads))
        self.assertNotEqual(caller_thread, spawn_threads[0])
        self.assertIsNotNone(self.supervisor._spawn_owner_thread)
        self.assertTrue(self.supervisor._spawn_owner_thread.is_alive())

    def test_malformed_invalid_utf8_oversized_unknown_duplicate_and_reverse_request_fail_closed(self):
        for mode in (
            "malformed_json",
            "invalid_utf8",
            "oversized",
            "unknown_id",
            "reverse_request",
        ):
            with self.subTest(mode=mode):
                self.supervisor = make_supervisor(mode, timeout_scale=0.01)
                with self.assertRaises(GrokSupervisorError):
                    self.supervisor.start()
                self.assertEqual(GrokSupervisorState.FAILED, self.supervisor.state)
                self.supervisor.stop()

        # A valid initialize response may release start() immediately before the stdout reader
        # consumes the adjacent duplicate line. The invariant is that the duplicate retires the
        # generation as soon as it is observed, not which of those two threads wins scheduling.
        self.supervisor = make_supervisor("duplicate_id", timeout_scale=0.01)
        try:
            self.supervisor.start()
        except GrokSupervisorError:
            pass
        deadline = time.monotonic() + 1.0
        while self.supervisor.state is not GrokSupervisorState.FAILED and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(GrokSupervisorState.FAILED, self.supervisor.state)
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
        terminal = [n for n in notifications if n.method == "_x.ai/session/prompt_complete"]
        self.assertEqual(1, len(terminal))
        self.assertEqual(sorted(n.sequence for n in notifications), [n.sequence for n in notifications])

    def test_authenticate_uses_scoped_sequence_without_forcing_loopback_oauth(self):
        self.start()
        self.assertEqual({}, self.supervisor.authenticate(17))
        with self.assertRaises(ValueError):
            self.supervisor.authenticate(0)
        with self.assertRaises(ValueError):
            self.supervisor.authenticate(True)

    def test_all_allowlisted_extensions_use_official_underscore_wire_prefix(self):
        self.start()
        self.assertEqual({"methodId": None}, self.supervisor.auth_info())
        self.assertEqual({"auth_url": None, "mode": None}, self.supervisor.get_auth_url())
        self.assertEqual({"auth_url": None, "mode": None}, self.supervisor.get_auth_url(0.5))
        for timeout in (0, -1, True, 31):
            with self.assertRaises(ValueError):
                self.supervisor.get_auth_url(timeout)
        self.assertEqual({"cancelled": True}, self.supervisor.cancel_auth(7))
        self.assertEqual({"ok": True}, self.supervisor.logout())
        self.assertEqual({"models": []}, self.supervisor.list_models())

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
