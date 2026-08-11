"""Thread-safe bounded JSON-RPC request/response multiplexing for one process generation."""

from collections import deque
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, Optional, Set


class RpcError(Exception):
    code = "rpc_error"


class RpcProtocolError(RpcError):
    code = "rpc_protocol_error"


class RpcTimeout(RpcError):
    code = "rpc_timeout"


class RpcProcessLost(RpcError):
    code = "codex_process_lost"


class RpcStopped(RpcError):
    code = "codex_process_stopped"


class RpcRemoteError(RpcError):
    code = "rpc_remote_error"

    def __init__(self, stable_code: str = "rpc_remote_error") -> None:
        self.code = stable_code if isinstance(stable_code, str) and stable_code else self.code
        super().__init__(self.code)


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict[str, Any]] = None
    error: Optional[RpcError] = None


class JsonRpcMultiplexer:
    """Owns monotonic request IDs and accepts only valid response/notification shapes."""

    def __init__(
        self,
        write_bytes: Callable[[bytes], None],
        max_pending: int = 32,
        max_completed_ids: int = 128,
    ) -> None:
        if max_pending <= 0 or max_completed_ids <= 0:
            raise ValueError("RPC bounds must be positive")
        self._write_bytes = write_bytes
        self._max_pending = max_pending
        self._lock = threading.RLock()
        self._next_id = 1
        self._pending: Dict[int, _PendingRequest] = {}
        self._completed: Deque[int] = deque(maxlen=max_completed_ids)
        self._completed_set: Set[int] = set()
        self._notification_listeners: Dict[int, Callable[[str, Dict[str, Any]], None]] = {}
        self._next_listener_id = 1
        self._terminal_error: Optional[RpcError] = None

    def request(self, method: str, params: Dict[str, Any], timeout_seconds: float) -> Dict[str, Any]:
        if not isinstance(method, str) or not method or not isinstance(params, dict):
            raise ValueError("invalid JSON-RPC request")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            if self._terminal_error is not None:
                raise self._terminal_error
            if len(self._pending) >= self._max_pending:
                raise RpcProtocolError("rpc_pending_limit")
            request_id = self._next_id
            self._next_id += 1
            pending = _PendingRequest()
            self._pending[request_id] = pending
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            try:
                self._write_bytes(payload)
            except Exception as error:
                self._pending.pop(request_id, None)
                self._record_completed(request_id)
                raise RpcProcessLost() from error

        if not pending.event.wait(timeout_seconds):
            with self._lock:
                current = self._pending.pop(request_id, None)
                if current is pending:
                    self._record_completed(request_id)
                    raise RpcTimeout()
            pending.event.wait(0)
        if pending.error is not None:
            raise pending.error
        if pending.result is None:
            raise RpcProtocolError("rpc_response_missing_result")
        return pending.result

    def add_notification_listener(
        self,
        listener: Callable[[str, Dict[str, Any]], None],
    ) -> Callable[[], None]:
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._notification_listeners[listener_id] = listener

        def remove() -> None:
            with self._lock:
                self._notification_listeners.pop(listener_id, None)

        return remove

    def handle_object(self, message: Dict[str, Any]) -> None:
        if message.get("jsonrpc") not in (None, "2.0"):
            raise RpcProtocolError("rpc_version_invalid")
        if "id" in message:
            self._handle_response(message)
            return
        method = message.get("method")
        if not isinstance(method, str) or not method:
            raise RpcProtocolError("rpc_message_shape_invalid")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise RpcProtocolError("rpc_notification_params_invalid")
        with self._lock:
            listeners: Iterable[Callable[[str, Dict[str, Any]], None]] = tuple(
                self._notification_listeners.values()
            )
        for listener in listeners:
            listener(method, params)

    def fail_all(self, error: RpcError) -> None:
        with self._lock:
            if self._terminal_error is not None:
                return
            self._terminal_error = error
            pending = tuple(self._pending.items())
            self._pending.clear()
            for request_id, item in pending:
                self._record_completed(request_id)
                item.error = error
                item.event.set()

    def _handle_response(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id <= 0:
            raise RpcProtocolError("rpc_response_id_invalid")
        has_result = "result" in message
        has_error = "error" in message
        if has_result == has_error:
            raise RpcProtocolError("rpc_response_shape_invalid")
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                if request_id in self._completed_set:
                    raise RpcProtocolError("rpc_response_id_duplicate")
                raise RpcProtocolError("rpc_response_id_unknown")
            self._record_completed(request_id)
            if has_result:
                result = message["result"]
                if not isinstance(result, dict):
                    pending.error = RpcProtocolError("rpc_result_object_required")
                else:
                    pending.result = result
            else:
                error = message["error"]
                stable_code = "rpc_remote_error"
                if isinstance(error, dict) and isinstance(error.get("code"), str):
                    stable_code = error["code"]
                pending.error = RpcRemoteError(stable_code)
            pending.event.set()

    def _record_completed(self, request_id: int) -> None:
        if len(self._completed) == self._completed.maxlen:
            expired = self._completed.popleft()
            self._completed_set.discard(expired)
        self._completed.append(request_id)
        self._completed_set.add(request_id)
