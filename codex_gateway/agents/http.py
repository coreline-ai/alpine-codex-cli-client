"""Authenticated-middleware-ready HTTP/SSE carrier for normalized Agent operations."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
from typing import Any, Callable, Dict, Mapping, Tuple
from urllib.parse import parse_qs, urlsplit

from codex_gateway.agents.service import AgentGatewayService, AgentServiceError
from codex_gateway.gateway import LOOPBACK_HOST, MAX_REQUEST_BYTES, MAX_SSE_EVENT_BYTES, _json_depth


RequestAuthorizer = Callable[[str, str, Mapping[str, Tuple[str, ...]], bytes], None]


def make_agent_handler(
    service: AgentGatewayService,
    authorize_request: RequestAuthorizer,
):
    """Build a handler only when a verifier is explicitly supplied.

    Phase 8 replaces the test authorizer with the session-capability verifier. There is no
    unsigned compatibility default for the normalized routes.
    """

    if not isinstance(service, AgentGatewayService) or not callable(authorize_request):
        raise ValueError("normalized Agent handler requires service and request authorizer")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AlpineAgentGateway/0.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if not self._is_loopback_client():
                self._error(403, "loopback_required")
                return
            try:
                parsed = self._target()
                self._authorize("GET", self.path, b"")
                if parsed.path == "/healthz" and not parsed.query:
                    self._json(200, service.health())
                elif parsed.path == "/v1/agents" and not parsed.query:
                    self._json(200, service.agents())
                elif parsed.path == "/v1/models":
                    agent_id = self._single_agent_query(parsed.query)
                    self._json(200, service.models(agent_id))
                else:
                    parts = parsed.path.strip("/").split("/")
                    if not parsed.query and len(parts) == 4 and parts[:2] == ["internal", "agents"] and parts[3] == "account":
                        self._json(200, service.account(parts[2]))
                    elif not parsed.query and len(parts) == 5 and parts[:2] == ["internal", "agents"] and parts[3] == "login":
                        self._json(200, service.login_status(parts[2], parts[4]))
                    else:
                        self._error(404, "not_found")
            except AgentServiceError as error:
                self._error(error.status, error.code)
            except PermissionError:
                self._error(401, "gateway_unauthorized")

        def do_POST(self) -> None:  # noqa: N802
            if not self._is_loopback_client():
                self._error(403, "loopback_required")
                return
            try:
                parsed = self._target()
                body = self._read_body()
                self._authorize("POST", self.path, body)
                if parsed.query:
                    raise AgentServiceError(404, "not_found")
                if parsed.path == "/internal/agents/select":
                    self._json(200, service.select(self._request_object(body)))
                    return
                if parsed.path == "/v1/chat/completions":
                    handle = service.start_chat(self._request_object(body))
                    self._stream(service, handle)
                    return
                parts = parsed.path.strip("/").split("/")
                if len(parts) == 5 and parts[:2] == ["internal", "agents"] and parts[3:] == ["login", "device"]:
                    self._require_empty(body)
                    self._json(200, service.start_login(parts[2]))
                elif len(parts) == 4 and parts[:2] == ["internal", "agents"] and parts[3] == "logout":
                    self._require_empty(body)
                    self._json(200, service.logout(parts[2]))
                elif len(parts) == 6 and parts[:2] == ["internal", "agents"] and parts[3] == "login" and parts[5:] == ["cancel"]:
                    self._require_empty(body)
                    self._json(200, service.cancel_login(parts[2], parts[4]))
                elif len(parts) == 6 and parts[:2] == ["internal", "agents"] and parts[3] == "turn" and parts[5:] == ["interrupt"]:
                    self._require_empty(body)
                    self._json(200, service.interrupt(parts[2], parts[4]))
                else:
                    self._error(404, "not_found")
            except AgentServiceError as error:
                self._error(error.status, error.code)
            except PermissionError:
                self._error(401, "gateway_unauthorized")

        def _target(self):
            if not isinstance(self.path, str) or len(self.path) > 2048 or not self.path.startswith("/"):
                raise AgentServiceError(400, "invalid_request")
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise AgentServiceError(400, "invalid_request")
            return parsed

        def _read_body(self) -> bytes:
            raw = self.headers.get("Content-Length")
            if raw is None:
                return b""
            try:
                length = int(raw)
            except ValueError as error:
                raise AgentServiceError(400, "invalid_request") from error
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise AgentServiceError(413, "request_too_large")
            return self.rfile.read(length)

        def _authorize(self, method: str, target: str, body: bytes) -> None:
            # Header values are copied only for immediate verification; neither this handler nor
            # the service stores or logs them.
            headers = {
                key.lower(): tuple(self.headers.get_all(key) or ())
                for key in self.headers.keys()
            }
            authorize_request(method, target, headers, body)

        @staticmethod
        def _request_object(body: bytes) -> Dict[str, Any]:
            if not body:
                raise AgentServiceError(400, "invalid_request")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise AgentServiceError(400, "invalid_request") from error
            if not isinstance(value, dict) or _json_depth(value) > 12:
                raise AgentServiceError(400, "invalid_request")
            return value

        @staticmethod
        def _require_empty(body: bytes) -> None:
            if body:
                raise AgentServiceError(400, "invalid_request")

        @staticmethod
        def _single_agent_query(query: str):
            if not query:
                return None
            try:
                values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
            except ValueError as error:
                raise AgentServiceError(400, "invalid_request") from error
            if set(values) != {"agent_id"} or len(values["agent_id"]) != 1:
                raise AgentServiceError(400, "invalid_request")
            return values["agent_id"][0]

        def _stream(self, gateway: AgentGatewayService, handle) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for value in gateway.stream(handle):
                    self._event(value)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                try:
                    gateway.interrupt(handle.agent_id, handle.request_id)
                except AgentServiceError:
                    pass

        def _event(self, value: Dict[str, Any]) -> None:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(payload) > MAX_SSE_EVENT_BYTES:
                raise OSError("sse_event_too_large")
            self.wfile.write(b"data: " + payload + b"\n\n")
            self.wfile.flush()

        def _json(self, status: int, value: Dict[str, Any]) -> None:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, status: int, code: str) -> None:
            self._json(status, {"error": {"code": code}})

        def _is_loopback_client(self) -> bool:
            return self.client_address[0] == LOOPBACK_HOST

    return Handler
