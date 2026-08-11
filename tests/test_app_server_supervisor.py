import os
import sys
import threading
import time
import unittest

from codex_gateway.app_server.jsonl import JsonlDecoder, JsonlProtocolError
from codex_gateway.app_server.process import AppServerSupervisor, SupervisorError, SupervisorState
from codex_gateway.app_server.protocol import CodexAppServerProtocol
from codex_gateway.app_server.rpc import RpcProcessLost, RpcProtocolError, RpcTimeout


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_app_server.py")


def make_supervisor(mode="normal"):
    return AppServerSupervisor(
        command=[sys.executable, "-u", FAKE, mode],
        working_directory=ROOT,
    )


class JsonlDecoderTest(unittest.TestCase):
    def test_split_coalesced_and_crlf(self):
        decoder = JsonlDecoder(max_line_bytes=64)
        self.assertEqual([], decoder.feed(b'{"a":'))
        self.assertEqual([{"a": 1}, {"b": 2}], decoder.feed(b'1}\r\n{"b":2}\n'))
        decoder.finish()

    def test_invalid_utf8_oversize_and_eof_fail_closed(self):
        with self.assertRaises(JsonlProtocolError):
            JsonlDecoder().feed(b'{"bad":"\xff"}\n')
        with self.assertRaises(JsonlProtocolError):
            JsonlDecoder(max_line_bytes=4).feed(b"12345")
        decoder = JsonlDecoder()
        decoder.feed(b'{"a":1}')
        with self.assertRaises(JsonlProtocolError):
            decoder.finish()


class AppServerSupervisorTest(unittest.TestCase):
    def tearDown(self):
        supervisor = getattr(self, "supervisor", None)
        if supervisor is not None:
            supervisor.stop()

    def start(self, mode="normal"):
        self.supervisor = make_supervisor(mode)
        return self.supervisor.start("alpine-codex-client", "debug-test", timeout_seconds=0.5)

    def test_initialize_and_account_read(self):
        response = self.start("split")
        self.assertEqual("unix", response["platformFamily"])
        account = CodexAppServerProtocol(self.supervisor).account_read()
        self.assertFalse(account.authenticated)
        self.assertTrue(account.requires_openai_auth)
        self.assertEqual(SupervisorState.READY, self.supervisor.state)

    def test_out_of_order_responses_are_matched_by_id(self):
        self.start("out_of_order")
        results = {}

        def request(name):
            results[name] = self.supervisor.request("test/out_of_order", {}, timeout_seconds=1.0)

        first = threading.Thread(target=request, args=("first",))
        second = threading.Thread(target=request, args=("second",))
        first.start()
        second.start()
        first.join(2.0)
        second.join(2.0)
        self.assertEqual({1, 2}, {results["first"]["position"], results["second"]["position"]})

    def test_timeout_late_response_and_active_turn_crash_are_terminal_once(self):
        self.start()
        with self.assertRaises(RpcTimeout):
            self.supervisor.request("test/delay", {}, timeout_seconds=0.05)
        deadline = time.time() + 1.5
        while self.supervisor.state is not SupervisorState.FAILED and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(SupervisorState.FAILED, self.supervisor.state)
        self.supervisor.stop()

        self.start()
        with self.assertRaises(RpcProcessLost):
            self.supervisor.request("turn/start", {"threadId": "opaque-thread"}, timeout_seconds=1.0)
        deadline = time.time() + 1.0
        while self.supervisor.state is not SupervisorState.FAILED and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(SupervisorState.FAILED, self.supervisor.state)

    def test_bad_initialize_unknown_and_duplicate_id_fail_closed(self):
        self.supervisor = make_supervisor("initialize_error")
        with self.assertRaises(SupervisorError) as error:
            self.supervisor.start("client", "test", timeout_seconds=0.5)
        self.assertEqual("codex_initialize_failed", error.exception.code)
        self.supervisor.stop()

        self.supervisor = make_supervisor("initialize_timeout")
        with self.assertRaises(SupervisorError) as error:
            self.supervisor.start("client", "test", timeout_seconds=0.05)
        self.assertEqual("codex_initialize_failed", error.exception.code)
        self.supervisor.stop()

        for mode in ("unknown_id", "duplicate_id"):
            self.supervisor = make_supervisor(mode)
            with self.assertRaises(SupervisorError):
                self.supervisor.start("client", "test", timeout_seconds=0.5)
            self.assertEqual(SupervisorState.FAILED, self.supervisor.state)
            self.supervisor.stop()

    def test_stderr_is_bounded_and_redacted(self):
        self.start()
        self.supervisor.request("test/stderr_flood", {}, timeout_seconds=1.0)
        deadline = time.time() + 1.0
        while not self.supervisor.stderr_diagnostic and time.time() < deadline:
            time.sleep(0.01)
        diagnostic = self.supervisor.stderr_diagnostic
        self.assertLessEqual(len(diagnostic.encode("utf-8")), 64 * 1024)
        self.assertIn("[REDACTED]", diagnostic)
        self.assertNotIn("synthetic-secret-value", diagnostic)


if __name__ == "__main__":
    unittest.main()
