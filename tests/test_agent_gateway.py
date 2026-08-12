from __future__ import annotations

import http.client
import json
import threading
import time
import unittest

from codex_gateway.agents.grok import GrokAgentAdapter
from codex_gateway.agents.http import make_agent_handler
from codex_gateway.agents.contracts import AgentConversationBinding, AgentId
from codex_gateway.agents.router import AgentRouter
from codex_gateway.agents.service import AgentGatewayService
from codex_gateway.gateway import LOOPBACK_HOST, LoopbackGatewayServer
from tests.test_grok_agent_adapter import FakeGrokSupervisor, chat


def sse_values(raw):
    values = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            values.append(payload if payload == "[DONE]" else json.loads(payload))
    return values


class AgentGatewayHarness:
    def __init__(self):
        self.supervisor = FakeGrokSupervisor()
        self.adapter = GrokAgentAdapter("/workspace", self.supervisor)
        self.adapter.activate()
        self.router = AgentRouter([self.adapter], selected_agent=self.adapter.agent_id)
        self.service = AgentGatewayService(self.router)
        self.authorization_calls = []

        def authorize(method, target, headers, body):
            self.authorization_calls.append((method, target, len(body)))
            if headers.get("x-test-authorization") != ("fixture",):
                raise PermissionError

        self.server = LoopbackGatewayServer(
            (LOOPBACK_HOST, 0),
            make_agent_handler(self.service, authorize),
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, value=None, authorized=True):
        body = None if value is None else json.dumps(value).encode("utf-8")
        headers = {"X-Test-Authorization": "fixture"} if authorized else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3.0)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload.decode("utf-8"))

    def stream(self, value):
        body = json.dumps(value).encode("utf-8")
        connection = http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3.0)
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-Test-Authorization": "fixture",
            },
        )
        response = connection.getresponse()
        content_type = response.getheader("Content-Type", "")
        payload = response.read()
        connection.close()
        if content_type.startswith("application/json"):
            return response.status, json.loads(payload.decode("utf-8"))
        return response.status, sse_values(payload)

    def close(self):
        self.supervisor.authenticate_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)
        if self.adapter.is_ready() and not self.adapter.activity().active_login:
            self.adapter.deactivate()


class AgentGatewayHttpTest(unittest.TestCase):
    def setUp(self):
        self.harness = AgentGatewayHarness()

    def tearDown(self):
        self.harness.close()

    def test_every_route_requires_injected_authorizer_before_adapter_work(self):
        for method, path, body in (
            ("GET", "/healthz", None),
            ("GET", "/v1/agents", None),
            ("GET", "/v1/models?agent_id=grok", None),
            ("GET", "/internal/agents/grok/account", None),
            ("GET", "/internal/agents/grok/login/request-fixture", None),
            ("POST", "/internal/agents/select", {"agent_id": "grok"}),
            ("POST", "/internal/agents/grok/login/device", None),
            ("POST", "/internal/agents/grok/login/request-fixture/cancel", None),
            ("POST", "/internal/agents/grok/logout", None),
            ("POST", "/v1/chat/completions", chat()),
            ("POST", "/internal/agents/grok/turn/request-fixture/interrupt", None),
        ):
            status, value = self.harness.request(method, path, body, authorized=False)
            self.assertEqual(401, status)
            self.assertEqual("gateway_unauthorized", value["error"]["code"])
        self.assertEqual([], self.harness.supervisor.authenticate_calls)
        self.assertEqual(11, len(self.harness.authorization_calls))

    def test_fake_device_login_model_session_stream_and_logout_lifecycle(self):
        status, agents = self.harness.request("GET", "/v1/agents")
        self.assertEqual(200, status)
        self.assertEqual(["grok"], [item["id"] for item in agents["data"]])
        self.assertTrue(agents["data"][0]["selected"])

        status, account = self.harness.request("GET", "/internal/agents/grok/account")
        self.assertEqual(200, status)
        self.assertEqual(
            {"agent_id": "grok", "authenticated": False, "requires_auth": True},
            account,
        )

        self.harness.supervisor.authenticate_release.clear()
        status, login = self.harness.request("POST", "/internal/agents/grok/login/device")
        self.assertEqual(200, status)
        self.assertEqual("pending", login["status"])
        self.assertIn("verification_url", login)
        self.assertNotIn("user_code", login)
        request_id = login["request_id"]

        status, duplicate = self.harness.request("POST", "/internal/agents/grok/login/device")
        self.assertEqual(409, status)
        self.assertEqual("agent_login_active", duplicate["error"]["code"])
        self.assertEqual([1], self.harness.supervisor.authenticate_calls)

        status, pending = self.harness.request(
            "GET", "/internal/agents/grok/login/" + request_id
        )
        self.assertEqual(200, status)
        self.assertEqual("pending", pending["status"])
        self.assertNotIn("verification_url", pending)

        self.harness.supervisor.authenticate_release.set()
        deadline = time.monotonic() + 1.0
        state = "pending"
        while state == "pending" and time.monotonic() < deadline:
            status, value = self.harness.request(
                "GET", "/internal/agents/grok/login/" + request_id
            )
            self.assertEqual(200, status)
            state = value["status"]
            if state == "pending":
                time.sleep(0.005)
        self.assertEqual("authenticated", state)

        status, models = self.harness.request("GET", "/v1/models?agent_id=grok")
        self.assertEqual(200, status)
        self.assertEqual(["model-alpha", "model-beta"], [item["id"] for item in models["data"]])

        status, values = self.harness.stream(chat())
        self.assertEqual(200, status)
        self.assertEqual(["start", "done", "[DONE]"], [
            item if isinstance(item, str) else item["type"] for item in values
        ])
        self.assertEqual(1, len(self.harness.supervisor.new_session_calls))
        self.assertEqual(1, len(self.harness.supervisor.prompt_calls))

        self.harness.supervisor.auth_info_payload = {
            "email": "private@example.invalid",
            "profileImageUrl": "https://private.invalid/profile",
            "token": "private-token",
        }
        status, redacted_account = self.harness.request(
            "GET", "/internal/agents/grok/account"
        )
        self.assertEqual(200, status)
        self.assertEqual(
            {"agent_id": "grok", "authenticated": True, "requires_auth": False},
            redacted_account,
        )
        status, logged_out = self.harness.request("POST", "/internal/agents/grok/logout")
        self.assertEqual(200, status)
        self.assertEqual({"agent_id": "grok", "status": "logged_out"}, logged_out)
        serialized = json.dumps(logged_out)
        for private in self.harness.supervisor.auth_info_payload.values():
            self.assertNotIn(private, serialized)

    def test_cancel_route_is_exact_and_late_auth_success_does_not_change_status(self):
        self.harness.supervisor.authenticate_release.clear()
        self.harness.supervisor.authenticate_ignores_cancel = True
        status, login = self.harness.request("POST", "/internal/agents/grok/login/device")
        self.assertEqual(200, status)
        request_id = login["request_id"]
        status, cancelled = self.harness.request(
            "POST", "/internal/agents/grok/login/" + request_id + "/cancel"
        )
        self.assertEqual(200, status)
        self.assertEqual("cancelled", cancelled["status"])
        self.harness.supervisor.authenticate_release.set()
        time.sleep(0.05)
        status, value = self.harness.request(
            "GET", "/internal/agents/grok/login/" + request_id
        )
        self.assertEqual(200, status)
        self.assertEqual("cancelled", value["status"])
        self.assertEqual([1], self.harness.supervisor.cancel_auth_calls)

    def test_agent_query_and_binding_fail_closed_before_backend_call(self):
        self.harness.supervisor.authenticated = True
        status, error = self.harness.request("GET", "/v1/models?agent_id=codex")
        self.assertEqual(404, status)
        self.assertEqual("agent_unavailable", error["error"]["code"])

        stale = AgentGatewayHarness()
        try:
            stale.supervisor.authenticated = True
            stale.adapter._bindings["conversation-stale"] = AgentConversationBinding(
                agent_id=AgentId.GROK,
                conversation_id="conversation-stale",
                backend_session_id="session-stale",
                model_id="model-alpha",
                process_generation=stale.supervisor.generation - 1,
            )
            value = chat("conversation-stale", resume=True)
            status, response = stale.stream(value)
            self.assertEqual(409, status)
            self.assertEqual("conversation_generation_mismatch", response["error"]["code"])
            self.assertEqual([], stale.supervisor.resume_session_calls)
            self.assertEqual([], stale.supervisor.prompt_calls)
        finally:
            stale.close()


if __name__ == "__main__":
    unittest.main()
