from __future__ import annotations

import http.client
import base64
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
import unittest
from unittest import mock

from codex_gateway import agent_gateway
from codex_gateway.agents.grok import GrokAgentAdapter
from codex_gateway.agents.http import make_agent_handler
from codex_gateway.agents.contracts import AgentConversationBinding, AgentId
from codex_gateway.agents.router import AgentRouter
from codex_gateway.agents.service import AgentGatewayService
from codex_gateway.gateway import LOOPBACK_HOST, LoopbackGatewayServer
from codex_gateway.security import SessionCapabilityVerifier
from codex_gateway.grok_acp.rpc import (
    AcpPendingLimit,
    AcpProcessLost,
    AcpProtocolError,
    AcpRemoteError,
    AcpStopped,
    AcpTimeout,
)
from codex_gateway.grok_acp.policy import CHILD_UMASK
from tests.test_grok_agent_adapter import FakeGrokSupervisor, agent_chunk, chat, retry_state


class AgentGatewayEntrypointTest(unittest.TestCase):
    def test_main_locks_private_process_umask_before_serving(self):
        argv = [
            "agent_gateway",
            "--codex",
            "/workspace/.alpine-codex/staging/codex-cli/0.147.0/codex",
            "--grok",
            agent_gateway.GUEST_EXECUTABLE.as_posix(),
            "--codex-home",
            agent_gateway.CODEX_HOME,
            "--grok-home",
            agent_gateway.GUEST_HOME.as_posix(),
            "--grok-work",
            agent_gateway.GUEST_WORK.as_posix(),
            "--workdir",
            agent_gateway.WORKSPACE,
            "--capability-file",
            agent_gateway.CAPABILITY_FILE,
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(agent_gateway.os, "umask") as umask,
            mock.patch.object(agent_gateway, "serve") as serve,
        ):
            result = agent_gateway.main()

        self.assertEqual(0, result)
        umask.assert_called_once_with(CHILD_UMASK)
        serve.assert_called_once()


def sse_values(raw):
    values = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            values.append(payload if payload == "[DONE]" else json.loads(payload))
    return values


class AgentGatewayHarness:
    def __init__(self, authorizer=None, header_factory=None):
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

        self.header_factory = header_factory
        self.server = LoopbackGatewayServer(
            (LOOPBACK_HOST, 0),
            make_agent_handler(self.service, authorizer or authorize),
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, path, value=None, authorized=True, extra_headers=None):
        body = None if value is None else json.dumps(value).encode("utf-8")
        if authorized and self.header_factory is not None:
            headers = self.header_factory(method, path, body or b"")
        else:
            headers = {"X-Test-Authorization": "fixture"} if authorized else {}
        headers.update(extra_headers or {})
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
            ("GET", "/v1/models", None),
            ("GET", "/internal/agents/grok/account", None),
            ("GET", "/internal/agents/grok/login/request-fixture", None),
            ("POST", "/internal/agents/select", {"agent_id": "grok"}),
            ("POST", "/internal/agents/grok/login/device", None),
            ("POST", "/internal/agents/grok/login/request-fixture/cancel", None),
            ("POST", "/internal/agents/grok/login/active/cancel", None),
            ("POST", "/internal/agents/grok/logout", None),
            ("POST", "/v1/chat/completions", chat()),
            ("POST", "/internal/agents/grok/turn/request-fixture/interrupt", None),
        ):
            status, value = self.harness.request(method, path, body, authorized=False)
            self.assertEqual(401, status)
            self.assertEqual("gateway_auth_failed", value["error"]["code"])
        self.assertEqual([], self.harness.supervisor.authenticate_calls)
        self.assertEqual(12, len(self.harness.authorization_calls))

    def test_grok_transport_failures_expose_only_closed_content_free_codes(self):
        cases = (
            (AcpProcessLost("private-process-detail"), "grok_acp_process_lost"),
            (AcpProtocolError("private-protocol-detail"), "grok_acp_protocol_error"),
            (AcpTimeout("private-timeout-detail"), "grok_acp_timeout"),
            (AcpRemoteError("private-remote-detail"), "grok_acp_remote_error"),
            (AcpPendingLimit("private-pending-detail"), "grok_acp_pending_limit"),
            (AcpStopped("private-stop-detail"), "grok_acp_stopped"),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected):
                self.harness.supervisor.auth_info = lambda failure=failure: (_ for _ in ()).throw(failure)
                status, value = self.harness.request("GET", "/internal/agents/grok/account")
                self.assertEqual(502, status)
                self.assertEqual(expected, value["error"]["code"])
                serialized = json.dumps(value)
                self.assertNotIn("private", serialized)

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

        status, models = self.harness.request("GET", "/v1/models")
        self.assertEqual(200, status)
        self.assertEqual(["model-alpha", "model-beta"], [item["id"] for item in models["data"]])

        status, values = self.harness.stream(chat())
        self.assertEqual(200, status)
        self.assertEqual(["start", "done", "[DONE]"], [
            item if isinstance(item, str) else item["type"] for item in values
        ])
        self.assertEqual(
            {
                "prompt_dispatch_count": 1,
                "visible_delta_count": 0,
                "terminal_count": 1,
                "cancel_dispatch_count": 0,
                "retry_classification": "none",
                "retry_attempts": 0,
                "retry_max": 0,
                "tool_event_count": 0,
                "subagent_event_count": 0,
                "mcp_event_count": 0,
                "filesystem_event_count": 0,
                "terminal_event_count": 0,
            },
            values[1]["diagnostics"],
        )
        self.assertEqual(1, len(self.harness.supervisor.new_session_calls))
        self.assertEqual(1, len(self.harness.supervisor.prompt_calls))
        turn_id = values[0]["id"]
        status, stopped_after_done = self.harness.request(
            "POST", f"/internal/agents/grok/turn/{turn_id}/interrupt"
        )
        self.assertEqual(200, status)
        self.assertEqual("interrupt_requested", stopped_after_done["status"])
        self.assertEqual([], self.harness.supervisor.cancel_session_calls)

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

    def test_production_hmac_accepts_signed_and_rejects_unsigned_replay_and_browser(self):
        secret = bytes(range(32))

        def signed(method, path, body):
            timestamp = int(time.time())
            nonce = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()
            body_hash = hashlib.sha256(body).hexdigest()
            canonical = f"v1\n{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}".encode()
            signature = base64.urlsafe_b64encode(
                hmac.new(secret, canonical, hashlib.sha256).digest()
            ).rstrip(b"=").decode()
            return {
                "X-Alpine-Auth-Version": "1",
                "X-Alpine-Timestamp": str(timestamp),
                "X-Alpine-Nonce": nonce,
                "X-Alpine-Content-SHA256": body_hash,
                "X-Alpine-Signature": signature,
            }

        harness = AgentGatewayHarness(
            authorizer=SessionCapabilityVerifier(secret).authorize,
            header_factory=signed,
        )
        try:
            status, value = harness.request("GET", "/healthz")
            self.assertEqual(200, status)
            self.assertEqual("ready", value["gateway"])
            status, value = harness.request("GET", "/internal/agents/grok/account", authorized=False)
            self.assertEqual(401, status)
            self.assertEqual("gateway_auth_failed", value["error"]["code"])

            replay_headers = signed("GET", "/healthz", b"")
            self.assertEqual(200, harness.request("GET", "/healthz", extra_headers=replay_headers, authorized=False)[0])
            status, value = harness.request(
                "GET", "/healthz", extra_headers=replay_headers, authorized=False
            )
            self.assertEqual(401, status)
            self.assertEqual("gateway_auth_failed", value["error"]["code"])

            browser_headers = signed("GET", "/healthz", b"")
            browser_headers["Origin"] = "https://browser.invalid"
            status, value = harness.request(
                "GET", "/healthz", extra_headers=browser_headers, authorized=False
            )
            self.assertEqual(400, status)
            self.assertEqual("invalid_request", value["error"]["code"])
        finally:
            harness.close()

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

    def test_recovered_active_login_can_be_cancelled_without_android_storing_opaque_id(self):
        self.harness.supervisor.authenticate_release.clear()
        status, login = self.harness.request("POST", "/internal/agents/grok/login/device")
        self.assertEqual(200, status)
        status, cancelled = self.harness.request(
            "POST", "/internal/agents/grok/login/active/cancel"
        )
        self.assertEqual(200, status)
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual(login["request_id"], cancelled["request_id"])
        self.assertEqual([1], self.harness.supervisor.cancel_auth_calls)

    def test_agent_query_and_binding_fail_closed_before_backend_call(self):
        self.harness.supervisor.authenticated = True
        status, error = self.harness.request(
            "POST", "/internal/agents/select", {"agent_id": "codex"}
        )
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

    def test_pre_output_cli_retry_keeps_one_gateway_dispatch_and_normalized_sse(self):
        self.harness.supervisor.authenticated = True

        def prompt(session_id, text):
            self.harness.supervisor.prompt_calls.append((session_id, text))
            self.harness.supervisor.emit(
                "_x.ai/session_notification",
                retry_state(
                    {
                        "type": "retrying",
                        "attempt": 1,
                        "max_retries": 3,
                        "reason": "private fixture detail",
                    }
                ),
            )
            self.harness.supervisor.emit("session/update", agent_chunk(""))
            self.harness.supervisor.emit("session/update", agent_chunk("visible fixture"))
            return {"stopReason": "end_turn"}

        self.harness.supervisor.prompt = prompt
        status, values = self.harness.stream(chat())
        self.assertEqual(200, status)
        self.assertEqual(
            ["start", "delta", "done", "[DONE]"],
            [item if isinstance(item, str) else item["type"] for item in values],
        )
        self.assertEqual(1, len(self.harness.supervisor.prompt_calls))
        self.assertEqual("pre_output", self.harness.adapter.turn_metrics().retry_classification)

    def test_retry_exhaustion_emits_one_terminal_error_without_replay_or_fallback(self):
        self.harness.supervisor.authenticated = True

        def prompt(session_id, text):
            self.harness.supervisor.prompt_calls.append((session_id, text))
            self.harness.supervisor.emit(
                "_x.ai/session_notification",
                retry_state(
                    {
                        "type": "exhausted",
                        "attempts": 4,
                        "reason": "private fixture detail",
                        "is_rate_limited": False,
                    }
                ),
            )
            return {"stopReason": "end_turn"}

        self.harness.supervisor.prompt = prompt
        status, values = self.harness.stream(chat())
        self.assertEqual(200, status)
        self.assertEqual(
            ["start", "error", "[DONE]"],
            [item if isinstance(item, str) else item["type"] for item in values],
        )
        self.assertEqual("grok_retry_exhausted", values[1]["code"])
        self.assertEqual(1, len(self.harness.supervisor.prompt_calls))
        self.assertEqual(["session-1"], self.harness.supervisor.cancel_session_calls)


if __name__ == "__main__":
    unittest.main()
