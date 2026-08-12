"""Production lifecycle adapters for the one-selected-backend Gateway."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import threading
from typing import Any, Iterator, Tuple

from codex_gateway.agents.codex import CodexAgentAdapter
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
from codex_gateway.app_server.process import AppServerSupervisor
from codex_gateway.app_server.protocol import CodexAppServerProtocol
from codex_gateway.gateway import CodexGatewayService


class ManagedCodexAgentAdapter:
    """Starts and stops the official app-server with Codex Agent selection."""

    agent_id = AgentId.CODEX
    capabilities = AgentCapabilities(
        device_oauth=True,
        dynamic_models=True,
        streaming=True,
        stop=True,
        resume=True,
    )

    def __init__(self, executable: str, home: str, workspace: str) -> None:
        self._executable = executable
        self._home = home
        self._workspace = workspace
        self._delegate: CodexAgentAdapter | None = None
        self._service: CodexGatewayService | None = None
        self._supervisor: AppServerSupervisor | None = None
        self._lock = threading.RLock()

    def is_ready(self) -> bool:
        with self._lock:
            return self._delegate is not None and self._delegate.is_ready()

    def activity(self) -> AgentActivity:
        with self._lock:
            delegate = self._delegate
        return delegate.activity() if delegate is not None else AgentActivity(False, False)

    def activate(self) -> None:
        with self._lock:
            if self._delegate is not None:
                if not self._delegate.is_ready():
                    raise RuntimeError("codex_not_ready")
                return
            _validate_codex_home(Path(self._home))
            supervisor = AppServerSupervisor(
                command=[self._executable, "app-server"],
                working_directory=self._workspace,
                environment={"HOME": self._home},
            )
            try:
                protocol = CodexAppServerProtocol(supervisor)
                protocol.initialize("codex-app-server-client", "0.2.0-debug")
                service = CodexGatewayService(
                    protocol,
                    self._workspace,
                    conversation_store_path=os.path.join(
                        self._home, "conversation-bindings.v1.json"
                    ),
                )
                delegate = CodexAgentAdapter(service)
                if not delegate.is_ready():
                    raise RuntimeError("codex_not_ready")
            except Exception:
                supervisor.stop()
                raise
            self._supervisor = supervisor
            self._service = service
            self._delegate = delegate

    def deactivate(self) -> None:
        with self._lock:
            delegate = self._delegate
            service = self._service
            supervisor = self._supervisor
            if delegate is None:
                return
            if delegate.activity().active_login or delegate.activity().active_turn:
                raise RuntimeError("codex_busy")
            self._delegate = None
            self._service = None
            self._supervisor = None
        try:
            if service is not None:
                service.close()
        finally:
            if supervisor is not None:
                supervisor.stop()

    def account(self) -> AgentAccount:
        return self._active().account()

    def start_device_login(self) -> AgentLogin:
        return self._active().start_device_login()

    def login_status(self, request_id: str) -> AgentLogin:
        return self._active().login_status(request_id)

    def cancel_login(self, request_id: str) -> AgentLogin:
        return self._active().cancel_login(request_id)

    def logout(self) -> None:
        self._active().logout()

    def models(self) -> Tuple[AgentModel, ...]:
        return self._active().models()

    def start_turn(self, value: dict[str, Any]) -> AgentTurnHandle:
        return self._active().start_turn(value)

    def stream(self, handle: AgentTurnHandle) -> Iterator[AgentTurnEvent]:
        return self._active().stream(handle)

    def interrupt(self, request_id: str) -> None:
        self._active().interrupt(request_id)

    def _active(self) -> CodexAgentAdapter:
        with self._lock:
            if self._delegate is None:
                raise RuntimeError("codex_not_ready")
            return self._delegate


def _validate_codex_home(home: Path) -> None:
    try:
        value = home.lstat()
        if home.is_symlink() or not stat.S_ISDIR(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o700:
            raise ValueError
        config = home / "config.toml"
        config_value = config.lstat()
        if (
            config.is_symlink()
            or not stat.S_ISREG(config_value.st_mode)
            or stat.S_IMODE(config_value.st_mode) != 0o600
        ):
            raise ValueError
        auth = home / "auth.json"
        if auth.exists() or auth.is_symlink():
            auth_value = auth.lstat()
            if (
                auth.is_symlink()
                or not stat.S_ISREG(auth_value.st_mode)
                or stat.S_IMODE(auth_value.st_mode) != 0o600
            ):
                raise ValueError
    except (OSError, ValueError) as error:
        raise RuntimeError("codex_home_policy_invalid") from error
