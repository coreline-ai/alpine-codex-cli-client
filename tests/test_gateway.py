"""HTTP/SSE integration tests for the loopback-only Codex gateway."""

import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest

from codex_gateway.app_server.process import AppServerSupervisor
from codex_gateway.app_server.protocol import CodexAppServerProtocol
import codex_gateway.gateway as gateway_module
from codex_gateway.gateway import (
    LOOPBACK_HOST,
    CodexGatewayService,
    GatewayError,
    LoopbackGatewayServer,
    make_handler,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(ROOT, "tests", "fake_app_server.py")


class GatewayHarness:
    def __init__(self, mode: str, conversation_store_path=None) -> None:
        self.supervisor = AppServerSupervisor(
            command=[sys.executable, "-u", FAKE, mode],
            working_directory=ROOT,
        )
        self.protocol = CodexAppServerProtocol(self.supervisor)
        self.protocol.initialize("alpine-codex-client", "gateway-test")
        self.service = CodexGatewayService(
            self.protocol,
            ROOT,
            conversation_store_path=conversation_store_path,
        )
        self.server = LoopbackGatewayServer((LOOPBACK_HOST, 0), make_handler(self.service))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, value=None):
        body = None if value is None else json.dumps(value).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3.0)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload.decode("utf-8"))

    def start_stream(self, value):
        body = json.dumps(value).encode("utf-8")
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3.0)
        connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json"})
        return connection, connection.getresponse()

    def query(self):
        return self.supervisor.request("test/query", {}, timeout_seconds=1.0)

    def close(self) -> None:
        self.service.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)
        self.supervisor.stop()


def chat(conversation_id="conversation-test"):
    return {
        "conversation_id": conversation_id,
        "model": "model-alpha",
        "stream": True,
        "messages": [{"role": "user", "content": "test message"}],
    }


def sse_values(raw: bytes):
    values = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data: "):
            value = line[6:]
            values.append(value if value == "[DONE]" else json.loads(value))
    return values


class GatewayHttpTest(unittest.TestCase):
    def setUp(self):
        self.harness = None

    def tearDown(self):
        if self.harness is not None:
            self.harness.close()

    def open(self, mode):
        self.harness = GatewayHarness(mode)
        return self.harness

    def test_unauthenticated_contract_and_loopback_bind(self):
        harness = self.open("normal")
        status, value = harness.request("GET", "/healthz")
        self.assertEqual(200, status)
        self.assertEqual({"runtime": "ready", "gateway": "ready", "codex": "ready"}, value)
        status, value = harness.request("GET", "/internal/codex/account")
        self.assertEqual(200, status)
        self.assertEqual({"authenticated": False, "requires_openai_auth": True}, value)
        for method, path, body in (
            ("GET", "/v1/models", None),
            ("POST", "/v1/chat/completions", chat()),
        ):
            status, value = harness.request(method, path, body)
            self.assertEqual(401, status)
            self.assertEqual("authentication_required", value["error"]["code"])
        with self.assertRaises(ValueError):
            LoopbackGatewayServer(("0.0.0.0", 0), make_handler(harness.service))

    def test_login_pending_completed_cancel_expiry_and_duplicate(self):
        harness = self.open("gateway_login_complete")
        status, started = harness.request("POST", "/internal/codex/login/device")
        self.assertEqual(200, status)
        login_id = started["login_id"]
        status, duplicate = harness.request("POST", "/internal/codex/login/device")
        self.assertEqual(409, status)
        self.assertEqual("login_already_active", duplicate["error"]["code"])
        time.sleep(0.12)
        status, completed = harness.request("GET", "/internal/codex/login/" + login_id)
        self.assertEqual(200, status)
        self.assertEqual("completed", completed["status"])
        harness.close()
        self.harness = GatewayHarness("normal")
        status, started = self.harness.request("POST", "/internal/codex/login/device")
        self.assertEqual(200, status)
        pending_id = started["login_id"]
        with self.harness.service._lock:
            self.harness.service._logins[pending_id].expires_at = 0.0
        status, expired = self.harness.request("GET", "/internal/codex/login/" + pending_id)
        self.assertEqual(200, status)
        self.assertEqual("expired", expired["status"])
        status, restarted = self.harness.request("POST", "/internal/codex/login/device")
        self.assertEqual(200, status)
        status, cancelled = self.harness.request(
            "POST", "/internal/codex/login/" + restarted["login_id"] + "/cancel",
        )
        self.assertEqual(200, status)
        self.assertEqual("cancelled", cancelled["status"])

    def test_device_login_rejects_an_unsafe_verification_url(self):
        harness = self.open("gateway_login_unsafe")
        status, error = harness.request("POST", "/internal/codex/login/device")
        self.assertEqual(502, status)
        self.assertEqual("codex_protocol_invalid", error["error"]["code"])

    def test_gateway_owned_binding_survives_restart_and_logout_clears_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = os.path.join(directory, "conversation-bindings.v1.json")
            self.harness = GatewayHarness("gateway_chat", store_path)
            connection, response = self.harness.start_stream(chat("conversation-persisted"))
            self.assertEqual(200, response.status)
            sse_values(response.read())
            connection.close()
            self.assertTrue(os.path.isfile(store_path))
            self.assertEqual(0o600, stat.S_IMODE(os.stat(store_path).st_mode))
            with open(store_path, "r", encoding="utf-8") as source:
                bindings = json.load(source)
            self.assertEqual(["conversation_id", "thread_id"], sorted(bindings[0].keys()))
            self.harness.close()
            self.harness = GatewayHarness("gateway_chat", store_path)
            resume = chat("conversation-persisted")
            resume["resume_existing"] = True
            connection, response = self.harness.start_stream(resume)
            self.assertEqual(200, response.status)
            sse_values(response.read())
            connection.close()
            self.assertEqual(0, self.harness.query()["thread_start"])
            self.assertEqual(1, self.harness.query()["thread_resume"])
            missing = chat("conversation-missing")
            missing["resume_existing"] = True
            with self.assertRaises(GatewayError) as error:
                self.harness.service.start_chat(missing)
            self.assertEqual("conversation_binding_not_found", error.exception.code)
            self.assertEqual(0, self.harness.query()["thread_start"])
            status, logged_out = self.harness.request("POST", "/internal/codex/logout")
            self.assertEqual(200, status)
            self.assertEqual("logged_out", logged_out["status"])
            with open(store_path, "r", encoding="utf-8") as source:
                self.assertEqual([], json.load(source))

    def test_model_pagination_deduplication_empty_and_malformed(self):
        harness = self.open("gateway_auth")
        status, models = harness.request("GET", "/v1/models")
        self.assertEqual(200, status)
        self.assertEqual(["model-alpha", "model-beta"], [item["id"] for item in models["data"]])
        harness.close()
        self.harness = GatewayHarness("gateway_models_empty")
        status, models = self.harness.request("GET", "/v1/models")
        self.assertEqual(200, status)
        self.assertEqual([], models["data"])
        self.harness.close()
        self.harness = GatewayHarness("gateway_models_malformed")
        status, error = self.harness.request("GET", "/v1/models")
        self.assertEqual(502, status)
        self.assertEqual("codex_protocol_invalid", error["error"]["code"])

    def test_one_turn_sse_and_existing_conversation_resume(self):
        harness = self.open("gateway_chat")
        connection, response = harness.start_stream(chat("conversation-a"))
        self.assertEqual(200, response.status)
        values = sse_values(response.read())
        connection.close()
        self.assertEqual(["start", "delta", "delta", "done", "[DONE]"], [
            item if isinstance(item, str) else item["type"] for item in values
        ])
        counts = harness.query()
        self.assertEqual(1, counts["thread_start"])
        self.assertEqual(0, counts["thread_resume"])
        connection, response = harness.start_stream(chat("conversation-a"))
        self.assertEqual(200, response.status)
        sse_values(response.read())
        connection.close()
        self.assertEqual(1, harness.query()["thread_resume"])

    def test_zero_delta_malformed_and_no_resume_fallback(self):
        harness = self.open("gateway_one_delta")
        connection, response = harness.start_stream(chat())
        self.assertEqual(200, response.status)
        values = sse_values(response.read())
        connection.close()
        self.assertEqual(["start", "delta", "done", "[DONE]"], [
            item if isinstance(item, str) else item["type"] for item in values
        ])
        harness.close()
        self.harness = GatewayHarness("gateway_zero_delta")
        connection, response = self.harness.start_stream(chat())
        self.assertEqual(200, response.status)
        values = sse_values(response.read())
        connection.close()
        self.assertEqual(["start", "done", "[DONE]"], [
            item if isinstance(item, str) else item["type"] for item in values
        ])
        self.harness = GatewayHarness("gateway_malformed")
        connection, response = self.harness.start_stream(chat())
        self.assertEqual(200, response.status)
        values = sse_values(response.read())
        connection.close()
        self.assertEqual("codex_notification_invalid", values[1]["code"])
        self.assertEqual("[DONE]", values[-1])
        time.sleep(0.05)
        self.assertEqual(1, self.harness.query()["turn_interrupt"])
        self.harness.close()
        self.harness = GatewayHarness("gateway_resume_failure")
        self.harness.service.models()
        with self.harness.service._lock:
            self.harness.service._threads["existing"] = "thread-existing"
        with self.assertRaises(GatewayError) as error:
            self.harness.service.start_chat(chat("existing"))
        self.assertEqual("codex_request_failed", error.exception.code)
        self.assertEqual(0, self.harness.query()["thread_start"])
        self.assertEqual(1, self.harness.query()["thread_resume"])

    def test_interrupt_and_stream_close_are_idempotent(self):
        harness = self.open("gateway_silent")
        connection, response = harness.start_stream(chat())
        self.assertEqual(200, response.status)
        first = response.readline()
        request_id = sse_values(first)[0]["id"]
        status, interrupted = harness.request("POST", "/internal/codex/turn/" + request_id + "/interrupt")
        self.assertEqual(200, status)
        self.assertEqual("interrupt_requested", interrupted["status"])
        values = sse_values(first + response.read())
        connection.close()
        self.assertEqual("turn_interrupted", values[-2]["code"])
        time.sleep(0.05)
        self.assertEqual(1, harness.query()["turn_interrupt"])

        active = harness.service.start_chat(chat("conversation-close"))
        stream = harness.service.stream(active)
        next(stream)
        stream.close()
        time.sleep(0.05)
        self.assertEqual(2, harness.query()["turn_interrupt"])

    def test_request_limits_and_active_turn_conflict(self):
        harness = self.open("gateway_silent")
        oversized = {"stream": True, "model": "model-alpha", "messages": [{"role": "user", "content": "x" * (17 * 1024)}]}
        status, error = harness.request("POST", "/v1/chat/completions", oversized)
        self.assertEqual(413, status)
        self.assertEqual("request_too_large", error["error"]["code"])
        active = harness.service.start_chat(chat())
        with self.assertRaises(GatewayError) as error:
            harness.service.start_chat(chat("other"))
        self.assertEqual("turn_already_active", error.exception.code)
        harness.service.interrupt(active.request_id)

    def test_turn_timeout_and_process_crash_have_one_terminal_error(self):
        harness = self.open("gateway_silent")
        original_timeout = gateway_module.TURN_TIMEOUT_SECONDS
        gateway_module.TURN_TIMEOUT_SECONDS = 0.05
        try:
            connection, response = harness.start_stream(chat())
            self.assertEqual(200, response.status)
            values = sse_values(response.read())
            connection.close()
        finally:
            gateway_module.TURN_TIMEOUT_SECONDS = original_timeout
        self.assertEqual("turn_timeout", values[-2]["code"])
        time.sleep(0.05)
        self.assertEqual(1, harness.query()["turn_interrupt"])
        harness.close()
        self.harness = GatewayHarness("gateway_crash")
        connection, response = self.harness.start_stream(chat())
        self.assertEqual(200, response.status)
        values = sse_values(response.read())
        connection.close()
        self.assertEqual("codex_process_lost", values[-2]["code"])
        self.assertEqual("[DONE]", values[-1])


if __name__ == "__main__":
    unittest.main()
