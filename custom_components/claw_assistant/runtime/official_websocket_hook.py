

from __future__ import annotations

import base64
import logging
import subprocess
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

from ..const import CONF_ENABLE_CONTEXT_STATUS_BAR, DOMAIN
from .continuous_conversation import (
    continuous_conversation_enabled,
    get_effective_conversation_id,
)
from .data_path import get_tmp_dir

_LOGGER = logging.getLogger(__name__)

_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
_VIDEO_TRIM_SECONDS = 30
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v", ".ts", ".3gp"})


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

        final_size = Path(file_path).stat().st_size if Path(file_path).is_file() else len(raw)
        return web.json_response({
            "path": file_path, "mime_type": mime,
            "size": final_size, "filename": filename,
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
    websocket_api.async_register_command(hass, websocket_upload_file)
    hass.http.register_view(ClawUploadView())


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
