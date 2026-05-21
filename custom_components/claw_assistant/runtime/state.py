

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Callable

from homeassistant.core import HomeAssistant

from ..const import DOMAIN

_active_conversation_id: ContextVar[str] = ContextVar(
    "claw_assistant_active_conversation_id", default="default"
)


def set_active_conversation(conversation_id: str | None) -> Token[str]:

    return _active_conversation_id.set(conversation_id or "default")


def reset_active_conversation(token: Token[str]) -> None:

    _active_conversation_id.reset(token)

_IM_PREFIXES = ("wechat:", "feishu:", "dingtalk:", "qq:")

PLATFORM_ANDROID_APP_V2 = "android_app_v2"
PLATFORM_ANDROID_APP = "android_app"
PLATFORM_IOS_APP = "ios_app"
PLATFORM_IOS_WEB = "ios_web"
PLATFORM_ANDROID_WEB = "android_web"
PLATFORM_MACOS_WEB = "macos_web"
PLATFORM_WINDOWS_WEB = "windows_web"
PLATFORM_LINUX_WEB = "linux_web"
PLATFORM_WEB = "web"

COMPANION_APP_PLATFORMS = (PLATFORM_ANDROID_APP_V2, PLATFORM_ANDROID_APP, PLATFORM_IOS_APP)
MOBILE_PLATFORMS = (
    PLATFORM_ANDROID_APP_V2, PLATFORM_ANDROID_APP, PLATFORM_IOS_APP,
    PLATFORM_IOS_WEB, PLATFORM_ANDROID_WEB,
)


def is_im_channel(conversation_id: str | None) -> bool:
    return bool(conversation_id and conversation_id.startswith(_IM_PREFIXES))


def get_channel_type(conversation_id: str | None) -> str:
    if not conversation_id:
        return "ha"
    for prefix in _IM_PREFIXES:
        if conversation_id.startswith(prefix):
            return prefix.rstrip(":")
    return "ha"


def is_companion_app(platform: str | None) -> bool:
    return platform in COMPANION_APP_PLATFORMS


def is_mobile_platform(platform: str | None) -> bool:
    return platform in MOBILE_PLATFORMS


def get_platform_display_name(platform: str | None) -> str:
    names = {
        PLATFORM_ANDROID_APP_V2: "Android Companion App",
        PLATFORM_ANDROID_APP: "Android Companion App",
        PLATFORM_IOS_APP: "iOS Companion App",
        PLATFORM_IOS_WEB: "iOS Safari",
        PLATFORM_ANDROID_WEB: "Android Browser",
        PLATFORM_MACOS_WEB: "macOS Browser",
        PLATFORM_WINDOWS_WEB: "Windows Browser",
        PLATFORM_LINUX_WEB: "Linux Browser",
        PLATFORM_WEB: "Web Browser",
    }
    return names.get(platform or "", "Unknown")


RUNTIME_STORE_KEY = "runtime_state"

_LEGACY_BUCKETS: dict[str, str] = {
    "conversation_status": "ha_crack",
    "task_loop": "ha_crack_task_loop",
    "active_conversation": "ha_crack_active_conversation",
    "should_end_flag": "ha_crack_should_end_flag",
    "tool_results": "ha_crack_tool_results",
    "tool_calls": "ha_crack_tool_calls",
    "global_state": "ha_crack_global",
    "output_state": "ha_crack_output",
    "memory_state": "ha_crack_memory",
    "adaptive_memory": "ha_crack_adaptive_memory",
    "config_approval_state": "ha_crack_config_approval_state",
}


def _default_conversation_status() -> dict[str, Any]:
    return {}


def _default_task_loop_entry() -> dict[str, Any]:

    return {
        "active": False,
        "iteration": 0,
        "max_iterations": 50,
        "conversation_id": None,
        "pending_feedback": None,
        "history": [],
        "waiting_choice": False,
        "last_choice": None,
    }


def _default_task_loop() -> dict[str, Any]:
    return {}


def _default_active_conversation() -> dict[str, Any]:
    return {"id": None}


def _default_should_end_flag() -> dict[str, Any]:
    return {"value": False}


def _default_adaptive_memory() -> dict[str, Any]:
    return {"agents": {}, "traces": []}


def _default_config_approval_state() -> dict[str, Any]:
    return {"pending": {}, "last_resolution": {}}


_BUCKET_FACTORIES: dict[str, Callable[[], Any]] = {
    "conversation_status": _default_conversation_status,
    "task_loop": _default_task_loop,
    "active_conversation": _default_active_conversation,
    "should_end_flag": _default_should_end_flag,
    "tool_results": dict,
    "tool_calls": dict,
    "global_state": dict,
    "output_state": dict,
    "memory_state": dict,
    "adaptive_memory": _default_adaptive_memory,
    "config_approval_state": _default_config_approval_state,
}


def get_runtime_store(hass: HomeAssistant) -> dict[str, Any]:

    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(RUNTIME_STORE_KEY, {})


def _ensure_bucket(hass: HomeAssistant, bucket: str) -> Any:
    runtime_store = get_runtime_store(hass)
    if bucket in runtime_store:
        return runtime_store[bucket]

    legacy_key = _LEGACY_BUCKETS[bucket]
    if legacy_key in hass.data:
        runtime_store[bucket] = hass.data[legacy_key]
        return runtime_store[bucket]

    value = _BUCKET_FACTORIES[bucket]()
    runtime_store[bucket] = value
    hass.data[legacy_key] = value
    return value


def prime_runtime_state(hass: HomeAssistant) -> dict[str, Any]:

    runtime_store = get_runtime_store(hass)
    for bucket in _LEGACY_BUCKETS:
        _ensure_bucket(hass, bucket)
    return runtime_store


def get_conversation_status(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "conversation_status")


def get_task_loop_state(hass: HomeAssistant) -> dict[str, Any]:
    container = _ensure_bucket(hass, "task_loop")
    conv_id = _active_conversation_id.get()
    if conv_id not in container:
        container[conv_id] = _default_task_loop_entry()
    return container[conv_id]


def get_active_conversation_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "active_conversation")


def get_should_end_flag(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "should_end_flag")


def get_tool_results_state(hass: HomeAssistant) -> list[Any]:
    container = _ensure_bucket(hass, "tool_results")
    if isinstance(container, list):
        container = {"default": container}
        runtime_store = get_runtime_store(hass)
        runtime_store["tool_results"] = container
        hass.data[_LEGACY_BUCKETS["tool_results"]] = container
    conv_id = _active_conversation_id.get()
    if conv_id not in container:
        container[conv_id] = []
    return container[conv_id]


def get_tool_calls_state(hass: HomeAssistant) -> list[Any]:
    container = _ensure_bucket(hass, "tool_calls")
    if isinstance(container, list):
        container = {"default": container}
        runtime_store = get_runtime_store(hass)
        runtime_store["tool_calls"] = container
        hass.data[_LEGACY_BUCKETS["tool_calls"]] = container
    conv_id = _active_conversation_id.get()
    if conv_id not in container:
        container[conv_id] = []
    return container[conv_id]


def get_global_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "global_state")


def get_output_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "output_state")


def get_memory_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "memory_state")


def get_adaptive_memory_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "adaptive_memory")


def get_config_approval_state(hass: HomeAssistant) -> dict[str, Any]:
    return _ensure_bucket(hass, "config_approval_state")


def mark_tool_called(hass: HomeAssistant, tool_name: str) -> None:

    status = get_conversation_status(hass)
    status["tool_called"] = True
    status["last_tool"] = tool_name


def consume_tool_called(hass: HomeAssistant) -> str:

    status = get_conversation_status(hass)
    tool_called = bool(status.pop("tool_called", False))
    last_tool = str(status.get("last_tool", ""))
    if tool_called:
        return last_tool
    return ""


def set_current_thought(hass: HomeAssistant, thought: str | None) -> None:

    status = get_conversation_status(hass)
    status["current_thought"] = thought


def set_conversation_state(
    hass: HomeAssistant, *, expecting_response: bool, reason: str = ""
) -> None:

    status = get_conversation_status(hass)
    status["expecting_response"] = expecting_response
    status["conversation_state_reason"] = reason


def request_agent_handoff(
    hass: HomeAssistant,
    *,
    direction: str = "next",
    reason: str = "",
    reply_content: str = "",
    handoff_intent: str = "request",
    expected_action: str = "reply",
    task_summary: str = "",
) -> None:

    status = get_conversation_status(hass)
    status["agent_handoff"] = {
        "requested": True,
        "direction": direction,
        "reason": reason,
        "reply_content": reply_content,
        "intent": handoff_intent,
        "expected_action": expected_action,
        "task_summary": task_summary,
    }


def consume_agent_handoff(hass: HomeAssistant) -> dict[str, str | bool]:

    status = get_conversation_status(hass)
    payload = status.pop("agent_handoff", None)
    if not isinstance(payload, dict):
        return {
            "requested": False,
            "direction": "next",
            "reason": "",
            "reply_content": "",
        }
    return {
        "requested": bool(payload.get("requested", False)),
        "direction": str(payload.get("direction", "next")),
        "reason": str(payload.get("reason", "")),
        "reply_content": str(payload.get("reply_content", "")),
        "intent": str(payload.get("intent", "request")),
        "expected_action": str(payload.get("expected_action", "reply")),
        "task_summary": str(payload.get("task_summary", "")),
    }


def request_next_agent_handoff(
    hass: HomeAssistant,
    *,
    reason: str = "",
    reply_content: str = "",
) -> None:

    request_agent_handoff(
        hass,
        direction="next",
        reason=reason,
        reply_content=reply_content,
    )


def consume_next_agent_handoff(hass: HomeAssistant) -> dict[str, str | bool]:

    return consume_agent_handoff(hass)
