

from __future__ import annotations

import asyncio
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

from ..const import CONF_ENABLE_CONTEXT_STATUS_BAR, CONF_ENABLE_FILE_UPLOAD, DOMAIN
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


def file_upload_enabled(hass) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_ENABLE_FILE_UPLOAD, False):
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
            "enable_file_upload": file_upload_enabled(hass),
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


# Binary signature prefixes; if any of these is the leading bytes of the file
# we MUST NOT treat it as text — even if a small chunk decodes as valid UTF-8
# by accident. Covers the common "weird" formats LLMs generate (PDF, zip,
# images, audio/video, sqlite, …) so the content-sniff fallback below stays
# safe.
_BINARY_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
    b"BM", b"RIFF", b"%PDF-", b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",
    b"\x1f\x8b",  # gzip
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"Rar!\x1a\x07",  # rar
    b"OggS", b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2",  # mp3 frames
    b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00 ftyp",
    b"\x1aE\xdf\xa3",  # mkv/webm
    b"SQLite format 3\x00",
    b"\x7fELF", b"MZ",  # executables
    b"\x00asm",  # wasm
)


def _sniff_is_text(head: bytes) -> bool:
    """Best-effort detection of text content from the first chunk of a file.

    The check is intentionally conservative: a file is treated as text only if
    (a) it does not start with a known binary magic, (b) it does not contain
    NUL bytes in the sniffed window, and (c) the chunk decodes cleanly as
    UTF-8 (or UTF-8 with a BOM). Anything else is served as binary so we
    never accidentally rewrite a media file's content-type and break the
    browser's renderer.
    """
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
        # Allow truncation in the middle of a multi-byte char: retry
        # without the trailing partial sequence.
        for cut in range(1, 4):
            try:
                sample[:-cut].decode("utf-8")
                return True
            except UnicodeDecodeError:
                continue
        return False
    return True


class ClawFileView(HomeAssistantView):
    """Serve files from ``<config>/www/claw_assistant/`` with explicit
    ``charset=utf-8`` for text content-types.

    HA's built-in ``/local/...`` static handler derives ``Content-Type`` from
    ``mimetypes.guess_type()`` which returns e.g. ``text/markdown`` *without*
    a charset parameter. Browsers running in a Chinese locale then default to
    GBK and render UTF-8 files as mojibake. This view routes claw_assistant
    output files through HA's HTTP stack with a guaranteed
    ``Content-Type: text/<x>; charset=utf-8`` header for text extensions.
    """

    url = "/claw_file/{filename:.+}"
    name = "claw_assistant:file"
    requires_auth = False

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        hass = request.app["hass"]
        from .data_path import output_dir_path
        output_dir = output_dir_path(hass).resolve()
        # Path-traversal guard: resolve and verify the target stays inside
        # OUTPUT_DIR. ``..`` segments / absolute paths in ``filename`` would
        # otherwise let any unauthenticated client read arbitrary files.
        try:
            target = (output_dir / filename).resolve(strict=False)
            target.relative_to(output_dir)
        except (ValueError, OSError):
            return web.Response(status=403, text="forbidden")

        def _probe() -> tuple[bool, bytes]:
            # Read just enough to (a) confirm the file exists and (b) feed
            # the text/binary sniffer below. 8KB is plenty: every common
            # binary magic fits in the first ~16 bytes and a non-trivial
            # UTF-8 truncation is impossible inside 8KB of valid text.
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
            # Treat the file as UTF-8 text regardless of extension — covers
            # the long tail of "weird" formats the AI invents (.tex, .toml,
            # .lua, made-up extensions, no extension at all, …) without
            # forcing us to maintain a hand-curated allow-list.
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
    from ..const import (
        CONF_CONVERSATION_MODE,
        CONVERSATION_MODE_NO_NAME,
        DEFAULT_CONVERSATION_MODE,
        DOMAIN,
    )
    from .reply_formatter import format_reply_speech, strip_reply_prefix

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

    from .state import get_conversation_status
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
    from .goals import get_goal_manager
    from .state import get_runtime_store

    if hass.data.get(_INTENT_PATCH_KEY):
        return

    _original_recognize = PipelineRun.recognize_intent

    async def _hooked_recognize_intent(self, intent_input, conversation_id, conversation_extra_system_prompt):
        original_converse = pipeline_mod.conversation.async_converse
        flag_key = f"_claw_pipeline_converse_cont_{conversation_id}"

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
                            from .native_chatlog_bridge import emit_live_content_delta
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
                self.hass.data.pop(flag_key, None)
                _LOGGER.info("pipeline async_converse hook: exit conv=%s", conversation_id)
            return result

        pipeline_mod.conversation.async_converse = _hooked_converse
        try:
            return await _original_recognize(
                self, intent_input, conversation_id, conversation_extra_system_prompt,
            )
        finally:
            pipeline_mod.conversation.async_converse = original_converse

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


def install_official_websocket_process_hook(hass) -> None:

    domain_data = hass.data.setdefault("claw_assistant", {})
    if _PATCH_KEY in domain_data:
        return
    handlers = hass.data.setdefault("websocket_api", {})
    domain_data[_PATCH_KEY] = handlers.get("conversation/process", _NO_HANDLER)
    _install_recognize_intent_hook(hass)
    _install_local_intent_format_hook(hass)
    websocket_api.async_register_command(hass, streaming_websocket_process)
    websocket_api.async_register_command(hass, websocket_get_pending_js)
    websocket_api.async_register_command(hass, websocket_report_state)
    websocket_api.async_register_command(hass, websocket_get_settings)
    websocket_api.async_register_command(hass, websocket_get_context_status)
    websocket_api.async_register_command(hass, websocket_upload_file)
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
