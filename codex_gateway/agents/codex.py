"""Codex app-server implementation of the fixed Agent adapter contract."""

from __future__ import annotations

from typing import Any, Iterator, Tuple

from codex_gateway.agents.contracts import (
    AgentAccount,
    AgentActivity,
    AgentCapabilities,
    AgentId,
    AgentLogin,
    AgentModel,
    AgentTurnEvent,
    AgentTurnHandle,
)
from codex_gateway.gateway import ActiveTurn, CodexGatewayService


class CodexAgentAdapter:
    agent_id = AgentId.CODEX
    capabilities = AgentCapabilities(
        device_oauth=True,
        dynamic_models=True,
        streaming=True,
        stop=True,
        resume=True,
    )

    def __init__(self, service: CodexGatewayService) -> None:
        self._service = service

    def is_ready(self) -> bool:
        return self._service.backend_ready()

    def activity(self) -> AgentActivity:
        active_login, active_turn = self._service.activity_snapshot()
        return AgentActivity(active_login=active_login, active_turn=active_turn)

    def activate(self) -> None:
        if not self.is_ready():
            raise RuntimeError("codex_not_ready")

    def deactivate(self) -> None:
        activity = self.activity()
        if activity.active_login or activity.active_turn:
            raise RuntimeError("codex_busy")

    def account(self) -> AgentAccount:
        value = self._service.account()
        return AgentAccount(
            agent_id=self.agent_id,
            authenticated=value["authenticated"],
            requires_auth=value["requires_openai_auth"],
        )

    def start_device_login(self) -> AgentLogin:
        return self._login(self._service.start_device_login())

    def login_status(self, request_id: str) -> AgentLogin:
        return self._login(self._service.login_status(request_id))

    def cancel_login(self, request_id: str) -> AgentLogin:
        return self._login(self._service.cancel_login(request_id))

    def logout(self) -> None:
        self._service.logout()

    def models(self) -> Tuple[AgentModel, ...]:
        values = self._service.models()["data"]
        return tuple(
            AgentModel(
                agent_id=self.agent_id,
                model_id=value["id"],
                display_name=value["display_name"],
                is_default=value["is_default"],
                modalities=tuple(value.get("modalities", ("text",))),
            )
            for value in values
        )

    def start_turn(self, value: dict[str, Any]) -> AgentTurnHandle:
        active = self._service.start_chat(value)
        return AgentTurnHandle(
            agent_id=self.agent_id,
            request_id=active.request_id,
            conversation_id=active.conversation_id,
            model_id=active.model,
            _native_handle=active,
        )

    def stream(self, handle: AgentTurnHandle) -> Iterator[AgentTurnEvent]:
        active = self._active(handle)
        for event in self._service.stream(active):
            yield AgentTurnEvent(
                agent_id=self.agent_id,
                request_id=active.request_id,
                event_type=event.type,
                text=event.value.get("text", ""),
                code=event.value.get("code"),
                conversation_id=event.value.get("conversation_id"),
            )

    def interrupt(self, request_id: str) -> None:
        self._service.interrupt(request_id)

    def _login(self, value: dict[str, Any]) -> AgentLogin:
        return AgentLogin(
            agent_id=self.agent_id,
            request_id=value["login_id"],
            state=value["status"],
            verification_url=value.get("verification_url"),
            user_code=value.get("user_code"),
            expires_in_seconds=value.get("expires_in_seconds"),
            poll_interval_seconds=value.get("poll_interval_seconds"),
        )

    def _active(self, handle: AgentTurnHandle) -> ActiveTurn:
        if handle.agent_id != self.agent_id or not isinstance(handle._native_handle, ActiveTurn):
            raise ValueError("invalid_turn_handle")
        return handle._native_handle
