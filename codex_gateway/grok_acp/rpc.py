"""Generation-scoped, bounded JSON-RPC multiplexing for Grok ACP."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import threading
from typing import Any, Callable, Deque, Dict, Optional, Set

from .contract import NOTIFICATION_METHODS, TERMINAL_NOTIFICATION_METHOD, _RequestMethod


# Closed, content-free categories emitted by the pinned CLI's persistence boundary. The adjacent
# human-readable ``message`` and ``detail`` values are deliberately never retained.
REMOTE_ERROR_CATEGORIES = frozenset(
    {
        "FS_DISK_QUOTA_EXCEEDED",
        "FS_NOT_FOUND",
        "FS_PERMISSION_DENIED",
        "FS_OTHER",
        "SESSION_AGENT_CONFIG_INVALID",
        "SESSION_AGENT_IO_FAILED",
        "SESSION_AGENT_MISSING_FIELD",
        "SESSION_AGENT_PARSE_FAILED",
        "SESSION_AGENT_RUNTIME_BUILD_FAILED",
        "SESSION_AGENT_TEMPLATE_FAILED",
        "SESSION_AGENT_TOOL_FAILED",
        "SESSION_AGENT_UNKNOWN_TOOL_OVERRIDE",
        "SESSION_DIR_FAILED",
        "SESSION_INITIALIZATION_FAILED",
        "SESSION_THREAD_PANIC",
        "SESSION_THREAD_SPAWN_FAILED",
        "WORKSPACE_INIT_FAILED",
    }
)


class AcpError(RuntimeError):
    code = "grok_acp_error"

    def __init__(self, code: Optional[str] = None) -> None:
        self.code = code or self.code
        super().__init__(self.code)


class AcpProtocolError(AcpError):
    code = "grok_acp_protocol_error"


class AcpTimeout(AcpError):
    code = "grok_acp_timeout"


class AcpPendingLimit(AcpError):
    code = "grok_acp_pending_limit"


class AcpProcessLost(AcpError):
    code = "grok_acp_process_lost"


class AcpStopped(AcpError):
    code = "grok_acp_stopped"


class AcpRemoteError(AcpError):
    code = "grok_acp_remote_error"

    def __init__(
        self,
        remote_code: Optional[int] = None,
        remote_category: Optional[str] = None,
    ) -> None:
        # Retain only the bounded JSON-RPC integer discriminator and one closed upstream category.
        # Remote message/detail fields may contain provider, account, request, path, or prompt
        # material and are always discarded.
        self.remote_code = remote_code
        self.remote_category = remote_category if remote_category in REMOTE_ERROR_CATEGORIES else None
        super().__init__()


@dataclass(frozen=True)
class AcpNotification:
    generation: int
    sequence: int
    method: str
    params: Dict[str, Any]


@dataclass(frozen=True)
class GrokProfileAudit:
    """Content-free counts of forbidden chat-profile activity in one process generation."""

    tool_event_count: int = 0
    subagent_event_count: int = 0
    mcp_event_count: int = 0
    filesystem_event_count: int = 0
    terminal_event_count: int = 0

    @property
    def clean(self) -> bool:
        return not any(
            (
                self.tool_event_count,
                self.subagent_event_count,
                self.mcp_event_count,
                self.filesystem_event_count,
                self.terminal_event_count,
            )
        )


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict[str, Any]] = None
    error: Optional[AcpError] = None


class _AcpMultiplexer:
    """The only raw writer; accepts the private method enum, never strings."""

    def __init__(
        self,
        write_bytes: Callable[[bytes], None],
        generation: int,
        max_pending: int = 16,
        max_completed_ids: int = 128,
        max_terminal_keys: int = 128,
    ) -> None:
        if generation <= 0 or max_pending <= 0 or max_completed_ids <= 0 or max_terminal_keys <= 0:
            raise ValueError("invalid Grok ACP bounds")
        self._write_bytes = write_bytes
        self._generation = generation
        self._max_pending = max_pending
        self._lock = threading.RLock()
        self._next_id = 1
        self._next_sequence = 1
        self._pending: Dict[int, _Pending] = {}
        self._completed: Deque[int] = deque(maxlen=max_completed_ids)
        self._completed_set: Set[int] = set()
        self._terminal_keys: Deque[tuple[str, str]] = deque(maxlen=max_terminal_keys)
        self._terminal_key_set: Set[tuple[str, str]] = set()
        self._listeners: Dict[int, Callable[[AcpNotification], None]] = {}
        self._next_listener_id = 1
        self._terminal_error: Optional[AcpError] = None
        self._stale_generation_count = 0
        self._discarded_notification_count = 0
        self._profile_audit = GrokProfileAudit()
        self._prompt_dispatch_total = 0

    def request(
        self,
        method: _RequestMethod,
        params: Dict[str, Any],
        timeout_seconds: float,
        *,
        require_clean_profile: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(method, _RequestMethod) or not isinstance(params, dict):
            raise ValueError("Grok ACP method is not allowlisted")
        if method is _RequestMethod.SESSION_CANCEL:
            raise ValueError("session cancel is notification-only")
        if require_clean_profile and method is not _RequestMethod.SESSION_PROMPT:
            raise ValueError("chat profile gate is prompt-only")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        with self._lock:
            if self._terminal_error is not None:
                raise self._terminal_error
            if require_clean_profile and not self._profile_audit.clean:
                raise AcpProtocolError("grok_chat_profile_violation")
            if len(self._pending) >= self._max_pending:
                raise AcpPendingLimit()
            request_id = self._next_id
            self._next_id += 1
            pending = _Pending()
            self._pending[request_id] = pending
            payload = _encode_request(request_id, method.value, params)
            try:
                self._write_bytes(payload)
                if method is _RequestMethod.SESSION_PROMPT:
                    self._prompt_dispatch_total += 1
            except Exception as error:
                self._pending.pop(request_id, None)
                self._record_completed(request_id)
                raise AcpProcessLost() from error

        if not pending.event.wait(timeout_seconds):
            with self._lock:
                current = self._pending.pop(request_id, None)
                if current is pending:
                    self._record_completed(request_id)
                    raise AcpTimeout()
            pending.event.wait(0)
        if pending.error is not None:
            raise pending.error
        if pending.result is None:
            raise AcpProtocolError("grok_acp_response_missing")
        return pending.result

    def notify(self, method: _RequestMethod, params: Dict[str, Any]) -> None:
        if method is not _RequestMethod.SESSION_CANCEL or not isinstance(params, dict):
            raise ValueError("Grok ACP notification is not allowlisted")
        with self._lock:
            if self._terminal_error is not None:
                raise self._terminal_error
            try:
                self._write_bytes(_encode_notification(method.value, params))
            except Exception as error:
                raise AcpProcessLost() from error

    def add_notification_listener(
        self,
        listener: Callable[[AcpNotification], None],
    ) -> Callable[[], None]:
        if not callable(listener):
            raise ValueError("notification listener must be callable")
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener

        def remove() -> None:
            with self._lock:
                self._listeners.pop(listener_id, None)

        return remove

    def handle_object(self, message: Dict[str, Any], generation: int) -> None:
        if generation != self._generation:
            with self._lock:
                self._stale_generation_count += 1
            return
        if message.get("jsonrpc") != "2.0":
            raise AcpProtocolError("grok_acp_version_invalid")
        if "id" in message:
            if "method" in message:
                self._record_profile_event(message.get("method"), message.get("params"))
                raise AcpProtocolError("grok_acp_reverse_request_forbidden")
            self._handle_response(message)
            return
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise AcpProtocolError("grok_acp_message_shape_invalid")
        self._record_profile_event(method, params)
        if method not in NOTIFICATION_METHODS:
            with self._lock:
                self._discarded_notification_count += 1
            return
        if len(params) > 128:
            raise AcpProtocolError("grok_acp_notification_too_wide")
        if method == TERMINAL_NOTIFICATION_METHOD and not self._accept_terminal(params):
            return
        with self._lock:
            notification = AcpNotification(
                generation=self._generation,
                sequence=self._next_sequence,
                method=method,
                params=dict(params),
            )
            self._next_sequence += 1
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            try:
                listener(notification)
            except Exception:
                continue

    def fail_all(self, error: AcpError) -> None:
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

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def stale_generation_count(self) -> int:
        with self._lock:
            return self._stale_generation_count

    @property
    def discarded_notification_count(self) -> int:
        with self._lock:
            return self._discarded_notification_count

    @property
    def profile_audit(self) -> GrokProfileAudit:
        with self._lock:
            return self._profile_audit

    @property
    def prompt_dispatch_total(self) -> int:
        with self._lock:
            return self._prompt_dispatch_total

    def _record_profile_event(self, method: Any, params: Any) -> None:
        category = _profile_event_category(method, params)
        if category is None:
            return
        with self._lock:
            current = self._profile_audit
            values = {
                "tool_event_count": current.tool_event_count,
                "subagent_event_count": current.subagent_event_count,
                "mcp_event_count": current.mcp_event_count,
                "filesystem_event_count": current.filesystem_event_count,
                "terminal_event_count": current.terminal_event_count,
            }
            values[category] += 1
            self._profile_audit = GrokProfileAudit(**values)
        raise AcpProtocolError("grok_chat_profile_violation")

    def _handle_response(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id <= 0:
            raise AcpProtocolError("grok_acp_response_id_invalid")
        has_result = "result" in message
        has_error = "error" in message
        if has_result == has_error:
            raise AcpProtocolError("grok_acp_response_shape_invalid")
        with self._lock:
            pending = self._pending.pop(request_id, None)
            if pending is None:
                if request_id in self._completed_set:
                    raise AcpProtocolError("grok_acp_response_id_duplicate")
                raise AcpProtocolError("grok_acp_response_id_unknown")
            self._record_completed(request_id)
            if has_result:
                result = message.get("result")
                if not isinstance(result, dict) or len(result) > 128:
                    pending.error = AcpProtocolError("grok_acp_result_object_required")
                else:
                    pending.result = dict(result)
            else:
                remote = message.get("error")
                remote_code = remote.get("code") if isinstance(remote, dict) else None
                if (
                    not isinstance(remote_code, int)
                    or isinstance(remote_code, bool)
                    or remote_code < -99999
                    or remote_code > 99999
                ):
                    remote_code = None
                remote_data = remote.get("data") if isinstance(remote, dict) else None
                remote_category = _remote_error_category(remote_data)
                pending.error = AcpRemoteError(remote_code, remote_category)
            pending.event.set()

    def _accept_terminal(self, params: Dict[str, Any]) -> bool:
        session_id = params.get("sessionId")
        prompt_id = params.get("promptId")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            raise AcpProtocolError("grok_acp_terminal_invalid")
        if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 512:
            raise AcpProtocolError("grok_acp_terminal_invalid")
        key = (session_id, prompt_id)
        with self._lock:
            if key in self._terminal_key_set:
                return False
            if len(self._terminal_keys) == self._terminal_keys.maxlen:
                expired = self._terminal_keys.popleft()
                self._terminal_key_set.discard(expired)
            self._terminal_keys.append(key)
            self._terminal_key_set.add(key)
        return True

    def _record_completed(self, request_id: int) -> None:
        if len(self._completed) == self._completed.maxlen:
            expired = self._completed.popleft()
            self._completed_set.discard(expired)
        self._completed.append(request_id)
        self._completed_set.add(request_id)


def _encode_request(request_id: int, method: str, params: Dict[str, Any]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _encode_notification(method: str, params: Dict[str, Any]) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _remote_error_category(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        value = data.get("code")
        return value if value in REMOTE_ERROR_CATEGORIES else None
    if not isinstance(data, str) or len(data) > 4096:
        return None
    if data.startswith("failed to spawn session thread:"):
        return "SESSION_THREAD_SPAWN_FAILED"
    if data == "session thread panicked during initialization":
        return "SESSION_THREAD_PANIC"
    initialization_categories = (
        (
            "session initialization failed: failed to build session runtime:",
            "SESSION_AGENT_RUNTIME_BUILD_FAILED",
        ),
        (
            "session initialization failed: failed to parse agent definition:",
            "SESSION_AGENT_PARSE_FAILED",
        ),
        (
            "session initialization failed: missing required field in agent definition:",
            "SESSION_AGENT_MISSING_FIELD",
        ),
        (
            "session initialization failed: tool name override references nonexistent tool",
            "SESSION_AGENT_UNKNOWN_TOOL_OVERRIDE",
        ),
        (
            "session initialization failed: IO error during agent construction:",
            "SESSION_AGENT_IO_FAILED",
        ),
        (
            "session initialization failed: template rendering error:",
            "SESSION_AGENT_TEMPLATE_FAILED",
        ),
        (
            "session initialization failed: tool error:",
            "SESSION_AGENT_TOOL_FAILED",
        ),
        (
            "session initialization failed: invalid configuration:",
            "SESSION_AGENT_CONFIG_INVALID",
        ),
    )
    for prefix, category in initialization_categories:
        if data.startswith(prefix):
            return category
    if data.startswith("session initialization failed:"):
        return "SESSION_INITIALIZATION_FAILED"
    if data.startswith("failed to create session dir:"):
        return "SESSION_DIR_FAILED"
    if data.startswith("Local workspace initialization failed; cannot create session."):
        return "WORKSPACE_INIT_FAILED"
    return None


def _profile_event_category(method: Any, params: Any) -> Optional[str]:
    if not isinstance(method, str):
        return None
    lowered = method.lower()
    if "subagent" in lowered:
        return "subagent_event_count"
    if lowered.startswith(("x.ai/mcp/", "mcp/")):
        return "mcp_event_count"
    if lowered.startswith(("x.ai/fs/", "x.ai/fs_", "fs/")):
        return "filesystem_event_count"
    if lowered.startswith(("x.ai/terminal/", "terminal/")):
        return "terminal_event_count"
    if lowered == "session/request_permission" or lowered.startswith("x.ai/tool/"):
        return "tool_event_count"
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    if not isinstance(update, dict):
        return None
    update_type = update.get("sessionUpdate")
    if not isinstance(update_type, str):
        return None
    normalized = update_type.lower()
    if normalized in ("tool_call", "tool_call_update", "tool_call_delta_chunk"):
        return "tool_event_count"
    if "subagent" in normalized:
        return "subagent_event_count"
    if "mcp" in normalized:
        return "mcp_event_count"
    if normalized.startswith(("fs_", "filesystem_")):
        return "filesystem_event_count"
    if normalized.startswith(("terminal_", "shell_")):
        return "terminal_event_count"
    if normalized.startswith(("task_", "scheduled_task_", "monitor_")):
        return "tool_event_count"
    return None
