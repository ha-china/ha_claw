from __future__ import annotations

import logging
import re
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from ...conversation_utils import get_conversation_history

_LOGGER = logging.getLogger(__name__)

_HISTORY_WINDOW_ID_KEY = "history_window_id"
_RESUME_HISTORY_ID_KEY = "resume_history_conversation_id"
_RESUME_HISTORY_WINDOW_ID_KEY = "resume_history_window_id"


def get_active_resume_history_id(hass: HomeAssistant) -> str:
    from ..core.state import get_conversation_status

    status = get_conversation_status(hass)
    resume_id = str(status.get(_RESUME_HISTORY_ID_KEY) or "")
    return resume_id


def clear_resume_history_binding(hass: HomeAssistant) -> None:
    from ..core.state import get_conversation_status

    status = get_conversation_status(hass)
    status.pop(_RESUME_HISTORY_ID_KEY, None)
    status.pop(_RESUME_HISTORY_WINDOW_ID_KEY, None)


_INTERNAL_TAG_RE = re.compile(r"\[-\s*\w+[:：][\s\S]*?\]", re.DOTALL)
_ACTIVITY_BLOCK_RE = re.compile(
    r"<\s*activity-context\s*>[\s\S]*?</\s*activity-context\s*>",
    flags=re.IGNORECASE,
)


def _strip_internal_tags(text: str) -> str:
    text = _INTERNAL_TAG_RE.sub("", text)
    text = _ACTIVITY_BLOCK_RE.sub("", text)
    return text.strip()


_CHANNEL_PREFIXES = {
    "wechat:": "WeChat",
    "feishu:": "Feishu",
    "dingtalk:": "DingTalk",
    "qq:": "QQ",
    "wecom:": "WeCom",
    "xiaoyi:": "XiaoYi",
}


def _extract_channel_from_conversation_id(conv_id: str) -> str:
    for prefix, name in _CHANNEL_PREFIXES.items():
        if conv_id.startswith(prefix):
            return name
    return ""


def _extract_channel_from_turns(turns: list) -> str:
    for turn in reversed(turns):
        channel = (turn.metadata or {}).get("channel", "")
        if channel:
            return channel
    return ""


def _summarize_conversation(turns: list, max_len: int = 60) -> str:
    title = str((turns[0].metadata or {}).get("title", "") or "").strip() if turns else ""
    if title:
        return title

    for t in turns:
        msg = _strip_internal_tags((t.user_message or "").strip())
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
    history.consolidate_duplicates()
    now = time.time()
    conversations = []

    for conv_id, turns in history._histories.items():
        if not turns:
            continue
        first_ts = turns[0].timestamp
        last_ts = turns[-1].timestamp
        summary = _summarize_conversation(turns)
        channel = _extract_channel_from_turns(turns) or _extract_channel_from_conversation_id(conv_id) or "Legacy"

        conversations.append({
            "conversation_id": conv_id,
            "summary": summary,
            "turn_count": len(turns),
            "first_message_at": first_ts,
            "last_message_at": last_ts,
            "seconds_ago": int(now - last_ts),
            "channel": channel,
            "in_progress": False,
        })

    for conv_id, progress in history._in_progress.items():
        existing = next((c for c in conversations if c["conversation_id"] == conv_id), None)
        if existing:
            existing["in_progress"] = True
        else:
            user_msg = progress.get("user_message", "")[:60]
            ts = progress.get("timestamp", now)
            conversations.append({
                "conversation_id": conv_id,
                "summary": user_msg or "进行中...",
                "turn_count": 0,
                "first_message_at": ts,
                "last_message_at": ts,
                "seconds_ago": int(now - ts),
                "channel": _extract_channel_from_conversation_id(conv_id) or "HA",
                "in_progress": True,
            })

    conversations.sort(key=lambda c: c["last_message_at"], reverse=True)
    connection.send_result(msg["id"], {"conversations": conversations})


DEFAULT_DISPLAY_DEPTH = 10
MAX_TOOL_CALLS_DISPLAY = 3


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/chat_history_get",
        vol.Required("conversation_id"): str,
        vol.Optional("max_turns", default=50): int,
        vol.Optional("display_depth", default=DEFAULT_DISPLAY_DEPTH): int,
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
    display_depth = msg.get("display_depth", DEFAULT_DISPLAY_DEPTH)
    turns = list(history.get_history(conv_id))
    in_progress = history.get_in_progress(conv_id)

    result_turns = []
    total_chars = 0
    for t in turns[-max_turns:]:
        metadata = t.metadata or {}
        user_msg = t.user_message or ""
        assistant_msg = t.assistant_response or ""
        raw_tool_calls = t.tool_calls or []
        total_chars += len(user_msg) + len(assistant_msg)

        tool_calls_normalized = []
        for tc in raw_tool_calls:
            if isinstance(tc, dict):
                tool_calls_normalized.append(tc)
            elif isinstance(tc, str):
                tool_calls_normalized.append({"tool_name": tc})

        if tool_calls_normalized:
            import json
            total_chars += len(json.dumps(tool_calls_normalized, ensure_ascii=False))

        source = metadata.get("source", "")
        is_command = source == "command" or user_msg.strip().startswith("/")
        result_turns.append({
            "user": _strip_internal_tags(user_msg),
            "assistant": assistant_msg,
            "assistant_display": metadata.get("assistant_display", ""),
            "agent_id": metadata.get("agent_id", ""),
            "agent_name": metadata.get("agent_name", ""),
            "timestamp": t.timestamp,
            "tool_calls": tool_calls_normalized,
            "source": source,
            "is_command": is_command,
        })

    if in_progress:
        user_msg = str(in_progress.get("user_message") or "")
        timestamp = float(in_progress.get("timestamp") or time.time())
        if user_msg and not (
            result_turns
            and result_turns[-1].get("user") == _strip_internal_tags(user_msg)
            and not result_turns[-1].get("assistant")
        ):
            total_chars += len(user_msg)
            is_cmd = user_msg.strip().startswith("/")
            result_turns.append({
                "user": _strip_internal_tags(user_msg),
                "assistant": "",
                "assistant_display": "",
                "agent_id": "",
                "agent_name": "",
                "timestamp": timestamp,
                "tool_calls": [],
                "in_progress": True,
                "source": "",
                "is_command": is_cmd,
            })

    display_turns = result_turns[-display_depth:] if display_depth > 0 else result_turns
    for dt in display_turns:
        dt["tool_calls"] = dt["tool_calls"][:MAX_TOOL_CALLS_DISPLAY]

    tokens_estimate = total_chars // 3 + len(turns) * 10 if total_chars else 0

    connection.send_result(msg["id"], {
        "conversation_id": conv_id,
        "turns": display_turns,
        "total_turns": len(result_turns),
        "tokens_used": tokens_estimate,
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
    from ..core.state import get_conversation_status

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
    from ..core.state import get_conversation_status

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
