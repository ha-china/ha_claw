

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import conversation, websocket_api
from homeassistant.components.conversation.agent_manager import agent_id_validator
from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
from homeassistant.components.conversation.const import ChatLogEventType
from homeassistant.core import callback
from homeassistant.helpers.chat_session import async_get_chat_session

from ..const import CONF_ENABLE_CONTEXT_STATUS_BAR, DOMAIN
from .continuous_conversation import (
    continuous_conversation_enabled,
    get_effective_conversation_id,
)

_PATCH_KEY = "_claw_assistant_streaming_conversation_process"
_NO_HANDLER = object()
_UNSET = object()
_PENDING_JS_KEY = "ha_crack_pending_js"
_FRONTEND_STATE_KEY = "ha_crack_frontend_state"


def _domain_data(hass) -> dict:
    return hass.data.setdefault("claw_assistant", {})


def context_status_bar_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_CONTEXT_STATUS_BAR, False):
            return True
    return False


def queue_frontend_js(hass, js_code: str) -> None:
    if not js_code:
        return
    _domain_data(hass).setdefault(_PENDING_JS_KEY, []).append(js_code)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/get_pending_js",
    }
)
@websocket_api.async_response
async def websocket_get_pending_js(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    pending = _domain_data(hass).setdefault(_PENDING_JS_KEY, [])
    js_codes = list(pending)
    pending.clear()
    connection.send_result(msg["id"], {"js_codes": js_codes})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/report_state",
        vol.Optional("data", default={}): dict,
    }
)
@websocket_api.async_response
async def websocket_report_state(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    _domain_data(hass)[_FRONTEND_STATE_KEY] = msg.get("data") or {}
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/get_settings",
    }
)
@websocket_api.async_response
async def websocket_get_settings(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    connection.send_result(
        msg["id"],
        {
            "continuous_conversation": continuous_conversation_enabled(hass),
            "enable_context_status_bar": context_status_bar_enabled(hass),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "conversation/process",
        vol.Required("text"): str,
        vol.Optional("conversation_id"): vol.Any(str, None),
        vol.Optional("language"): str,
        vol.Optional("agent_id"): agent_id_validator,
        vol.Optional("device_id"): vol.Any(str, None),
        vol.Optional("satellite_id"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def streaming_websocket_process(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:

    requested_conversation_id = get_effective_conversation_id(
        hass,
        msg.get("conversation_id"),
    )
    with async_get_chat_session(hass, requested_conversation_id) as session:
        conversation_id = session.conversation_id

    @callback
    def forward_events(
        event_conversation_id: str,
        event_type: ChatLogEventType,
        data: dict,
    ) -> None:
        if event_conversation_id != conversation_id:
            return
        connection.send_event(
            msg["id"],
            {
                "conversation_id": event_conversation_id,
                "event_type": event_type,
                "data": data,
            },
        )

    unsubscribe = async_subscribe_chat_logs(hass, forward_events)
    try:
        result = await conversation.async_converse(
            hass=hass,
            text=msg["text"],
            conversation_id=conversation_id,
            context=connection.context(msg),
            language=msg.get("language"),
            agent_id=msg.get("agent_id"),
            device_id=msg.get("device_id"),
            satellite_id=msg.get("satellite_id"),
        )
    finally:
        unsubscribe()

    connection.send_result(msg["id"], result.as_dict())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/get_context_status",
    }
)
@websocket_api.async_response
async def websocket_get_context_status(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    from homeassistant.util.hass_dict import HassKey

    model = ""
    tokens_used = 0
    context_window = 0
    agent_id = ""
    conversation_id = ""

    status = hass.data.get("claw_assistant", {}).get("runtime_state", {}).get("conversation_status", {})
    agent_id = status.get("current_agent_id", "")

    DATA_CHAT_LOGS: HassKey = HassKey("conversation_chat_log")
    all_logs = hass.data.get(DATA_CHAT_LOGS) or {}
    active_conv = hass.data.get("claw_assistant", {}).get("runtime_state", {}).get("active_conversation", {})
    conversation_id = active_conv.get("id") or ""

    if conversation_id and conversation_id in all_logs:
        chat_log = all_logs[conversation_id]
        content = chat_log.content if chat_log else []
        from .context_compressor import _estimate_total_tokens, get_compressor
        tokens_used = _estimate_total_tokens(content)
        try:
            cc = get_compressor()
            context_window = cc.context_length
        except Exception:
            context_window = 262144
    else:
        for cid, clog in all_logs.items():
            if clog and hasattr(clog, "content") and clog.content:
                from .context_compressor import _estimate_total_tokens, get_compressor
                t = _estimate_total_tokens(clog.content)
                if t > tokens_used:
                    tokens_used = t
                    conversation_id = cid
        if tokens_used:
            try:
                from .context_compressor import get_compressor
                cc = get_compressor()
                context_window = cc.context_length
            except Exception:
                context_window = 262144

    if agent_id:
        try:
            entry = hass.config_entries.async_get_entry(agent_id)
            if entry:
                model = entry.title or ""
                if not model:
                    model = (entry.data or {}).get("model", "") or (entry.options or {}).get("model", "")
        except Exception:
            pass
        if not model:
            model = agent_id.split(".")[-1] if "." in agent_id else agent_id

    connection.send_result(msg["id"], {
        "model": model,
        "tokens_used": tokens_used,
        "context_window": context_window or 262144,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
    })


def install_official_websocket_process_hook(hass) -> None:

    domain_data = hass.data.setdefault("claw_assistant", {})
    if _PATCH_KEY in domain_data:
        return
    handlers = hass.data.setdefault("websocket_api", {})
    domain_data[_PATCH_KEY] = handlers.get("conversation/process", _NO_HANDLER)
    websocket_api.async_register_command(hass, streaming_websocket_process)
    websocket_api.async_register_command(hass, websocket_get_pending_js)
    websocket_api.async_register_command(hass, websocket_report_state)
    websocket_api.async_register_command(hass, websocket_get_settings)
    websocket_api.async_register_command(hass, websocket_get_context_status)


def uninstall_official_websocket_process_hook(hass) -> None:

    domain_data = hass.data.setdefault("claw_assistant", {})
    original_handler = domain_data.pop(_PATCH_KEY, _UNSET)
    if original_handler is _UNSET:
        return

    handlers = hass.data.setdefault("websocket_api", {})
    if original_handler is _NO_HANDLER:
        handlers.pop("conversation/process", None)
        return

    handlers["conversation/process"] = original_handler
