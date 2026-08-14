"""Lifecycle owner for one official ``codex app-server`` JSONL child process."""

import os
import subprocess
import threading
import time
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence

from .jsonl import JsonlDecoder, JsonlProtocolError
from .redaction import RedactingRingBuffer
from .rpc import JsonRpcMultiplexer, RpcError, RpcProcessLost, RpcProtocolError, RpcStopped, RpcTimeout


class SupervisorState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class SupervisorError(Exception):
    """A stable lifecycle error. It intentionally contains no process output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AppServerSupervisor:
    """Starts, initializes, owns and stops a single app-server process generation.

    The class is deliberately transport-only: callers issue known JSON-RPC methods through
    :meth:`request`; credential material is owned by the official CLI HOME directory and never
    enters this object's diagnostics or public state.
    """

    def __init__(
        self,
        command: Sequence[str],
        working_directory: str,
        environment: Optional[Mapping[str, str]] = None,
        max_jsonl_line_bytes: int = 1024 * 1024,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("app-server command must be a non-empty argv sequence")
        if not os.path.isabs(working_directory):
            raise ValueError("app-server working directory must be absolute")
        self._command = tuple(command)
        self._working_directory = working_directory
        self._environment = dict(environment or {})
        if any(key not in _ALLOWED_ENVIRONMENT_KEYS for key in self._environment):
            raise ValueError("unsupported app-server environment key")
        self._max_jsonl_line_bytes = max_jsonl_line_bytes
        self._stderr = RedactingRingBuffer(max_stderr_bytes)
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = SupervisorState.STOPPED
        self._generation = 0
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._rpc: Optional[JsonRpcMultiplexer] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._wait_thread: Optional[threading.Thread] = None
        self._initialize_response: Optional[Dict[str, Any]] = None

    @property
    def state(self) -> SupervisorState:
        with self._lock:
            return self._state

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def initialize_response(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._initialize_response) if self._initialize_response is not None else None

    @property
    def stderr_diagnostic(self) -> str:
        return self._stderr.snapshot()

    def start(self, client_name: str, client_version: str, timeout_seconds: float = 15.0) -> Dict[str, Any]:
        if not client_name or not client_version:
            raise ValueError("client name and version are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        deadline = time.monotonic() + timeout_seconds
        with self._lock:
            if self._state is not SupervisorState.STOPPED:
                raise SupervisorError("supervisor_not_stopped")
            self._state = SupervisorState.STARTING
            self._generation += 1
            self._initialize_response = None
            try:
                self._process = subprocess.Popen(
                    self._command,
                    cwd=self._working_directory,
                    env=self._build_environment(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    close_fds=True,
                )
            except (OSError, ValueError) as error:
                self._state = SupervisorState.FAILED
                raise SupervisorError("codex_process_start_failed") from error
            if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
                self._state = SupervisorState.FAILED
                raise SupervisorError("codex_process_stdio_unavailable")
            self._rpc = JsonRpcMultiplexer(self._write_stdin)
            self._stdout_thread = threading.Thread(target=self._read_stdout, name="codex-app-server-stdout", daemon=True)
            self._stderr_thread = threading.Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True)
            self._wait_thread = threading.Thread(target=self._wait_for_exit, name="codex-app-server-wait", daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()
            self._wait_thread.start()
            self._state = SupervisorState.INITIALIZING

        try:
            response = self._require_rpc().request(
                "initialize",
                {
                    "clientInfo": {"name": client_name, "version": client_version},
                    "capabilities": {
                        "experimentalApi": False,
                        "optOutNotificationMethods": [],
                    },
                },
                self._remaining_timeout(deadline),
            )
            self._validate_initialize_response(response)
            # The pinned app-server contract declares this post-initialize client notification.
            # It has no parameters and carries no capability, credential, or account material.
            self._write_stdin(b'{"jsonrpc":"2.0","method":"initialized"}\n')
            # Readiness is published only after an ordered post-initialize request completes.
            # This acts as a protocol barrier: a duplicate/unknown initialize response or child
            # exit already queued on stdout must fail the generation before start() can return.
            self._require_rpc().request("account/read", {}, self._remaining_timeout(deadline))
        except RpcError as error:
            self._fail(error)
            raise SupervisorError("codex_initialize_failed") from error
        except Exception as error:
            self._fail(RpcProtocolError("initialize_response_invalid"))
            raise SupervisorError("codex_initialize_failed") from error
        with self._lock:
            if self._state is not SupervisorState.INITIALIZING:
                raise SupervisorError("codex_initialize_failed")
            self._initialize_response = dict(response)
            self._state = SupervisorState.READY
        return dict(response)

    def request(self, method: str, params: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
        with self._lock:
            if self._state is not SupervisorState.READY:
                raise SupervisorError("codex_not_ready")
        return self._require_rpc().request(method, params, timeout_seconds)

    def add_notification_listener(self, listener):
        return self._require_rpc().add_notification_listener(listener)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            if self._state is SupervisorState.STOPPED:
                return
            process = self._process
            rpc = self._rpc
            self._state = SupervisorState.STOPPING
        if rpc is not None:
            rpc.fail_all(RpcStopped())
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
        with self._lock:
            self._process = None
            self._rpc = None
            self._state = SupervisorState.STOPPED

    def _build_environment(self) -> Dict[str, str]:
        # Do not inherit the Python/Android process environment. This confines the official CLI
        # to its app-private HOME and avoids passing unrelated host variables into the child.
        environment = {"PATH": DEFAULT_PATH}
        environment.update(self._environment)
        return environment

    def _write_stdin(self, payload: bytes) -> None:
        with self._write_lock:
            with self._lock:
                process = self._process
                if process is None or process.stdin is None or self._state not in (
                    SupervisorState.INITIALIZING,
                    SupervisorState.READY,
                ):
                    raise BrokenPipeError("app-server stdin unavailable")
                stdin = process.stdin
            stdin.write(payload)
            stdin.flush()

    def _read_stdout(self) -> None:
        decoder = JsonlDecoder(self._max_jsonl_line_bytes)
        try:
            with self._lock:
                process = self._process
                stdout = process.stdout if process is not None else None
            if stdout is None:
                raise JsonlProtocolError("stdout_unavailable")
            while True:
                chunk = stdout.read(64 * 1024)
                if not chunk:
                    decoder.finish()
                    break
                for message in decoder.feed(chunk):
                    self._require_rpc().handle_object(message)
        except (JsonlProtocolError, RpcProtocolError) as error:
            self._fail(error)
        except (OSError, ValueError) as error:
            self._fail(RpcProcessLost())
        finally:
            with self._lock:
                state = self._state
            if state not in (SupervisorState.STOPPING, SupervisorState.STOPPED, SupervisorState.FAILED):
                self._fail(RpcProcessLost())

    def _read_stderr(self) -> None:
        try:
            with self._lock:
                process = self._process
                stderr = process.stderr if process is not None else None
            if stderr is None:
                return
            while True:
                chunk = stderr.read(4096)
                if not chunk:
                    return
                self._stderr.append(chunk)
        except OSError:
            return

    def _wait_for_exit(self) -> None:
        with self._lock:
            process = self._process
        if process is None:
            return
        try:
            process.wait()
        except OSError:
            return
        with self._lock:
            state = self._state
        if state not in (SupervisorState.STOPPING, SupervisorState.STOPPED, SupervisorState.FAILED):
            self._fail(RpcProcessLost())

    def _fail(self, error: RpcError) -> None:
        with self._lock:
            if self._state in (SupervisorState.FAILED, SupervisorState.STOPPED, SupervisorState.STOPPING):
                return
            self._state = SupervisorState.FAILED
            rpc = self._rpc
            process = self._process
        if rpc is not None:
            rpc.fail_all(error)
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    @staticmethod
    def _close_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except OSError:
                pass

    def _require_rpc(self) -> JsonRpcMultiplexer:
        with self._lock:
            if self._rpc is None:
                raise SupervisorError("codex_not_ready")
            return self._rpc

    @staticmethod
    def _validate_initialize_response(response: Dict[str, Any]) -> None:
        required = ("codexHome", "platformFamily", "platformOs", "userAgent")
        if any(not isinstance(response.get(field), str) or not response[field] for field in required):
            raise ValueError("initialize response is incomplete")

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RpcTimeout()
        return remaining


DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_ALLOWED_ENVIRONMENT_KEYS = frozenset({"HOME", "PATH", "TMPDIR", "LANG", "TERM"})
