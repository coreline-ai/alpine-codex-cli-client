from __future__ import annotations

import http.client
import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from codex_gateway import agent_gateway
from codex_gateway import gateway as gateway_module
from codex_gateway.agents.grok import GrokAgentAdapter
from codex_gateway.agents.http import make_agent_handler
from codex_gateway.agents.contracts import AgentConversationBinding, AgentId
from codex_gateway.agents.router import AgentRouter
from codex_gateway.agents.service import AgentGatewayService
from codex_gateway.gateway import (
    BoundedGatewayRequestHandler,
    FIXED_HTTP_HOST,
    LOOPBACK_HOST,
    LoopbackGatewayServer,
    MAX_REQUEST_HEADER_BYTES,
    PrivateUnixGatewayServer,
)
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
            "--socket-path",
            agent_gateway.SOCKET_PATH,
            "--peer-uid",
            "12345",
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

    def test_socket_path_allowlist_rejects_traversal_alternate_names_and_oversize(self):
        self.assertTrue(agent_gateway._valid_socket_path(agent_gateway.SOCKET_PATH))
        invalid = (
            agent_gateway.SOCKET_PATH.replace("/.gateway/", "/.gateway/../.gateway/"),
            agent_gateway.SOCKET_PATH.replace("gateway.sock", "alternate.sock"),
            agent_gateway.SOCKET_PATH.replace("dev.alpine.codexclient.debug", "bad/package"),
            "/data/user/0/" + "x" * 128 + "/files/alpine-codex-runtime/workspace/.gateway/gateway.sock",
        )
        for path in invalid:
            with self.subTest(path=path):
                self.assertFalse(agent_gateway._valid_socket_path(path))


class PrivateUnixGatewayServerTest(unittest.TestCase):
    @staticmethod
    def _server(directory, handler, *, max_connections=8):
        path = os.path.join(directory, "gateway.sock")
        server = PrivateUnixGatewayServer(
            path,
            handler,
            expected_peer_uid=12345,
            peer_uid_reader=lambda _connection: 12345,
            max_connections=max_connections,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return path, server, thread

    @staticmethod
    def _close_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(2.0)

    @staticmethod
    def _wait_for(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_private_socket_preserves_agent_http_contract_and_unlinks_on_close(self):
        supervisor = FakeGrokSupervisor()
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        router = AgentRouter([adapter], selected_agent=adapter.agent_id)
        service = AgentGatewayService(router)

        def authorize(_method, _target, headers, _body):
            if headers.get("x-test-authorization") != ("fixture",):
                raise PermissionError

        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path = os.path.join(directory, "gateway.sock")
            server = PrivateUnixGatewayServer(
                path,
                make_agent_handler(service, authorize),
                expected_peer_uid=12345,
                peer_uid_reader=lambda _connection: 12345,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(3.0)
                client.connect(path)
                client.sendall(
                    (
                        "GET /healthz HTTP/1.1\r\n"
                        f"Host: {FIXED_HTTP_HOST}\r\n"
                        "X-Test-Authorization: fixture\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii"),
                )
                chunks = []
                while True:
                    value = client.recv(4096)
                    if not value:
                        break
                    chunks.append(value)
                client.close()
                response = b"".join(chunks)
                self.assertTrue(response.startswith(b"HTTP/1.0 200"))
                self.assertIn(b'"selected_agent":"grok"', response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2.0)
                supervisor.authenticate_release.set()
                if adapter.is_ready() and not adapter.activity().active_login:
                    adapter.deactivate()
            self.assertFalse(os.path.exists(path))

    def test_private_socket_rejects_untrusted_peer_before_handler(self):
        supervisor = FakeGrokSupervisor()
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        service = AgentGatewayService(AgentRouter([adapter], selected_agent=adapter.agent_id))
        authorization_calls = []

        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path = os.path.join(directory, "gateway.sock")
            server = PrivateUnixGatewayServer(
                path,
                make_agent_handler(service, lambda *_args: authorization_calls.append(True)),
                expected_peer_uid=12345,
                peer_uid_reader=lambda _connection: 54321,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(3.0)
                client.connect(path)
                client.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:8787\r\n\r\n")
                self.assertEqual(b"", client.recv(1))
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2.0)
                supervisor.authenticate_release.set()
                if adapter.is_ready() and not adapter.activity().active_login:
                    adapter.deactivate()
        self.assertEqual([], authorization_calls)

    def test_private_socket_rejects_unsafe_parent_regular_file_and_symlink(self):
        class FixtureHandler(BoundedGatewayRequestHandler):
            pass

        with tempfile.TemporaryDirectory() as raw:
            parent = os.path.join(raw, "private")
            os.mkdir(parent, mode=0o700)
            path = os.path.join(parent, "gateway.sock")

            os.chmod(parent, 0o755)
            with self.assertRaises(PermissionError):
                PrivateUnixGatewayServer(path, FixtureHandler, expected_peer_uid=os.getuid())
            os.chmod(parent, 0o700)

            with open(path, "wb") as value:
                value.write(b"fixture")
            with self.assertRaises(PermissionError):
                PrivateUnixGatewayServer(path, FixtureHandler, expected_peer_uid=os.getuid())
            with open(path, "rb") as value:
                self.assertEqual(b"fixture", value.read())
            os.unlink(path)

            target = os.path.join(parent, "target")
            with open(target, "wb") as value:
                value.write(b"fixture")
            os.symlink(target, path)
            with self.assertRaises(PermissionError):
                PrivateUnixGatewayServer(path, FixtureHandler, expected_peer_uid=os.getuid())
            self.assertTrue(os.path.islink(path))

    def test_stale_owned_socket_is_reclaimed_but_replacement_inode_is_preserved(self):
        class FixtureHandler(BoundedGatewayRequestHandler):
            pass

        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path = os.path.join(directory, "gateway.sock")
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(path)
            stale.close()

            server = PrivateUnixGatewayServer(
                path,
                FixtureHandler,
                expected_peer_uid=os.getuid(),
            )
            os.unlink(path)
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement.bind(path)
            try:
                server.server_close()
                self.assertTrue(os.path.exists(path))
            finally:
                replacement.close()

    def test_connection_slots_bound_partial_clients_and_return_to_baseline(self):
        class FixtureHandler(BoundedGatewayRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):  # noqa: N802
                self._authorization_complete()
                self.send_response(204)
                self.end_headers()

        descriptor_baseline = len(os.listdir("/dev/fd"))
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path, server, thread = self._server(directory, FixtureHandler, max_connections=2)
            clients = []
            try:
                for _ in range(2):
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(1.0)
                    client.connect(path)
                    clients.append(client)
                self.assertTrue(
                    self._wait_for(lambda: server.security_snapshot()["active_connections"] == 2)
                )

                for _ in range(256):
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(1.0)
                    client.connect(path)
                    self.assertEqual(b"", client.recv(1))
                    client.close()

                snapshot = server.security_snapshot()
                self.assertEqual(2, snapshot["max_active_connections"])
                self.assertGreaterEqual(snapshot["capacity_rejected"], 256)
            finally:
                for client in clients:
                    client.close()
                self.assertTrue(
                    self._wait_for(lambda: server.security_snapshot()["active_connections"] == 0)
                )
                self._close_server(server, thread)
            self.assertFalse(os.path.exists(path))
        self.assertLessEqual(len(os.listdir("/dev/fd")), descriptor_baseline)

    def test_absolute_pre_auth_deadline_closes_partial_body(self):
        authorization_calls = []

        class PartialBodyHandler(BoundedGatewayRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(10)
                if len(body) == 10:
                    authorization_calls.append(True)
                    self._authorization_complete()

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            gateway_module,
            "GATEWAY_PRE_AUTH_DEADLINE_SECONDS",
            0.1,
        ):
            os.chmod(directory, 0o700)
            path, server, thread = self._server(directory, PartialBodyHandler)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            try:
                client.connect(path)
                client.sendall(
                    (
                        "POST /v1/chat/completions HTTP/1.1\r\n"
                        f"Host: {FIXED_HTTP_HOST}\r\n"
                        "Content-Length: 10\r\n\r\nx"
                    ).encode("ascii")
                )
                self.assertEqual(b"", client.recv(1))
                self.assertTrue(
                    self._wait_for(lambda: server.security_snapshot()["preauth_timeout"] == 1)
                )
                self.assertEqual([], authorization_calls)
            finally:
                client.close()
                self._close_server(server, thread)

    def test_aggregate_header_budget_rejects_before_handler(self):
        handler_calls = []

        class HeaderHandler(BoundedGatewayRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):  # noqa: N802
                handler_calls.append(True)
                self._authorization_complete()

        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            path, server, thread = self._server(directory, HeaderHandler)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            try:
                client.connect(path)
                client.sendall(
                    b"GET /healthz HTTP/1.1\r\nX-Fill: "
                    + b"a" * MAX_REQUEST_HEADER_BYTES
                    + b"\r\n\r\n"
                )
                while client.recv(4096):
                    pass
                self.assertTrue(
                    self._wait_for(lambda: server.security_snapshot()["header_rejected"] == 1)
                )
                self.assertEqual([], handler_calls)
            finally:
                client.close()
                self._close_server(server, thread)

    def test_authenticated_long_stream_is_not_subject_to_pre_auth_deadline(self):
        class StreamHandler(BoundedGatewayRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):  # noqa: N802
                self._authorization_complete()
                time.sleep(0.15)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            gateway_module,
            "GATEWAY_PRE_AUTH_DEADLINE_SECONDS",
            0.05,
        ):
            os.chmod(directory, 0o700)
            path, server, thread = self._server(directory, StreamHandler)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            try:
                client.connect(path)
                client.sendall(b"GET /stream HTTP/1.1\r\nHost: fixture\r\n\r\n")
                response = b""
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                self.assertTrue(response.startswith(b"HTTP/1.0 200"))
                self.assertTrue(response.endswith(b"ok"))
                self.assertEqual(0, server.security_snapshot()["preauth_timeout"])
            finally:
                client.close()
                self._close_server(server, thread)


def sse_values(raw):
    values = []
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data: "):
            payload = line[6:]
            values.append(payload if payload == "[DONE]" else json.loads(payload))
    return values


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path, timeout=3.0):
        super().__init__(LOOPBACK_HOST, 8787, timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


class AgentGatewayHarness:
    def __init__(self, authorizer=None, header_factory=None, private_socket=False):
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
        self.private_directory = tempfile.TemporaryDirectory() if private_socket else None
        if self.private_directory is not None:
            os.chmod(self.private_directory.name, 0o700)
            self.socket_path = os.path.join(self.private_directory.name, "gateway.sock")
            self.server = PrivateUnixGatewayServer(
                self.socket_path,
                make_agent_handler(self.service, authorizer or authorize),
                expected_peer_uid=os.getuid(),
                # macOS test hosts do not expose Linux SO_PEERCRED; Android instrumentation
                # covers the real kernel credential path.
                peer_uid_reader=lambda _connection: os.getuid(),
            )
            self.port = None
        else:
            self.socket_path = None
            self.server = LoopbackGatewayServer(
                (LOOPBACK_HOST, 0),
                make_agent_handler(self.service, authorizer or authorize),
            )
            self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def connection(self):
        if self.socket_path is not None:
            return UnixHTTPConnection(self.socket_path)
        return http.client.HTTPConnection(LOOPBACK_HOST, self.port, timeout=3.0)

    def request(self, method, path, value=None, authorized=True, extra_headers=None):
        body = None if value is None else json.dumps(value).encode("utf-8")
        if authorized and self.header_factory is not None:
            headers = self.header_factory(method, path, body or b"")
        else:
            headers = {"X-Test-Authorization": "fixture"} if authorized else {}
        headers.update(extra_headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = self.connection()
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload.decode("utf-8"))

    def stream(self, value):
        body = json.dumps(value).encode("utf-8")
        connection = self.connection()
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
        if self.private_directory is not None:
            self.private_directory.cleanup()


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
        self.harness.supervisor.authenticated = True
        cases = (
            (AcpProcessLost("private-process-detail"), "grok_acp_process_lost"),
            (AcpProtocolError("private-protocol-detail"), "grok_acp_protocol_error"),
            (AcpTimeout("private-timeout-detail"), "grok_acp_timeout"),
            (AcpRemoteError(), "grok_session_new_remote_unknown"),
            (AcpPendingLimit("private-pending-detail"), "grok_acp_pending_limit"),
            (AcpStopped("private-stop-detail"), "grok_acp_stopped"),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected):
                self.harness.supervisor.new_session = (
                    lambda *_args, failure=failure, **_kwargs: (_ for _ in ()).throw(failure)
                )
                status, value = self.harness.stream(chat())
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

    def test_private_uds_preserves_post_sse_and_stop_contract(self):
        private = AgentGatewayHarness(private_socket=True)
        try:
            private.supervisor.authenticated = True
            status, models = private.request("GET", "/v1/models")
            self.assertEqual(200, status)
            self.assertEqual(["model-alpha", "model-beta"], [item["id"] for item in models["data"]])

            status, values = private.stream(chat())
            self.assertEqual(200, status)
            self.assertEqual(
                ["start", "done", "[DONE]"],
                [item if isinstance(item, str) else item["type"] for item in values],
            )
            turn_id = values[0]["id"]
            status, stopped = private.request(
                "POST",
                f"/internal/agents/grok/turn/{turn_id}/interrupt",
            )
            self.assertEqual(200, status)
            self.assertEqual("interrupt_requested", stopped["status"])
            self.assertEqual(1, len(private.supervisor.prompt_calls))
            self.assertEqual([], private.supervisor.cancel_session_calls)
        finally:
            private.close()

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

    def test_agent_query_fails_closed_and_stale_binding_loads_before_prompt(self):
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
            self.assertEqual(200, status)
            self.assertEqual("[DONE]", response[-1])
            self.assertEqual(
                [("session-stale", "/workspace")],
                stale.supervisor.load_session_calls,
            )
            self.assertEqual([], stale.supervisor.resume_session_calls)
            self.assertEqual(
                [("session-stale", "fixture prompt")],
                stale.supervisor.prompt_calls,
            )
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
