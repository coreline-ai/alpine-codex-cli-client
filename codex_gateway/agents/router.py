"""Single-selected-Agent state machine independent from process implementation."""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from typing import Dict, Iterable, Optional

from codex_gateway.agents.contracts import AgentActivity, AgentAdapter, AgentId


OPAQUE_OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class AgentRoutingError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AgentOperation:
    agent_id: AgentId
    request_id: str


@dataclass(frozen=True)
class AgentRouterState:
    selected_agent: AgentId
    gateway_ready: bool
    backend_ready: bool
    switching: bool
    active_login: Optional[AgentOperation]
    active_turn: Optional[AgentOperation]
    stable_error: Optional[str]


class AgentRouter:
    """Owns selected Agent and the one-login/one-turn/switch invariants."""

    def __init__(self, adapters: Iterable[AgentAdapter], selected_agent: AgentId = AgentId.CODEX) -> None:
        mapped: Dict[AgentId, AgentAdapter] = {}
        for adapter in adapters:
            if not isinstance(adapter.agent_id, AgentId) or adapter.agent_id in mapped:
                raise ValueError("invalid_agent_adapter")
            mapped[adapter.agent_id] = adapter
        if selected_agent not in mapped:
            raise ValueError("selected_agent_unavailable")
        self._adapters = mapped
        self._selected_agent = selected_agent
        self._active_login: Optional[AgentOperation] = None
        self._active_turn: Optional[AgentOperation] = None
        self._switching = False
        self._stable_error: Optional[str] = None
        self._lock = threading.RLock()

    def state(self) -> AgentRouterState:
        with self._lock:
            selected = self._selected_agent
            adapter = self._adapters[selected]
            return AgentRouterState(
                selected_agent=selected,
                gateway_ready=True,
                backend_ready=adapter.is_ready(),
                switching=self._switching,
                active_login=self._active_login,
                active_turn=self._active_turn,
                stable_error=self._stable_error,
            )

    def available_agents(self) -> tuple[AgentId, ...]:
        return tuple(self._adapters)

    def selected_adapter(self) -> AgentAdapter:
        with self._lock:
            return self._adapters[self._selected_agent]

    def adapter_for(self, value: object, *, require_selected: bool = True) -> AgentAdapter:
        parsed = self._parse_agent(value)
        with self._lock:
            adapter = self._adapters.get(parsed)
            if adapter is None:
                raise AgentRoutingError(404, "agent_unavailable")
            if require_selected and parsed != self._selected_agent:
                raise AgentRoutingError(409, "agent_not_selected")
            return adapter

    def select(self, value: object) -> AgentRouterState:
        try:
            target_id = value if isinstance(value, AgentId) else AgentId.parse_exact(value)
        except ValueError as error:
            raise AgentRoutingError(400, "invalid_agent") from error
        with self._lock:
            if self._switching:
                raise AgentRoutingError(409, "agent_switch_in_progress")
            if self._active_login is not None:
                raise AgentRoutingError(409, "agent_login_active")
            if self._active_turn is not None:
                raise AgentRoutingError(409, "agent_turn_active")
            current_id = self._selected_agent
            target = self._adapters.get(target_id)
            if target is None:
                raise AgentRoutingError(404, "agent_unavailable")
            current = self._adapters[current_id]
            if target_id == current_id and current.is_ready():
                return self.state()
            current_activity = current.activity()
            if current_activity.active_login:
                raise AgentRoutingError(409, "agent_login_active")
            if current_activity.active_turn:
                raise AgentRoutingError(409, "agent_turn_active")
            self._switching = True
            self._stable_error = None
        try:
            current.deactivate()
        except Exception as error:
            with self._lock:
                self._switching = False
                self._stable_error = "agent_stop_failed"
            raise AgentRoutingError(502, "agent_stop_failed") from error
        with self._lock:
            # The previous Agent, or the failed selected generation during same-Agent recovery,
            # is already inactive. Keep the requested target selected even if activation fails;
            # silently reactivating another Agent would be an auto fallback.
            self._selected_agent = target_id
        try:
            target.activate()
        except Exception as error:
            with self._lock:
                self._switching = False
                self._stable_error = "agent_start_failed"
            raise AgentRoutingError(502, "agent_start_failed") from error
        with self._lock:
            self._switching = False
            self._stable_error = None
            return self.state()

    def begin_login(self, agent_id: object, request_id: str) -> AgentOperation:
        return self._begin_operation(agent_id, request_id, login=True)

    def finish_login(self, agent_id: object, request_id: str) -> None:
        self._finish_operation(agent_id, request_id, login=True)

    def begin_turn(self, agent_id: object, request_id: str) -> AgentOperation:
        return self._begin_operation(agent_id, request_id, login=False)

    def finish_turn(self, agent_id: object, request_id: str) -> None:
        self._finish_operation(agent_id, request_id, login=False)

    def _begin_operation(self, agent_id: object, request_id: str, *, login: bool) -> AgentOperation:
        parsed = self._parse_selected(agent_id)
        self._validate_request_id(request_id)
        with self._lock:
            if self._switching:
                raise AgentRoutingError(409, "agent_switch_in_progress")
            if self._active_login is not None:
                raise AgentRoutingError(409, "agent_login_active")
            if self._active_turn is not None:
                raise AgentRoutingError(409, "agent_turn_active")
            operation = AgentOperation(parsed, request_id)
            if login:
                self._active_login = operation
            else:
                self._active_turn = operation
            return operation

    def _finish_operation(self, agent_id: object, request_id: str, *, login: bool) -> None:
        parsed = self._parse_agent(agent_id)
        self._validate_request_id(request_id)
        with self._lock:
            current = self._active_login if login else self._active_turn
            if current != AgentOperation(parsed, request_id):
                raise AgentRoutingError(409, "agent_operation_mismatch")
            if login:
                self._active_login = None
            else:
                self._active_turn = None

    def _parse_selected(self, value: object) -> AgentId:
        parsed = self._parse_agent(value)
        with self._lock:
            if parsed != self._selected_agent:
                raise AgentRoutingError(409, "agent_not_selected")
        return parsed

    @staticmethod
    def _parse_agent(value: object) -> AgentId:
        try:
            return value if isinstance(value, AgentId) else AgentId.parse_exact(value)
        except ValueError as error:
            raise AgentRoutingError(400, "invalid_agent") from error

    @staticmethod
    def _validate_request_id(value: object) -> None:
        if not isinstance(value, str) or OPAQUE_OPERATION_ID.fullmatch(value) is None:
            raise AgentRoutingError(400, "invalid_request")
