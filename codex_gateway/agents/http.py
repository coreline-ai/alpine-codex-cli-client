"""Authenticated-middleware-ready HTTP/SSE carrier for normalized Agent operations."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Tuple
from urllib.parse import urlsplit

from codex_gateway.agents.service import AgentGatewayService, AgentServiceError
from codex_gateway.gateway import (
    BoundedGatewayRequestHandler,
    LOOPBACK_HOST,
    MAX_REQUEST_BYTES,
    MAX_SSE_EVENT_BYTES,
    _json_depth,
)


RequestAuthorizer = Callable[[str, str, Mapping[str, Tuple[str, ...]], bytes], None]


def make_agent_handler(
    service: AgentGatewayService,
    authorize_request: RequestAuthorizer,
):
    """Build a handler only when a verifier is explicitly supplied.

    Production supplies the session-capability verifier. There is no unsigned compatibility
    default for the normalized routes.
    """

    if not isinstance(service, AgentGatewayService) or not callable(authorize_request):
        raise ValueError("normalized Agent handler requires service and request authorizer")

    class Handler(BoundedGatewayRequestHandler):
        server_version = "AlpineAgentGateway/0.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if not self._is_trusted_client():
                self._error(403, "loopback_required")
                return
            try:
                parsed = self._target()
                self._validate_request_shape("GET", parsed.path)
                self._authorize("GET", parsed.path, b"")
                if parsed.path == "/healthz":
                    self._json(200, service.health())
                elif parsed.path == "/v1/agents":
                    self._json(200, service.agents())
                elif parsed.path == "/v1/models":
                    self._json(200, service.models())
                else:
                    parts = parsed.path.strip("/").split("/")
                    if len(parts) == 4 and parts[:2] == ["internal", "agents"] and parts[3] == "account":
                        self._json(200, service.account(parts[2]))
                    elif len(parts) == 5 and parts[:2] == ["internal", "agents"] and parts[3] == "login":
                        self._json(200, service.login_status(parts[2], parts[4]))
                    else:
                        self._error(404, "not_found")
            except AgentServiceError as error:
                self._error(error.status, error.code)
            except PermissionError:
                self._record_auth_rejection()
                self._error(401, "gateway_auth_failed")

        def do_POST(self) -> None:  # noqa: N802
            if not self._is_trusted_client():
                self._error(403, "loopback_required")
                return
            try:
                parsed = self._target()
                self._validate_request_shape("POST", parsed.path)
                body = self._read_body()
                self._authorize("POST", parsed.path, body)
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
                elif len(parts) == 6 and parts[:2] == ["internal", "agents"] and parts[3:] == ["login", "active", "cancel"]:
                    self._require_empty(body)
                    self._json(200, service.cancel_active_login(parts[2]))
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
                self._record_auth_rejection()
                self._error(401, "gateway_auth_failed")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._error(405, "method_not_allowed")

        def do_PUT(self) -> None:  # noqa: N802
            self._error(405, "method_not_allowed")

        def do_DELETE(self) -> None:  # noqa: N802
            self._error(405, "method_not_allowed")

        def _target(self):
            if not isinstance(self.path, str) or len(self.path) > 2048 or not self.path.startswith("/"):
                raise AgentServiceError(400, "invalid_request")
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise AgentServiceError(400, "invalid_request")
            return parsed

        def _validate_request_shape(self, method: str, path: str) -> None:
            host_values = self.headers.get_all("Host") or []
            expected_host = getattr(self.server, "expected_host", None)
            if len(host_values) != 1 or host_values[0] != expected_host:
                raise AgentServiceError(400, "invalid_request")
            if self.headers.get_all("Origin") or self.headers.get_all("Transfer-Encoding"):
                raise AgentServiceError(400, "invalid_request")
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) > 1:
                raise AgentServiceError(400, "invalid_request")
            content_type = self.headers.get_all("Content-Type") or []
            if len(content_type) > 1:
                raise AgentServiceError(400, "invalid_request")
            if method == "GET":
                if lengths not in ([], ["0"]) or content_type:
                    raise AgentServiceError(400, "invalid_request")
                return
            json_path = path in {"/internal/agents/select", "/v1/chat/completions"}
            if json_path:
                if content_type != ["application/json"]:
                    raise AgentServiceError(400, "invalid_request")
            elif content_type not in ([], ["application/json"]):
                raise AgentServiceError(400, "invalid_request")

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
            body = self.rfile.read(length)
            if len(body) != length:
                raise AgentServiceError(400, "invalid_request")
            return body

        def _authorize(self, method: str, target: str, body: bytes) -> None:
            # Header values are copied only for immediate verification; neither this handler nor
            # the service stores or logs them.
            headers = {
                key.lower(): tuple(self.headers.get_all(key) or ())
                for key in self.headers.keys()
            }
            authorize_request(method, target, headers, body)
            self._authorization_complete()

        def _record_auth_rejection(self) -> None:
            self._record_security_event("auth_rejected")

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

        def _is_trusted_client(self) -> bool:
            checker = getattr(self.server, "is_trusted_client", None)
            return bool(checker and checker(self.request, self.client_address))

    return Handler
