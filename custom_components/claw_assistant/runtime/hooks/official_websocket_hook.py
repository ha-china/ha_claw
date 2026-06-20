

from __future__ import annotations

import asyncio
import base64
import logging
import re
import subprocess
from time import monotonic
import uuid
from pathlib import Path

from aiohttp import web
import voluptuous as vol

from homeassistant.components import conversation, websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.conversation.agent_manager import agent_id_validator
from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
from homeassistant.components.conversation.const import ChatLogEventType
from homeassistant.core import callback
from homeassistant.helpers.chat_session import async_get_chat_session

from ...const import CONF_ENABLE_CONTEXT_STATUS_BAR, CONF_ENABLE_FILE_UPLOAD, CONF_ENABLE_SIDEBAR_DOCK, CONF_ENABLE_SOUND_NOTIFICATIONS, DOMAIN
from ..history.continuous_conversation import (
    continuous_conversation_enabled,
    get_effective_conversation_id,
)
from ..storage.live_turn_store import (
    async_save_live_turn_snapshot,
    async_get_live_turn_events,
    async_get_live_turn_snapshot,
)
from ..utils.data_path import get_tmp_dir

_LOGGER = logging.getLogger(__name__)

_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
_VIDEO_TRIM_SECONDS = 30
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v", ".ts", ".3gp"})
_COMMAND_LINE_RE = re.compile(r"(^|\n)\s*/[a-zA-Z][\w\-]*\b", re.MULTILINE)


def _get_duration(file_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _trim_video_if_needed(file_path: str, seg: int = 10) -> str:
    p = Path(file_path)
    if p.suffix.lower() not in _VIDEO_EXTENSIONS:
        return file_path
    dur = _get_duration(file_path)
    if dur <= seg * 3:
        return file_path
    mid = (dur - seg) / 2
    end = max(0, dur - seg)
    parts = []
    tmp_dir = p.parent
    try:
        for i, ss in enumerate([0, mid, end]):
            part = tmp_dir / f"{p.stem}_seg{i}{p.suffix}"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-i", str(p),
                 "-t", str(seg), "-c", "copy",
                 "-avoid_negative_ts", "make_zero", str(part)],
                capture_output=True, timeout=30,
            )
            if proc.returncode == 0 and part.is_file() and part.stat().st_size > 0:
                parts.append(part)
        if len(parts) >= 2:
            concat_list = tmp_dir / f"{p.stem}_concat.txt"
            concat_list.write_text("\n".join(f"file '{pt}'" for pt in parts))
            out = p.with_stem(p.stem + "_trim")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat_list), "-c", "copy", str(out)],
                capture_output=True, timeout=30,
            )
            concat_list.unlink(missing_ok=True)
            if out.is_file() and out.stat().st_size > 0:
                p.unlink(missing_ok=True)
                out.rename(p)
                _LOGGER.info("Trimmed video (front+mid+end %ds each): %s", seg, p.name)
    except Exception as err:
        _LOGGER.debug("Video trim skipped: %s", err)
    finally:
        for pt in parts:
            pt.unlink(missing_ok=True)
    return file_path

_PATCH_KEY = "_claw_assistant_streaming_conversation_process"
_NO_HANDLER = object()
_UNSET = object()
_PENDING_JS_KEY = "ha_crack_pending_js"
_FRONTEND_STATE_KEY = "ha_crack_frontend_state"


def _domain_data(hass) -> dict:
    return hass.data.setdefault("claw_assistant", {})


def get_frontend_state(hass) -> dict:
    return _domain_data(hass).get(_FRONTEND_STATE_KEY, {})


def get_frontend_platform(hass) -> str | None:
    return get_frontend_state(hass).get("platform")


def context_status_bar_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_CONTEXT_STATUS_BAR, True):
            return True
    return False


def file_upload_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_FILE_UPLOAD, True):
            return True
    return False


def sidebar_dock_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_SIDEBAR_DOCK, True):
            return True
    return False


def sound_notifications_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_SOUND_NOTIFICATIONS, True):
            return True
    return False


def activity_tracking_enabled(hass) -> bool:
    from ...const import CONF_ENABLE_ACTIVITY_TRACKING
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_ACTIVITY_TRACKING, True):
            return True
    return False


def tool_details_enabled(hass) -> bool:
    from ...const import CONF_ENABLE_TOOL_DETAILS
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_TOOL_DETAILS, False):
            return True
    return False


def tool_progress_enabled(hass) -> bool:
    from ...const import CONF_ENABLE_TOOL_PROGRESS
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_TOOL_PROGRESS, True):
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
    dd = _domain_data(hass)
    pending = dd.setdefault(_PENDING_JS_KEY, [])
    cursors = dd.setdefault("_pending_js_cursors", {})
    conn_id = id(connection)
    is_new = conn_id not in cursors
    cursor = cursors.get(conn_id, len(pending))
    unseen = pending[cursor:]
    cursors[conn_id] = len(pending)
    if len(pending) > 200:
        min_cursor = min(cursors.values()) if cursors else len(pending)
        if min_cursor > 0:
            del pending[:min_cursor]
            for k in cursors:
                cursors[k] -= min_cursor
    connection.send_result(msg["id"], {"js_codes": unseen})


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
            "enable_file_upload": file_upload_enabled(hass),
            "enable_sidebar_dock": sidebar_dock_enabled(hass),
            "enable_sound_notifications": sound_notifications_enabled(hass),
            "enable_activity_tracking": activity_tracking_enabled(hass),
            "enable_tool_details": tool_details_enabled(hass),
            "enable_tool_progress": tool_progress_enabled(hass),
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

    dd = _domain_data(hass)
    detached_tasks = dd.setdefault("_claw_detached_converse_tasks", {})

    @callback
    def forward_events(
        event_conversation_id: str,
        event_type: ChatLogEventType,
        data: dict,
    ) -> None:
        if event_conversation_id != conversation_id:
            return
        from ...const import CONF_ENABLE_TOOL_DETAILS, DOMAIN
        _details_on = False
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.options.get(CONF_ENABLE_TOOL_DETAILS, False):
                _details_on = True
                break
        if not _details_on and isinstance(data, dict):
            delta = data.get("delta")
            if isinstance(delta, dict) and "tool_calls" in delta and not delta.get("content"):
                return
        evt_payload = {
            "conversation_id": event_conversation_id,
            "event_type": event_type,
            "data": data,
        }
        _buffer_live_event(hass, event_conversation_id, evt_payload)
        try:
            connection.send_event(msg["id"], evt_payload)
        except Exception:
            pass

    unsubscribe = async_subscribe_chat_logs(hass, forward_events)

    captured_context = connection.context(msg)
    text = str(msg.get("text") or "").strip()
    is_command_like = False
    if text:
        try:
            from ...chat_commands import parse_chat_command

            is_command_like = parse_chat_command(text) is not None
        except Exception:
            # Fallback heuristic if command parser is temporarily unavailable.
            is_command_like = bool(re.search(r"(?<![\\w/])/[a-zA-Z][\\w\\-]*", text))
    recent_map = hass.data.setdefault("_claw_cn_recent_messages", {})
    if text in ("/new", "/reset"):
        recent_map.pop(conversation_id, None)
        _clear_live_event_buffer(hass, conversation_id)
    elif text and is_command_like:
        # Command turns must not pollute stitched "recent user messages".
        recent_map.pop(conversation_id, None)
    elif text and not is_command_like:
        recent = recent_map.setdefault(conversation_id, [])
        recent.append(text)
        # Never let slash-commands participate in "recent user message" context
        # stitching. Otherwise an old command (e.g. "/model 12") can be replayed
        # by later plain-text turns when the combined text is parsed.
        filtered_recent: list[str] = []
        for item in recent:
            item_text = str(item or "").strip()
            if not item_text:
                continue
            try:
                from ...chat_commands import parse_chat_command

                if parse_chat_command(item_text) is not None:
                    continue
            except Exception:
                if re.search(r"(?<![\\w/])/[a-zA-Z][\\w\\-]*", item_text):
                    continue
            # Extra hard filter: drop any message that contains a slash-command
            # line even if parser heuristics miss it.
            if _COMMAND_LINE_RE.search(item_text):
                continue
            filtered_recent.append(item_text)
        recent[:] = filtered_recent
        del recent[:-3]
        if len(recent) > 1:
            combined = "\n".join(
                f"[Recent user message {idx + 1}] {item}"
                for idx, item in enumerate(recent)
            )
            text = f"{combined}\n\n[Current request] {text}"

    converse_kwargs = dict(
        text=text,
        conversation_id=conversation_id,
        context=captured_context,
        language=msg.get("language"),
        agent_id=msg.get("agent_id"),
        device_id=msg.get("device_id"),
        satellite_id=msg.get("satellite_id"),
    )

    existing_task = detached_tasks.get(conversation_id)
    if existing_task is not None and not existing_task.done():
        _LOGGER.info("Interrupting in-flight detached conversation task for %s", conversation_id)
        existing_task.cancel("Interrupted by new websocket message")
        detached_tasks.pop(conversation_id, None)

    async def _run_converse():
        try:
            result = await conversation.async_converse(hass=hass, **converse_kwargs)
        except asyncio.CancelledError:
            await async_save_live_turn_snapshot(
                hass,
                conversation_id=conversation_id,
                active=False,
                status="cancelled",
                reason="Interrupted by a newer message",
                text=text,
                phase="websocket_cancelled",
            )
            raise
        except Exception as err:
            await async_save_live_turn_snapshot(
                hass,
                conversation_id=conversation_id,
                active=False,
                status="error",
                reason=str(err),
                text=text,
                phase="websocket_error",
            )
            raise
        else:
            await async_save_live_turn_snapshot(
                hass,
                conversation_id=conversation_id,
                active=False,
                status="finished",
                text=text,
                phase="websocket_finished",
            )
            return result
        finally:
            _buffer_live_event(hass, conversation_id, {
                "conversation_id": conversation_id,
                "event_type": "stream_end",
                "data": {},
            })
            if detached_tasks.get(conversation_id) is asyncio.current_task():
                detached_tasks.pop(conversation_id, None)
            async def _delayed_clear():
                await asyncio.sleep(10)
                if not detached_tasks.get(conversation_id):
                    _clear_live_event_buffer(hass, conversation_id)
            hass.async_create_background_task(_delayed_clear(), name=f"claw_clear_buf_{conversation_id}")

    try:
        await async_save_live_turn_snapshot(
            hass,
            conversation_id=conversation_id,
            active=True,
            status="running",
            text=text,
            phase="websocket_detached",
        )
    except Exception:
        _LOGGER.debug("Failed to persist websocket turn start for %s", conversation_id, exc_info=True)

    converse_task = hass.async_create_background_task(
        _run_converse(),
        name=f"claw_converse_{conversation_id}",
    )
    detached_tasks[conversation_id] = converse_task

    try:
        result = await asyncio.shield(asyncio.wrap_future(asyncio.ensure_future(converse_task)) if False else _await_task(converse_task))
    except asyncio.CancelledError:
        _LOGGER.info("WebSocket cancelled for conv=%s; backend converse continues detached", conversation_id)
        unsubscribe()
        raise
    except Exception as err:
        _LOGGER.error("conversation/process error: %s", err)
        try:
            connection.send_error(msg["id"], "conversation_error", str(err))
        except Exception:
            pass
        unsubscribe()
        return
    finally:
        try:
            unsubscribe()
        except Exception:
            pass

    try:
        connection.send_event(
            msg["id"],
            {
                "conversation_id": conversation_id,
                "event_type": "stream_end",
                "data": {},
            },
        )
        connection.send_result(msg["id"], result.as_dict())
    except Exception:
        pass


async def _await_task(task):
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        raise


_LIVE_EVENT_BUFFER_KEY = "_claw_live_event_buffer"
_LIVE_EVENT_MAX = 200
_LIVE_SUBSCRIBERS_KEY = "_claw_live_subscribers"
_LIVE_EVENT_PERSIST_TS_KEY = "_claw_live_event_persist_ts"
_LIVE_EVENT_PERSIST_INTERVAL = 1.0


def _should_persist_live_event(dd: dict, conversation_id: str, evt: dict) -> bool:
    event_type = evt.get("event_type")
    if event_type == "stream_end":
        return True
    data = evt.get("data")
    delta = data.get("delta") if isinstance(data, dict) else None
    if isinstance(delta, dict) and (delta.get("tool_calls") or delta.get("role") == "tool_result"):
        return True
    timestamps = dd.setdefault(_LIVE_EVENT_PERSIST_TS_KEY, {})
    now = monotonic()
    last = float(timestamps.get(conversation_id, 0.0) or 0.0)
    if now - last < _LIVE_EVENT_PERSIST_INTERVAL:
        return False
    timestamps[conversation_id] = now
    return True


def _buffer_live_event(hass, conversation_id: str, evt: dict) -> None:
    dd = _domain_data(hass)
    buf = dd.setdefault(_LIVE_EVENT_BUFFER_KEY, {})
    events = buf.setdefault(conversation_id, [])
    events.append(evt)
    if len(events) > _LIVE_EVENT_MAX:
        events[:] = events[-_LIVE_EVENT_MAX:]
    if _should_persist_live_event(dd, conversation_id, evt):
        try:
            hass.async_create_task(
                async_save_live_turn_snapshot(
                    hass,
                    conversation_id=conversation_id,
                    active=True,
                    status="running",
                    event=evt,
                    update_status=False,
                ),
                "claw_live_event_persist",
            )
        except Exception:
            _LOGGER.debug("Failed to persist live event for %s", conversation_id, exc_info=True)
    for sub_conn, sub_msg_id, sub_conv_id in dd.get(_LIVE_SUBSCRIBERS_KEY, []):
        if sub_conv_id == conversation_id:
            try:
                sub_conn.send_event(sub_msg_id, evt)
            except Exception:
                pass


def _clear_live_event_buffer(hass, conversation_id: str) -> None:
    dd = _domain_data(hass)
    buf = dd.get(_LIVE_EVENT_BUFFER_KEY, {})
    buf.pop(conversation_id, None)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/subscribe_live_stream",
        vol.Optional("conversation_id", default=""): str,
    }
)
@websocket_api.async_response
async def websocket_subscribe_live_stream(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    from ..core.state import get_active_conversation_state, get_conversation_status

    requested_conv_id = msg.get("conversation_id") or ""
    if not requested_conv_id:
        status = get_conversation_status(hass)
        active_conv = get_active_conversation_state(hass)
        requested_conv_id = active_conv.get("id") or status.get("last_conversation_id") or ""

    if not requested_conv_id:
        connection.send_result(msg["id"], {"replayed": 0, "conversation_id": None})
        return

    dd = _domain_data(hass)
    buf = dd.get(_LIVE_EVENT_BUFFER_KEY, {})
    cached = list(buf.get(requested_conv_id, []))
    if not cached:
        cached = await async_get_live_turn_events(hass, requested_conv_id)

    subs = dd.setdefault(_LIVE_SUBSCRIBERS_KEY, [])
    sub_entry = (connection, msg["id"], requested_conv_id)
    subs.append(sub_entry)

    @callback
    def _on_disconnect():
        try:
            subs.remove(sub_entry)
        except ValueError:
            pass

    connection.subscriptions[msg["id"]] = _on_disconnect
    connection.send_result(msg["id"], {"replayed": len(cached), "conversation_id": requested_conv_id})

    for evt in cached:
        connection.send_event(msg["id"], evt)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/live_turn_snapshot",
    }
)
@websocket_api.async_response
async def websocket_live_turn_snapshot(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    from ..core.state import (
        get_conversation_status,
        get_active_conversation_state,
        get_runtime_store,
    )
    from ...chat_commands import _task_registry
    from ...conversation_utils import get_conversation_history

    status = get_conversation_status(hass)
    active_conv = get_active_conversation_state(hass)
    runtime_store = get_runtime_store(hass)
    conv_history = get_conversation_history()

    conversation_id = active_conv.get("id") or status.get("last_conversation_id") or ""
    running_tasks = _task_registry(hass)
    is_running = bool(conversation_id and conversation_id in running_tasks)

    if not is_running and conv_history:
        in_progress_ids = list(conv_history._in_progress.keys())
        if in_progress_ids:
            conversation_id = in_progress_ids[0]
            is_running = True

    if not is_running:
        persisted = await async_get_live_turn_snapshot(hass, conversation_id or "")
        if persisted:
            persisted_tools = persisted.get("tool_results", [])
            persisted_events = persisted.get("events", [])
            connection.send_result(msg["id"], {
                "active": bool(persisted.get("active", False)),
                "conversation_id": persisted.get("conversation_id") or conversation_id or None,
                "current_thought": persisted.get("current_thought"),
                "tool_activities": persisted_tools[:10] if isinstance(persisted_tools, list) else [],
                "response_parts": persisted.get("response_parts", []),
                "phase": persisted.get("phase") or persisted.get("status") or "recovered",
                "status": persisted.get("status", ""),
                "reason": persisted.get("reason", ""),
                "started_at": persisted.get("started_at", ""),
                "updated_at": persisted.get("updated_at", ""),
                "recovered": True,
                "replay_events": persisted_events[-20:] if isinstance(persisted_events, list) else [],
            })
            return
        connection.send_result(msg["id"], {
            "active": False,
            "conversation_id": conversation_id or None,
        })
        return

    current_thought = status.get("current_thought") or ""
    tool_activities = []
    live_response_parts = runtime_store.get("live_response_parts", {}).get(conversation_id, [])

    tool_results_container = runtime_store.get("tool_results", {})
    if isinstance(tool_results_container, dict):
        tool_results = tool_results_container.get(conversation_id, [])
    else:
        tool_results = []

    for tr in tool_results:
        if isinstance(tr, dict):
            tool_activities.append({
                "tool_name": tr.get("tool_name", "tool"),
                "success": tr.get("success", False),
                "result_preview": str(tr.get("result", ""))[:200] if tr.get("result") else None,
                "error": tr.get("error"),
            })

    turn_start_times = runtime_store.get("turn_start_times", {})
    window_start_times = runtime_store.get("window_start_times", {})
    
    connection.send_result(msg["id"], {
        "active": True,
        "conversation_id": conversation_id,
        "current_thought": current_thought[:500] if current_thought else None,
        "tool_activities": tool_activities[:10],
        "response_parts": live_response_parts[-5:] if live_response_parts else [],
        "phase": status.get("current_phase", "thinking"),
        "turn_start_time": turn_start_times.get(conversation_id),
        "window_start_time": window_start_times.get(conversation_id),
    })


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
    model = ""
    agent_id = ""
    conversation_id = ""

    status = hass.data.get("claw_assistant", {}).get("runtime_state", {}).get("conversation_status", {})
    agent_id = status.get("current_agent_id", "")

    active_conv = hass.data.get("claw_assistant", {}).get("runtime_state", {}).get("active_conversation", {})
    conversation_id = active_conv.get("id") or ""

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
        "tokens_used": 0,
        "context_window": 262144,
        "agent_id": agent_id,
        "conversation_id": conversation_id,
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/upload_file",
        vol.Required("filename"): str,
        vol.Required("data"): str,
        vol.Optional("mime_type", default=""): str,
    }
)
@websocket_api.async_response
async def websocket_upload_file(
    hass,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    filename = msg["filename"].strip()
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        connection.send_error(msg["id"], "invalid_filename", "Invalid filename")
        return

    try:
        raw = base64.b64decode(msg["data"])
    except Exception:
        connection.send_error(msg["id"], "decode_error", "Invalid base64 data")
        return

    if len(raw) > _UPLOAD_MAX_BYTES:
        connection.send_error(
            msg["id"], "too_large",
            f"File too large: {len(raw)} bytes (max {_UPLOAD_MAX_BYTES})",
        )
        return

    ext = Path(filename).suffix.lower() or ".bin"
    safe_name = f"upload_{uuid.uuid4().hex[:12]}{ext}"

    def _write():
        tmp = get_tmp_dir(hass)
        dest = tmp / safe_name
        dest.write_bytes(raw)
        return str(dest)

    try:
        file_path = await hass.async_add_executor_job(_write)
    except Exception as err:
        _LOGGER.warning("Upload write failed: %s", err)
        connection.send_error(msg["id"], "write_error", str(err))
        return

    mime = msg.get("mime_type") or _guess_mime(filename)
    _LOGGER.info("File uploaded: %s (%d bytes) -> %s", filename, len(raw), file_path)
    connection.send_result(msg["id"], {
        "path": file_path,
        "mime_type": mime,
        "size": len(raw),
        "filename": filename,
    })


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    _MIME_MAP = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".ico": "image/x-icon",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
        ".json": "application/json", ".xml": "application/xml",
        ".yaml": "text/yaml", ".yml": "text/yaml",
        ".zip": "application/zip", ".gz": "application/gzip",
        ".tar": "application/x-tar",
    }
    return _MIME_MAP.get(ext, "application/octet-stream")


_BINARY_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
    b"BM", b"RIFF", b"%PDF-", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",
    b"\x1f\x8b",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"OggS", b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",
    b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00 ftyp",
    b"\x1aE\xdf\xa3",
    b"SQLite format 3\x00",
    b"\x7fELF", b"MZ",
    b"\x00asm",
)


def _sniff_is_text(head: bytes) -> bool:
    if not head:
        return False
    for sig in _BINARY_MAGIC_PREFIXES:
        if head.startswith(sig):
            return False
    if b"\x00" in head:
        return False
    sample = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        for cut in range(1, 4):
            try:
                sample[:-cut].decode("utf-8")
                return True
            except UnicodeDecodeError:
                continue
        return False
    return True


class ClawFileView(HomeAssistantView):
    url = "/claw_file/{filename:.+}"
    name = "claw_assistant:file"
    requires_auth = False

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        hass = request.app["hass"]
        from ..utils.data_path import output_dir_path
        output_dir = output_dir_path(hass).resolve()
        try:
            target = (output_dir / filename).resolve(strict=False)
            target.relative_to(output_dir)
        except (ValueError, OSError):
            return web.Response(status=403, text="forbidden")

        def _probe() -> tuple[bool, bytes]:
            try:
                if not target.is_file():
                    return False, b""
                with target.open("rb") as fh:
                    return True, fh.read(8192)
            except OSError:
                return False, b""

        is_file, head = await hass.async_add_executor_job(_probe)
        if not is_file:
            return web.Response(status=404, text="not found")

        mime = _guess_mime(target.name)
        if _sniff_is_text(head):
            base = mime if mime.startswith("text/") else "text/plain"
            content_type = f"{base}; charset=utf-8"
        else:
            content_type = mime
        return web.FileResponse(
            target, headers={"Content-Type": content_type}
        )


class ClawUploadView(HomeAssistantView):
    url = "/api/claw_assistant/upload"
    name = "api:claw_assistant:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "file":
            return web.json_response({"error": "no file field"}, status=400)
        filename = (field.filename or "upload").strip()
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            return web.json_response({"error": "invalid filename"}, status=400)
        chunks = []
        size = 0
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            if size > _UPLOAD_MAX_BYTES:
                return web.json_response({"error": "too large"}, status=413)
            chunks.append(chunk)
        raw = b"".join(chunks)
        ext = Path(filename).suffix.lower() or ".bin"
        safe_name = f"upload_{uuid.uuid4().hex[:12]}{ext}"
        mime = _guess_mime(filename)

        def _write():
            tmp = get_tmp_dir(hass)
            dest = tmp / safe_name
            dest.write_bytes(raw)
            return str(dest)

        try:
            file_path = await hass.async_add_executor_job(_write)
        except Exception as err:
            return web.json_response({"error": str(err)}, status=500)

        if mime.startswith("video/"):
            await hass.async_add_executor_job(_trim_video_if_needed, file_path)

        def _file_size() -> int:
            path = Path(file_path)
            return path.stat().st_size if path.is_file() else len(raw)

        final_size = await hass.async_add_executor_job(_file_size)
        return web.json_response({
            "path": file_path, "mime_type": mime,
            "size": final_size, "filename": filename,
        })


_INTENT_PATCH_KEY = "_claw_original_recognize_intent"


_LOCAL_INTENT_PATCH_KEY = "_claw_local_intent_patched"
_AIHUB_INTENT_PATCH_KEY = "_claw_aihub_intent_original"


def _install_local_intent_format_hook(hass) -> None:
    from homeassistant.components import conversation as conv_module

    if hass.data.get(_LOCAL_INTENT_PATCH_KEY):
        return

    original_handle_intents = conv_module.async_handle_intents

    async def _patched_handle_intents(hass_inner, user_input, chat_log, **kwargs):
        text = getattr(user_input, "text", "") or ""
        if len(text) > 200:
            return None
        response = await original_handle_intents(hass_inner, user_input, chat_log, **kwargs)
        if response is None:
            return None
        try:
            _stamp_intent_response_speech(hass_inner, response)
        except Exception:
            _LOGGER.debug("local intent speech stamp failed", exc_info=True)
        return response

    conv_module.async_handle_intents = _patched_handle_intents
    hass.data[_LOCAL_INTENT_PATCH_KEY] = original_handle_intents
    _LOGGER.debug("Installed local intent format hook on async_handle_intents")

    _install_aihub_intent_simplify_hook(hass)


def _uninstall_local_intent_format_hook(hass) -> None:
    _uninstall_aihub_intent_simplify_hook(hass)

    original = hass.data.pop(_LOCAL_INTENT_PATCH_KEY, None)
    if original is None:
        return
    from homeassistant.components import conversation as conv_module
    conv_module.async_handle_intents = original
    _LOGGER.debug("Uninstalled local intent format hook")


def _install_aihub_intent_simplify_hook(hass) -> None:
    if hass.data.get(_AIHUB_INTENT_PATCH_KEY):
        return
    try:
        from custom_components.ai_hub.conversation import AIHubConversationAgent
    except ImportError:
        _LOGGER.debug("ai_hub not installed, skip intent simplify hook")
        return

    original = AIHubConversationAgent._async_handle_local_and_builtin_intents

    async def _simplified_handle(self, user_input, chat_log):
        language = (getattr(user_input, "language", None) or "").lower()
        if not language.startswith("zh"):
            return None
        try:
            from custom_components.ai_hub.intents import get_global_intent_handler
            intent_handler = get_global_intent_handler(self.hass)
        except Exception as e:
            _LOGGER.debug("Local intent handler init failed: %s", e)
            return None
        try:
            return await self._async_try_local_intent_fallback(
                user_input, chat_log, intent_handler,
            )
        except Exception as e:
            _LOGGER.warning("Local intent strict match failed: %s", e, exc_info=True)
            return None

    AIHubConversationAgent._async_handle_local_and_builtin_intents = _simplified_handle
    hass.data[_AIHUB_INTENT_PATCH_KEY] = original
    _LOGGER.info("Installed ai_hub intent simplify hook (strict local match only)")


def _uninstall_aihub_intent_simplify_hook(hass) -> None:
    original = hass.data.pop(_AIHUB_INTENT_PATCH_KEY, None)
    if original is None:
        return
    try:
        from custom_components.ai_hub.conversation import AIHubConversationAgent
        AIHubConversationAgent._async_handle_local_and_builtin_intents = original
        _LOGGER.info("Uninstalled ai_hub intent simplify hook")
    except ImportError:
        pass


def _stamp_intent_response_speech(hass, response) -> None:
    from ...const import (
        CONF_CONVERSATION_MODE,
        CONVERSATION_MODE_NO_NAME,
        DEFAULT_CONVERSATION_MODE,
        DOMAIN,
    )
    from ..output.reply_formatter import format_reply_speech, strip_reply_prefix

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return
    mode = entries[0].options.get(CONF_CONVERSATION_MODE, DEFAULT_CONVERSATION_MODE)
    if mode == CONVERSATION_MODE_NO_NAME:
        return

    plain = response.speech.get("plain") if response.speech else None
    if not isinstance(plain, dict):
        return
    speech = plain.get("speech", "")
    if not speech or not isinstance(speech, str):
        return
    clean = strip_reply_prefix(speech)
    if clean.startswith("("):
        return

    from ..core.state import get_conversation_status
    lang = get_conversation_status(hass).get("user_language") or hass.config.language or "zh"
    stamped = format_reply_speech("Home Assistant", clean, lang)
    plain["speech"] = stamped


def _sanitize_orphaned_tool_results(chat_log) -> None:
    from homeassistant.components.conversation.chat_log import (
        AssistantContent,
        ToolResultContent,
    )

    content = getattr(chat_log, "content", None)
    if not content:
        return
    call_ids: set[str] = set()
    for msg in content:
        if isinstance(msg, AssistantContent) and msg.tool_calls:
            for tc in msg.tool_calls:
                cid = getattr(tc, "id", "") or ""
                if cid:
                    call_ids.add(cid)
    orphaned = []
    for i, msg in enumerate(content):
        if isinstance(msg, ToolResultContent):
            if msg.tool_call_id and msg.tool_call_id not in call_ids:
                orphaned.append(i)
    if orphaned:
        for idx in reversed(orphaned):
            content.pop(idx)
        _LOGGER.info("Sanitized %d orphaned tool_result(s) before continuation", len(orphaned))


def _install_recognize_intent_hook(hass) -> None:
    from homeassistant.components.assist_pipeline import pipeline as pipeline_mod
    from homeassistant.components.assist_pipeline.pipeline import PipelineRun
    from ..storage.goals import get_goal_manager
    from ..core.state import get_runtime_store

    if hass.data.get(_INTENT_PATCH_KEY):
        return

    _original_recognize = PipelineRun.recognize_intent

    async def _hooked_recognize_intent(self, intent_input, conversation_id, conversation_extra_system_prompt):
        original_converse = pipeline_mod.conversation.async_converse
        original_get_chat_log = pipeline_mod.conversation.async_get_chat_log
        flag_key = f"_claw_pipeline_converse_cont_{conversation_id}"

        def _hooked_get_chat_log(*args, **kwargs):
            listener = kwargs.get("chat_log_delta_listener")
            if listener is not None:
                def _tts_filtered_listener(chat_log, delta):
                    if (
                        isinstance(delta, dict)
                        and delta.get("_claw_skip_tts")
                        and delta.get("content")
                    ):
                        event_delta = dict(delta)
                        event_delta.pop("_claw_skip_tts", None)
                        self.process_event(
                            pipeline_mod.PipelineEvent(
                                pipeline_mod.PipelineEventType.INTENT_PROGRESS,
                                {"chat_log_delta": event_delta},
                            )
                        )
                        return
                    listener(chat_log, delta)

                kwargs = dict(kwargs)
                kwargs["chat_log_delta_listener"] = _tts_filtered_listener
            return original_get_chat_log(*args, **kwargs)

        async def _hooked_converse(*args, **kwargs):
            active_conversation_id = kwargs.get("conversation_id") or conversation_id
            _LOGGER.info(
                "pipeline async_converse hook: enter conv=%s text=%s",
                active_conversation_id, str(kwargs.get("text", ""))[:80],
            )
            result = await original_converse(*args, **kwargs)
            _LOGGER.info(
                "pipeline async_converse hook: first return conv=%s continue=%s",
                active_conversation_id, getattr(result, "continue_conversation", None),
            )
            if self.hass.data.get(flag_key):
                _LOGGER.info("pipeline async_converse hook: recursive call skipped conv=%s", active_conversation_id)
                return result
            self.hass.data[flag_key] = True
            try:
                for _ci in range(50):
                    active_conversation_id = kwargs.get("conversation_id") or conversation_id
                    runtime_store = get_runtime_store(self.hass)

                    from ..core.state import consume_should_end_flag
                    if consume_should_end_flag(self.hass):
                        _LOGGER.info(
                            "pipeline async_converse hook: should_end_flag set, stop conv=%s",
                            active_conversation_id,
                        )
                        break
                    from ...chat_commands import _stop_requests
                    sr = _stop_requests(self.hass)
                    if (active_conversation_id and active_conversation_id in sr) or (conversation_id and conversation_id in sr):
                        _LOGGER.info(
                            "pipeline async_converse hook: stop requested, stop conv=%s",
                            active_conversation_id,
                        )
                        break

                    completed = runtime_store.get("completed_goal_conversations", set())
                    if (
                        str(active_conversation_id) in completed
                        or str(conversation_id) in completed
                        or "default" in completed
                    ):
                        _LOGGER.info(
                            "pipeline async_converse hook: completed goal, stop conv=%s",
                            active_conversation_id,
                        )
                        break
                    pending = runtime_store.get("pending_goal_continuations", {})
                    prompt = (
                        pending.pop(str(active_conversation_id), None)
                        or pending.pop(str(conversation_id), None)
                        or pending.pop("latest", None)
                    )
                    goal_conversation_id = active_conversation_id
                    mgr = get_goal_manager(self.hass, goal_conversation_id)
                    await mgr.async_ensure_loaded()
                    active = bool(prompt) or mgr.is_active()
                    if not active and goal_conversation_id != conversation_id:
                        mgr = get_goal_manager(self.hass, conversation_id)
                        await mgr.async_ensure_loaded()
                        active = mgr.is_active()
                        goal_conversation_id = conversation_id
                    if not active and goal_conversation_id != "default":
                        mgr = get_goal_manager(self.hass, "default")
                        await mgr.async_ensure_loaded()
                        active = mgr.is_active()
                        goal_conversation_id = "default"
                    _LOGGER.info(
                        "pipeline async_converse hook: loop=%d active=%s conv=%s goal_conv=%s",
                        _ci, active, active_conversation_id, goal_conversation_id,
                    )
                    if not active:
                        break
                    if not prompt:
                        prompt = mgr.next_continuation_prompt()
                        if not prompt:
                            _LOGGER.warning(
                                "pipeline async_converse hook: no continuation prompt conv=%s",
                                goal_conversation_id,
                            )
                            break
                    try:
                        await async_save_live_turn_snapshot(
                            self.hass,
                            conversation_id=str(active_conversation_id or conversation_id or "default"),
                            active=True,
                            status="continuing",
                            text=prompt,
                            phase="pipeline_goal_continuation",
                        )
                    except Exception:
                        _LOGGER.debug(
                            "pipeline async_converse hook: failed to persist continuation snapshot",
                            exc_info=True,
                        )
                    _LOGGER.info(
                        "pipeline async_converse hook: continuation turn %d for %s prompt=%s",
                        _ci, active_conversation_id, prompt[:120],
                    )
                    next_kwargs = dict(kwargs)
                    next_kwargs["text"] = prompt
                    next_kwargs["conversation_id"] = active_conversation_id
                    restore_listener = None
                    listener_state = {"pending": True}
                    try:
                        from homeassistant.components.conversation.chat_log import current_chat_log
                        chat_log = current_chat_log.get()
                        if chat_log is not None:
                            _sanitize_orphaned_tool_results(chat_log)
                            if chat_log.delta_listener:
                                original_listener = chat_log.delta_listener

                                def prefixed_listener(inner_chat_log, delta):
                                    if listener_state["pending"] and delta.get("content"):
                                        delta = dict(delta)
                                        delta["content"] = "\n\n" + delta["content"]
                                        listener_state["pending"] = False
                                    original_listener(inner_chat_log, delta)

                                chat_log.delta_listener = prefixed_listener
                                restore_listener = (chat_log, original_listener)
                    except Exception:
                        _LOGGER.debug("pipeline async_converse hook: newline listener wrap failed", exc_info=True)
                    try:
                        result = await asyncio.wait_for(
                            original_converse(*args, **next_kwargs),
                            timeout=180,
                        )
                    except TimeoutError:
                        _LOGGER.warning(
                            "pipeline async_converse hook: continuation timeout %d conv=%s",
                            _ci, active_conversation_id,
                        )
                        break
                    finally:
                        await asyncio.sleep(0.2)
                        if restore_listener is not None:
                            restore_listener[0].delta_listener = restore_listener[1]
                    plain = result.response.speech.get("plain", {}) if getattr(result.response, "speech", None) else {}
                    speech_text = str(plain.get("speech", "") or "")
                    if speech_text.strip() and listener_state["pending"]:
                        try:
                            from ..history.native_chatlog_bridge import emit_live_content_delta
                            await emit_live_content_delta(
                                agent_id=str(next_kwargs.get("agent_id") or "assistant"),
                                text="\n\n" + speech_text,
                            )
                        except Exception:
                            _LOGGER.debug("pipeline async_converse hook: final speech delta failed", exc_info=True)
                    _LOGGER.info(
                        "pipeline async_converse hook: continuation return %d continue=%s speech=%s",
                        _ci, getattr(result, "continue_conversation", None),
                        speech_text[:120],
                    )
            finally:
                try:
                    await async_save_live_turn_snapshot(
                        self.hass,
                        conversation_id=str(conversation_id or "default"),
                        active=False,
                        status="finished",
                        phase="pipeline_goal_continuation_done",
                    )
                except Exception:
                    _LOGGER.debug(
                        "pipeline async_converse hook: failed to persist final continuation state",
                        exc_info=True,
                    )
                self.hass.data.pop(flag_key, None)
                _LOGGER.info("pipeline async_converse hook: exit conv=%s", conversation_id)
            return result

        pipeline_mod.conversation.async_converse = _hooked_converse
        pipeline_mod.conversation.async_get_chat_log = _hooked_get_chat_log

        start_stage = getattr(self, "start_stage", None)
        end_stage = getattr(self, "end_stage", None)
        start_stage_str = str(start_stage).lower() if start_stage else ""
        end_stage_str = str(end_stage).lower() if end_stage else ""
        is_voice_input = "stt" in start_stage_str or "wake_word" in start_stage_str
        has_tts_output = "tts" in end_stage_str
        is_voice_pipeline = is_voice_input or has_tts_output

        device_id = getattr(self, "_device_id", None)
        satellite_id = getattr(self, "_satellite_id", None)
        device_info = None
        detected_platform = None
        ava_identity = None
        if device_id or satellite_id:
            try:
                from ...ava_detector import detect_ava_identity

                ava_identity = detect_ava_identity(
                    self.hass,
                    satellite_id=satellite_id,
                    device_id=device_id,
                )
            except Exception:
                ava_identity = None
        if device_id:
            try:
                from homeassistant.helpers import device_registry as dr
                device_registry = dr.async_get(self.hass)
                device_entry = device_registry.async_get(device_id)
                if device_entry:
                    device_info = {
                        "manufacturer": device_entry.manufacturer,
                        "model": device_entry.model,
                        "name": device_entry.name,
                        "sw_version": device_entry.sw_version,
                    }
                    mfr = (device_entry.manufacturer or "").lower()
                    model = (device_entry.model or "").lower()
                    if "apple" in mfr or "iphone" in model or "ipad" in model:
                        detected_platform = "ios_app"
                    elif "samsung" in mfr or "google" in mfr or "xiaomi" in mfr or "huawei" in mfr or "oneplus" in mfr or "oppo" in mfr or "vivo" in mfr or "pixel" in model:
                        detected_platform = "android_app"
            except Exception:
                pass

        from ..core.state import get_conversation_status, PLATFORM_IOS_APP, PLATFORM_ANDROID_APP_V2, PLATFORM_AVA_SATELLITE
        conv_status = get_conversation_status(self.hass)
        conv_status["is_voice_pipeline"] = is_voice_pipeline
        conv_status["_voice_detection_source"] = f"pipeline:start={start_stage},end={end_stage}" if is_voice_pipeline else "pipeline:text"
        conv_status["_pipeline_start_stage"] = start_stage_str
        conv_status["_pipeline_end_stage"] = end_stage_str
        conv_status["_pipeline_device_id"] = device_id
        conv_status["_pipeline_device_info"] = device_info
        if ava_identity:
            from ...ava_detector import apply_ava_identity

            apply_ava_identity(conv_status, ava_identity)
            conv_status["_voice_detection_source"] = (
                f"pipeline:ava:start={start_stage},end={end_stage}"
                if is_voice_pipeline
                else "pipeline:ava:text"
            )
        elif detected_platform:
            conv_status["detected_platform"] = PLATFORM_IOS_APP if detected_platform == "ios_app" else PLATFORM_ANDROID_APP_V2
        if is_voice_pipeline:
            if ava_identity:
                from ...ava_detector import merge_voice_system_prompt

                conversation_extra_system_prompt = merge_voice_system_prompt(
                    conversation_extra_system_prompt,
                    ava_identity,
                )
            else:
                device_desc = ""
                if detected_platform == "ios_app":
                    device_desc = "Device: iOS Companion App.\n"
                elif detected_platform == "android_app":
                    device_desc = "Device: Android Companion App.\n"
                elif device_info and device_info.get("name"):
                    device_desc = f"Device: {device_info['name']}.\n"

                voice_hint = (
                    "## Channel\n"
                    "Type: voice (speech-to-text to text-to-speech pipeline).\n"
                    f"{device_desc}"
                    "The user spoke into a microphone and their words were transcribed by STT. "
                    "Your entire reply will be synthesized by a TTS engine and played back as audio. "
                    "The user will hear your answer, not read it. "
                    "Write exactly as you would speak to someone in person. "
                    "Reply as one continuous spoken paragraph in plain text only. "
                    "Do not use line breaks. "
                    "Use short, natural, conversational sentences. "
                    "Never use markdown formatting such as bold, italic, headings, lists, tables, or code blocks. "
                    "TTS will read the raw symbols aloud and it sounds terrible. "
                    "Never use emoji or special symbols. "
                    "They are either skipped or read as Unicode names. "
                    "Avoid media tags, URLs, file paths, and long numbers. "
                    "Paraphrase instead. "
                    "If the answer is complex, give a brief spoken summary and suggest "
                    "the user check the Home Assistant dashboard for details."
                )
                if conversation_extra_system_prompt:
                    conversation_extra_system_prompt = conversation_extra_system_prompt + "\n\n" + voice_hint
                else:
                    conversation_extra_system_prompt = voice_hint

        try:
            return await _original_recognize(
                self, intent_input, conversation_id, conversation_extra_system_prompt,
            )
        finally:
            pipeline_mod.conversation.async_converse = original_converse
            pipeline_mod.conversation.async_get_chat_log = original_get_chat_log

    PipelineRun.recognize_intent = _hooked_recognize_intent
    hass.data[_INTENT_PATCH_KEY] = _original_recognize
    _LOGGER.info("Installed recognize_intent goal-continuation hook on PipelineRun")


def _uninstall_recognize_intent_hook(hass) -> None:
    _original = hass.data.pop(_INTENT_PATCH_KEY, None)
    if _original is None:
        return
    from homeassistant.components.assist_pipeline.pipeline import PipelineRun
    PipelineRun.recognize_intent = _original
    _LOGGER.info("Uninstalled recognize_intent goal-continuation hook")


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/frontend_snapshot",
        vol.Required("snapshot"): dict,
    }
)
@websocket_api.async_response
async def websocket_frontend_snapshot(hass, connection, msg):
    from ...tools.frontend_tools import store_frontend_snapshot
    store_frontend_snapshot(hass, msg["snapshot"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/frontend_exec_poll",
    }
)
@websocket_api.async_response
async def websocket_frontend_exec_poll(hass, connection, msg):
    from ...tools.frontend_tools import _domain_data, _FRONTEND_EXEC_QUEUE
    dd = _domain_data(hass)
    q = dd.setdefault(_FRONTEND_EXEC_QUEUE, [])
    cursors = dd.setdefault("_exec_poll_cursors", {})
    conn_id = id(connection)
    is_new = conn_id not in cursors
    cursor = cursors.get(conn_id, len(q))
    unseen = q[cursor:]
    cursors[conn_id] = len(q)
    if len(q) > 200:
        min_cursor = min(cursors.values()) if cursors else len(q)
        if min_cursor > 0:
            del q[:min_cursor]
            for k in cursors:
                cursors[k] -= min_cursor
    connection.send_result(msg["id"], {"tasks": unseen})


class _ExecSub:
    __slots__ = ("connection", "ws_msg_id")
    def __init__(self, connection, ws_msg_id: int):
        self.connection = connection
        self.ws_msg_id = ws_msg_id
    def send_message(self, msg):
        self.connection.send_message(msg)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/frontend_exec_subscribe",
    }
)
@websocket_api.async_response
async def websocket_frontend_exec_subscribe(hass, connection, msg):
    from ...tools.frontend_tools import _domain_data, _FRONTEND_EXEC_SUBS
    dd = _domain_data(hass)
    subs: list = dd.setdefault(_FRONTEND_EXEC_SUBS, [])
    sub = _ExecSub(connection, msg["id"])
    subs.append(sub)
    def unsub():
        try:
            subs.remove(sub)
        except ValueError:
            pass
    connection.subscriptions[msg["id"]] = unsub
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/frontend_exec_result",
        vol.Required("exec_id"): str,
        vol.Required("result"): vol.Any(dict, str, int, float, bool, list, None),
    }
)
@websocket_api.async_response
async def websocket_frontend_exec_result(hass, connection, msg):
    from ...tools.frontend_tools import store_frontend_exec_result
    store_frontend_exec_result(hass, msg["exec_id"], msg["result"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/dialog_snapshot",
        vol.Required("dialogs"): list,
    }
)
@websocket_api.async_response
async def websocket_dialog_snapshot(hass, connection, msg):
    from ...tools.frontend_tools import store_frontend_text_cache, _domain_data
    dialogs = msg["dialogs"]
    domain = _domain_data(hass)
    if dialogs:
        domain["claw_active_dialogs"] = dialogs
        store_frontend_text_cache(hass, "dialog_snapshot", dialogs)
    else:
        domain.pop("claw_active_dialogs", None)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/user_activity",
        vol.Required("actions"): list,
    }
)
@websocket_api.async_response
async def websocket_user_activity(hass, connection, msg):
    from ..storage.user_activity import record_activity
    for action in msg["actions"][:10]:
        if isinstance(action, dict):
            record_activity(hass, action)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ha_crack/get_commands",
    }
)
@websocket_api.async_response
async def websocket_get_commands(hass, connection, msg):
    from ...command_registry import all_command_specs
    from ...chat_commands import _skill_command_registry
    from ..storage.plugin_store import get_plugin_tool_registry
    commands = []
    for spec in all_command_specs():
        commands.append({
            "name": spec.name,
            "description": spec.description,
            "description_zh": spec.description_zh,
            "category": spec.category,
            "aliases": [],
        })
    skill_registry = _skill_command_registry()
    for name, skill in skill_registry.items():
        raw_desc = skill.get("description", "") if isinstance(skill, dict) else ""
        desc = f"[Skill: {raw_desc}]" if raw_desc else f"[Skill: {name}]"
        commands.append({
            "name": name,
            "description": desc,
            "description_zh": "",
            "category": "Skill",
            "aliases": [],
        })
    plugin_tool_registry = get_plugin_tool_registry()
    for tool_name, meta in plugin_tool_registry.items():
        commands.append({
            "name": tool_name,
            "description": meta.get("desc", ""),
            "description_zh": "",
            "category": "Plugin",
            "aliases": [],
        })
    connection.send_result(msg["id"], {"commands": commands})


def install_official_websocket_process_hook(hass) -> None:

    domain_data = hass.data.setdefault("claw_assistant", {})
    handlers = hass.data.setdefault("websocket_api", {})
    for cmd_type, cmd in (
        ("conversation/process", streaming_websocket_process),
        ("ha_crack/get_pending_js", websocket_get_pending_js),
        ("ha_crack/report_state", websocket_report_state),
        ("ha_crack/get_settings", websocket_get_settings),
        ("ha_crack/get_context_status", websocket_get_context_status),
        ("ha_crack/live_turn_snapshot", websocket_live_turn_snapshot),
        ("ha_crack/subscribe_live_stream", websocket_subscribe_live_stream),
        ("ha_crack/upload_file", websocket_upload_file),
        ("ha_crack/frontend_snapshot", websocket_frontend_snapshot),
        ("ha_crack/frontend_exec_poll", websocket_frontend_exec_poll),
        ("ha_crack/frontend_exec_result", websocket_frontend_exec_result),
        ("ha_crack/frontend_exec_subscribe", websocket_frontend_exec_subscribe),
        ("ha_crack/dialog_snapshot", websocket_dialog_snapshot),
        ("ha_crack/user_activity", websocket_user_activity),
        ("ha_crack/get_commands", websocket_get_commands),
    ):
        if cmd_type not in handlers:
            websocket_api.async_register_command(hass, cmd)
    from ..history.chat_history_api import register_chat_history_websocket
    register_chat_history_websocket(hass)

    if _PATCH_KEY in domain_data:
        return
    domain_data[_PATCH_KEY] = handlers.get("conversation/process", _NO_HANDLER)
    _install_recognize_intent_hook(hass)
    _install_local_intent_format_hook(hass)
    hass.http.register_view(ClawUploadView())
    hass.http.register_view(ClawFileView())


def uninstall_official_websocket_process_hook(hass) -> None:

    _uninstall_recognize_intent_hook(hass)
    _uninstall_local_intent_format_hook(hass)
    domain_data = hass.data.setdefault("claw_assistant", {})
    original_handler = domain_data.pop(_PATCH_KEY, _UNSET)
    if original_handler is _UNSET:
        return

    handlers = hass.data.setdefault("websocket_api", {})
    if original_handler is _NO_HANDLER:
        handlers.pop("conversation/process", None)
        return

    handlers["conversation/process"] = original_handler
