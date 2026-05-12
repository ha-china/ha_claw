from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from ..conversation_utils import get_conversation_history

_LOGGER = logging.getLogger(__name__)

_HISTORY_WINDOW_ID_KEY = "history_window_id"
_RESUME_HISTORY_ID_KEY = "resume_history_conversation_id"
_RESUME_HISTORY_WINDOW_ID_KEY = "resume_history_window_id"


def get_active_resume_history_id(hass: HomeAssistant) -> str:
    from .state import get_conversation_status

    status = get_conversation_status(hass)
    window_id = str(status.get(_HISTORY_WINDOW_ID_KEY) or "")
    resume_window_id = str(status.get(_RESUME_HISTORY_WINDOW_ID_KEY) or "")
    if not window_id or resume_window_id != window_id:
        return ""
    return str(status.get(_RESUME_HISTORY_ID_KEY) or "")


def clear_resume_history_binding(hass: HomeAssistant) -> None:
    from .state import get_conversation_status

    status = get_conversation_status(hass)
    status.pop(_RESUME_HISTORY_ID_KEY, None)
    status.pop(_RESUME_HISTORY_WINDOW_ID_KEY, None)


def _summarize_conversation(turns: list, max_len: int = 60) -> str:
    title = str((turns[0].metadata or {}).get("title", "") or "").strip() if turns else ""
    if title:
        return title

    for t in turns:
        msg = (t.user_message or "").strip()
        if msg and not msg.startswith("/"):
            return msg[:max_len] + ("..." if len(msg) > max_len else "")
    for t in turns:
        resp = (t.assistant_response or "").strip()
        if resp:
            return resp[:max_len] + ("..." if len(resp) > max_len else "")
    return "New conversation"


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_list",
    }
)
@websocket_api.async_response
async def websocket_chat_history_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    history = get_conversation_history()
    now = time.time()
    conversations = []

    for conv_id, turns in history._histories.items():
        if not turns:
            continue
        first_ts = turns[0].timestamp
        last_ts = turns[-1].timestamp
        summary = _summarize_conversation(turns)

        conversations.append({
            "conversation_id": conv_id,
            "summary": summary,
            "turn_count": len(turns),
            "first_message_at": first_ts,
            "last_message_at": last_ts,
            "seconds_ago": int(now - last_ts),
        })

    conversations.sort(key=lambda c: c["last_message_at"], reverse=True)
    connection.send_result(msg["id"], {"conversations": conversations})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_get",
        vol.Required("conversation_id"): str,
        vol.Optional("max_turns", default=50): int,
    }
)
@websocket_api.async_response
async def websocket_chat_history_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    history = get_conversation_history()
    conv_id = msg["conversation_id"]
    max_turns = msg.get("max_turns", 50)
    turns = history.get_history(conv_id)

    result_turns = []
    for t in turns[-max_turns:]:
        metadata = t.metadata or {}
        result_turns.append({
            "user": t.user_message,
            "assistant": t.assistant_response,
            "assistant_display": metadata.get("assistant_display", ""),
            "agent_id": metadata.get("agent_id", ""),
            "agent_name": metadata.get("agent_name", ""),
            "timestamp": t.timestamp,
            "tool_calls": t.tool_calls or [],
        })

    connection.send_result(msg["id"], {
        "conversation_id": conv_id,
        "turns": result_turns,
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_delete",
        vol.Required("conversation_id"): str,
    }
)
@websocket_api.async_response
async def websocket_chat_history_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    history = get_conversation_history()
    conv_id = msg["conversation_id"]
    removed = history.clear(conv_id)
    connection.send_result(msg["id"], {
        "conversation_id": conv_id,
        "removed_turns": removed,
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_resume",
        vol.Required("conversation_id"): str,
        vol.Required("window_id"): str,
    }
)
@websocket_api.async_response
async def websocket_chat_history_resume(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    from .state import get_conversation_status

    history = get_conversation_history()
    conv_id = msg["conversation_id"]
    window_id = msg["window_id"]
    turns = history.get_history(conv_id)

    if not turns:
        connection.send_result(msg["id"], {
            "success": False,
            "error": "No history found for this conversation",
        })
        return

    status = get_conversation_status(hass)
    status[_HISTORY_WINDOW_ID_KEY] = window_id
    status[_RESUME_HISTORY_ID_KEY] = conv_id
    status[_RESUME_HISTORY_WINDOW_ID_KEY] = window_id

    connection.send_result(msg["id"], {
        "success": True,
        "conversation_id": conv_id,
        "turn_count": len(turns),
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_window",
        vol.Required("window_id"): str,
    }
)
@websocket_api.async_response
async def websocket_chat_history_window(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    from .state import get_conversation_status

    window_id = msg["window_id"]
    status = get_conversation_status(hass)
    previous_window_id = str(status.get(_HISTORY_WINDOW_ID_KEY) or "")
    status[_HISTORY_WINDOW_ID_KEY] = window_id
    if previous_window_id != window_id:
        status.pop(_RESUME_HISTORY_ID_KEY, None)
        status.pop(_RESUME_HISTORY_WINDOW_ID_KEY, None)
    connection.send_result(msg["id"], {"success": True, "window_id": window_id})


def register_chat_history_websocket(hass: HomeAssistant) -> None:
    handlers = hass.data.setdefault("websocket_api", {})
    for cmd_type, cmd in (
        ("ha_crack/chat_history_list", websocket_chat_history_list),
        ("ha_crack/chat_history_get", websocket_chat_history_get),
        ("ha_crack/chat_history_delete", websocket_chat_history_delete),
        ("ha_crack/chat_history_resume", websocket_chat_history_resume),
        ("ha_crack/chat_history_window", websocket_chat_history_window),
    ):
        if cmd_type not in handlers:
            websocket_api.async_register_command(hass, cmd)
    _LOGGER.debug("Registered chat history websocket handlers")
