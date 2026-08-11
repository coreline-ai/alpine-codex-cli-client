"""Narrow, typed facade for only the Codex app-server methods required by this app."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .process import AppServerSupervisor


@dataclass(frozen=True)
class AccountState:
    authenticated: bool
    requires_openai_auth: bool


@dataclass(frozen=True)
class DeviceCodeLoginStart:
    login_id: str
    verification_url: str
    user_code: str


class CodexAppServerProtocol:
    """Exposes a closed method surface; no direct-provider or token methods exist here."""

    def __init__(self, supervisor: AppServerSupervisor) -> None:
        self._supervisor = supervisor

    def initialize(self, client_name: str, client_version: str) -> Dict[str, Any]:
        return self._supervisor.start(client_name, client_version)

    @property
    def is_ready(self) -> bool:
        return self._supervisor.state.value == "READY"

    def add_notification_listener(
        self,
        listener: Callable[[str, Dict[str, Any]], None],
    ) -> Callable[[], None]:
        return self._supervisor.add_notification_listener(listener)

    def account_read(self) -> AccountState:
        response = self._supervisor.request("account/read", {}, timeout_seconds=15.0)
        requires_auth = response.get("requiresOpenaiAuth")
        account = response.get("account")
        if not isinstance(requires_auth, bool):
            raise ValueError("account_read_response_invalid")
        # Deliberately discard email, plan, and every nonessential account field.
        return AccountState(
            authenticated=isinstance(account, dict) and account.get("type") == "chatgpt",
            requires_openai_auth=requires_auth,
        )

    def start_device_code_login(self) -> DeviceCodeLoginStart:
        response = self._supervisor.request(
            "account/login/start",
            {"type": "chatgptDeviceCode"},
            timeout_seconds=30.0,
        )
        return DeviceCodeLoginStart(
            login_id=self._required_string(response, "loginId"),
            verification_url=self._required_string(response, "verificationUrl"),
            user_code=self._required_string(response, "userCode"),
        )

    def cancel_login(self, login_id: str) -> None:
        self._supervisor.request("account/login/cancel", {"loginId": self._opaque_id(login_id)}, 15.0)

    def logout(self) -> None:
        self._supervisor.request("account/logout", {}, 15.0)

    def model_list(self, cursor: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {} if cursor is None else {"cursor": self._opaque_id(cursor)}
        return self._supervisor.request("model/list", params, 15.0)

    def thread_start(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._supervisor.request("thread/start", dict(params), 30.0)

    def thread_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._supervisor.request("thread/resume", dict(params), 30.0)

    def turn_start(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._supervisor.request("turn/start", dict(params), 30.0)

    def turn_interrupt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._supervisor.request("turn/interrupt", dict(params), 30.0)

    @staticmethod
    def _required_string(response: Dict[str, Any], field: str) -> str:
        value = response.get(field)
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("app_server_response_invalid")
        return value

    @staticmethod
    def _opaque_id(value: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError("opaque identifier is invalid")
        return value
