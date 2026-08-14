"""Normalized Gateway operations over exactly one selected Agent adapter."""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterator, Optional

from codex_gateway.agents.contracts import AgentId, AgentLogin, AgentTurnHandle
from codex_gateway.agents.grok import GrokAdapterError
from codex_gateway.agents.router import AgentOperation, AgentRouter, AgentRoutingError
from codex_gateway.grok_acp.rpc import (
    AcpPendingLimit,
    AcpProcessLost,
    AcpProtocolError,
    AcpRemoteError,
    AcpStopped,
    AcpTimeout,
)


class AgentServiceError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class AgentGatewayService:
    """Coordinates normalized calls and router operation ownership."""

    def __init__(self, router: AgentRouter) -> None:
        self._router = router
        self._lock = threading.RLock()
        self._login_reservation = False
        self._turn_reservation = False

    def health(self) -> Dict[str, Any]:
        state = self._router.state()
        if not state.gateway_ready:
            raise AgentServiceError(503, "gateway_not_ready")
        return {
            "runtime": "ready",
            "gateway": "ready",
            "selected_agent": state.selected_agent.value,
            "backend_ready": state.backend_ready,
        }

    def agents(self) -> Dict[str, Any]:
        state = self._router.state()
        values = []
        for agent_id in self._router.available_agents():
            adapter = self._router.adapter_for(agent_id, require_selected=False)
            capability = adapter.capabilities
            values.append(
                {
                    "id": agent_id.value,
                    "selected": agent_id is state.selected_agent,
                    "ready": adapter.is_ready(),
                    "capabilities": {
                        "device_oauth": capability.device_oauth,
                        "dynamic_models": capability.dynamic_models,
                        "streaming": capability.streaming,
                        "stop": capability.stop,
                        "resume": capability.resume,
                    },
                }
            )
        return {"object": "list", "data": values}

    def select(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"agent_id"}:
            raise AgentServiceError(400, "invalid_request")
        try:
            state = self._router.select(value.get("agent_id"))
        except AgentRoutingError as error:
            raise AgentServiceError(error.status, error.code) from error
        return {
            "selected_agent": state.selected_agent.value,
            "backend_ready": state.backend_ready,
        }

    def account(self, agent_id: object) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        try:
            value = adapter.account()
        except Exception as error:
            raise self._translate(error)
        return {
            "agent_id": value.agent_id.value,
            "authenticated": value.authenticated,
            "requires_auth": value.requires_auth,
        }

    def start_login(self, agent_id: object) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        parsed = adapter.agent_id
        with self._lock:
            state = self._router.state()
            if self._login_reservation or state.active_login is not None:
                raise AgentServiceError(409, "agent_login_active")
            if self._turn_reservation or state.active_turn is not None:
                raise AgentServiceError(409, "agent_turn_active")
            self._login_reservation = True
        login: Optional[AgentLogin] = None
        try:
            login = adapter.start_device_login()
            self._router.begin_login(parsed, login.request_id)
            return self._login_start_value(login)
        except Exception as error:
            if login is not None:
                try:
                    adapter.cancel_login(login.request_id)
                except Exception:
                    pass
            if isinstance(error, AgentRoutingError):
                raise AgentServiceError(error.status, error.code) from error
            raise self._translate(error)
        finally:
            with self._lock:
                self._login_reservation = False

    def login_status(self, agent_id: object, request_id: str) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        try:
            login = adapter.login_status(request_id)
        except Exception as error:
            raise self._translate(error)
        if login.state in ("authenticated", "completed", "failed", "cancelled", "expired"):
            self._finish_login_once(adapter.agent_id, login.request_id)
        return self._login_status_value(login)

    def cancel_login(self, agent_id: object, request_id: str) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        try:
            login = adapter.cancel_login(request_id)
        except Exception as error:
            raise self._translate(error)
        self._finish_login_once(adapter.agent_id, login.request_id)
        return self._login_status_value(login)

    def cancel_active_login(self, agent_id: object) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        state = self._router.state()
        active = state.active_login
        if active is None or active.agent_id is not adapter.agent_id:
            raise AgentServiceError(404, "agent_login_not_found")
        return self.cancel_login(adapter.agent_id, active.request_id)

    def logout(self, agent_id: object) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        state = self._router.state()
        if state.active_login is not None:
            raise AgentServiceError(409, "agent_login_active")
        if state.active_turn is not None:
            raise AgentServiceError(409, "agent_turn_active")
        try:
            adapter.logout()
        except Exception as error:
            raise self._translate(error)
        return {"agent_id": adapter.agent_id.value, "status": "logged_out"}

    def models(self, agent_id: Optional[object] = None) -> Dict[str, Any]:
        selected = self._router.state().selected_agent if agent_id is None else agent_id
        adapter = self._adapter(selected)
        try:
            values = adapter.models()
        except Exception as error:
            raise self._translate(error)
        return {
            "object": "list",
            "agent_id": adapter.agent_id.value,
            "data": [
                {
                    "id": value.model_id,
                    "display_name": value.display_name,
                    "is_default": value.is_default,
                    "modalities": list(value.modalities),
                }
                for value in values
            ],
        }

    def start_chat(self, value: Dict[str, Any]) -> AgentTurnHandle:
        if not isinstance(value, dict):
            raise AgentServiceError(400, "invalid_request")
        try:
            parsed = AgentId.parse_exact(value.get("agent_id"))
        except ValueError as error:
            raise AgentServiceError(400, "invalid_agent") from error
        adapter = self._adapter(parsed)
        with self._lock:
            state = self._router.state()
            if self._login_reservation or state.active_login is not None:
                raise AgentServiceError(409, "agent_login_active")
            if self._turn_reservation or state.active_turn is not None:
                raise AgentServiceError(409, "agent_turn_active")
            self._turn_reservation = True
        handle: Optional[AgentTurnHandle] = None
        try:
            handle = adapter.start_turn(value)
            self._router.begin_turn(parsed, handle.request_id)
            return handle
        except Exception as error:
            if handle is not None:
                try:
                    adapter.interrupt(handle.request_id)
                except Exception:
                    pass
            if isinstance(error, AgentRoutingError):
                raise AgentServiceError(error.status, error.code) from error
            raise self._translate(error)
        finally:
            with self._lock:
                self._turn_reservation = False

    def stream(self, handle: AgentTurnHandle) -> Iterator[Dict[str, Any]]:
        adapter = self._adapter(handle.agent_id)
        try:
            for event in adapter.stream(handle):
                value: Dict[str, Any] = {
                    "id": event.request_id,
                    "agent_id": event.agent_id.value,
                    "type": event.event_type,
                }
                if event.text:
                    value["text"] = event.text
                if event.code is not None:
                    value["code"] = event.code
                    value["retryable"] = False
                if event.conversation_id is not None:
                    value["conversation_id"] = event.conversation_id
                if event.diagnostics is not None:
                    diagnostics = event.diagnostics
                    value["diagnostics"] = {
                        "prompt_dispatch_count": diagnostics.prompt_dispatch_count,
                        "visible_delta_count": diagnostics.visible_delta_count,
                        "terminal_count": diagnostics.terminal_count,
                        "cancel_dispatch_count": diagnostics.cancel_dispatch_count,
                        "retry_classification": diagnostics.retry_classification,
                        "retry_attempts": diagnostics.retry_attempts,
                        "retry_max": diagnostics.retry_max,
                        "tool_event_count": diagnostics.tool_event_count,
                        "subagent_event_count": diagnostics.subagent_event_count,
                        "mcp_event_count": diagnostics.mcp_event_count,
                        "filesystem_event_count": diagnostics.filesystem_event_count,
                        "terminal_event_count": diagnostics.terminal_event_count,
                    }
                yield value
        finally:
            self._finish_turn_once(handle.agent_id, handle.request_id)

    def interrupt(self, agent_id: object, request_id: str) -> Dict[str, Any]:
        adapter = self._adapter(agent_id)
        try:
            adapter.interrupt(request_id)
        except Exception as error:
            raise self._translate(error)
        return {
            "agent_id": adapter.agent_id.value,
            "id": request_id,
            "status": "interrupt_requested",
        }

    def _adapter(self, agent_id: object):
        try:
            return self._router.adapter_for(agent_id)
        except AgentRoutingError as error:
            raise AgentServiceError(error.status, error.code) from error

    def _finish_login_once(self, agent_id: AgentId, request_id: str) -> None:
        state = self._router.state()
        if state.active_login == AgentOperation(agent_id, request_id):
            try:
                self._router.finish_login(agent_id, request_id)
            except AgentRoutingError:
                pass

    def _finish_turn_once(self, agent_id: AgentId, request_id: str) -> None:
        state = self._router.state()
        if state.active_turn == AgentOperation(agent_id, request_id):
            try:
                self._router.finish_turn(agent_id, request_id)
            except AgentRoutingError:
                pass

    @staticmethod
    def _login_start_value(login: AgentLogin) -> Dict[str, Any]:
        value = AgentGatewayService._login_status_value(login)
        if login.verification_url is not None:
            value["verification_url"] = login.verification_url
        if login.expires_in_seconds is not None:
            value["expires_in_seconds"] = login.expires_in_seconds
        if login.poll_interval_seconds is not None:
            value["poll_interval_seconds"] = login.poll_interval_seconds
        # Grok never exposes a separate challenge code. Codex compatibility can retain its
        # existing endpoint until the Android migration handles the differing contract.
        if login.agent_id is AgentId.CODEX and login.user_code is not None:
            value["user_code"] = login.user_code
        return value

    @staticmethod
    def _login_status_value(login: AgentLogin) -> Dict[str, Any]:
        return {
            "agent_id": login.agent_id.value,
            "request_id": login.request_id,
            "status": login.state,
        }

    @staticmethod
    def _translate(error: Exception) -> AgentServiceError:
        if isinstance(error, AgentServiceError):
            return error
        status = getattr(error, "status", None)
        code = getattr(error, "code", None)
        if isinstance(status, int) and isinstance(code, str):
            return AgentServiceError(status, code)
        if isinstance(error, GrokAdapterError):
            code = error.code
            if code in {"invalid_request", "agent_mismatch", "request_too_large"}:
                return AgentServiceError(400 if code != "request_too_large" else 413, code)
            if code in {"authentication_required"}:
                return AgentServiceError(401, code)
            if code in {"login_not_found", "turn_not_found"}:
                return AgentServiceError(404, code)
            if code in {
                "already_authenticated",
                "login_already_active",
                "login_not_active",
                "login_active",
                "turn_active",
                "turn_already_active",
                "conversation_binding_not_found",
                "conversation_agent_mismatch",
                "conversation_generation_mismatch",
                "model_not_available",
            }:
                return AgentServiceError(409, code)
            if code == "grok_not_ready":
                return AgentServiceError(503, code)
            return AgentServiceError(502, code)
        # Transport failures are already content-free exception classes. Preserve only the
        # closed category here so a real-device failure can be diagnosed without forwarding
        # child output, request data, identifiers, or an exception message over HTTP.
        acp_codes = (
            (AcpProcessLost, "grok_acp_process_lost"),
            (AcpProtocolError, "grok_acp_protocol_error"),
            (AcpTimeout, "grok_acp_timeout"),
            (AcpRemoteError, "grok_acp_remote_error"),
            (AcpPendingLimit, "grok_acp_pending_limit"),
            (AcpStopped, "grok_acp_stopped"),
        )
        for error_type, stable_code in acp_codes:
            if isinstance(error, error_type):
                return AgentServiceError(502, stable_code)
        return AgentServiceError(502, "agent_request_failed")
