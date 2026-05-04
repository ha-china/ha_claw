

from __future__ import annotations

from contextvars import ContextVar
import logging
import re
from pathlib import Path

from custom_components.claw_assistant.conversation_utils import get_conversation_history

from homeassistant.components.conversation import ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .agent_fallback import (
    _finalize_completed_response,
    _finalize_synthesized_success,
    _snapshot_tool_results,
    get_agent_name,
    run_agent_fallback_chain,
)
from .config import DEFAULT_FALLBACK_AGENT_ID, DEFAULT_THRESHOLDS
from .data_path import get_tmp_dir
from .i18n import t
from .ha_guide_store import async_refresh_homeassistant_guide_store
from .internal_llm import _MAX_SYSTEM_PROMPT_CHARS, _fit_head_section_to_required_suffix
from .loop_controller import (
    record_continuation,
    record_user_turn,
    reset_continuation_count,
    reset_loop_for_conversation,
)
from .native_chatlog_bridge import reset_live_delta_state
from .options import build_conversation_runtime_config_for_hass
from .prompting import _fit_base_prompt, build_base_prompt
from .response_format import is_marshaled_tool_payload, sanitize_response_text
from .response_policy import analyze_response_state
from .skill_store import async_refresh_prompt_store
from .state import (
    consume_tool_called,
    get_active_conversation_state,
    get_conversation_status,
    get_task_loop_state,
    get_tool_calls_state,
    get_tool_results_state,
    reset_active_conversation,
    set_active_conversation,
)
from .summary import process_ai_summary
from .tool_result_summary import extract_successful_tool_response
from .turn_kernel import execute_kernel_turn
from .workspace_store import async_refresh_workspace_store, get_user_context_prefix

LOGGER = logging.getLogger(__name__)


_ATTACHMENT_RE = re.compile(r"\[ATTACHMENT:([\w/]+):(.+?)\]")
_PENDING_ATTACHMENTS: ContextVar[list[tuple[str, str]] | None] = ContextVar(
    "claw_pending_attachments",
    default=None,
)

_ATTACHMENT_COMPRESS_THRESHOLD = 100 * 1024
_ATTACHMENT_MAX_DIM = 1280
_ATTACHMENT_TARGET_KB = 180
_ATTACHMENT_QUALITY_LADDER = (82, 75, 68, 60)
_ATTACHMENT_MIN_QUALITY = 60
_ATTACHMENT_MAX_INPUT_BYTES = 32 * 1024 * 1024


def _compress_image_blocking(raw: bytes) -> bytes | None:
    try:
        from io import BytesIO
        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception:
        return None
    try:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > _ATTACHMENT_MAX_DIM:
            scale = _ATTACHMENT_MAX_DIM / longest
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.LANCZOS,
            )
        target = _ATTACHMENT_TARGET_KB * 1024
        last: bytes = b""
        for quality in _ATTACHMENT_QUALITY_LADDER:
            buf = BytesIO()
            img.save(
                buf,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            last = buf.getvalue()
            if len(last) <= target or quality == _ATTACHMENT_MIN_QUALITY:
                return last
        return last or None
    except Exception:
        return None


def _sanitize_attachment(mime: str, fpath: str, tmp_dir: Path | None = None) -> tuple[str, Path] | None:
    """Validate, size-check and (for images) re-encode an attachment.

    Goals:
    - Never let a malformed file crash the conversation pipeline.
    - Strip EXIF / container metadata via a clean PIL re-encode.
    - Cap oversized images so downstream vision models don't OOM / overflow.
    Returns ``None`` when the source is unusable.
    """
    try:
        p = Path(fpath)
        if not p.is_file():
            return None
        size = p.stat().st_size
    except Exception:
        return None
    if size <= 0:
        return None

    mime_lower = (mime or "").strip().lower() or "application/octet-stream"

    if mime_lower.startswith("video/") or mime_lower == "image/gif":
        return (mime_lower, p)

    if size > _ATTACHMENT_MAX_INPUT_BYTES:
        LOGGER.warning(
            "Rejecting attachment %s: size=%s bytes (limit=%s)",
            p.name,
            size,
            _ATTACHMENT_MAX_INPUT_BYTES,
        )
        return None

    if not mime_lower.startswith("image/"):
        return (mime_lower, p)
    if mime_lower == "image/gif":
        return (mime_lower, p)
    if size <= _ATTACHMENT_COMPRESS_THRESHOLD:
        return (mime_lower, p)

    try:
        raw = p.read_bytes()
    except Exception:
        return None

    compressed = _compress_image_blocking(raw)
    if not compressed:
        LOGGER.warning(
            "Image re-encode failed for %s (%d bytes); dropping attachment",
            p.name,
            size,
        )
        return None

    try:
        import uuid

        if tmp_dir is not None:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        dest = (tmp_dir or Path(".")) / f"claw_att_{uuid.uuid4().hex[:12]}.jpg"
        dest.write_bytes(compressed)
        new_path = dest
    except Exception:
        return None

    LOGGER.info(
        "Compressed attachment %s: %d -> %d bytes",
        p.name,
        size,
        len(compressed),
    )
    return ("image/jpeg", new_path)


def _extract_attachment_tags(
    text: str,
    *,
    language: str | None,
) -> tuple[str, list[tuple[str, str]]]:
    attachments: list[tuple[str, str]] = []
    for m in _ATTACHMENT_RE.finditer(text):
        attachments.append((m.group(1), m.group(2)))
    clean = _ATTACHMENT_RE.sub("", text).strip()
    if not clean and attachments:
        clean = t("attachment_only_input", language)
    return clean, attachments


def _append_attachment_tags(text: str, attachments: list[tuple[str, str]]) -> str:
    if not attachments:
        return text
    tags = "\n".join(f"[ATTACHMENT:{mime}:{fpath}]" for mime, fpath in attachments)
    if not text:
        return tags
    return f"{text}\n{tags}"


def _install_attachment_hook(hass: HomeAssistant) -> None:
    from homeassistant.components.conversation.chat_log import ChatLog, UserContent, Attachment

    if getattr(ChatLog, "_claw_attachment_hooked", False):
        return

    _original_add = ChatLog.async_add_user_content

    def _hooked_add(self: ChatLog, content: UserContent) -> None:
        pending = _PENDING_ATTACHMENTS.get()
        if pending and not content.attachments:
            att_list = []
            for mime, fpath in pending:
                p = Path(fpath)
                if p.is_file():
                    size = p.stat().st_size
                    att_list.append(Attachment(
                        media_content_id="",
                        mime_type=mime,
                        path=p,
                    ))
                    LOGGER.info(
                        "Prepared attachment for ChatLog: mime=%s path=%s size=%d",
                        mime,
                        p,
                        size,
                    )
            if att_list:
                content = UserContent(
                    content=content.content,
                    attachments=att_list,
                )
                LOGGER.info("Injected %d attachment(s) into ChatLog", len(att_list))
            _PENDING_ATTACHMENTS.set(None)
        _original_add(self, content)

    ChatLog.async_add_user_content = _hooked_add
    ChatLog._claw_attachment_hooked = True
    LOGGER.debug("ChatLog attachment hook installed")


def _build_continuation_prompt(
    base_prompt: str,
    previous_thought: str,
    continuation_index: int,
) -> str:

    continuation_suffix = (
        f"## Continuation #{continuation_index + 1}\n"
        f"Your previous response was:\n"
        f"---\n{{previous_thought}}\n---\n"
        f"This was classified as incomplete. Continue your analysis and provide "
        f"a complete response. If you're done, give your final answer."
    )
    trimmed_thought = _fit_head_section_to_required_suffix(
        previous_thought,
        [continuation_suffix.format(previous_thought="")],
        max_chars=max(_MAX_SYSTEM_PROMPT_CHARS // 3, 1024),
    ).strip()
    continuation_prompt = continuation_suffix.format(previous_thought=trimmed_thought)
    return _fit_base_prompt(base_prompt, [continuation_prompt])


async def execute_conversation_turn(
    hass: HomeAssistant,
    entry: ConfigEntry,
    original_async_converse,
    *,
    text: str,
    conversation_id,
    context,
    language=None,
    agent_id=None,
    device_id=None,
    satellite_id=None,
    extra_system_prompt=None,
):

    conv_token = set_active_conversation(conversation_id)
    try:
        return await _execute_conversation_turn_inner(
            hass,
            entry,
            original_async_converse,
            text=text,
            conversation_id=conversation_id,
            context=context,
            language=language,
            agent_id=agent_id,
            device_id=device_id,
            satellite_id=satellite_id,
            extra_system_prompt=extra_system_prompt,
        )
    finally:
        reset_active_conversation(conv_token)


async def _execute_conversation_turn_inner(
    hass: HomeAssistant,
    entry: ConfigEntry,
    original_async_converse,
    *,
    text: str,
    conversation_id,
    context,
    language=None,
    agent_id=None,
    device_id=None,
    satellite_id=None,
    extra_system_prompt=None,
):

    task_loop = get_task_loop_state(hass)
    if conversation_id and task_loop.get("conversation_id") != conversation_id:
        task_loop = reset_loop_for_conversation(
            hass,
            conversation_id=conversation_id,
            max_iterations=int(task_loop.get("max_iterations", 50) or 50),
        )
    reset_live_delta_state(hass)


    if task_loop.get("budget_exhausted"):
        LOGGER.warning("Budget exhausted for conversation %s", conversation_id)
        intent_response = intent.IntentResponse(
            language=language or hass.config.language
        )
        intent_response.async_set_speech(
            t("budget_exhausted", language or hass.config.language)
        )

        return ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )

    reset_continuation_count(hass)
    text, pending_attachments = _extract_attachment_tags(text, language=language)
    attachment_token = None
    if pending_attachments:
        sanitized: list[tuple[str, str]] = []
        for mime, fpath in pending_attachments:
            try:
                _tmp_dir = get_tmp_dir(hass)
                result = await hass.async_add_executor_job(
                    _sanitize_attachment, mime, fpath, _tmp_dir
                )
            except Exception as err:
                LOGGER.warning(
                    "Attachment sanitize crashed for %s (%s): %s", fpath, mime, err
                )
                result = None
            if result is not None:
                new_mime, new_path = result
                sanitized.append((new_mime, str(new_path)))
        if sanitized:
            _MEDIA_MIMES = ("image/", "video/")
            media_atts = [(m, p) for m, p in sanitized if any(m.startswith(pfx) for pfx in _MEDIA_MIMES)]
            other_atts = [(m, p) for m, p in sanitized if not any(m.startswith(pfx) for pfx in _MEDIA_MIMES)]
            if media_atts:
                calls = []
                for _mime, fpath in media_atts:
                    calls.append(f'MediaAnalyze(file_path="{fpath}")')
                call_list = ", ".join(calls)
                hint_block = (
                    f"[SYSTEM MANDATORY] User attached {len(media_atts)} media file(s). "
                    f"You MUST call {call_list} IMMEDIATELY as your FIRST action before generating ANY text response. "
                    f"Do NOT reply, comment, or acknowledge until you have the analysis result. "
                    f"After analysis, respond naturally based on content — react like a human, do not just describe."
                )
                text = f"{text}\n{hint_block}" if text else hint_block
                LOGGER.info(
                    "Routed %d media file(s) to MediaAnalyze tool path", len(media_atts)
                )
            if other_atts:
                text = _append_attachment_tags(text, other_atts)
                attachment_token = _PENDING_ATTACHMENTS.set(other_atts)
                _install_attachment_hook(hass)
        else:
            LOGGER.info(
                "All %d attachment(s) dropped after sanitization",
                len(pending_attachments),
            )
    task_loop = record_user_turn(hass, text=text)
    LOGGER.info("Task loop turn %s: %s...", task_loop["turn_count"], text[:50])

    active_conv = get_active_conversation_state(hass)
    if conversation_id and active_conv.get("id") != conversation_id:
        LOGGER.info("New conversation detected: %s...", conversation_id[:20])
        task_loop["waiting_choice"] = False
        active_conv["id"] = conversation_id
        await async_refresh_workspace_store(hass)
        await async_refresh_homeassistant_guide_store(hass)
        await async_refresh_prompt_store(hass)

    if int(task_loop.get("turn_count", 0) or 0) > 1:
        user_prefix = get_user_context_prefix()
        if user_prefix:
            text = f"{user_prefix}\n\n{text}"

    runtime_config = build_conversation_runtime_config_for_hass(entry, hass)
    fallback_agents = runtime_config.fallback_agents
    summary_agents = runtime_config.summary_agents
    conversation_mode = runtime_config.conversation_mode
    enable_ai_summary = runtime_config.enable_ai_summary

    get_conversation_status(hass)["last_conversation_id"] = conversation_id

    effective_agent = agent_id or (fallback_agents[0] if fallback_agents else "")
    if effective_agent:
        get_conversation_status(hass)["current_agent_id"] = effective_agent

    original_text = text
    is_first_turn = bool(task_loop.get("is_first_turn", False))

    if is_first_turn:
        def _build_prompt():
            bp = build_base_prompt(
                hass,
                text=text,
                conversation_id=conversation_id,
                runtime_config=runtime_config,
            )
            return _fit_base_prompt(
                bp,
                [extra_system_prompt] if extra_system_prompt else [],
            )
        extra_system_prompt = await hass.async_add_executor_job(_build_prompt)

    if not fallback_agents:
        continuation_index = 0
        max_cont = DEFAULT_THRESHOLDS.max_continuations_per_turn
        current_prompt = extra_system_prompt

        while True:
            tool_calls_state = get_tool_calls_state(hass)
            tool_calls_state.clear()
            tool_results_state = get_tool_results_state(hass)
            tool_results_state.clear()
            direct_result = await execute_kernel_turn(
                hass,
                original_async_converse=original_async_converse,
                user_text=text,
                conversation_id=conversation_id,
                context=context,
                language=language,
                agent_id=agent_id,
                device_id=device_id,
                satellite_id=satellite_id,
                extra_system_prompt=current_prompt,
            )
            if direct_result is None:
                direct_result = await original_async_converse(
                    hass,
                    text,
                    conversation_id,
                    context,
                    language,
                    agent_id,
                    device_id,
                    satellite_id,
                    current_prompt,
                )
            direct_tool_results = _snapshot_tool_results(get_tool_results_state(hass))
            synthesized_response = extract_successful_tool_response(direct_tool_results)
            raw_response_text = ""
            response = getattr(direct_result, "response", None)
            if response and getattr(response, "speech", None):
                raw_response_text = (
                    response.speech.get("plain", {}).get("speech", "").strip()
                    if isinstance(response.speech, dict)
                    else ""
                )

            should_synthesize = bool(synthesized_response) and (
                getattr(response, "response_type", None) == intent.IntentResponseType.ERROR
                or is_marshaled_tool_payload(raw_response_text)
            )
            if should_synthesize:
                response.async_set_speech(synthesized_response)
                if attachment_token is not None:
                    _PENDING_ATTACHMENTS.reset(attachment_token)
                    attachment_token = None
                return await _finalize_synthesized_success(
                    hass,
                    result=direct_result,
                    agent_id=agent_id or DEFAULT_FALLBACK_AGENT_ID,
                    agent_name=get_agent_name(hass, agent_id or DEFAULT_FALLBACK_AGENT_ID),
                    response_text=synthesized_response,
                    conversation_mode=conversation_mode,
                    conversation_id=conversation_id,
                    original_text=original_text,
                    user_text=text,
                    conv_history=get_conversation_history(),
                    task_loop=task_loop,
                )

            response_text_for_analysis = raw_response_text or ""
            last_tool = consume_tool_called(hass)
            response_state = analyze_response_state(
                response_text_for_analysis,
                task_loop.get("history", []),
                last_tool=last_tool,
            )

            should_continue = (
                response_state.get("continuation_eligible")
                and response_state["state"] in ("continue", "need_action")
                and continuation_index < max_cont
            )

            if should_continue:
                can_continue = record_continuation(
                    hass,
                    thought=response_text_for_analysis,
                    continuation_index=continuation_index,
                )
                if can_continue:
                    LOGGER.info(
                        "Continuation #%d for conversation %s: %s",
                        continuation_index + 1,
                        conversation_id,
                        response_state.get("reason", ""),
                    )
                    current_prompt = _build_continuation_prompt(
                        base_prompt=extra_system_prompt,
                        previous_thought=response_text_for_analysis,
                        continuation_index=continuation_index,
                    )
                    continuation_index += 1
                    continue
                LOGGER.info("Continuation budget exhausted for %s", conversation_id)

            break

        if response and getattr(response, "speech", None):
            plain = response.speech.get("plain", {}) if isinstance(response.speech, dict) else {}
            final_text = sanitize_response_text(
                plain.get("original_speech", plain.get("speech", ""))
            )
            if final_text:
                plain["speech"] = final_text
                plain["original_speech"] = final_text
                await _finalize_completed_response(
                    hass,
                    response=response,
                    task_loop=task_loop,
                    original_text=original_text,
                    conversation_id=conversation_id,
                    agent_id=agent_id or DEFAULT_FALLBACK_AGENT_ID,
                    conv_history=get_conversation_history(),
                    tool_results=direct_tool_results,
                    language=language,
                    original_async_converse=original_async_converse,
                )
        if attachment_token is not None:
            _PENDING_ATTACHMENTS.reset(attachment_token)
            attachment_token = None
        return direct_result

    if enable_ai_summary and len(summary_agents) >= 2:
        summary_result = await process_ai_summary(
            hass,
            text,
            conversation_id,
            context,
            language,
            summary_agents,
            conversation_mode,
            original_async_converse,
            extra_system_prompt,
            device_id,
            satellite_id,
        )
        if summary_result:
            summary_response = getattr(summary_result, "response", None)
            if summary_response and getattr(summary_response, "speech", None):
                plain = (
                    summary_response.speech.get("plain", {})
                    if isinstance(summary_response.speech, dict)
                    else {}
                )
                final_text = sanitize_response_text(
                    plain.get("original_speech", plain.get("speech", ""))
                )
                if final_text:
                    plain["speech"] = final_text
                    plain["original_speech"] = final_text
                    await _finalize_completed_response(
                        hass,
                        response=summary_response,
                        task_loop=task_loop,
                        original_text=original_text,
                        conversation_id=conversation_id,
                        agent_id=str(plain.get("agent_id") or summary_agents[-1]),
                        conv_history=get_conversation_history(),
                        tool_results=list(get_tool_results_state(hass)),
                        language=language,
                        original_async_converse=original_async_converse,
                    )
            if attachment_token is not None:
                _PENDING_ATTACHMENTS.reset(attachment_token)
                attachment_token = None
            return summary_result

    try:
        return await run_agent_fallback_chain(
            hass,
            text=text,
            original_text=original_text,
            conversation_id=conversation_id,
            context=context,
            language=language,
            fallback_agents=fallback_agents,
            conversation_mode=conversation_mode,
            original_async_converse=original_async_converse,
            extra_system_prompt=extra_system_prompt,
            device_id=device_id,
            satellite_id=satellite_id,
            conv_history=get_conversation_history(),
        )
    finally:
        if attachment_token is not None:
            _PENDING_ATTACHMENTS.reset(attachment_token)
