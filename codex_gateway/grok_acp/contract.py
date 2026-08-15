"""Pinned, closed Grok ACP wire contract for the official CLI 1.0.0."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Tuple


MAX_IDENTIFIER_LENGTH = 512
MAX_MODEL_COUNT = 128
MAX_MODEL_NAME_LENGTH = 256
MAX_RESPONSE_FIELDS = 128
AUTH_METHOD_ID = "grok.com"
CACHED_TOKEN_AUTH_METHOD_ID = "cached_token"
AUTHENTICATED_METHOD_IDS = frozenset({AUTH_METHOD_ID, CACHED_TOKEN_AUTH_METHOD_ID})


class _RequestMethod(str, Enum):
    """Every method the gateway is allowed to write to Grok ACP.

    This enum is intentionally private. Public callers use typed supervisor methods, so an
    Android or HTTP value can never become a JSON-RPC method name.
    """

    INITIALIZE = "initialize"
    AUTHENTICATE = "authenticate"
    # ACP 0.10.4 prefixes extension names with one underscore on the JSON-RPC
    # wire, then strips it before invoking Grok's logical x.ai/* handler.
    AUTH_URL = "_x.ai/auth/get_url"
    AUTH_CANCEL = "_x.ai/auth/cancel"
    AUTH_INFO = "_x.ai/auth/info"
    AUTH_LOGOUT = "_x.ai/auth/logout"
    MODELS_LIST = "_x.ai/models/list"
    SESSION_NEW = "session/new"
    SESSION_LOAD = "session/load"
    SESSION_RESUME = "session/resume"
    SESSION_SET_MODEL = "session/set_model"
    SESSION_PROMPT = "session/prompt"
    SESSION_CANCEL = "session/cancel"
    SESSION_CLOSE = "session/close"


REQUEST_METHODS = frozenset(method.value for method in _RequestMethod)
NOTIFICATION_METHODS = frozenset(
    {
        "session/update",
        "_x.ai/session/update",
        "_x.ai/session_notification",
        "_x.ai/session/prompt_complete",
    }
)
TERMINAL_NOTIFICATION_METHOD = "_x.ai/session/prompt_complete"


@dataclass(frozen=True)
class GrokModelSummary:
    model_id: str
    display_name: str


@dataclass(frozen=True)
class GrokInitializeState:
    protocol_version: str
    auth_method_id: str
    models: Tuple[GrokModelSummary, ...]
    current_model_id: str
    can_load_session: bool
    can_resume_session: bool
    can_close_session: bool


def initialize_params() -> dict[str, Any]:
    """Return the one fixed capability declaration used by the Android gateway."""

    return {
        "protocolVersion": "1",
        "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        },
        "_meta": {
            "clientType": "alpine-android",
            "clientVersion": "0.1.0-debug",
            "startupHints": {
                "nonInteractive": True,
                "skipGitStatus": True,
                "skipProjectLayout": True,
            },
        },
    }


def parse_initialize_result(result: Mapping[str, Any]) -> GrokInitializeState:
    """Discard all nonessential initialize metadata and return a bounded state."""

    _bounded_mapping(result)
    protocol = result.get("protocolVersion")
    if protocol not in ("1", 1):
        raise ValueError("grok_initialize_protocol_invalid")

    methods = result.get("authMethods")
    if not isinstance(methods, list):
        raise ValueError("grok_initialize_auth_invalid")
    method_ids: list[str] = []
    for method in methods:
        if not isinstance(method, dict):
            raise ValueError("grok_initialize_auth_invalid")
        _bounded_mapping(method)
        method_id = method.get("id")
        if not isinstance(method_id, str):
            raise ValueError("grok_initialize_auth_invalid")
        method_ids.append(method_id)
    # With the fixed OAuth-only launch policy, the pinned official CLI advertises exactly one of
    # these ordered shapes. A persisted OAuth session adds ``cached_token`` ahead of the interactive
    # ``grok.com`` method; rejecting that second valid shape makes an already-signed-in Runtime
    # impossible to reconnect after the Grok process restarts.
    method_shape = tuple(method_ids)
    if method_shape not in (
        (AUTH_METHOD_ID,),
        (CACHED_TOKEN_AUTH_METHOD_ID, AUTH_METHOD_ID),
    ):
        raise ValueError("grok_initialize_auth_invalid")

    capabilities = result.get("agentCapabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("grok_initialize_capability_invalid")
    _bounded_mapping(capabilities)
    can_load = capabilities.get("loadSession") is True
    session_capabilities = capabilities.get("sessionCapabilities")
    if not isinstance(session_capabilities, dict):
        raise ValueError("grok_initialize_session_capability_invalid")
    _bounded_mapping(session_capabilities)
    can_close = isinstance(session_capabilities.get("close"), dict)
    if not can_load or not can_close:
        raise ValueError("grok_initialize_session_capability_invalid")
    can_resume = isinstance(session_capabilities.get("resume"), dict)

    metadata = result.get("_meta")
    if not isinstance(metadata, dict):
        raise ValueError("grok_initialize_metadata_invalid")
    _bounded_mapping(metadata)
    default_auth_method_id = metadata.get("defaultAuthMethodId")
    if method_shape == (CACHED_TOKEN_AUTH_METHOD_ID, AUTH_METHOD_ID):
        if default_auth_method_id not in (None, CACHED_TOKEN_AUTH_METHOD_ID):
            raise ValueError("grok_initialize_auth_invalid")
        selected_auth_method_id = CACHED_TOKEN_AUTH_METHOD_ID
    else:
        if default_auth_method_id is not None:
            raise ValueError("grok_initialize_auth_invalid")
        selected_auth_method_id = AUTH_METHOD_ID
    model_state = metadata.get("modelState")
    if not isinstance(model_state, dict):
        raise ValueError("grok_initialize_models_invalid")
    models, current = parse_model_state(model_state)

    return GrokInitializeState(
        protocol_version="1",
        auth_method_id=selected_auth_method_id,
        models=models,
        current_model_id=current,
        can_load_session=True,
        can_resume_session=can_resume,
        can_close_session=True,
    )


def parse_model_state(value: Mapping[str, Any]) -> tuple[Tuple[GrokModelSummary, ...], str]:
    models, current = parse_model_catalog(value, require_current=True)
    if current is None:
        raise ValueError("grok_model_state_invalid")
    return models, current


def parse_model_catalog(
    value: Mapping[str, Any],
    *,
    require_current: bool = False,
) -> tuple[Tuple[GrokModelSummary, ...], str | None]:
    """Normalize a dynamic model state; duplicates keep the first official row."""

    _bounded_mapping(value)
    available = value.get("availableModels")
    current = value.get("currentModelId")
    if not isinstance(available, list) or len(available) > MAX_MODEL_COUNT:
        raise ValueError("grok_model_state_invalid")
    if current is None and not require_current:
        current_id = None
    else:
        current_id = _identifier(current)
    parsed: list[GrokModelSummary] = []
    seen: set[str] = set()
    for item in available:
        if not isinstance(item, dict):
            raise ValueError("grok_model_state_invalid")
        _bounded_mapping(item)
        model_id = _identifier(item.get("modelId"))
        name = item.get("name")
        if not isinstance(name, str) or not name or len(name) > MAX_MODEL_NAME_LENGTH:
            raise ValueError("grok_model_state_invalid")
        if model_id in seen:
            continue
        seen.add(model_id)
        parsed.append(GrokModelSummary(model_id=model_id, display_name=name))
    if current_id is not None and current_id not in seen:
        raise ValueError("grok_model_state_invalid")
    return tuple(parsed), current_id


def opaque_identifier(value: Any) -> str:
    return _identifier(value)


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError("grok_identifier_invalid")
    return value


def _bounded_mapping(value: Mapping[str, Any]) -> None:
    if len(value) > MAX_RESPONSE_FIELDS or any(not isinstance(key, str) for key in value):
        raise ValueError("grok_response_invalid")
