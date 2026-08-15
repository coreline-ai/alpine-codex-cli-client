"""Private-carrier HTTP/SSE adapter for the official Codex app-server protocol.

This module intentionally exposes a very small API surface.  It never accepts credentials,
does not load provider configuration, and owns exactly one active device login and one active
turn.  Codex itself owns authentication material in the guest ``HOME`` directory.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import socket
import socketserver
import stat
import struct
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterator, Optional, Tuple
from urllib.parse import urlsplit

from codex_gateway.app_server.process import AppServerSupervisor
from codex_gateway.app_server.protocol import CodexAppServerProtocol, DeviceCodeLoginStart
from codex_gateway.security import BoundedSecurityCounters


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
FIXED_HTTP_HOST = f"{LOOPBACK_HOST}:{DEFAULT_PORT}"
# Linux sockaddr_un.sun_path is 108 bytes including the trailing NUL.
MAX_UNIX_SOCKET_PATH_BYTES = 107
MAX_REQUEST_BYTES = 32 * 1024
MAX_REQUEST_LINE_BYTES = 4 * 1024
MAX_REQUEST_HEADER_BYTES = 16 * 1024
MAX_CONCURRENT_GATEWAY_CONNECTIONS = 8
GATEWAY_ACCEPT_BACKLOG = 8
GATEWAY_IO_TIMEOUT_SECONDS = 5.0
GATEWAY_PRE_AUTH_DEADLINE_SECONDS = 5.0
MAX_MESSAGE_BYTES = 16 * 1024
MAX_SSE_EVENT_BYTES = 24 * 1024
MAX_MODELS = 64
MAX_MODEL_PAGES = 8
MAX_CONVERSATIONS = 64
MAX_LOGINS = 16
LOGIN_TTL_SECONDS = 10 * 60
LOGIN_POLL_INTERVAL_SECONDS = 2
DEVICE_CODE_ALLOWED_HOSTS = frozenset({"auth.openai.com", "chatgpt.com"})
MODEL_CACHE_SECONDS = 60.0
TURN_TIMEOUT_SECONDS = 120.0
TRANSPORT_SECURITY_COUNTERS = (
    "peer_rejected",
    "capacity_rejected",
    "preauth_timeout",
    "header_rejected",
    "auth_rejected",
)


class _HeaderBudgetReader:
    """Expose only ``readline`` while enforcing one total HTTP header budget."""

    def __init__(self, source, maximum_bytes: int) -> None:
        self._source = source
        self._remaining = maximum_bytes
        self.exceeded = False

    def readline(self, limit: int = -1) -> bytes:
        allowed = self._remaining + 1
        if limit >= 0:
            allowed = min(allowed, limit)
        value = self._source.readline(allowed)
        if len(value) > self._remaining:
            self.exceeded = True
            raise http.client.LineTooLong("gateway header budget")
        self._remaining -= len(value)
        return value


class BoundedGatewayRequestHandler(BaseHTTPRequestHandler):
    """One-request HTTP handler with bounded pre-auth bytes and absolute deadline."""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(GATEWAY_IO_TIMEOUT_SECONDS)
        self._preauth_lock = threading.Lock()
        self._authorization_finished = False
        self._preauth_timer = threading.Timer(
            GATEWAY_PRE_AUTH_DEADLINE_SECONDS,
            self._expire_pre_auth,
        )
        self._preauth_timer.daemon = True
        self._preauth_timer.start()

    def finish(self) -> None:
        self._cancel_pre_auth_timer()
        super().finish()

    def handle_one_request(self) -> None:
        """Parse one request only; reject oversized request lines/aggregate headers."""

        try:
            self.raw_requestline = self.rfile.readline(MAX_REQUEST_LINE_BYTES + 1)
            if len(self.raw_requestline) > MAX_REQUEST_LINE_BYTES:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.close_connection = True
                self._record_security_event("header_rejected")
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            original = self.rfile
            bounded = _HeaderBudgetReader(original, MAX_REQUEST_HEADER_BYTES)
            self.rfile = bounded
            try:
                parsed = self.parse_request()
            finally:
                self.rfile = original
            if bounded.exceeded:
                self._record_security_event("header_rejected")
            if not parsed:
                self.close_connection = True
                return
            self.close_connection = True
            method_name = "do_" + self.command
            if not hasattr(self, method_name):
                self.send_error(501, "Unsupported method")
                return
            getattr(self, method_name)()
            self.wfile.flush()
        except (TimeoutError, socket.timeout):
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _authorization_complete(self) -> None:
        with self._preauth_lock:
            if self._authorization_finished:
                raise TimeoutError("gateway pre-auth deadline")
            self._authorization_finished = True
            self._preauth_timer.cancel()

    def _expire_pre_auth(self) -> None:
        with self._preauth_lock:
            if self._authorization_finished:
                return
            self._authorization_finished = True
        self._record_security_event("preauth_timeout")
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _cancel_pre_auth_timer(self) -> None:
        timer = getattr(self, "_preauth_timer", None)
        if timer is None:
            return
        with self._preauth_lock:
            self._authorization_finished = True
            timer.cancel()

    def _record_security_event(self, name: str) -> None:
        recorder = getattr(self.server, "record_security_event", None)
        if callable(recorder):
            recorder(name)


class GatewayError(Exception):
    """A stable public failure without child-process output or account detail."""

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChatRequest:
    request_id: str
    conversation_id: str
    resume_existing: bool
    model: str
    text: str


@dataclass
class LoginRecord:
    login_id: str
    state: str
    expires_at: float


@dataclass(frozen=True)
class StreamEvent:
    type: str
    value: Dict[str, Any]


@dataclass
class ActiveTurn:
    request_id: str
    conversation_id: str
    thread_id: str
    model: str
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))
    events: Deque[StreamEvent] = field(default_factory=lambda: deque(maxlen=128))
    turn_id: Optional[str] = None
    terminal: bool = False
    interrupt_requested: bool = False
    interrupt_sent: bool = False


class CodexGatewayService:
    """Maps fixed loopback routes to a single initialized app-server session."""

    def __init__(
        self,
        protocol: CodexAppServerProtocol,
        workspace_directory: str,
        runtime_ready: Optional[Callable[[], bool]] = None,
        conversation_store_path: Optional[str] = None,
    ) -> None:
        if not workspace_directory.startswith("/"):
            raise ValueError("workspace directory must be absolute")
        self._protocol = protocol
        self._workspace_directory = workspace_directory
        self._runtime_ready = runtime_ready or (lambda: True)
        if conversation_store_path is not None and not conversation_store_path.startswith("/"):
            raise ValueError("conversation store path must be absolute")
        self._conversation_store_path = conversation_store_path
        self._lock = threading.RLock()
        self._threads = self._load_thread_bindings()
        self._logins: "OrderedDict[str, LoginRecord]" = OrderedDict()
        self._active_login_id: Optional[str] = None
        self._active_turn: Optional[ActiveTurn] = None
        self._turn_reservation = False
        self._model_cache: Tuple[Dict[str, Any], ...] = ()
        self._model_cache_expires = 0.0
        self._remove_listener = protocol.add_notification_listener(self._on_notification)

    def close(self) -> None:
        self._remove_listener()
        with self._lock:
            active = self._active_turn
        if active is not None:
            self.interrupt(active.request_id)

    def backend_ready(self) -> bool:
        """Returns protocol readiness without conflating it with Gateway process readiness."""
        return self._protocol.is_ready

    def activity_snapshot(self) -> Tuple[bool, bool]:
        """Returns only busy booleans; no login, thread, turn, or account identifier escapes."""
        with self._lock:
            self._expire_login_locked(time.monotonic())
            return (
                self._active_login_id is not None,
                self._active_turn is not None or self._turn_reservation,
            )

    def health(self) -> Dict[str, str]:
        if not self._runtime_ready():
            raise GatewayError(503, "runtime_not_ready")
        if not self._protocol.is_ready:
            raise GatewayError(503, "codex_not_ready")
        self._account_state()
        return {"runtime": "ready", "gateway": "ready", "codex": "ready"}

    def account(self) -> Dict[str, bool]:
        state = self._account_state()
        return {
            "authenticated": state.authenticated,
            "requires_openai_auth": state.requires_openai_auth,
        }

    def start_device_login(self) -> Dict[str, Any]:
        if self._account_state().authenticated:
            raise GatewayError(409, "already_authenticated")
        with self._lock:
            self._expire_login_locked(time.monotonic())
            if self._active_login_id is not None:
                raise GatewayError(409, "login_already_active")
        result = self._call(self._protocol.start_device_code_login)
        if not isinstance(result, DeviceCodeLoginStart):
            raise GatewayError(502, "codex_protocol_invalid")
        if not self._is_safe_device_challenge(result.verification_url, result.user_code):
            raise GatewayError(502, "codex_protocol_invalid")
        record = LoginRecord(
            login_id=result.login_id,
            state="pending",
            expires_at=time.monotonic() + LOGIN_TTL_SECONDS,
        )
        with self._lock:
            self._expire_login_locked(time.monotonic())
            if self._active_login_id is not None:
                # A concurrent request won the single-login slot. Cancel this just-created login
                # once; this is cleanup, not a retry or another login attempt.
                self._call(self._protocol.cancel_login, result.login_id)
                raise GatewayError(409, "login_already_active")
            self._remember_login_locked(record)
            self._active_login_id = result.login_id
        # The verification information is returned only to the loopback caller and is never
        # retained in this service, diagnostics, files, or UI status strings.
        return {
            "login_id": result.login_id,
            "verification_url": result.verification_url,
            "user_code": result.user_code,
            "status": "pending",
            "expires_in_seconds": LOGIN_TTL_SECONDS,
            "poll_interval_seconds": LOGIN_POLL_INTERVAL_SECONDS,
        }

    def login_status(self, login_id: str) -> Dict[str, str]:
        self._opaque_id(login_id)
        with self._lock:
            self._expire_login_locked(time.monotonic())
            record = self._logins.get(login_id)
            if record is None:
                raise GatewayError(404, "login_not_found")
            return {"login_id": record.login_id, "status": record.state}

    def cancel_login(self, login_id: str) -> Dict[str, str]:
        self._opaque_id(login_id)
        with self._lock:
            self._expire_login_locked(time.monotonic())
            record = self._logins.get(login_id)
            if record is None:
                raise GatewayError(404, "login_not_found")
            if record.state != "pending" or self._active_login_id != login_id:
                raise GatewayError(409, "login_not_active")
        self._call(self._protocol.cancel_login, login_id)
        with self._lock:
            record.state = "cancelled"
            if self._active_login_id == login_id:
                self._active_login_id = None
        return {"login_id": login_id, "status": "cancelled"}

    def cancel_active_login(self) -> Dict[str, str]:
        """Cancels a process-recovered pending login without revealing its opaque ID or code."""
        with self._lock:
            self._expire_login_locked(time.monotonic())
            login_id = self._active_login_id
        if login_id is None:
            raise GatewayError(409, "no_active_login")
        self.cancel_login(login_id)
        return {"status": "cancelled"}

    def logout(self) -> Dict[str, str]:
        with self._lock:
            if self._active_turn is not None or self._turn_reservation:
                raise GatewayError(409, "turn_active")
        self._call(self._protocol.logout)
        with self._lock:
            self._active_login_id = None
            self._logins.clear()
            self._model_cache = ()
            self._model_cache_expires = 0.0
            self._threads.clear()
            self._persist_thread_bindings_locked()
        return {"status": "logged_out"}

    def models(self) -> Dict[str, Any]:
        self._require_authenticated()
        now = time.monotonic()
        with self._lock:
            if now < self._model_cache_expires:
                return {"object": "list", "data": [dict(item) for item in self._model_cache]}
        models = self._fetch_models()
        with self._lock:
            self._model_cache = tuple(models)
            self._model_cache_expires = time.monotonic() + MODEL_CACHE_SECONDS
        return {"object": "list", "data": [dict(item) for item in models]}

    def start_chat(self, value: Dict[str, Any]) -> ActiveTurn:
        request = self._parse_chat_request(value)
        self._require_authenticated()
        model_ids = {item["id"] for item in self.models()["data"]}
        if request.model not in model_ids:
            raise GatewayError(400, "model_not_available")
        with self._lock:
            if self._active_turn is not None or self._turn_reservation:
                raise GatewayError(409, "turn_already_active")
            self._turn_reservation = True
        try:
            thread_id = self._resolve_thread(
                request.conversation_id,
                request.model,
                request.resume_existing,
            )
            active = ActiveTurn(
                request_id=request.request_id,
                conversation_id=request.conversation_id,
                thread_id=thread_id,
                model=request.model,
            )
            with self._lock:
                if self._active_turn is not None:
                    raise GatewayError(409, "turn_already_active")
                self._active_turn = active
            response = self._call(
                self._protocol.turn_start,
                {
                    "threadId": thread_id,
                    "model": request.model,
                    "input": [{"type": "text", "text": request.text}],
                },
            )
            turn_id = self._turn_id_from_response(response)
            with active.condition:
                active.turn_id = turn_id
                active.condition.notify_all()
            self._send_interrupt_if_requested(active)
            return active
        except GatewayError:
            self._release_active(request.request_id)
            raise
        except Exception as error:
            self._release_active(request.request_id)
            raise GatewayError(502, "codex_request_failed") from error
        finally:
            with self._lock:
                self._turn_reservation = False

    def stream(self, active: ActiveTurn) -> Iterator[StreamEvent]:
        completed_normally = False
        try:
            yield StreamEvent(
                "start",
                {
                    "id": active.request_id,
                    "type": "start",
                    "model": active.model,
                    "conversation_id": active.conversation_id,
                },
            )
            deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
            while True:
                event = self._next_stream_event(active, deadline)
                if event is None:
                    self._terminate(active, "turn_timeout", request_interrupt=True)
                    continue
                yield event
                if event.type == "done":
                    completed_normally = True
                    return
                if event.type == "error":
                    return
        finally:
            with active.condition:
                needs_cleanup = not completed_normally and (
                    not active.terminal or active.interrupt_requested
                )
            if needs_cleanup:
                try:
                    self.interrupt(active.request_id)
                except GatewayError:
                    pass
            self._release_active(active.request_id)

    def interrupt(self, request_id: str) -> Dict[str, str]:
        self._opaque_id(request_id)
        with self._lock:
            active = self._active_turn
            if active is None or active.request_id != request_id:
                raise GatewayError(404, "turn_not_found")
        with active.condition:
            active.interrupt_requested = True
        self._send_interrupt_if_requested(active)
        return {"id": request_id, "status": "interrupt_requested"}

    def _next_stream_event(self, active: ActiveTurn, deadline: float) -> Optional[StreamEvent]:
        with active.condition:
            while not active.events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if not self._protocol.is_ready:
                    self._terminal_event_locked(active, "codex_process_lost")
                    break
                active.condition.wait(min(remaining, 0.5))
            return active.events.popleft() if active.events else None

    def _resolve_thread(self, conversation_id: str, model: str, resume_existing: bool) -> str:
        with self._lock:
            existing = self._threads.get(conversation_id)
        if existing is not None:
            response = self._call(
                self._protocol.thread_resume,
                {
                    "threadId": existing,
                    "cwd": self._workspace_directory,
                    "model": model,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                },
            )
            thread_id = self._thread_id_from_response(response)
            if thread_id != existing:
                raise GatewayError(502, "codex_protocol_invalid")
            with self._lock:
                self._threads.move_to_end(conversation_id)
                self._persist_thread_bindings_locked()
            return existing
        if resume_existing:
            # The Android side may retain an opaque conversation ID after a process death, but a
            # missing gateway-owned binding must never create a replacement thread or replay text.
            raise GatewayError(409, "conversation_binding_not_found")
        response = self._call(
            self._protocol.thread_start,
            {
                "cwd": self._workspace_directory,
                "model": model,
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": False,
            },
        )
        thread_id = self._thread_id_from_response(response)
        with self._lock:
            self._threads[conversation_id] = thread_id
            self._threads.move_to_end(conversation_id)
            while len(self._threads) > MAX_CONVERSATIONS:
                self._threads.popitem(last=False)
            self._persist_thread_bindings_locked()
        return thread_id

    def _fetch_models(self) -> list[Dict[str, Any]]:
        cursor: Optional[str] = None
        cursors = set()
        models: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        for _ in range(MAX_MODEL_PAGES):
            response = self._call(self._protocol.model_list, cursor)
            raw_models = response.get("data")
            if not isinstance(raw_models, list):
                raise GatewayError(502, "codex_protocol_invalid")
            for raw_model in raw_models:
                normalized = self._normalize_model(raw_model)
                if normalized is not None and normalized["id"] not in models:
                    models[normalized["id"]] = normalized
                    if len(models) >= MAX_MODELS:
                        return list(models.values())
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return list(models.values())
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
                raise GatewayError(502, "codex_protocol_invalid")
            cursors.add(next_cursor)
            cursor = next_cursor
        raise GatewayError(502, "model_page_limit")

    @staticmethod
    def _normalize_model(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict) or value.get("hidden") is True:
            return None
        identifier = value.get("model")
        display_name = value.get("displayName")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
            raise GatewayError(502, "codex_protocol_invalid")
        if not isinstance(display_name, str) or not display_name or len(display_name) > 256:
            display_name = identifier
        return {
            "id": identifier,
            "display_name": display_name,
            "is_default": value.get("isDefault") is True,
            "modalities": ["text"],
        }

    def _on_notification(self, method: str, params: Dict[str, Any]) -> None:
        if method == "account/login/completed":
            self._on_login_completed(params)
            return
        if method == "item/agentMessage/delta":
            self._on_agent_delta(params)
            return
        if method == "turn/completed":
            self._on_turn_completed(params)

    def _on_login_completed(self, params: Dict[str, Any]) -> None:
        success = params.get("success")
        login_id = params.get("loginId")
        if not isinstance(success, bool):
            return
        with self._lock:
            self._expire_login_locked(time.monotonic())
            active_id = self._active_login_id
            if active_id is None:
                return
            if login_id is not None and login_id != active_id:
                return
            record = self._logins.get(active_id)
            if record is None or record.state != "pending":
                return
            record.state = "completed" if success else "failed"
            self._active_login_id = None

    def _on_agent_delta(self, params: Dict[str, Any]) -> None:
        active = self._matching_active(params)
        if active is None:
            return
        delta = params.get("delta")
        turn_id = params.get("turnId")
        item_id = params.get("itemId")
        if (
            not isinstance(delta, str)
            or len(delta.encode("utf-8")) > MAX_SSE_EVENT_BYTES
            or not isinstance(turn_id, str)
            or not isinstance(item_id, str)
        ):
            self._terminate(active, "codex_notification_invalid", request_interrupt=True)
            return
        with active.condition:
            if active.turn_id is not None and active.turn_id != turn_id:
                active.interrupt_requested = True
                self._terminal_event_locked(active, "codex_notification_invalid")
                self._schedule_interrupt(active)
                return
            if active.terminal or len(active.events) == active.events.maxlen:
                if not active.terminal:
                    active.interrupt_requested = True
                    self._terminal_event_locked(active, "stream_event_limit")
                    self._schedule_interrupt(active)
                return
            active.events.append(StreamEvent("delta", {"id": active.request_id, "type": "delta", "text": delta}))
            active.condition.notify_all()

    def _on_turn_completed(self, params: Dict[str, Any]) -> None:
        active = self._matching_active(params)
        if active is None:
            return
        turn = params.get("turn")
        if not isinstance(turn, dict):
            self._terminate(active, "codex_notification_invalid", request_interrupt=True)
            return
        turn_id = turn.get("id")
        status = turn.get("status")
        if not isinstance(turn_id, str) or status not in {"completed", "interrupted", "failed"}:
            self._terminate(active, "codex_notification_invalid", request_interrupt=True)
            return
        with active.condition:
            if active.turn_id is not None and active.turn_id != turn_id:
                active.interrupt_requested = True
                self._terminal_event_locked(active, "codex_notification_invalid")
                self._schedule_interrupt(active)
                return
            if active.terminal:
                return
            active.terminal = True
            if status == "completed":
                active.events.append(StreamEvent("done", {"id": active.request_id, "type": "done"}))
            else:
                code = "turn_interrupted" if status == "interrupted" else "turn_failed"
                active.events.append(StreamEvent("error", self._error_event(active.request_id, code)))
            active.condition.notify_all()

    def _matching_active(self, params: Dict[str, Any]) -> Optional[ActiveTurn]:
        thread_id = params.get("threadId")
        with self._lock:
            active = self._active_turn
        if active is None:
            return None
        if not isinstance(thread_id, str):
            self._terminate(active, "codex_notification_invalid", request_interrupt=True)
            return None
        return active if thread_id == active.thread_id else None

    def _terminate(self, active: ActiveTurn, code: str, *, request_interrupt: bool) -> None:
        with active.condition:
            if active.terminal:
                return
            if request_interrupt:
                active.interrupt_requested = True
            self._terminal_event_locked(active, code)
        if request_interrupt:
            self._schedule_interrupt(active)

    def _terminal_event_locked(self, active: ActiveTurn, code: str) -> None:
        if active.terminal:
            return
        active.terminal = True
        active.events.append(StreamEvent("error", self._error_event(active.request_id, code)))
        active.condition.notify_all()

    @staticmethod
    def _error_event(request_id: str, code: str) -> Dict[str, Any]:
        return {"id": request_id, "type": "error", "code": code, "retryable": False}

    def _schedule_interrupt(self, active: ActiveTurn) -> None:
        thread = threading.Thread(
            target=self._interrupt_background,
            args=(active.request_id,),
            name="codex-turn-interrupt",
            daemon=True,
        )
        thread.start()

    def _interrupt_background(self, request_id: str) -> None:
        try:
            self.interrupt(request_id)
        except GatewayError:
            return

    def _send_interrupt_if_requested(self, active: ActiveTurn) -> None:
        with active.condition:
            if not active.interrupt_requested or active.interrupt_sent or active.turn_id is None:
                return
            active.interrupt_sent = True
            thread_id = active.thread_id
            turn_id = active.turn_id
        try:
            self._call(self._protocol.turn_interrupt, {"threadId": thread_id, "turnId": turn_id})
        except GatewayError:
            self._terminate(active, "interrupt_failed", request_interrupt=False)

    def _release_active(self, request_id: str) -> None:
        with self._lock:
            if self._active_turn is not None and self._active_turn.request_id == request_id:
                self._active_turn = None

    def _account_state(self):
        return self._call(self._protocol.account_read)

    def _require_authenticated(self) -> None:
        if not self._account_state().authenticated:
            raise GatewayError(401, "authentication_required")

    def _call(self, function: Callable[..., Any], *args: Any) -> Any:
        try:
            return function(*args)
        except GatewayError:
            raise
        except Exception as error:
            raise GatewayError(502, "codex_request_failed") from error

    @staticmethod
    def _thread_id_from_response(response: Any) -> str:
        if not isinstance(response, dict) or not isinstance(response.get("thread"), dict):
            raise GatewayError(502, "codex_protocol_invalid")
        thread_id = response["thread"].get("id")
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 4096:
            raise GatewayError(502, "codex_protocol_invalid")
        return thread_id

    @staticmethod
    def _turn_id_from_response(response: Any) -> str:
        if not isinstance(response, dict) or not isinstance(response.get("turn"), dict):
            raise GatewayError(502, "codex_protocol_invalid")
        turn_id = response["turn"].get("id")
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 4096:
            raise GatewayError(502, "codex_protocol_invalid")
        return turn_id

    @staticmethod
    def _opaque_id(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise GatewayError(400, "invalid_request")
        return value

    @staticmethod
    def _parse_chat_request(value: Any) -> ChatRequest:
        if not isinstance(value, dict) or value.get("stream") is not True:
            raise GatewayError(400, "invalid_request")
        model = value.get("model")
        if not isinstance(model, str) or not model or len(model) > 256:
            raise GatewayError(400, "invalid_request")
        messages = value.get("messages")
        if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
            raise GatewayError(400, "invalid_request")
        message = messages[0]
        text = message.get("content")
        if message.get("role") != "user" or not isinstance(text, str) or not text.strip():
            raise GatewayError(400, "invalid_request")
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise GatewayError(413, "request_too_large")
        conversation = value.get("conversation_id")
        generated_conversation = conversation is None
        if generated_conversation:
            conversation = "conversation_" + secrets.token_hex(12)
        if not isinstance(conversation, str) or not conversation or len(conversation) > 128:
            raise GatewayError(400, "invalid_request")
        resume_existing = value.get("resume_existing", False)
        if not isinstance(resume_existing, bool):
            raise GatewayError(400, "invalid_request")
        if generated_conversation and resume_existing:
            raise GatewayError(400, "invalid_request")
        request_id = "chat_" + secrets.token_hex(12)
        return ChatRequest(
            request_id=request_id,
            conversation_id=conversation,
            resume_existing=resume_existing,
            model=model,
            text=text,
        )

    def _expire_login_locked(self, now: float) -> None:
        active_id = self._active_login_id
        if active_id is None:
            return
        record = self._logins.get(active_id)
        if record is not None and record.state == "pending" and record.expires_at <= now:
            record.state = "expired"
            self._active_login_id = None

    def _remember_login_locked(self, record: LoginRecord) -> None:
        self._logins[record.login_id] = record
        self._logins.move_to_end(record.login_id)
        while len(self._logins) > MAX_LOGINS:
            old_id, _ = self._logins.popitem(last=False)
            if old_id == self._active_login_id:
                self._active_login_id = None

    def _load_thread_bindings(self) -> "OrderedDict[str, str]":
        path = self._conversation_store_path
        if path is None:
            return OrderedDict()
        try:
            with open(path, "r", encoding="utf-8") as source:
                raw = json.load(source)
        except (OSError, ValueError, TypeError):
            return OrderedDict()
        if isinstance(raw, list):
            # v1 was a bare Codex-only list. Missing agent_id migrates to Codex in memory.
            bindings = raw
        elif isinstance(raw, dict) and raw.get("schema_version") == 2:
            bindings = raw.get("bindings")
            if not isinstance(bindings, list):
                return OrderedDict()
        else:
            return OrderedDict()
        values: "OrderedDict[str, str]" = OrderedDict()
        for entry in bindings[-MAX_CONVERSATIONS:]:
            if not isinstance(entry, dict):
                continue
            if entry.get("agent_id", "codex") != "codex":
                continue
            conversation_id = entry.get("conversation_id")
            thread_id = entry.get("thread_id")
            if (
                isinstance(conversation_id, str)
                and 0 < len(conversation_id) <= 128
                and isinstance(thread_id, str)
                and 0 < len(thread_id) <= 4096
            ):
                values[conversation_id] = thread_id
        return values

    def _persist_thread_bindings_locked(self) -> None:
        path = self._conversation_store_path
        if path is None:
            return
        directory = os.path.dirname(path)
        temporary = path + ".tmp-" + secrets.token_hex(8)
        payload = {
            "schema_version": 2,
            "bindings": [
                {
                    "agent_id": "codex",
                    "conversation_id": conversation_id,
                    "thread_id": thread_id,
                }
                for conversation_id, thread_id in self._threads.items()
            ],
        }
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as destination:
                os.chmod(temporary, 0o600)
                json.dump(payload, destination, separators=(",", ":"))
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, path)
        except OSError as error:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise GatewayError(503, "conversation_store_failed") from error

    @staticmethod
    def _is_safe_device_challenge(verification_url: Any, user_code: Any) -> bool:
        if not isinstance(verification_url, str) or not isinstance(user_code, str):
            return False
        if not (0 < len(verification_url) <= 2048 and 0 < len(user_code) <= 64):
            return False
        if not all(character.isalnum() or character in "-_" for character in user_code):
            return False
        parsed = urlsplit(verification_url)
        try:
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "https"
            and parsed.hostname in DEVICE_CODE_ALLOWED_HOSTS
            and parsed.username is None
            and parsed.password is None
            and port in (None, 443)
            and bool(parsed.path)
        )


def make_handler(service: CodexGatewayService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AlpineCodexGateway/0.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            # Request paths, login codes, prompts, and assistant text must never reach logs.
            return

        def do_GET(self) -> None:  # noqa: N802
            if not self._is_loopback_client():
                self._error(403, "loopback_required")
                return
            try:
                if self.path == "/healthz":
                    self._json(200, service.health())
                elif self.path == "/internal/codex/account":
                    self._json(200, service.account())
                elif self.path == "/v1/models":
                    self._json(200, service.models())
                elif self.path.startswith("/internal/codex/login/") and self.path.count("/") == 4:
                    self._json(200, service.login_status(self.path.rsplit("/", 1)[1]))
                else:
                    self._error(404, "not_found")
            except GatewayError as error:
                self._error(error.status, error.code)

        def do_POST(self) -> None:  # noqa: N802
            if not self._is_loopback_client():
                self._error(403, "loopback_required")
                return
            try:
                if self.path == "/internal/codex/login/device":
                    self._require_empty_body()
                    self._json(200, service.start_device_login())
                elif self.path == "/internal/codex/login/active/cancel":
                    self._require_empty_body()
                    self._json(200, service.cancel_active_login())
                elif self.path == "/internal/codex/logout":
                    self._require_empty_body()
                    self._json(200, service.logout())
                elif self.path.startswith("/internal/codex/login/") and self.path.endswith("/cancel"):
                    login_id = self.path[len("/internal/codex/login/"):-len("/cancel")]
                    if not login_id or "/" in login_id:
                        raise GatewayError(404, "not_found")
                    self._require_empty_body()
                    self._json(200, service.cancel_login(login_id))
                elif self.path.startswith("/internal/codex/turn/") and self.path.endswith("/interrupt"):
                    request_id = self.path[len("/internal/codex/turn/"):-len("/interrupt")]
                    if not request_id or "/" in request_id:
                        raise GatewayError(404, "not_found")
                    self._require_empty_body()
                    self._json(200, service.interrupt(request_id))
                elif self.path == "/v1/chat/completions":
                    active = service.start_chat(self._request_object())
                    self._stream(service, active)
                else:
                    self._error(404, "not_found")
            except GatewayError as error:
                self._error(error.status, error.code)

        def _request_object(self) -> Dict[str, Any]:
            raw = self._read_body(required=True)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GatewayError(400, "invalid_request") from error
            if not isinstance(value, dict) or _json_depth(value) > 12:
                raise GatewayError(400, "invalid_request")
            return value

        def _require_empty_body(self) -> None:
            if self._read_body(required=False):
                raise GatewayError(400, "invalid_request")

        def _read_body(self, *, required: bool) -> bytes:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                if required:
                    raise GatewayError(400, "invalid_request")
                return b""
            try:
                length = int(raw_length)
            except ValueError as error:
                raise GatewayError(400, "invalid_request") from error
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise GatewayError(413, "request_too_large")
            if required and length == 0:
                raise GatewayError(400, "invalid_request")
            return self.rfile.read(length)

        def _stream(self, gateway: CodexGatewayService, active: ActiveTurn) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in gateway.stream(active):
                    self._event(event.value)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                try:
                    gateway.interrupt(active.request_id)
                except GatewayError:
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
            checker = getattr(self.server, "is_trusted_client", None)
            return bool(checker and checker(self.request, self.client_address))

    return Handler


class LoopbackGatewayServer(ThreadingHTTPServer):
    """A loopback-only server that can reclaim a closed port after process recreation."""

    daemon_threads = True
    # SO_REUSEADDR permits a new app-private Gateway to reclaim 127.0.0.1:8787 while connections
    # from the force-stopped process are still in TIME_WAIT. It does not enable SO_REUSEPORT, so a
    # second live listener still fails closed with EADDRINUSE.
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        if address[0] != LOOPBACK_HOST:
            raise ValueError("gateway must bind to loopback")
        super().__init__(address, handler)
        self.expected_host = f"{LOOPBACK_HOST}:{self.server_address[1]}"

    @staticmethod
    def is_trusted_client(_request, client_address) -> bool:
        return bool(client_address and client_address[0] == LOOPBACK_HOST)


class PrivateUnixGatewayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """App-private UDS server with fail-closed Linux peer-UID authentication."""

    daemon_threads = True
    request_queue_size = GATEWAY_ACCEPT_BACKLOG

    def __init__(
        self,
        path: str,
        handler: type[BaseHTTPRequestHandler],
        expected_peer_uid: int,
        peer_uid_reader: Optional[Callable[[socket.socket], int]] = None,
        max_connections: int = MAX_CONCURRENT_GATEWAY_CONNECTIONS,
    ) -> None:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or len(os.fsencode(path)) > MAX_UNIX_SOCKET_PATH_BYTES
            or expected_peer_uid < 0
            or not isinstance(max_connections, int)
            or isinstance(max_connections, bool)
            or max_connections <= 0
            or max_connections > MAX_CONCURRENT_GATEWAY_CONNECTIONS
        ):
            raise ValueError("invalid private gateway socket")
        parent = os.path.dirname(path)
        parent_stat = os.lstat(parent)
        if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_IMODE(parent_stat.st_mode) & 0o077:
            raise PermissionError("private gateway parent permissions")
        self._socket_path = path
        self._expected_peer_uid = expected_peer_uid
        self._peer_uid_reader = peer_uid_reader or _linux_peer_uid
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._connection_state_lock = threading.Lock()
        self._active_connections = 0
        self._maximum_active_connections = 0
        self._security_counters = BoundedSecurityCounters(TRANSPORT_SECURITY_COUNTERS)
        self.expected_host = FIXED_HTTP_HOST
        self.server_name = "localhost"
        self.server_port = 0
        self._remove_stale_socket()
        try:
            super().__init__(path, handler)
            # Record the inode as soon as UnixStreamServer binds it so constructor failures can
            # unlink only the socket created by this server, never a replacement at the path.
            created = os.lstat(path)
            self._bound_identity = (created.st_dev, created.st_ino)
            # Alpine's Python may not expose chmod(follow_symlinks=False) even though Linux
            # chmod itself never follows a Unix-socket inode. The private 0700 parent plus the
            # inode checks before and after chmod keep this portable fallback fail closed.
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
            except (NotImplementedError, TypeError):
                os.chmod(path, 0o600)
            bound = os.lstat(path)
            if not stat.S_ISSOCK(bound.st_mode) or stat.S_IMODE(bound.st_mode) != 0o600:
                raise PermissionError("private gateway socket permissions")
            if (bound.st_dev, bound.st_ino) != self._bound_identity:
                raise PermissionError("private gateway socket replaced")
        except Exception:
            self._unlink_owned_socket()
            raise

    def is_trusted_client(self, request, _client_address) -> bool:
        try:
            return self._peer_uid_reader(request) == self._expected_peer_uid
        except Exception:
            return False

    def verify_request(self, request, client_address) -> bool:
        trusted = self.is_trusted_client(request, client_address)
        if not trusted:
            self.record_security_event("peer_rejected")
        return trusted

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(GATEWAY_IO_TIMEOUT_SECONDS)
        return request, address

    def process_request(self, request, client_address) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self.record_security_event("capacity_rejected")
            self.shutdown_request(request)
            return
        with self._connection_state_lock:
            self._active_connections += 1
            self._maximum_active_connections = max(
                self._maximum_active_connections,
                self._active_connections,
            )
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release_connection_slot()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_connection_slot()

    def _release_connection_slot(self) -> None:
        with self._connection_state_lock:
            self._active_connections -= 1
        self._connection_slots.release()

    def record_security_event(self, name: str) -> None:
        self._security_counters.increment(name)

    def security_snapshot(self) -> Dict[str, int]:
        snapshot = dict(self._security_counters.snapshot())
        with self._connection_state_lock:
            snapshot["active_connections"] = self._active_connections
            snapshot["max_active_connections"] = self._maximum_active_connections
        return snapshot

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._unlink_owned_socket()

    def _remove_stale_socket(self) -> None:
        try:
            current = os.lstat(self._socket_path)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(current.st_mode) or current.st_uid != os.getuid():
            raise PermissionError("unsafe stale gateway socket")
        os.unlink(self._socket_path)

    def _unlink_owned_socket(self) -> None:
        identity = getattr(self, "_bound_identity", None)
        if identity is None:
            return
        try:
            current = os.lstat(self._socket_path)
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == identity:
            os.unlink(self._socket_path)


def _linux_peer_uid(connection: socket.socket) -> int:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise OSError("peer credentials unavailable")
    value = connection.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", value)
    return uid


def _json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0
