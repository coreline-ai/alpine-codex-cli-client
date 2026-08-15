"""Official Grok ACP implementation of the closed Agent adapter contract."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
import json
import os
import secrets
import stat
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
    AgentTurnDiagnostics,
    AgentTurnHandle,
)
from codex_gateway.grok_acp.contract import (
    AUTHENTICATED_METHOD_IDS,
)
from codex_gateway.grok_acp.process import GrokAcpSupervisor, GrokSupervisorState
from codex_gateway.grok_acp.rpc import AcpNotification, AcpRemoteError, GrokProfileAudit


GROK_LOGIN_TTL_SECONDS = 10 * 60
GROK_AUTH_URL_READY_TIMEOUT_SECONDS = 15.0
GROK_AUTH_URL_POLL_SECONDS = 0.1
GROK_STREAM_DRAIN_POLL_SECONDS = 0.15
GROK_STREAM_DRAIN_STABLE_CHECKS = 2
GROK_STREAM_DRAIN_EMPTY_TIMEOUT_SECONDS = 1.0
MAX_LOGIN_RECORDS = 16
MAX_CONVERSATIONS = 64
MAX_MESSAGE_BYTES = 16 * 1024
MAX_STREAM_TEXT_BYTES = 24 * 1024
MAX_STREAM_TOTAL_BYTES = 256 * 1024
MAX_STREAM_EVENTS = 128
MAX_RETRY_ATTEMPTS = 32
MAX_COMPLETED_TURN_IDS = 32
MAX_BINDING_STORE_BYTES = 64 * 1024
GROK_AUTH_ALLOWED_HOSTS = frozenset({"auth.x.ai", "accounts.x.ai"})
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
    tool_event_count: int
    subagent_event_count: int
    mcp_event_count: int
    filesystem_event_count: int
    terminal_event_count: int


class _GrokSupervisor(Protocol):
    @property
    def state(self) -> Any: ...

    @property
    def generation(self) -> int: ...

    @property
    def initialize_state(self) -> Any: ...

    @property
    def authenticated_method_id(self) -> Optional[str]: ...

    def start(self) -> Any: ...

    def stop(self, timeout_seconds: float = 5.0) -> None: ...

    def add_notification_listener(
        self, listener: Callable[[AcpNotification], None]
    ) -> Callable[[], None]: ...

    def authenticate(self, request_sequence: int) -> Dict[str, Any]: ...

    def get_auth_url(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]: ...

    def cancel_auth(self, request_sequence: int) -> Dict[str, Any]: ...

    def logout(self) -> Dict[str, Any]: ...

    def new_session(self, working_directory: str) -> Dict[str, Any]: ...

    def load_session(self, session_id: str, working_directory: str) -> Dict[str, Any]: ...

    def resume_session(self, session_id: str, working_directory: str) -> Dict[str, Any]: ...

    def set_session_model(self, session_id: str, model_id: str) -> Dict[str, Any]: ...

    def prompt(self, session_id: str, text: str) -> Dict[str, Any]: ...

    def cancel_session(self, session_id: str) -> None: ...

    def close_session(self, session_id: str) -> Dict[str, Any]: ...

    @property
    def profile_audit(self) -> GrokProfileAudit: ...

    @property
    def prompt_dispatch_count(self) -> int: ...


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
    prompt_worker_started: bool = False
    prompt_dispatch_baseline: int = 0
    tool_event_count: int = 0
    subagent_event_count: int = 0
    mcp_event_count: int = 0
    filesystem_event_count: int = 0
    terminal_event_count: int = 0


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
        binding_store_path: Optional[str] = None,
    ) -> None:
        if not isinstance(workspace_directory, str) or not workspace_directory.startswith("/"):
            raise ValueError("Grok workspace directory must be absolute")
        self._workspace_directory = workspace_directory
        if not isinstance(retry_policy, GrokRetryPolicy):
            raise ValueError("invalid Grok retry policy")
        if binding_store_path is not None and (
            not isinstance(binding_store_path, str) or not binding_store_path.startswith("/")
        ):
            raise ValueError("Grok binding store path must be absolute")
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
        self._binding_store_path = binding_store_path
        self._restored_binding_ids: set[str] = set()
        self._remove_listener: Optional[Callable[[], None]] = None
        for binding in restored_bindings:
            self._validate_binding(binding)
            if binding.conversation_id in self._bindings:
                raise ValueError("duplicate Grok conversation binding")
            self._bindings[binding.conversation_id] = binding
        for binding in self._load_binding_store():
            if binding.conversation_id in self._bindings:
                continue
            self._bindings[binding.conversation_id] = binding
            self._restored_binding_ids.add(binding.conversation_id)

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
        method_id = self._supervisor.authenticated_method_id
        if method_id not in ({None} | AUTHENTICATED_METHOD_IDS):
            raise GrokAdapterError("grok_auth_response_invalid")
        authenticated = method_id in AUTHENTICATED_METHOD_IDS
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
            verification_url = self._wait_for_device_url()
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

    def _wait_for_device_url(self) -> str:
        deadline = self._now() + GROK_AUTH_URL_READY_TIMEOUT_SECONDS
        while True:
            remaining = deadline - self._now()
            if remaining <= 0:
                raise GrokAdapterError("grok_login_challenge_invalid")
            response = self._supervisor.get_auth_url(remaining)
            if not self._auth_url_unready(response):
                return self._validated_device_url(response)
            remaining = deadline - self._now()
            if remaining <= 0:
                raise GrokAdapterError("grok_login_challenge_invalid")
            time.sleep(min(GROK_AUTH_URL_POLL_SECONDS, remaining))

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
            self._restored_binding_ids.clear()
            self._logins.clear()
            self._active_login_id = None
            self._models = ()
            self._model_ids.clear()
            self._persist_binding_store_locked()
        for binding in bindings:
            if binding.process_generation == self._supervisor.generation and self.is_ready():
                try:
                    self._supervisor.close_session(binding.backend_session_id)
                except Exception:
                    pass

    def models(self) -> Tuple[AgentModel, ...]:
        self._require_authenticated()
        state = self._supervisor.initialize_state
        if state is None:
            raise GrokAdapterError("grok_models_invalid")
        try:
            summaries = state.models
            current = state.current_model_id
            values = tuple(
                AgentModel(
                    agent_id=self.agent_id,
                    model_id=item.model_id,
                    display_name=item.display_name,
                    is_default=item.model_id == current,
                )
                for item in summaries
            )
        except (AttributeError, TypeError, ValueError) as error:
            raise GrokAdapterError("grok_models_invalid") from error
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
        audit = self._profile_audit()
        if not audit.clean:
            raise GrokAdapterError("grok_chat_profile_violation")
        dispatch_baseline = self._supervisor.prompt_dispatch_count
        request_id = "grok_turn_" + secrets.token_hex(12)
        active = _GrokActiveTurn(
            request_id=request_id,
            conversation_id=conversation_id,
            session_id=binding.backend_session_id,
            model_id=model_id,
            generation=binding.process_generation,
            tool_event_count=audit.tool_event_count,
            subagent_event_count=audit.subagent_event_count,
            mcp_event_count=audit.mcp_event_count,
            filesystem_event_count=audit.filesystem_event_count,
            terminal_event_count=audit.terminal_event_count,
            prompt_dispatch_baseline=dispatch_baseline,
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
                            self._refresh_profile_audit(active)
                            self._refresh_prompt_dispatch(active)
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
            # `session/cancel` is the authoritative acknowledgement for the user's Stop. Grok
            # 1.0.0 can keep the outstanding `session/prompt` RPC open after acknowledging that
            # cancel, so waiting for its late response can outlive Android's bounded SSE timeout.
            # Publish the single terminal event immediately; the prompt worker and any late
            # notification are already guarded by `active.terminal` and cannot publish twice.
            self._refresh_profile_audit(active)
            self._refresh_prompt_dispatch(active)
            with active.condition:
                self._terminal_locked(active, "error", "turn_interrupted")

    def turn_metrics(self) -> Optional[GrokTurnMetrics]:
        """Return redacted counters only; no request, session, text, or retry reason."""

        with self._lock:
            return self._last_turn_metrics

    def conversation_bindings(self) -> Tuple[AgentConversationBinding, ...]:
        with self._lock:
            return tuple(self._bindings.values())

    def _load_binding_store(self) -> Tuple[AgentConversationBinding, ...]:
        path = self._binding_store_path
        if path is None:
            return ()
        descriptor: Optional[int] = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            value = os.fstat(descriptor)
            if (
                not stat.S_ISREG(value.st_mode)
                or stat.S_IMODE(value.st_mode) != 0o600
                or value.st_size <= 0
                or value.st_size > MAX_BINDING_STORE_BYTES
            ):
                return ()
            with os.fdopen(descriptor, "r", encoding="utf-8") as source:
                descriptor = None
                payload = json.load(source)
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, ValueError, TypeError):
            return ()
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return ()
        rows = payload.get("bindings")
        if not isinstance(rows, list):
            return ()
        bindings = []
        for row in rows[-MAX_CONVERSATIONS:]:
            if not isinstance(row, dict) or set(row) != {
                "agent_id",
                "conversation_id",
                "backend_session_id",
                "model_id",
            }:
                continue
            try:
                binding = AgentConversationBinding(
                    agent_id=AgentId.parse_exact(row["agent_id"]),
                    conversation_id=row["conversation_id"],
                    backend_session_id=row["backend_session_id"],
                    model_id=row["model_id"],
                    # Persisted generations are intentionally not trusted across process lives.
                    process_generation=1,
                )
                self._validate_binding(binding)
            except (TypeError, ValueError):
                continue
            bindings.append(binding)
        return tuple(bindings)

    def _persist_binding_store_locked(self) -> None:
        path = self._binding_store_path
        if path is None:
            return
        directory = os.path.dirname(path)
        temporary = path + ".tmp-" + secrets.token_hex(8)
        payload = {
            "schema_version": 1,
            "bindings": [
                {
                    "agent_id": binding.agent_id.value,
                    "conversation_id": binding.conversation_id,
                    "backend_session_id": binding.backend_session_id,
                    "model_id": binding.model_id,
                }
                for binding in self._bindings.values()
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BINDING_STORE_BYTES:
            raise GrokAdapterError("conversation_store_failed")
        descriptor: Optional[int] = None
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
            directory_value = os.lstat(directory)
            if (
                stat.S_ISLNK(directory_value.st_mode)
                or not stat.S_ISDIR(directory_value.st_mode)
                or stat.S_IMODE(directory_value.st_mode) != 0o700
            ):
                raise OSError
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                descriptor = None
                destination.write(encoded)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, path)
        except OSError as error:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise GrokAdapterError("conversation_store_failed") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _remember_binding(self, binding: AgentConversationBinding) -> None:
        with self._lock:
            before = OrderedDict(self._bindings)
            self._bindings[binding.conversation_id] = binding
            self._bindings.move_to_end(binding.conversation_id)
            while len(self._bindings) > MAX_CONVERSATIONS:
                self._bindings.popitem(last=False)
            try:
                self._persist_binding_store_locked()
            except Exception:
                self._bindings = before
                raise

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
            # Standard ACP authenticate is the authentication authority. Private x.ai auth-info
            # probes are deliberately not used: the pinned CLI can reject repeated extension
            # calls after the browser hand-off even though authenticate completed successfully.
            succeeded = True
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
            restored = conversation_id in self._restored_binding_ids
        if existing is not None:
            try:
                if restored or existing.process_generation != generation:
                    self._supervisor.load_session(
                        existing.backend_session_id,
                        self._workspace_directory,
                    )
                else:
                    self._supervisor.resume_session(
                        existing.backend_session_id,
                        self._workspace_directory,
                    )
            except AcpRemoteError as error:
                stage = (
                    "session_load"
                    if restored or existing.process_generation != generation
                    else "session_resume"
                )
                raise GrokAdapterError(self._remote_stage_code(stage, error)) from error
            if existing.model_id != model_id:
                try:
                    self._supervisor.set_session_model(existing.backend_session_id, model_id)
                except AcpRemoteError as error:
                    raise GrokAdapterError(self._remote_stage_code("set_model", error)) from error
            existing = AgentConversationBinding(
                agent_id=self.agent_id,
                conversation_id=existing.conversation_id,
                backend_session_id=existing.backend_session_id,
                model_id=model_id,
                process_generation=generation,
            )
            self._remember_binding(existing)
            with self._lock:
                self._restored_binding_ids.discard(conversation_id)
            return existing
        if resume_existing:
            raise GrokAdapterError("conversation_binding_not_found")
        try:
            response = self._supervisor.new_session(self._workspace_directory)
        except AcpRemoteError as error:
            raise GrokAdapterError(self._remote_stage_code("session_new", error)) from error
        session_id = self._required_string(response, "sessionId")
        state = self._supervisor.initialize_state
        current_model_id = getattr(state, "current_model_id", None)
        if model_id != current_model_id:
            try:
                self._supervisor.set_session_model(session_id, model_id)
            except Exception as error:
                # The session was created but never published as a conversation binding. Close it
                # best-effort so a rejected model selection cannot leak an orphaned live session.
                try:
                    self._supervisor.close_session(session_id)
                except Exception:
                    pass
                if isinstance(error, AcpRemoteError):
                    raise GrokAdapterError(self._remote_stage_code("set_model", error)) from error
                raise
        binding = AgentConversationBinding(
            agent_id=self.agent_id,
            conversation_id=conversation_id,
            backend_session_id=session_id,
            model_id=model_id,
            process_generation=generation,
        )
        try:
            self._remember_binding(binding)
        except Exception:
            try:
                self._supervisor.close_session(session_id)
            except Exception:
                pass
            raise
        return binding

    def _prompt_worker(self, active: _GrokActiveTurn, text: str) -> None:
        with active.condition:
            if active.terminal:
                return
            if active.prompt_worker_started:
                self._terminal_locked(active, "error", "grok_prompt_dispatch_duplicate")
                return
            active.prompt_worker_started = True
        try:
            response = self._supervisor.prompt(active.session_id, text)
            self._refresh_profile_audit(active)
            self._refresh_prompt_dispatch(active)
            reason = response.get("stopReason")
            if reason in ("end_turn", "endTurn"):
                # xAI's official ACP client example keeps accepting session/update chunks after
                # the session/prompt response until the text length is stable for two 150 ms
                # checks. Do the same here; otherwise a mobile/proot scheduling gap can turn a
                # valid response into a completed but blank assistant card.
                self._wait_for_stream_drain(active)
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
            self._refresh_profile_audit(active)
            self._refresh_prompt_dispatch(active)
            with active.condition:
                code = (
                    "grok_chat_profile_violation"
                    if self._profile_violation(active)
                    else "grok_turn_failed"
                )
                self._terminal_locked(active, "error", code)

    @staticmethod
    def _wait_for_stream_drain(active: _GrokActiveTurn) -> None:
        empty_deadline = time.monotonic() + GROK_STREAM_DRAIN_EMPTY_TIMEOUT_SECONDS
        last_size = -1
        stable_checks = 0
        while True:
            time.sleep(GROK_STREAM_DRAIN_POLL_SECONDS)
            with active.condition:
                if active.terminal:
                    return
                current_size = active.total_output_bytes
            if current_size > 0:
                if current_size == last_size:
                    stable_checks += 1
                else:
                    stable_checks = 0
                if stable_checks >= GROK_STREAM_DRAIN_STABLE_CHECKS:
                    return
            elif time.monotonic() >= empty_deadline:
                return
            last_size = current_size

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
        if notification.method in (
            "session/update",
            "_x.ai/session/update",
            "_x.ai/session_notification",
        ):
            update = params.get("update")
            update_type = update.get("sessionUpdate") if isinstance(update, dict) else None
            if update_type == "agent_message_chunk":
                # Grok 1.0.0 may deliver standard ACP content on either the standard rail or its
                # replay-capable x.ai update rail. The payload remains the same bounded ACP
                # ContentChunk in both cases.
                self._handle_content_update(active, update)
            elif notification.method != "session/update":
                self._handle_retry_update(active, update)
        elif notification.method == "_x.ai/session/prompt_complete":
            self._refresh_profile_audit(active)
            self._refresh_prompt_dispatch(active)
            reason = params.get("stopReason")
            with active.condition:
                if reason in ("end_turn", "endTurn"):
                    # This private notification may precede the last buffered standard ACP text
                    # chunk. The authoritative session/prompt response owns successful terminal
                    # publication after the bounded drain window above.
                    return
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
                diagnostics=self._diagnostics(active),
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
        prompt_id = params.get("promptId") if method == "_x.ai/session/prompt_complete" else None
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
            tool_event_count=active.tool_event_count,
            subagent_event_count=active.subagent_event_count,
            mcp_event_count=active.mcp_event_count,
            filesystem_event_count=active.filesystem_event_count,
            terminal_event_count=active.terminal_event_count,
        )

    @staticmethod
    def _diagnostics(active: _GrokActiveTurn) -> AgentTurnDiagnostics:
        metrics = GrokAgentAdapter._metrics(active)
        return AgentTurnDiagnostics(**metrics.__dict__)

    def _profile_audit(self) -> GrokProfileAudit:
        try:
            return self._supervisor.profile_audit
        except Exception as error:
            raise GrokAdapterError("grok_profile_audit_unavailable") from error

    def _refresh_profile_audit(self, active: _GrokActiveTurn) -> None:
        try:
            audit = self._profile_audit()
        except GrokAdapterError:
            return
        with active.condition:
            active.tool_event_count = max(active.tool_event_count, audit.tool_event_count)
            active.subagent_event_count = max(active.subagent_event_count, audit.subagent_event_count)
            active.mcp_event_count = max(active.mcp_event_count, audit.mcp_event_count)
            active.filesystem_event_count = max(
                active.filesystem_event_count,
                audit.filesystem_event_count,
            )
            active.terminal_event_count = max(
                active.terminal_event_count,
                audit.terminal_event_count,
            )

    def _refresh_prompt_dispatch(self, active: _GrokActiveTurn) -> None:
        try:
            total = self._supervisor.prompt_dispatch_count
        except Exception:
            return
        with active.condition:
            delta = max(0, total - active.prompt_dispatch_baseline)
            active.prompt_dispatch_count = min(2, delta)

    @staticmethod
    def _profile_violation(active: _GrokActiveTurn) -> bool:
        return any(
            (
                active.tool_event_count,
                active.subagent_event_count,
                active.mcp_event_count,
                active.filesystem_event_count,
                active.terminal_event_count,
            )
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
    def _auth_url_unready(response: Dict[str, Any]) -> bool:
        return (
            isinstance(response, dict)
            and len(response) <= 16
            and response.get("auth_url") is None
            and response.get("mode") in (None, "device")
        )

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
    def _remote_stage_code(stage: str, error: AcpRemoteError) -> str:
        if stage not in {"session_new", "session_load", "session_resume", "set_model"}:
            raise ValueError("invalid remote error stage")
        remote = error.remote_code
        suffix = "unknown" if remote is None else (f"n{abs(remote)}" if remote < 0 else str(remote))
        if error.remote_category is not None:
            suffix += "_" + error.remote_category.lower()
        return f"grok_{stage}_remote_{suffix}"

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
