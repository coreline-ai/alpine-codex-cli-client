"""Small provider-neutral values shared by Codex and Grok adapters.

These values deliberately contain no executable path, process environment, raw protocol method,
credential, bearer token, or account identifier. Backend-specific protocol objects stay inside the
adapter implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Optional, Protocol, Tuple


class AgentId(str, Enum):
    CODEX = "codex"
    GROK = "grok"

    @classmethod
    def parse_exact(cls, value: object) -> "AgentId":
        if not isinstance(value, str):
            raise ValueError("invalid_agent")
        try:
            return cls(value)
        except ValueError as error:
            raise ValueError("invalid_agent") from error


@dataclass(frozen=True)
class AgentCapabilities:
    device_oauth: bool
    dynamic_models: bool
    streaming: bool
    stop: bool
    resume: bool


@dataclass(frozen=True)
class AgentActivity:
    active_login: bool = False
    active_turn: bool = False


@dataclass(frozen=True)
class AgentAccount:
    agent_id: AgentId
    authenticated: bool
    requires_auth: bool


@dataclass(frozen=True)
class AgentModel:
    agent_id: AgentId
    model_id: str
    display_name: str
    is_default: bool
    modalities: Tuple[str, ...] = ("text",)


@dataclass(frozen=True)
class AgentLogin:
    agent_id: AgentId
    request_id: str
    state: str
    verification_url: Optional[str] = None
    user_code: Optional[str] = None
    expires_in_seconds: Optional[int] = None
    poll_interval_seconds: Optional[int] = None


@dataclass(frozen=True)
class AgentConversationBinding:
    agent_id: AgentId
    conversation_id: str
    backend_session_id: str
    model_id: str
    process_generation: int


@dataclass(frozen=True)
class AgentTurnDiagnostics:
    """Content-free terminal counters suitable for authenticated redacted evidence."""

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


@dataclass(frozen=True)
class AgentTurnEvent:
    agent_id: AgentId
    request_id: str
    event_type: str
    text: str = ""
    code: Optional[str] = None
    conversation_id: Optional[str] = None
    diagnostics: Optional[AgentTurnDiagnostics] = None


@dataclass(frozen=True)
class AgentTurnHandle:
    agent_id: AgentId
    request_id: str
    conversation_id: str
    model_id: str
    _native_handle: Any = field(repr=False, compare=False)


class AgentAdapter(Protocol):
    """Fixed operations an Agent backend may expose to the router."""

    @property
    def agent_id(self) -> AgentId: ...

    @property
    def capabilities(self) -> AgentCapabilities: ...

    def is_ready(self) -> bool: ...

    def activity(self) -> AgentActivity: ...

    def activate(self) -> None: ...

    def deactivate(self) -> None: ...

    def account(self) -> AgentAccount: ...

    def start_device_login(self) -> AgentLogin: ...

    def login_status(self, request_id: str) -> AgentLogin: ...

    def cancel_login(self, request_id: str) -> AgentLogin: ...

    def logout(self) -> None: ...

    def models(self) -> Tuple[AgentModel, ...]: ...

    def start_turn(self, value: dict[str, Any]) -> AgentTurnHandle: ...

    def stream(self, handle: AgentTurnHandle) -> Iterator[AgentTurnEvent]: ...

    def interrupt(self, request_id: str) -> None: ...
