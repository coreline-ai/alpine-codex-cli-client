"""Lifecycle owner and typed method facade for one official Grok ACP process."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import queue
import subprocess
import threading
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from codex_gateway.app_server.jsonl import JsonlDecoder, JsonlProtocolError

from .contract import (
    AUTH_METHOD_ID,
    CACHED_TOKEN_AUTH_METHOD_ID,
    GrokInitializeState,
    _RequestMethod,
    initialize_params,
    opaque_identifier,
    parse_initialize_result,
)
from .diagnostics import DiscardingStderrDiagnostics
from .policy import GrokLaunchPolicy, GrokPolicyError
from .rpc import (
    AcpError,
    AcpNotification,
    AcpProcessLost,
    AcpProtocolError,
    AcpStopped,
    AcpTimeout,
    GrokProfileAudit,
    _AcpMultiplexer,
)


class GrokSupervisorState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class GrokSupervisorError(RuntimeError):
    """A stable lifecycle error which never embeds child output or request data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_METHOD_TIMEOUT_SECONDS: Mapping[_RequestMethod, float] = {
    _RequestMethod.INITIALIZE: 30.0,
    _RequestMethod.AUTHENTICATE: 900.0,
    _RequestMethod.AUTH_URL: 30.0,
    _RequestMethod.AUTH_CANCEL: 15.0,
    _RequestMethod.AUTH_INFO: 15.0,
    _RequestMethod.AUTH_LOGOUT: 15.0,
    _RequestMethod.MODELS_LIST: 30.0,
    _RequestMethod.SESSION_NEW: 60.0,
    _RequestMethod.SESSION_LOAD: 90.0,
    _RequestMethod.SESSION_RESUME: 90.0,
    _RequestMethod.SESSION_SET_MODEL: 30.0,
    _RequestMethod.SESSION_PROMPT: 900.0,
    _RequestMethod.SESSION_CLOSE: 15.0,
}
POST_INITIALIZE_STABILITY_SECONDS = 0.5
CACHED_AUTHENTICATE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class _ProcessSpec:
    command: tuple[str, ...]
    working_directory: str
    environment: Mapping[str, str]
    validate_policy: bool


class GrokAcpSupervisor:
    """Fixed-process supervisor with a closed public ACP method surface.

    Production construction accepts no executable, environment, path, profile, or method. The
    underscored fixture constructor exists only for local deterministic tests.
    """

    def __init__(self) -> None:
        policy = GrokLaunchPolicy.production()
        self._policy: Optional[GrokLaunchPolicy] = policy
        self._spec = _ProcessSpec(
            command=tuple(policy.command()),
            working_directory=policy.work.as_posix(),
            environment=dict(policy.environment()),
            validate_policy=True,
        )
        self._initialize_runtime()

    @classmethod
    def _for_test(
        cls,
        command: Sequence[str],
        working_directory: str,
        *,
        environment: Optional[Mapping[str, str]] = None,
        timeout_scale: float = 1.0,
        max_pending: int = 16,
        max_jsonl_line_bytes: int = 1024 * 1024,
    ) -> "GrokAcpSupervisor":
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("fixture command invalid")
        if not os.path.isabs(working_directory) or timeout_scale <= 0 or max_pending <= 0:
            raise ValueError("fixture bounds invalid")
        instance = cls.__new__(cls)
        instance._policy = None
        instance._spec = _ProcessSpec(
            command=tuple(command),
            working_directory=working_directory,
            environment=dict(environment or {}),
            validate_policy=False,
        )
        instance._fixture_timeout_scale = timeout_scale
        instance._fixture_max_pending = max_pending
        instance._fixture_max_jsonl_line_bytes = max_jsonl_line_bytes
        instance._initialize_runtime()
        return instance

    def _initialize_runtime(self) -> None:
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = GrokSupervisorState.STOPPED
        self._generation = 0
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._rpc: Optional[_AcpMultiplexer] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._wait_thread: Optional[threading.Thread] = None
        self._exit_event = threading.Event()
        self._spawn_requests: "queue.SimpleQueue[tuple[Dict[str, Any], Dict[str, Any], threading.Event]]" = (
            queue.SimpleQueue()
        )
        self._spawn_owner_thread: Optional[threading.Thread] = None
        if self._spec.validate_policy:
            self._spawn_owner_thread = threading.Thread(
                target=self._spawn_owner_loop,
                name="grok-acp-spawn-owner",
                daemon=True,
            )
            self._spawn_owner_thread.start()
        self._initialize_state: Optional[GrokInitializeState] = None
        self._authenticated_method_id: Optional[str] = None
        self._stderr = DiscardingStderrDiagnostics()
        self._fixture_timeout_scale = getattr(self, "_fixture_timeout_scale", 1.0)
        self._fixture_max_pending = getattr(self, "_fixture_max_pending", 16)
        self._fixture_max_jsonl_line_bytes = getattr(
            self, "_fixture_max_jsonl_line_bytes", 1024 * 1024
        )

    @property
    def state(self) -> GrokSupervisorState:
        with self._lock:
            return self._state

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def initialize_state(self) -> Optional[GrokInitializeState]:
        with self._lock:
            return self._initialize_state

    @property
    def authenticated_method_id(self) -> Optional[str]:
        with self._lock:
            return self._authenticated_method_id

    @property
    def stderr_diagnostic(self) -> str:
        return self._stderr.snapshot()

    def start(self) -> GrokInitializeState:
        with self._lock:
            if self._state is not GrokSupervisorState.STOPPED:
                raise GrokSupervisorError("grok_supervisor_not_stopped")
            self._state = GrokSupervisorState.STARTING
            self._generation += 1
            generation = self._generation
            self._exit_event.clear()
            self._initialize_state = None
            self._authenticated_method_id = None

        try:
            if self._spec.validate_policy:
                if self._policy is None:
                    raise GrokPolicyError()
                self._policy.validate()
                self._policy.permission_probe()
            # The production Gateway requests this process from a ThreadingHTTPServer worker, but
            # the long-lived spawn owner performs the actual Popen call. Keep the fixed working
            # directory and close all unrelated descriptors without running any Python callback in
            # the child. The entrypoint fixes umask 077 before starting request threads. Fixtures
            # use the same descriptor-safe launch shape directly.
            spawn_options: Dict[str, Any] = {
                "cwd": self._spec.working_directory,
                "close_fds": True,
            }
            process = self._spawn_process(spawn_options)
        except (OSError, ValueError, GrokPolicyError) as error:
            with self._lock:
                self._state = GrokSupervisorState.FAILED
            raise GrokSupervisorError("grok_process_start_failed") from error

        if process.stdin is None or process.stdout is None or process.stderr is None:
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
            with self._lock:
                self._state = GrokSupervisorState.FAILED
            raise GrokSupervisorError("grok_process_stdio_unavailable")

        with self._lock:
            self._process = process
            self._rpc = _AcpMultiplexer(
                self._write_stdin,
                generation,
                max_pending=self._fixture_max_pending,
            )
            self._stdout_thread = threading.Thread(
                target=self._read_stdout,
                args=(generation, process),
                name="grok-acp-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                name="grok-acp-stderr",
                daemon=True,
            )
            self._wait_thread = threading.Thread(
                target=self._wait_for_exit,
                args=(generation, process),
                name="grok-acp-wait",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            self._wait_thread.start()
            self._state = GrokSupervisorState.INITIALIZING

        try:
            response = self._typed_request(_RequestMethod.INITIALIZE, initialize_params())
            normalized = parse_initialize_result(response)
            if self._spec.validate_policy:
                # Require a bounded no-exit interval before publishing READY. Authentication and
                # session setup then follow the standard ACP sequence without private readiness
                # probes.
                if self._exit_event.wait(POST_INITIALIZE_STABILITY_SECONDS):
                    raise AcpProcessLost()
                with self._lock:
                    if self._state is not GrokSupervisorState.INITIALIZING:
                        raise AcpProcessLost()
            if normalized.auth_method_id == CACHED_TOKEN_AUTH_METHOD_ID:
                # The official ACP client eagerly authenticates the initialize response's
                # ``defaultAuthMethodId``. Merely observing ``cached_token`` in auth/info is not
                # enough: authenticate installs the persisted OAuth session into the live sampler.
                # The response can contain account metadata, so validate only its envelope and
                # discard it immediately.
                cached_response = self._require_rpc().request(
                    _RequestMethod.AUTHENTICATE,
                    {"methodId": CACHED_TOKEN_AUTH_METHOD_ID},
                    min(
                        self._method_timeout(_RequestMethod.AUTHENTICATE),
                        CACHED_AUTHENTICATE_TIMEOUT_SECONDS * self._fixture_timeout_scale,
                    ),
                )
                if not isinstance(cached_response, dict) or len(cached_response) > 128:
                    raise AcpProtocolError("grok_cached_auth_invalid")
        except (AcpError, ValueError, GrokSupervisorError) as error:
            self._fail(AcpProtocolError("grok_initialize_failed"), generation)
            raise GrokSupervisorError("grok_initialize_failed") from error

        with self._lock:
            if self._state is not GrokSupervisorState.INITIALIZING or generation != self._generation:
                raise GrokSupervisorError("grok_initialize_failed")
            self._initialize_state = normalized
            if normalized.auth_method_id == CACHED_TOKEN_AUTH_METHOD_ID:
                self._authenticated_method_id = CACHED_TOKEN_AUTH_METHOD_ID
            self._state = GrokSupervisorState.READY
        return normalized

    def add_notification_listener(
        self,
        listener: Callable[[AcpNotification], None],
    ) -> Callable[[], None]:
        return self._require_rpc().add_notification_listener(listener)

    def authenticate(self, request_sequence: int) -> Dict[str, Any]:
        if not isinstance(request_sequence, int) or isinstance(request_sequence, bool) or request_sequence <= 0:
            raise ValueError("request sequence invalid")
        response = self._typed_request(
            _RequestMethod.AUTHENTICATE,
            {
                "methodId": AUTH_METHOD_ID,
                "_meta": {"request_seq": request_sequence},
            },
        )
        with self._lock:
            self._authenticated_method_id = AUTH_METHOD_ID
        return response

    def get_auth_url(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        return self._typed_request(
            _RequestMethod.AUTH_URL,
            {},
            timeout_seconds=timeout_seconds,
        )

    def cancel_auth(self, request_sequence: int) -> Dict[str, Any]:
        if not isinstance(request_sequence, int) or isinstance(request_sequence, bool) or request_sequence <= 0:
            raise ValueError("request sequence invalid")
        return self._typed_request(
            _RequestMethod.AUTH_CANCEL,
            {"request_seq": request_sequence},
        )

    def auth_info(self) -> Dict[str, Any]:
        return self._typed_request(_RequestMethod.AUTH_INFO, {})

    def logout(self) -> Dict[str, Any]:
        response = self._typed_request(_RequestMethod.AUTH_LOGOUT, {"scope": None})
        with self._lock:
            self._authenticated_method_id = None
        return response

    def list_models(self) -> Dict[str, Any]:
        return self._typed_request(_RequestMethod.MODELS_LIST, {})

    def new_session(self, working_directory: str) -> Dict[str, Any]:
        cwd = self._fixed_working_directory(working_directory)
        # Keep session/new on the documented ACP surface. Grok does not define modelId as a
        # session/new _meta field; sending it makes the official CLI reject the whole request.
        # A non-default model is selected through the standard session/set_model method after the
        # session exists.
        return self._typed_request(
            _RequestMethod.SESSION_NEW,
            {"cwd": cwd, "mcpServers": []},
        )

    def load_session(self, session_id: str, working_directory: str) -> Dict[str, Any]:
        return self._typed_request(
            _RequestMethod.SESSION_LOAD,
            {
                "sessionId": opaque_identifier(session_id),
                "cwd": self._fixed_working_directory(working_directory),
                "mcpServers": [],
            },
        )

    def resume_session(self, session_id: str, working_directory: str) -> Dict[str, Any]:
        return self._typed_request(
            _RequestMethod.SESSION_RESUME,
            {
                "sessionId": opaque_identifier(session_id),
                "cwd": self._fixed_working_directory(working_directory),
                "mcpServers": [],
            },
        )

    def set_session_model(self, session_id: str, model_id: str) -> Dict[str, Any]:
        return self._typed_request(
            _RequestMethod.SESSION_SET_MODEL,
            {
                "sessionId": opaque_identifier(session_id),
                "modelId": opaque_identifier(model_id),
            },
        )

    def prompt(self, session_id: str, text: str) -> Dict[str, Any]:
        if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 256 * 1024:
            raise ValueError("prompt invalid")
        return self._typed_request(
            _RequestMethod.SESSION_PROMPT,
            {
                "sessionId": opaque_identifier(session_id),
                "prompt": [{"type": "text", "text": text}],
            },
            require_clean_profile=True,
        )

    @property
    def profile_audit(self) -> GrokProfileAudit:
        return self._require_rpc().profile_audit

    @property
    def prompt_dispatch_count(self) -> int:
        return self._require_rpc().prompt_dispatch_total

    def cancel_session(self, session_id: str) -> None:
        with self._lock:
            if self._state is not GrokSupervisorState.READY:
                raise GrokSupervisorError("grok_not_ready")
        with self._lock:
            generation = self._generation
        try:
            self._require_rpc().notify(
                _RequestMethod.SESSION_CANCEL,
                {"sessionId": opaque_identifier(session_id)},
            )
        except AcpProcessLost as error:
            self._fail(error, generation)
            raise

    def close_session(self, session_id: str) -> Dict[str, Any]:
        return self._typed_request(
            _RequestMethod.SESSION_CLOSE,
            {"sessionId": opaque_identifier(session_id)},
        )

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            if self._state is GrokSupervisorState.STOPPED:
                return
            self._state = GrokSupervisorState.STOPPING
            process = self._process
            rpc = self._rpc
            threads = (self._stdout_thread, self._stderr_thread, self._wait_thread)
        if rpc is not None:
            rpc.fail_all(AcpStopped())
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass
            try:
                process.terminate()
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=timeout_seconds)
            except OSError:
                pass
            self._close_streams(process)
        for thread in threads:
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=min(timeout_seconds, 1.0))
        with self._lock:
            self._process = None
            self._rpc = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._wait_thread = None
            self._initialize_state = None
            self._authenticated_method_id = None
            self._state = GrokSupervisorState.STOPPED

    def _typed_request(
        self,
        method: _RequestMethod,
        params: Dict[str, Any],
        *,
        require_clean_profile: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if method is _RequestMethod.INITIALIZE:
                permitted = self._state is GrokSupervisorState.INITIALIZING
            else:
                permitted = self._state is GrokSupervisorState.READY
            if not permitted:
                raise GrokSupervisorError("grok_not_ready")
            generation = self._generation
        try:
            method_timeout = self._method_timeout(method)
            if timeout_seconds is not None:
                if (
                    not isinstance(timeout_seconds, (int, float))
                    or isinstance(timeout_seconds, bool)
                    or timeout_seconds <= 0
                    or timeout_seconds > method_timeout
                ):
                    raise ValueError("Grok request timeout invalid")
                method_timeout = float(timeout_seconds)
            return self._require_rpc().request(
                method,
                params,
                method_timeout,
                require_clean_profile=require_clean_profile,
            )
        except (AcpTimeout, AcpProcessLost) as error:
            # A timed-out ID can still arrive later. Retire the whole generation so a late
            # response can never be confused with subsequent work and no request is replayed.
            self._fail(error, generation)
            raise

    def _method_timeout(self, method: _RequestMethod) -> float:
        return _METHOD_TIMEOUT_SECONDS[method] * self._fixture_timeout_scale

    def _spawn_process(self, spawn_options: Dict[str, Any]) -> subprocess.Popen[bytes]:
        if not self._spec.validate_policy:
            return self._open_process(spawn_options)
        owner = self._spawn_owner_thread
        if owner is None or not owner.is_alive():
            raise OSError("Grok spawn owner unavailable")
        completed = threading.Event()
        outcome: Dict[str, Any] = {}
        self._spawn_requests.put((spawn_options, outcome, completed))
        completed.wait()
        error = outcome.get("error")
        if error is not None:
            if isinstance(error, (OSError, ValueError)):
                raise error
            raise OSError("Grok spawn owner failed") from error
        process = outcome.get("process")
        if process is None:
            raise OSError("Grok spawn owner returned no process")
        return process

    def _spawn_owner_loop(self) -> None:
        while True:
            spawn_options, outcome, completed = self._spawn_requests.get()
            try:
                outcome["process"] = self._open_process(spawn_options)
            except Exception as error:
                outcome["error"] = error
            finally:
                completed.set()

    def _open_process(self, spawn_options: Dict[str, Any]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            self._spec.command,
            env=dict(self._spec.environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            **spawn_options,
        )

    def _fixed_working_directory(self, value: str) -> str:
        if not isinstance(value, str) or value != self._spec.working_directory:
            raise ValueError("Grok session working directory is not fixed")
        return value

    def _write_stdin(self, payload: bytes) -> None:
        with self._write_lock:
            with self._lock:
                process = self._process
                if process is None or process.stdin is None or self._state not in (
                    GrokSupervisorState.INITIALIZING,
                    GrokSupervisorState.READY,
                ):
                    raise BrokenPipeError("Grok ACP stdin unavailable")
                stream = process.stdin
            stream.write(payload)
            stream.flush()

    def _read_stdout(self, generation: int, process: subprocess.Popen[bytes]) -> None:
        decoder = JsonlDecoder(self._fixture_max_jsonl_line_bytes)
        try:
            if process.stdout is None:
                raise JsonlProtocolError("stdout unavailable")
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    decoder.finish()
                    break
                for message in decoder.feed(chunk):
                    self._require_rpc().handle_object(message, generation)
        except (JsonlProtocolError, AcpProtocolError):
            self._fail(AcpProtocolError(), generation)
        except (OSError, ValueError, GrokSupervisorError):
            self._fail(AcpProcessLost(), generation)
        finally:
            with self._lock:
                active = generation == self._generation and self._state not in (
                    GrokSupervisorState.STOPPING,
                    GrokSupervisorState.STOPPED,
                    GrokSupervisorState.FAILED,
                )
            if active:
                self._fail(AcpProcessLost(), generation)

    def _read_stderr(self, process: subprocess.Popen[bytes]) -> None:
        try:
            if process.stderr is None:
                return
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    return
                self._stderr.append(chunk)
        except OSError:
            return

    def _wait_for_exit(self, generation: int, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait()
        except OSError:
            return
        finally:
            self._exit_event.set()
        with self._lock:
            active = generation == self._generation and self._state not in (
                GrokSupervisorState.STOPPING,
                GrokSupervisorState.STOPPED,
                GrokSupervisorState.FAILED,
            )
        if active:
            self._fail(AcpProcessLost(), generation)

    def _fail(self, error: AcpError, generation: int) -> None:
        with self._lock:
            if generation != self._generation or self._state in (
                GrokSupervisorState.FAILED,
                GrokSupervisorState.STOPPED,
                GrokSupervisorState.STOPPING,
            ):
                return
            self._state = GrokSupervisorState.FAILED
            self._authenticated_method_id = None
            rpc = self._rpc
            process = self._process
        if rpc is not None:
            rpc.fail_all(error)
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    def _require_rpc(self) -> _AcpMultiplexer:
        with self._lock:
            if self._rpc is None:
                raise GrokSupervisorError("grok_not_ready")
            return self._rpc

    @staticmethod
    def _close_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except OSError:
                pass
