"""Official Grok ACP implementation of the closed Agent adapter contract."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
import secrets
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterator, Optional, Protocol, Tuple
from urllib.parse import urlsplit

from codex_gateway.agents.contracts import (
    AgentAccount,
    AgentActivity,
    AgentCapabilities,
    AgentConversationBinding,
    AgentId,
    AgentLogin,
    AgentModel,
    AgentTurnEvent,
    AgentTurnHandle,
)
from codex_gateway.grok_acp.contract import AUTH_METHOD_ID, parse_model_catalog
from codex_gateway.grok_acp.process import GrokAcpSupervisor, GrokSupervisorState
from codex_gateway.grok_acp.rpc import AcpNotification


GROK_LOGIN_TTL_SECONDS = 10 * 60
MAX_LOGIN_RECORDS = 16
MAX_CONVERSATIONS = 64
MAX_MESSAGE_BYTES = 16 * 1024
MAX_STREAM_TEXT_BYTES = 24 * 1024
MAX_STREAM_TOTAL_BYTES = 256 * 1024
MAX_STREAM_EVENTS = 128
MAX_RETRY_ATTEMPTS = 32
MAX_COMPLETED_TURN_IDS = 32
GROK_AUTH_ALLOWED_HOSTS = frozenset({"auth.x.ai"})
TERMINAL_LOGIN_STATES = frozenset({"authenticated", "failed", "cancelled", "expired"})


class GrokAdapterError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GrokRetryPolicy(str, Enum):
    """Build-owned policy; it is never configurable through Android or HTTP."""

    ALLOW_PRE_OUTPUT = "allow_pre_output"
    STRICT = "strict"


@dataclass(frozen=True)
class GrokTurnMetrics:
    """Content-free counters for the most recently completed Grok turn."""

    prompt_dispatch_count: int
    visible_delta_count: int
    terminal_count: int
    cancel_dispatch_count: int
    retry_classification: str
    retry_attempts: int
    retry_max: int


class _GrokSupervisor(Protocol):
    @property
    def state(self) -> Any: ...

    @property
    def generation(self) -> int: ...

    @property
    def initialize_state(self) -> Any: ...

    def start(self) -> Any: ...

    def stop(self, timeout_seconds: float = 5.0) -> None: ...

    def add_notification_listener(
        self, listener: Callable[[AcpNotification], None]
    ) -> Callable[[], None]: ...

    def authenticate(self, request_sequence: int) -> Dict[str, Any]: ...

    def get_auth_url(self) -> Dict[str, Any]: ...

    def cancel_auth(self, request_sequence: int) -> Dict[str, Any]: ...

    def auth_info(self) -> Dict[str, Any]: ...

    def logout(self) -> Dict[str, Any]: ...

    def list_models(self) -> Dict[str, Any]: ...

    def new_session(self, working_directory: str, model_id: Optional[str] = None) -> Dict[str, Any]: ...

    def load_session(self, session_id: str, working_directory: str) -> Dict[str, Any]: ...

    def resume_session(self, session_id: str, working_directory: str) -> Dict[str, Any]: ...

    def set_session_model(self, session_id: str, model_id: str) -> Dict[str, Any]: ...

    def prompt(self, session_id: str, text: str) -> Dict[str, Any]: ...

    def cancel_session(self, session_id: str) -> None: ...

    def close_session(self, session_id: str) -> Dict[str, Any]: ...


@dataclass
class _LoginAttempt:
    request_id: str
    sequence: int
    state: str
    expires_at: float
    cancel_sent: bool = False


@dataclass
class _GrokActiveTurn:
    request_id: str
    conversation_id: str
    session_id: str
    model_id: str
    generation: int
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock())
    )
    events: Deque[AgentTurnEvent] = field(default_factory=lambda: deque(maxlen=MAX_STREAM_EVENTS))
    terminal: bool = False
    cancel_sent: bool = False
    prompt_dispatch_count: int = 0
    visible_delta_count: int = 0
    terminal_count: int = 0
    cancel_dispatch_count: int = 0
    total_output_bytes: int = 0
    retry_classification: str = "none"
    retry_attempts: int = 0
    retry_max: int = 0
    last_notification_sequence: int = 0
    prompt_id: Optional[str] = None


class GrokAgentAdapter:
    """Owns Grok login/model/session state without reading CLI-owned credentials."""

    agent_id = AgentId.GROK
    capabilities = AgentCapabilities(
        device_oauth=True,
        dynamic_models=True,
        streaming=True,
        stop=True,
        resume=True,
    )

    def __init__(
        self,
        workspace_directory: str,
        supervisor: Optional[_GrokSupervisor] = None,
        restored_bindings: Tuple[AgentConversationBinding, ...] = (),
        now: Callable[[], float] = time.monotonic,
        retry_policy: GrokRetryPolicy = GrokRetryPolicy.ALLOW_PRE_OUTPUT,
    ) -> None:
        if not isinstance(workspace_directory, str) or not workspace_directory.startswith("/"):
            raise ValueError("Grok workspace directory must be absolute")
        self._workspace_directory = workspace_directory
        if not isinstance(retry_policy, GrokRetryPolicy):
            raise ValueError("invalid Grok retry policy")
        self._supervisor: _GrokSupervisor = supervisor or GrokAcpSupervisor()
        self._now = now
        self._retry_policy = retry_policy
        self._lock = threading.RLock()
        self._login_sequence = 0
        self._logins: "OrderedDict[str, _LoginAttempt]" = OrderedDict()
        self._active_login_id: Optional[str] = None
        self._active_turn: Optional[_GrokActiveTurn] = None
        self._completed_turn_ids: Deque[str] = deque(maxlen=MAX_COMPLETED_TURN_IDS)
        self._completed_turn_id_set: set[str] = set()
        self._last_turn_metrics: Optional[GrokTurnMetrics] = None
        self._models: Tuple[AgentModel, ...] = ()
        self._model_ids: set[str] = set()
        self._bindings: "OrderedDict[str, AgentConversationBinding]" = OrderedDict()
        self._remove_listener: Optional[Callable[[], None]] = None
        for binding in restored_bindings:
            self._validate_binding(binding)
            if binding.conversation_id in self._bindings:
                raise ValueError("duplicate Grok conversation binding")
            self._bindings[binding.conversation_id] = binding

    def is_ready(self) -> bool:
        return getattr(self._supervisor.state, "value", self._supervisor.state) == "READY"

    def activity(self) -> AgentActivity:
        cancel_sequence = None
        with self._lock:
            cancel_sequence = self._expire_login_locked()
            value = AgentActivity(
                active_login=self._active_login_id is not None,
                active_turn=self._active_turn is not None,
            )
        self._cancel_expired(cancel_sequence)
        return value

    def activate(self) -> None:
        state = getattr(self._supervisor.state, "value", self._supervisor.state)
        if state == "STOPPED":
            self._supervisor.start()
        elif state != "READY":
            raise GrokAdapterError("grok_not_ready")
        with self._lock:
            if self._remove_listener is None:
                self._remove_listener = self._supervisor.add_notification_listener(
                    self._on_notification
                )

    def deactivate(self) -> None:
        activity = self.activity()
        if activity.active_login or activity.active_turn:
            raise GrokAdapterError("grok_busy")
        with self._lock:
            bindings = tuple(self._bindings.values())
            remove = self._remove_listener
            self._remove_listener = None
        for binding in bindings:
            if binding.process_generation == self._supervisor.generation and self.is_ready():
                try:
                    self._supervisor.close_session(binding.backend_session_id)
                except Exception:
                    pass
        if remove is not None:
            remove()
        self._supervisor.stop()
        with self._lock:
            self._models = ()
            self._model_ids.clear()

    def account(self) -> AgentAccount:
        self._require_ready()
        authenticated = self._authenticated_boolean(self._supervisor.auth_info())
        return AgentAccount(
            agent_id=self.agent_id,
            authenticated=authenticated,
            requires_auth=not authenticated,
        )

    def start_device_login(self) -> AgentLogin:
        self._require_ready()
        if self.account().authenticated:
            raise GrokAdapterError("already_authenticated")
        cancel_sequence = None
        with self._lock:
            cancel_sequence = self._expire_login_locked()
            if self._active_login_id is not None:
                raise GrokAdapterError("login_already_active")
            self._login_sequence += 1
            sequence = self._login_sequence
            request_id = "grok_login_" + secrets.token_hex(12)
            attempt = _LoginAttempt(
                request_id=request_id,
                sequence=sequence,
                state="pending",
                expires_at=self._now() + GROK_LOGIN_TTL_SECONDS,
            )
            self._remember_login_locked(attempt)
            self._active_login_id = request_id
        self._cancel_expired(cancel_sequence)

        started = threading.Event()
        worker = threading.Thread(
            target=self._authenticate_worker,
            args=(request_id, sequence, started),
            name="grok-device-auth",
            daemon=True,
        )
        worker.start()
        if not started.wait(1.0):
            self._fail_login_start(request_id, sequence)
            raise GrokAdapterError("grok_login_start_failed")
        try:
            response = self._supervisor.get_auth_url()
            verification_url = self._validated_device_url(response)
        except Exception as error:
            self._fail_login_start(request_id, sequence)
            raise GrokAdapterError("grok_login_challenge_invalid") from error
        return AgentLogin(
            agent_id=self.agent_id,
            request_id=request_id,
            state="pending",
            verification_url=verification_url,
            # The complete URL already carries the challenge. A separate code is never parsed.
            user_code=None,
            expires_in_seconds=GROK_LOGIN_TTL_SECONDS,
            poll_interval_seconds=None,
        )

    def login_status(self, request_id: str) -> AgentLogin:
        self._identifier(request_id)
        cancel_sequence = None
        with self._lock:
            cancel_sequence = self._expire_login_locked()
            attempt = self._logins.get(request_id)
            if attempt is None:
                raise GrokAdapterError("login_not_found")
            result = self._login_value(attempt)
        self._cancel_expired(cancel_sequence)
        return result

    def cancel_login(self, request_id: str) -> AgentLogin:
        self._identifier(request_id)
        expired_sequence = None
        with self._lock:
            expired_sequence = self._expire_login_locked()
            attempt = self._logins.get(request_id)
            if (
                attempt is None
                or attempt.state != "pending"
                or self._active_login_id != request_id
                or attempt.cancel_sent
            ):
                error = GrokAdapterError("login_not_active")
            else:
                error = None
                attempt.cancel_sent = True
                attempt.state = "cancelled"
                self._active_login_id = None
                sequence = attempt.sequence
                result = self._login_value(attempt)
        self._cancel_expired(expired_sequence)
        if error is not None:
            raise error
        try:
            # The response may contain future fields; all are discarded here.
            self._supervisor.cancel_auth(sequence)
        except Exception:
            pass
        return result

    def logout(self) -> None:
        activity = self.activity()
        if activity.active_turn:
            raise GrokAdapterError("turn_active")
        if activity.active_login:
            raise GrokAdapterError("login_active")
        # Discard email, profile, account, and every extension field in the response.
        self._supervisor.logout()
        with self._lock:
            bindings = tuple(self._bindings.values())
            self._bindings.clear()
            self._logins.clear()
            self._active_login_id = None
            self._models = ()
            self._model_ids.clear()
        for binding in bindings:
            if binding.process_generation == self._supervisor.generation and self.is_ready():
                try:
                    self._supervisor.close_session(binding.backend_session_id)
                except Exception:
                    pass

    def models(self) -> Tuple[AgentModel, ...]:
        self._require_authenticated()
        response = self._supervisor.list_models()
        state = self._model_result(response)
        try:
            summaries, current = parse_model_catalog(state)
        except ValueError as error:
            raise GrokAdapterError("grok_models_invalid") from error
        values = tuple(
            AgentModel(
                agent_id=self.agent_id,
                model_id=item.model_id,
                display_name=item.display_name,
                is_default=item.model_id == current,
            )
            for item in summaries
        )
        with self._lock:
            self._models = values
            self._model_ids = {item.model_id for item in values}
        return values

    def start_turn(self, value: dict[str, Any]) -> AgentTurnHandle:
        request = self._parse_turn_request(value)
        self._require_authenticated()
        available = {item.model_id for item in self.models()}
        if request[2] not in available:
            raise GrokAdapterError("model_not_available")
        conversation_id, resume_existing, model_id, text = request
        with self._lock:
            if self._active_turn is not None:
                raise GrokAdapterError("turn_already_active")
        binding = self._resolve_session(conversation_id, resume_existing, model_id)
        request_id = "grok_turn_" + secrets.token_hex(12)
        active = _GrokActiveTurn(
            request_id=request_id,
            conversation_id=conversation_id,
            session_id=binding.backend_session_id,
            model_id=model_id,
            generation=binding.process_generation,
        )
        with self._lock:
            if self._active_turn is not None:
                raise GrokAdapterError("turn_already_active")
            self._active_turn = active
        threading.Thread(
            target=self._prompt_worker,
            args=(active, text),
            name="grok-session-prompt",
            daemon=True,
        ).start()
        return AgentTurnHandle(
            agent_id=self.agent_id,
            request_id=request_id,
            conversation_id=conversation_id,
            model_id=model_id,
            _native_handle=active,
        )

    def stream(self, handle: AgentTurnHandle) -> Iterator[AgentTurnEvent]:
        active = self._active_handle(handle)
        try:
            yield AgentTurnEvent(
                agent_id=self.agent_id,
                request_id=active.request_id,
                event_type="start",
                conversation_id=active.conversation_id,
            )
            while True:
                with active.condition:
                    while not active.events:
                        active.condition.wait(0.5)
                        if not self.is_ready() and not active.terminal:
                            self._terminal_locked(active, "error", "grok_process_lost")
                    event = active.events.popleft()
                yield event
                if event.event_type in ("done", "error"):
                    return
        finally:
            should_cancel = False
            with active.condition:
                if not active.terminal:
                    should_cancel = self._reserve_cancel_locked(active)
            if should_cancel:
                self._send_cancel(active, suppress_error=True)
            self._remember_completed_turn(active.request_id)
            with self._lock:
                self._last_turn_metrics = self._metrics(active)
                if self._active_turn is active:
                    self._active_turn = None

    def interrupt(self, request_id: str) -> None:
        self._identifier(request_id)
        with self._lock:
            active = self._active_turn
            completed = request_id in self._completed_turn_id_set
        if completed:
            return
        if active is None or active.request_id != request_id:
            raise GrokAdapterError("turn_not_found")
        with active.condition:
            if active.terminal:
                return
            should_cancel = self._reserve_cancel_locked(active)
        if should_cancel:
            self._send_cancel(active, suppress_error=False)

    def turn_metrics(self) -> Optional[GrokTurnMetrics]:
        """Return redacted counters only; no request, session, text, or retry reason."""

        with self._lock:
            return self._last_turn_metrics

    def conversation_bindings(self) -> Tuple[AgentConversationBinding, ...]:
        with self._lock:
            return tuple(self._bindings.values())

    def _authenticate_worker(
        self,
        request_id: str,
        sequence: int,
        started: threading.Event,
    ) -> None:
        started.set()
        succeeded = False
        try:
            self._supervisor.authenticate(sequence)
            succeeded = self._authenticated_boolean(self._supervisor.auth_info())
        except Exception:
            succeeded = False
        with self._lock:
            attempt = self._logins.get(request_id)
            if (
                attempt is None
                or attempt.sequence != sequence
                or attempt.state != "pending"
                or self._active_login_id != request_id
            ):
                # A cancelled/expired attempt owns its terminal state. Late success is discarded.
                return
            attempt.state = "authenticated" if succeeded else "failed"
            self._active_login_id = None

    def _fail_login_start(self, request_id: str, sequence: int) -> None:
        should_cancel = False
        with self._lock:
            attempt = self._logins.get(request_id)
            if attempt is not None and attempt.sequence == sequence and attempt.state == "pending":
                attempt.state = "failed"
                attempt.cancel_sent = True
                should_cancel = True
                if self._active_login_id == request_id:
                    self._active_login_id = None
        if should_cancel:
            try:
                self._supervisor.cancel_auth(sequence)
            except Exception:
                pass

    def _resolve_session(
        self,
        conversation_id: str,
        resume_existing: bool,
        model_id: str,
    ) -> AgentConversationBinding:
        generation = self._supervisor.generation
        with self._lock:
            existing = self._bindings.get(conversation_id)
        if existing is not None:
            self._validate_live_binding(existing, generation)
            self._supervisor.resume_session(existing.backend_session_id, self._workspace_directory)
            if existing.model_id != model_id:
                self._supervisor.set_session_model(existing.backend_session_id, model_id)
                existing = AgentConversationBinding(
                    agent_id=self.agent_id,
                    conversation_id=existing.conversation_id,
                    backend_session_id=existing.backend_session_id,
                    model_id=model_id,
                    process_generation=generation,
                )
                with self._lock:
                    self._bindings[conversation_id] = existing
            return existing
        if resume_existing:
            raise GrokAdapterError("conversation_binding_not_found")
        response = self._supervisor.new_session(self._workspace_directory, model_id)
        session_id = self._required_string(response, "sessionId")
        binding = AgentConversationBinding(
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            backend_session_id=session_id,
            model_id=model_id,
            process_generation=generation,
        )
        with self._lock:
            self._bindings[conversation_id] = binding
            self._bindings.move_to_end(conversation_id)
            while len(self._bindings) > MAX_CONVERSATIONS:
                self._bindings.popitem(last=False)
        return binding

    def _prompt_worker(self, active: _GrokActiveTurn, text: str) -> None:
        with active.condition:
            if active.terminal:
                return
            if active.prompt_dispatch_count != 0:
                self._terminal_locked(active, "error", "grok_prompt_dispatch_duplicate")
                return
            active.prompt_dispatch_count = 1
        try:
            response = self._supervisor.prompt(active.session_id, text)
            reason = response.get("stopReason")
            with active.condition:
                if active.terminal:
                    return
                if reason in ("end_turn", "endTurn"):
                    self._terminal_locked(active, "done", None)
                elif reason == "cancelled":
                    self._terminal_locked(active, "error", "turn_interrupted")
                else:
                    self._terminal_locked(active, "error", "grok_turn_failed")
        except Exception:
            with active.condition:
                self._terminal_locked(active, "error", "grok_turn_failed")

    def _on_notification(self, notification: AcpNotification) -> None:
        with self._lock:
            active = self._active_turn
        if active is None or notification.generation != active.generation:
            return
        params = notification.params
        if params.get("sessionId") != active.session_id:
            return
        with active.condition:
            if active.terminal or notification.sequence <= active.last_notification_sequence:
                return
            active.last_notification_sequence = notification.sequence
            if not self._bind_prompt_locked(active, params, notification.method):
                return
        if notification.method == "session/update":
            self._handle_content_update(active, params.get("update"))
        elif notification.method in ("x.ai/session_notification", "_x.ai/session/update"):
            self._handle_retry_update(active, params.get("update"))
        elif notification.method == "x.ai/session/prompt_complete":
            reason = params.get("stopReason")
            with active.condition:
                if reason in ("end_turn", "endTurn"):
                    self._terminal_locked(active, "done", None)
                elif reason == "cancelled":
                    self._terminal_locked(active, "error", "turn_interrupted")
                else:
                    self._terminal_locked(active, "error", "grok_turn_failed")

    def _handle_content_update(self, active: _GrokActiveTurn, update: Any) -> None:
        if not isinstance(update, dict) or update.get("sessionUpdate") != "agent_message_chunk":
            return
        content = update.get("content")
        text = content.get("text") if isinstance(content, dict) and content.get("type") == "text" else None
        if not isinstance(text, str):
            self._fail_active(active, "grok_notification_invalid")
            return
        if not text:
            return
        text_bytes = len(text.encode("utf-8"))
        if text_bytes > MAX_STREAM_TEXT_BYTES:
            self._fail_active(active, "grok_stream_overflow")
            return
        with active.condition:
            if active.terminal:
                return
            if (
                active.total_output_bytes + text_bytes > MAX_STREAM_TOTAL_BYTES
                or len(active.events) >= MAX_STREAM_EVENTS - 1
            ):
                should_cancel = self._reserve_cancel_locked(active)
                self._terminal_locked(active, "error", "grok_stream_overflow")
            else:
                should_cancel = False
                active.total_output_bytes += text_bytes
                active.visible_delta_count += 1
                active.events.append(
                    AgentTurnEvent(
                        agent_id=self.agent_id,
                        request_id=active.request_id,
                        event_type="delta",
                        text=text,
                    )
                )
                active.condition.notify_all()
        if should_cancel:
            self._send_cancel(active, suppress_error=True)

    def _handle_retry_update(self, active: _GrokActiveTurn, update: Any) -> None:
        if not isinstance(update, dict) or update.get("sessionUpdate") != "retry_state":
            return
        kind = update.get("type")
        if kind == "retrying":
            if set(update) != {"sessionUpdate", "type", "attempt", "max_retries", "reason"}:
                self._fail_active(active, "grok_notification_invalid")
                return
            attempt = self._bounded_retry_integer(update.get("attempt"))
            retry_max = self._bounded_retry_integer(update.get("max_retries"))
            reason = update.get("reason")
            if attempt is None or retry_max is None or attempt > retry_max or not self._bounded_private_text(reason):
                self._fail_active(active, "grok_notification_invalid")
                return
            with active.condition:
                if active.terminal:
                    return
                active.retry_attempts = max(active.retry_attempts, attempt)
                active.retry_max = max(active.retry_max, retry_max)
                if self._retry_policy is GrokRetryPolicy.STRICT:
                    active.retry_classification = "strict_blocked"
                    code = "grok_cli_retry_forbidden"
                elif active.visible_delta_count > 0:
                    active.retry_classification = "post_output"
                    code = "grok_retry_after_output"
                else:
                    active.retry_classification = "pre_output"
                    return
            self._fail_active(active, code)
            return
        if kind == "exhausted":
            if set(update) != {
                "sessionUpdate",
                "type",
                "attempts",
                "reason",
                "is_rate_limited",
            }:
                self._fail_active(active, "grok_notification_invalid")
                return
            attempts = self._bounded_retry_integer(update.get("attempts"))
            reason = update.get("reason")
            rate_limited = update.get("is_rate_limited")
            if attempts is None or not self._bounded_private_text(reason) or not isinstance(rate_limited, bool):
                self._fail_active(active, "grok_notification_invalid")
                return
            with active.condition:
                if active.terminal:
                    return
                active.retry_classification = "exhausted"
                active.retry_attempts = max(active.retry_attempts, attempts)
            self._fail_active(active, "grok_retry_exhausted")
            return
        if kind == "failed":
            if set(update) != {"sessionUpdate", "type", "error_type", "message"}:
                self._fail_active(active, "grok_notification_invalid")
                return
            error_type = update.get("error_type")
            message = update.get("message")
            if not self._bounded_private_text(error_type, maximum=64) or not self._bounded_private_text(message):
                self._fail_active(active, "grok_notification_invalid")
                return
            with active.condition:
                if active.terminal:
                    return
                active.retry_classification = "auth_failed" if error_type == "auth" else "failed"
            self._fail_active(
                active,
                "grok_auth_recovery_failed" if error_type == "auth" else "grok_retry_failed",
            )
            return
        self._fail_active(active, "grok_notification_invalid")

    def _terminal_locked(
        self,
        active: _GrokActiveTurn,
        event_type: str,
        code: Optional[str],
    ) -> None:
        if active.terminal:
            return
        active.terminal = True
        active.terminal_count += 1
        active.events.append(
            AgentTurnEvent(
                agent_id=self.agent_id,
                request_id=active.request_id,
                event_type=event_type,
                code=code,
            )
        )
        active.condition.notify_all()
        self._remember_completed_turn(active.request_id)

    def _remember_completed_turn(self, request_id: str) -> None:
        with self._lock:
            if request_id not in self._completed_turn_id_set:
                if len(self._completed_turn_ids) == self._completed_turn_ids.maxlen:
                    expired = self._completed_turn_ids.popleft()
                    self._completed_turn_id_set.discard(expired)
                self._completed_turn_ids.append(request_id)
                self._completed_turn_id_set.add(request_id)

    def _fail_active(self, active: _GrokActiveTurn, code: str) -> None:
        with active.condition:
            if active.terminal:
                return
            should_cancel = self._reserve_cancel_locked(active)
            self._terminal_locked(active, "error", code)
        if should_cancel:
            self._send_cancel(active, suppress_error=True)

    @staticmethod
    def _reserve_cancel_locked(active: _GrokActiveTurn) -> bool:
        if active.cancel_sent:
            return False
        active.cancel_sent = True
        active.cancel_dispatch_count += 1
        return True

    def _send_cancel(self, active: _GrokActiveTurn, *, suppress_error: bool) -> None:
        try:
            self._supervisor.cancel_session(active.session_id)
        except Exception as error:
            if not suppress_error:
                raise GrokAdapterError("grok_cancel_failed") from error

    @staticmethod
    def _bind_prompt_locked(
        active: _GrokActiveTurn,
        params: Dict[str, Any],
        method: str,
    ) -> bool:
        meta = params.get("_meta")
        if meta is not None and not isinstance(meta, dict):
            return False
        if isinstance(meta, dict) and meta.get("isReplay") is True:
            return False
        prompt_id = params.get("promptId") if method == "x.ai/session/prompt_complete" else None
        if prompt_id is None and isinstance(meta, dict):
            prompt_id = meta.get("promptId")
        if prompt_id is None:
            return True
        if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 512:
            return False
        if active.prompt_id is None:
            active.prompt_id = prompt_id
            return True
        return active.prompt_id == prompt_id

    @staticmethod
    def _bounded_retry_integer(value: Any) -> Optional[int]:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_RETRY_ATTEMPTS:
            return None
        return value

    @staticmethod
    def _bounded_private_text(value: Any, *, maximum: int = 2048) -> bool:
        return isinstance(value, str) and 0 < len(value) <= maximum

    @staticmethod
    def _metrics(active: _GrokActiveTurn) -> GrokTurnMetrics:
        return GrokTurnMetrics(
            prompt_dispatch_count=active.prompt_dispatch_count,
            visible_delta_count=active.visible_delta_count,
            terminal_count=active.terminal_count,
            cancel_dispatch_count=active.cancel_dispatch_count,
            retry_classification=active.retry_classification,
            retry_attempts=active.retry_attempts,
            retry_max=active.retry_max,
        )

    def _active_handle(self, handle: AgentTurnHandle) -> _GrokActiveTurn:
        if handle.agent_id != self.agent_id or not isinstance(handle._native_handle, _GrokActiveTurn):
            raise GrokAdapterError("invalid_turn_handle")
        return handle._native_handle

    def _require_ready(self) -> None:
        if not self.is_ready():
            raise GrokAdapterError("grok_not_ready")

    def _require_authenticated(self) -> None:
        if not self.account().authenticated:
            raise GrokAdapterError("authentication_required")

    @staticmethod
    def _authenticated_boolean(response: Dict[str, Any]) -> bool:
        if not isinstance(response, dict) or len(response) > 128:
            raise GrokAdapterError("grok_auth_response_invalid")
        # Deliberately read one non-sensitive discriminator and discard the whole response.
        return response.get("methodId") == AUTH_METHOD_ID

    @staticmethod
    def _model_result(response: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(response, dict) or set(response) != {"result"}:
            raise GrokAdapterError("grok_extension_response_invalid")
        result = response.get("result")
        if not isinstance(result, dict) or len(result) > 128:
            raise GrokAdapterError("grok_extension_response_invalid")
        return result

    @staticmethod
    def _validated_device_url(response: Dict[str, Any]) -> str:
        if not isinstance(response, dict) or len(response) > 16:
            raise GrokAdapterError("grok_login_challenge_invalid")
        value = response
        if value.get("mode") != "device":
            raise GrokAdapterError("grok_login_challenge_invalid")
        url = value.get("auth_url")
        if not isinstance(url, str) or not (0 < len(url) <= 2048):
            raise GrokAdapterError("grok_login_challenge_invalid")
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise GrokAdapterError("grok_login_challenge_invalid") from error
        if (
            parsed.scheme != "https"
            or parsed.hostname not in GROK_AUTH_ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or not parsed.path
            or parsed.fragment
        ):
            raise GrokAdapterError("grok_login_challenge_invalid")
        return url

    @staticmethod
    def _required_string(response: Dict[str, Any], field: str) -> str:
        value = response.get(field) if isinstance(response, dict) else None
        if not isinstance(value, str) or not value or len(value) > 512:
            raise GrokAdapterError("grok_session_response_invalid")
        return value

    @staticmethod
    def _identifier(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > 512:
            raise GrokAdapterError("invalid_request")
        return value

    @staticmethod
    def _parse_turn_request(value: Any) -> tuple[str, bool, str, str]:
        if not isinstance(value, dict) or value.get("stream") is not True:
            raise GrokAdapterError("invalid_request")
        if value.get("agent_id", "grok") != "grok":
            raise GrokAdapterError("agent_mismatch")
        model = value.get("model")
        if not isinstance(model, str) or not model or len(model) > 512:
            raise GrokAdapterError("invalid_request")
        messages = value.get("messages")
        if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
            raise GrokAdapterError("invalid_request")
        message = messages[0]
        text = message.get("content")
        if message.get("role") != "user" or not isinstance(text, str) or not text.strip():
            raise GrokAdapterError("invalid_request")
        if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise GrokAdapterError("request_too_large")
        conversation = value.get("conversation_id")
        generated = conversation is None
        if generated:
            conversation = "conversation_" + secrets.token_hex(12)
        if not isinstance(conversation, str) or not conversation or len(conversation) > 128:
            raise GrokAdapterError("invalid_request")
        resume = value.get("resume_existing", False)
        if not isinstance(resume, bool) or (generated and resume):
            raise GrokAdapterError("invalid_request")
        return conversation, resume, model, text

    @staticmethod
    def _validate_binding(binding: AgentConversationBinding) -> None:
        if (
            not isinstance(binding, AgentConversationBinding)
            or binding.agent_id is not AgentId.GROK
            or binding.process_generation <= 0
        ):
            raise ValueError("invalid Grok conversation binding")
        for value in (
            binding.conversation_id,
            binding.backend_session_id,
            binding.model_id,
        ):
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError("invalid Grok conversation binding")

    @staticmethod
    def _validate_live_binding(binding: AgentConversationBinding, generation: int) -> None:
        if binding.agent_id is not AgentId.GROK:
            raise GrokAdapterError("conversation_agent_mismatch")
        if binding.process_generation != generation:
            raise GrokAdapterError("conversation_generation_mismatch")

    def _expire_login_locked(self) -> Optional[int]:
        active_id = self._active_login_id
        if active_id is None:
            return None
        attempt = self._logins.get(active_id)
        if attempt is None or attempt.state != "pending" or attempt.expires_at > self._now():
            return None
        attempt.state = "expired"
        self._active_login_id = None
        if attempt.cancel_sent:
            return None
        attempt.cancel_sent = True
        return attempt.sequence

    def _cancel_expired(self, sequence: Optional[int]) -> None:
        if sequence is None:
            return
        try:
            self._supervisor.cancel_auth(sequence)
        except Exception:
            pass

    def _remember_login_locked(self, attempt: _LoginAttempt) -> None:
        self._logins[attempt.request_id] = attempt
        self._logins.move_to_end(attempt.request_id)
        while len(self._logins) > MAX_LOGIN_RECORDS:
            self._logins.popitem(last=False)

    def _login_value(self, attempt: _LoginAttempt) -> AgentLogin:
        return AgentLogin(
            agent_id=self.agent_id,
            request_id=attempt.request_id,
            state=attempt.state,
        )
