

from __future__ import annotations

import asyncio
import logging
import re

from typing import Any

from homeassistant.core import HomeAssistant, callback

from ..const import (
    CONF_CONVERSATION_MODE,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
    DOMAIN,
)
from homeassistant.components.conversation.chat_log import AssistantContent

from .tool_result_summary import build_synthesized_assistant_from_chat_log
from .state import get_conversation_status

LOGGER = logging.getLogger(__name__)

_LOCAL_INTENTS_PATCHED = "_ha_crack_intents_patched"
_LOCAL_INTENTS_ORIGINAL = "_ha_crack_original_async_handle_intents"
_INTENTS_DOWNGRADE_DONE = "_ha_crack_intents_downgrade_done"
_INTENTS_DOWNGRADE_TARGET = "home-assistant-intents==2026.3.3"
_RESULT_PATCHED = "_ha_crack_result_patched"
_RESULT_ORIGINAL = "_ha_crack_original_result_extraction"
_STREAM_CLOSURE_PATCHED = "_ha_crack_stream_closure_patched"
_STREAM_CLOSURE_ORIGINAL = "_ha_crack_original_stream_closure"
_PIPELINE_FILTER_PATCHED = "_claw_assistant_tool_filter_patched"
_PIPELINE_FILTER_ORIGINAL = "_claw_assistant_original_process_event"
_TOOL_PROGRESS_PATCHED = "_claw_assistant_tool_progress_patched"
_TOOL_PROGRESS_ORIGINAL = "_claw_assistant_tool_progress_original"
_THINKING_STRIP_PATCHED = "_claw_assistant_thinking_strip_patched"
_THINKING_STRIP_ORIGINAL = "_claw_assistant_thinking_strip_original"
_TOOL_FALLBACK_PATCHED = "_claw_assistant_tool_fallback_patched"
_TOOL_FALLBACK_ORIGINAL = "_claw_assistant_tool_fallback_original"
_GLOBAL_FORMAT_PATCHED = "_claw_assistant_global_format_patched"
_GLOBAL_FORMAT_ORIGINAL = "_claw_assistant_global_format_original"

# Upstream LLM integrations whose thinking_content streaming triggers the
# HA frontend lit truncation bug. Centralized so both pipeline-event and
# chat_log-subscription leak paths use the same allowlist.
_THINKING_BUGGY_UPSTREAM_PLATFORMS = (
    "google_generative_ai_conversation",
    "openai_conversation",
    "open_router",
)


def _pop_empty_trailing_assistant(chat_log) -> None:

    content = getattr(chat_log, "content", None)
    if not content:
        return
    last = content[-1]
    if isinstance(last, AssistantContent) and not last.content:
        content.pop()


def _chat_log_turn_has_assistant_content(chat_log) -> bool:
    content = getattr(chat_log, "content", None)
    if not content:
        return False
    for item in reversed(content):
        if getattr(item, "role", None) == "user":
            return False
        if isinstance(item, AssistantContent) and item.content:
            return True
    return False


def patch_local_intents(hass: HomeAssistant) -> None:

    from homeassistant.components import conversation as conv_module

    if hasattr(conv_module, _LOCAL_INTENTS_PATCHED):
        return

    original_async_handle_intents = conv_module.async_handle_intents

    async def patched_async_handle_intents(
        hass_arg,
        user_input,
        chat_log,
        *,
        intent_filter=None,
    ):
        result = await original_async_handle_intents(
            hass_arg, user_input, chat_log, intent_filter=intent_filter
        )

        if result is not None and result.speech:
            entries = hass.config_entries.async_entries(DOMAIN)
            if entries:
                options = entries[0].options
                conversation_mode = options.get(
                    CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME
                )

                speech_text = result.speech.get("plain", {}).get("speech", "")
                if speech_text:
                    from .reply_formatter import stamp_plain
                    stamp_plain(
                        result.speech.setdefault("plain", {}),
                        agent_name="Home Assistant",
                        agent_id="conversation.home_assistant",
                        text=speech_text,
                        language=getattr(user_input, "language", None),
                        add_prefix=conversation_mode != CONVERSATION_MODE_NO_NAME,
                    )

        return result

    conv_module.async_handle_intents = patched_async_handle_intents
    setattr(conv_module, _LOCAL_INTENTS_ORIGINAL, original_async_handle_intents)
    setattr(conv_module, _LOCAL_INTENTS_PATCHED, True)
    LOGGER.debug("Patched async_handle_intents to add local intent prefixes")


def unpatch_local_intents() -> None:

    from homeassistant.components import conversation as conv_module

    original_async_handle_intents = getattr(conv_module, _LOCAL_INTENTS_ORIGINAL, None)
    if original_async_handle_intents is None:
        return

    conv_module.async_handle_intents = original_async_handle_intents
    delattr(conv_module, _LOCAL_INTENTS_ORIGINAL)
    if hasattr(conv_module, _LOCAL_INTENTS_PATCHED):
        delattr(conv_module, _LOCAL_INTENTS_PATCHED)
    LOGGER.debug("Restored async_handle_intents after claw_assistant unload")


async def async_downgrade_intents_package(hass: HomeAssistant) -> None:
    # Pin home-assistant-intents to 2026.3.3 on disk. Newer versions ship a
    # bloated zh-CN.json (~533 sentence templates) that explode into ~76k
    # regex matches per recognize call and stall the event loop.
    # get_intents() reads the JSON from disk on every call, so a force-
    # reinstall takes effect immediately without restarting the process.
    import os
    import sys

    if os.environ.get(_INTENTS_DOWNGRADE_DONE) == "1":
        return

    def _check_current_version() -> str | None:
        try:
            from importlib.metadata import version
            return version("home-assistant-intents")
        except Exception:  # noqa: BLE001
            return None

    current = await hass.async_add_executor_job(_check_current_version)
    if current == "2026.3.3":
        os.environ[_INTENTS_DOWNGRADE_DONE] = "1"
        LOGGER.debug("home-assistant-intents already at 2026.3.3")
        return

    def _pip_install() -> tuple[int, str, str]:
        import subprocess
        try:
            proc = subprocess.run(
                [
                    sys.executable, "-m", "pip", "install",
                    "--quiet", "--no-deps", "--force-reinstall",
                    _INTENTS_DOWNGRADE_TARGET,
                ],
                capture_output=True, text=True, timeout=120,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except Exception as exc:  # noqa: BLE001
            return -1, "", str(exc)

    LOGGER.info("Downgrading home-assistant-intents %s -> 2026.3.3", current)
    code, _stdout, stderr = await hass.async_add_executor_job(_pip_install)
    if code != 0:
        LOGGER.warning("home-assistant-intents downgrade failed (rc=%s): %s", code, stderr.strip())
        return

    os.environ[_INTENTS_DOWNGRADE_DONE] = "1"
    LOGGER.info("home-assistant-intents downgraded to 2026.3.3")

    # Clear default_agent's cached zh-* intents so next recognize re-reads disk.
    try:
        for entry in list(hass.data.values()):
            if not isinstance(entry, dict):
                continue
            for value in list(entry.values()):
                cache = getattr(value, "_lang_intents", None)
                if isinstance(cache, dict):
                    for key in [k for k in cache if isinstance(k, str) and k.lower().startswith("zh")]:
                        cache.pop(key, None)
    except Exception:  # noqa: BLE001
        LOGGER.debug("Skipped clearing default_agent zh cache", exc_info=True)


def patch_chat_log_result_extraction(hass: HomeAssistant) -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import util as conv_util

    if hasattr(conv_util, _RESULT_PATCHED):
        return

    original_async_get_result_from_chat_log = conv_util.async_get_result_from_chat_log

    @callback
    def patched_async_get_result_from_chat_log(user_input, chat_log):
        synthesized_content = build_synthesized_assistant_from_chat_log(
            chat_log,
            hass=hass,
            language=getattr(user_input, "language", None),
        )
        if synthesized_content is not None:
            if _chat_log_turn_has_assistant_content(chat_log):
                return original_async_get_result_from_chat_log(user_input, chat_log)
            _pop_empty_trailing_assistant(chat_log)
            chat_log.async_add_assistant_content_without_tools(synthesized_content)
            LOGGER.debug(
                "Synthesized final AssistantContent from trailing tool results: %s",
                synthesized_content.agent_id,
            )
        return original_async_get_result_from_chat_log(user_input, chat_log)

    conv_util.async_get_result_from_chat_log = patched_async_get_result_from_chat_log
    conv_module.async_get_result_from_chat_log = patched_async_get_result_from_chat_log
    setattr(conv_util, _RESULT_ORIGINAL, original_async_get_result_from_chat_log)
    setattr(conv_util, _RESULT_PATCHED, True)
    LOGGER.debug("Patched chat log result extraction to close tool-only turns")


def unpatch_chat_log_result_extraction() -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import util as conv_util

    original_async_get_result_from_chat_log = getattr(conv_util, _RESULT_ORIGINAL, None)
    if original_async_get_result_from_chat_log is None:
        return

    conv_util.async_get_result_from_chat_log = original_async_get_result_from_chat_log
    conv_module.async_get_result_from_chat_log = original_async_get_result_from_chat_log
    delattr(conv_util, _RESULT_ORIGINAL)
    if hasattr(conv_util, _RESULT_PATCHED):
        delattr(conv_util, _RESULT_PATCHED)
    LOGGER.debug("Restored chat log result extraction after claw_assistant unload")


def patch_chat_log_stream_closure(hass: HomeAssistant) -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    if hasattr(chat_log_module.ChatLog, _STREAM_CLOSURE_PATCHED):
        return

    original_async_add_delta_content_stream = (
        chat_log_module.ChatLog.async_add_delta_content_stream
    )

    async def patched_async_add_delta_content_stream(self, agent_id, stream):
        if not getattr(self, "_claw_listener_wrapped", False):
            orig = getattr(self, "delta_listener", None)
            if orig:
                self.delta_listener = _wrap_listener_for_tracking(self, orig)

        async for content in original_async_add_delta_content_stream(
            self, agent_id, stream
        ):
            yield content

        synthesized_content = build_synthesized_assistant_from_chat_log(self)
        if synthesized_content is None:
            return

        if _chat_log_turn_has_assistant_content(self):
            return
        _pop_empty_trailing_assistant(self)
        self.async_add_assistant_content_without_tools(synthesized_content)
        LOGGER.debug(
            "Closed native ChatLog stream with synthesized AssistantContent: %s",
            synthesized_content.agent_id,
        )

    chat_log_module.ChatLog.async_add_delta_content_stream = (
        patched_async_add_delta_content_stream
    )
    setattr(
        chat_log_module.ChatLog,
        _STREAM_CLOSURE_ORIGINAL,
        original_async_add_delta_content_stream,
    )
    setattr(chat_log_module.ChatLog, _STREAM_CLOSURE_PATCHED, True)
    LOGGER.debug("Patched ChatLog.async_add_delta_content_stream to close tool-only turns")


def unpatch_chat_log_stream_closure() -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    original_async_add_delta_content_stream = getattr(
        chat_log_module.ChatLog, _STREAM_CLOSURE_ORIGINAL, None
    )
    if original_async_add_delta_content_stream is None:
        return

    chat_log_module.ChatLog.async_add_delta_content_stream = (
        original_async_add_delta_content_stream
    )
    delattr(chat_log_module.ChatLog, _STREAM_CLOSURE_ORIGINAL)
    if hasattr(chat_log_module.ChatLog, _STREAM_CLOSURE_PATCHED):
        delattr(chat_log_module.ChatLog, _STREAM_CLOSURE_PATCHED)
    LOGGER.debug("Restored chat log stream closure after claw_assistant unload")


def patch_chatlog_tools(hass: HomeAssistant) -> None:

    from .internal_llm import patch_chatlog_tools as patch_internal_llm_chatlog_tools

    patch_internal_llm_chatlog_tools(hass)


def patch_hide_tool_calls_from_pipeline(hass: HomeAssistant) -> None:

    from homeassistant.components.assist_pipeline.pipeline import (
        PipelineEvent,
        PipelineEventType,
        PipelineRun,
    )

    if getattr(PipelineRun, _PIPELINE_FILTER_PATCHED, False):
        return

    original_process_event = PipelineRun.process_event

    def _agent_is_ours(run: PipelineRun) -> bool:
        agent_info = getattr(run, "intent_agent", None)
        agent_id = getattr(agent_info, "id", None)
        if not agent_id:
            return False

        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entity = registry.async_get(agent_id)
        if entity is None:
            return False
        return entity.platform in (DOMAIN, "ai_hub")

    def _agent_emits_buggy_thinking(run: PipelineRun) -> bool:
        agent_info = getattr(run, "intent_agent", None)
        agent_id = getattr(agent_info, "id", None)
        if not agent_id:
            return False
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entity = registry.async_get(agent_id)
        if entity is None:
            return False
        return entity.platform in _THINKING_BUGGY_UPSTREAM_PLATFORMS

    def _is_tool_only_delta(delta: dict) -> bool:
        if not isinstance(delta, dict):
            return False
        if delta.get("role") == "tool_result":
            return True
        if "tool_calls" in delta and not delta.get("content") and not delta.get(
            "thinking_content"
        ):
            return True
        return False

    def _extract_step_event(delta: dict) -> dict | None:
        if not isinstance(delta, dict):
            return None
        payload = delta.get("km_step_event")
        return payload if isinstance(payload, dict) else None

    def _is_detailed_mode() -> bool:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return False
        return entries[0].options.get(
            CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME
        ) == CONVERSATION_MODE_DETAILED

    def filtered_process_event(self, event) -> None:
        delta = (event.data or {}).get("chat_log_delta")

        if (
            event.type == PipelineEventType.INTENT_PROGRESS
            and isinstance(delta, dict)
            and "_tts_skip_content" in delta
        ):
            restored = {k: v for k, v in delta.items() if k != "_tts_skip_content"}
            restored["content"] = delta["_tts_skip_content"]
            return original_process_event(
                self,
                PipelineEvent(
                    event.type,
                    {**(event.data or {}), "chat_log_delta": restored},
                ),
            )

        # Suppress the frontend `.thinking-wrapper` UNCONDITIONALLY.
        # Reason: claw_assistant is the visible conversation agent, while
        # gemini / openai_conversation / open_router are only internal
        # backends invoked through internal_llm. The PipelineRun.intent_agent
        # platform therefore reads as `claw_assistant`, never as the upstream
        # platform that actually emitted the chunk, so any platform-based
        # gate is a no-op here. The wrapper is gated by
        # `e.thinking || (e.tool_calls && length>0)` in the dialog template,
        # so we strip BOTH fields from every forwarded delta. chat_log
        # internal accumulation is untouched: tool execution, history,
        # TTS, and diagnostics still receive the full payload.
        if (
            event.type == PipelineEventType.INTENT_PROGRESS
            and isinstance(delta, dict)
            and ("thinking_content" in delta or "tool_calls" in delta)
        ):
            cleaned = {
                k: v
                for k, v in delta.items()
                if k not in ("thinking_content", "tool_calls")
            }
            if not cleaned or set(cleaned.keys()) == {"role"}:
                return
            return original_process_event(
                self,
                PipelineEvent(
                    event.type,
                    {**(event.data or {}), "chat_log_delta": cleaned},
                ),
            )

        if _is_detailed_mode() and _agent_is_ours(self):
            return original_process_event(self, event)
        if (
            event.type == PipelineEventType.INTENT_PROGRESS
            and get_conversation_status(hass).get("kernel_mode_active")
            and _agent_is_ours(self)
            and isinstance(delta, dict)
            and not _extract_step_event(delta)
            and not delta.get("content")
            and delta.get("role") != "assistant"
        ):
            return
        if (
            event.type == PipelineEventType.INTENT_PROGRESS
            and isinstance(delta, dict)
            and (step_event := _extract_step_event(delta))
            and _agent_is_ours(self)
        ):
            return original_process_event(
                self,
                PipelineEvent(
                    PipelineEventType.INTENT_PROGRESS,
                    {"chat_log_delta": {"role": "assistant", "content": step_event.get("title", "")}},
                ),
            )
        if (
            event.type == PipelineEventType.INTENT_PROGRESS
            and _is_tool_only_delta(delta)
        ):
            return
        return original_process_event(self, event)

    PipelineRun.process_event = filtered_process_event
    setattr(PipelineRun, _PIPELINE_FILTER_ORIGINAL, original_process_event)

    original_text_to_speech = PipelineRun.text_to_speech

    async def filtered_text_to_speech(self, tts_input, override_media_path=None):
        if tts_input and _agent_is_ours(self):
            tts_input = _clean_for_tts(tts_input)
        return await original_text_to_speech(self, tts_input, override_media_path=override_media_path)

    PipelineRun.text_to_speech = filtered_text_to_speech
    setattr(PipelineRun, "_claw_original_text_to_speech", original_text_to_speech)

    setattr(PipelineRun, _PIPELINE_FILTER_PATCHED, True)
    LOGGER.debug(
        "Patched PipelineRun.process_event + text_to_speech for claw_assistant pipelines"
    )


def unpatch_hide_tool_calls_from_pipeline() -> None:

    from homeassistant.components.assist_pipeline.pipeline import PipelineRun

    original_process_event = getattr(PipelineRun, _PIPELINE_FILTER_ORIGINAL, None)
    if original_process_event is None:
        return

    PipelineRun.process_event = original_process_event
    delattr(PipelineRun, _PIPELINE_FILTER_ORIGINAL)
    original_tts = getattr(PipelineRun, "_claw_original_text_to_speech", None)
    if original_tts:
        PipelineRun.text_to_speech = original_tts
        delattr(PipelineRun, "_claw_original_text_to_speech")
    if hasattr(PipelineRun, _PIPELINE_FILTER_PATCHED):
        delattr(PipelineRun, _PIPELINE_FILTER_PATCHED)
    LOGGER.debug("Restored PipelineRun.process_event + text_to_speech after claw_assistant unload")


def patch_strip_thinking_content_serialization(hass: HomeAssistant) -> None:
    """Strip thinking_content from AssistantContent.as_dict() for upstream LLM agents.

    The HA frontend's chat panel reaches AssistantContent thinking_content
    via TWO independent channels:

    1. PipelineRun.process_event INTENT_PROGRESS deltas (handled by the
       patch above on filtered_process_event).
    2. The conversation/chat_log/subscribe websocket, which forwards
       ChatLogEventType.CONTENT_ADDED / UPDATED / CREATED events whose
       payloads come from AssistantContent.as_dict() and ChatLog.as_dict().

    This patch closes channel 2 by removing thinking_content from the
    serialized dict whenever the owning agent_id resolves to one of the
    known buggy upstream LLM integrations. Internal Python access to
    AssistantContent.thinking_content is unaffected, so history,
    diagnostics, and any in-process logic still see the full text.
    """

    from homeassistant.components.conversation.chat_log import AssistantContent

    if getattr(AssistantContent, _THINKING_STRIP_PATCHED, False):
        return

    original_as_dict = AssistantContent.as_dict

    def patched_as_dict(self) -> dict[str, Any]:
        # Strip unconditionally: see the rationale on the pipeline-event
        # patch above. The agent_id on the AssistantContent reflects the
        # claw_assistant entity, not the upstream backend, so platform
        # filtering would always miss the case we actually need to fix.
        result = original_as_dict(self)
        result.pop("thinking_content", None)
        result.pop("tool_calls", None)
        return result

    AssistantContent.as_dict = patched_as_dict  # type: ignore[method-assign]
    setattr(AssistantContent, _THINKING_STRIP_ORIGINAL, original_as_dict)
    setattr(AssistantContent, _THINKING_STRIP_PATCHED, True)
    LOGGER.debug(
        "Patched AssistantContent.as_dict to strip thinking_content + "
        "tool_calls unconditionally (frontend thinking-wrapper suppression)"
    )


def unpatch_strip_thinking_content_serialization() -> None:

    from homeassistant.components.conversation.chat_log import AssistantContent

    original_as_dict = getattr(AssistantContent, _THINKING_STRIP_ORIGINAL, None)
    if original_as_dict is None:
        return

    AssistantContent.as_dict = original_as_dict  # type: ignore[method-assign]
    delattr(AssistantContent, _THINKING_STRIP_ORIGINAL)
    if hasattr(AssistantContent, _THINKING_STRIP_PATCHED):
        delattr(AssistantContent, _THINKING_STRIP_PATCHED)
    LOGGER.debug("Restored AssistantContent.as_dict after claw_assistant unload")


def _is_streaming_enabled(hass: HomeAssistant) -> bool:
    from ..const import CONF_ENABLE_STREAMING_EFFECT
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return True
    return entries[0].options.get(CONF_ENABLE_STREAMING_EFFECT, True)


def _is_tool_progress_enabled(hass: HomeAssistant) -> bool:
    from ..const import CONF_ENABLE_TOOL_PROGRESS
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return True
    return entries[0].options.get(CONF_ENABLE_TOOL_PROGRESS, True)


_HEADING_STRIP_RE = re.compile(r"^#{1,6}\s+")

_MD_BOLD_ITALIC_RE = re.compile(r"(?<!\w)\*{1,3}([^*]+?)\*{1,3}(?!\w)")
_MD_STRIKE_RE = re.compile(r"~~([^~]+)~~")
_MD_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n[\s\S]*?```")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)\s]+\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(https?://[^)\s]+\)")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s?", re.MULTILINE)
_MD_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MD_LIST_RE = re.compile(r"^[\s]*[-*+]\s", re.MULTILINE)
_MD_ORDERED_LIST_RE = re.compile(r"^[\s]*\d+\.\s", re.MULTILINE)
_MD_TABLE_SEP_RE = re.compile(r"\|?[-:]+[-|: ]+\|?")
_MD_TABLE_PIPE_RE = re.compile(r"\|")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _clean_for_tts(text: str) -> str:
    if not text:
        return text
    t = _MD_CODE_BLOCK_RE.sub("", text)
    t = _MD_IMAGE_RE.sub(r"\1", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _MD_BOLD_ITALIC_RE.sub(r"\1", t)
    t = _MD_STRIKE_RE.sub(r"\1", t)
    t = _MD_INLINE_CODE_RE.sub(r"\1", t)
    t = _MD_HEADING_RE.sub("", t)
    t = _MD_BLOCKQUOTE_RE.sub("", t)
    t = _MD_HR_RE.sub("", t)
    t = _MD_TABLE_SEP_RE.sub("", t)
    t = _MD_TABLE_PIPE_RE.sub(" ", t)
    t = _MD_LIST_RE.sub("", t)
    t = _MD_ORDERED_LIST_RE.sub("", t)
    t = _MULTI_SPACE_RE.sub(" ", t)
    t = _MULTI_NEWLINE_RE.sub("\n\n", t)
    return t.strip()


def _wrap_listener_for_tracking(chat_log, original_listener):
    """Wrap delta_listener to track the last emitted character, strip headings,
    and separate TTS content (cleaned) from frontend display content (original)."""
    if getattr(chat_log, "_claw_listener_wrapped", False):
        return original_listener

    def tracked_listener(log, delta):
        if not isinstance(delta, dict):
            return original_listener(log, delta)
        skip_tts = delta.pop("_claw_skip_tts", False)
        content = delta.get("content")
        if content:
            last = getattr(chat_log, "_claw_last_char", "\n")
            if last == "\n":
                stripped = content.lstrip("#")
                if stripped != content:
                    content = stripped.lstrip(" ") if stripped else None
                    if not content:
                        return
                    delta = {**delta, "content": content}
            chat_log._claw_last_char = content[-1]
        if skip_tts:
            if content:
                delta = {k: v for k, v in delta.items() if k != "content"}
                delta["_tts_skip_content"] = content
            return original_listener(log, delta)
        if content:
            tts_text = _clean_for_tts(content)
            delta = {**delta, "_tts_skip_content": content}
            if tts_text:
                delta["content"] = tts_text
            else:
                del delta["content"]
        return original_listener(log, delta)

    chat_log._claw_listener_wrapped = True
    chat_log._claw_last_char = "\n"
    return tracked_listener


_PROGRESS_CHUNK_RE = re.compile(r"(\*[^*]*\*|[^\s*]+|\s+)")


async def _emit_frontend_progress(hass: HomeAssistant, chat_log, text: str) -> None:
    listener = getattr(chat_log, "delta_listener", None)
    if not listener or not text:
        return

    from .state import get_channel_type

    if get_channel_type(getattr(chat_log, "conversation_id", None)) != "ha":
        return

    if not getattr(chat_log, "_claw_listener_wrapped", False):
        wrapped = _wrap_listener_for_tracking(chat_log, listener)
        chat_log.delta_listener = wrapped
        listener = wrapped

    if not getattr(chat_log, "_claw_progress_active", False):
        listener(chat_log, {"role": "assistant", "_claw_skip_tts": True})
        chat_log._claw_progress_active = True
    else:
        listener(chat_log, {"content": "\n", "_claw_skip_tts": True})

    if not text.startswith("\n"):
        text = "\n" + text
    full = text

    if not _is_streaming_enabled(hass):
        listener(chat_log, {"content": full, "_claw_skip_tts": True})
        listener(chat_log, {"content": "\n", "_claw_skip_tts": True})
        return

    chunks = _PROGRESS_CHUNK_RE.findall(full)
    if not chunks:
        listener(chat_log, {"content": full, "_claw_skip_tts": True})
        listener(chat_log, {"content": "\n", "_claw_skip_tts": True})
        return

    await asyncio.sleep(0)
    for chunk in chunks:
        listener(chat_log, {"content": chunk, "_claw_skip_tts": True})
        await asyncio.sleep(0.02)
    listener(chat_log, {"content": "\n", "_claw_skip_tts": True})


def patch_tool_progress(hass: HomeAssistant) -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module
    from .events import fire_live_progress
    from .tool_progress import tool_progress_line

    if getattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED, False):
        return

    original_async_add_assistant_content = (
        chat_log_module.ChatLog.async_add_assistant_content
    )

    _THINK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL)

    async def patched_async_add_assistant_content(
        self, content, /, tool_call_tasks=None
    ):
        from dataclasses import replace as _replace

        if not getattr(self, "_claw_listener_wrapped", False):
            orig = getattr(self, "delta_listener", None)
            if orig:
                self.delta_listener = _wrap_listener_for_tracking(self, orig)

        thinking_text = ""
        raw_text = getattr(content, "content", None) or ""

        if raw_text and raw_text.strip():
            self._claw_has_content = True

        think_match = _THINK_RE.search(raw_text)
        if think_match:
            thinking_text = think_match.group(1).strip()
            content = _replace(content, content=_THINK_RE.sub("", raw_text).strip() or None)
        native_thinking = getattr(content, "thinking_content", None)
        if native_thinking and native_thinking.strip():
            thinking_text = native_thinking.strip()
        _progress_on = _is_tool_progress_enabled(hass)
        if thinking_text and _progress_on:
            truncated = thinking_text[:120].replace("#", "").replace(">", "").replace("<", "").replace("|", "")
            lines = [l.strip() for l in truncated.splitlines() if l.strip()]
            display = " ".join(lines)
            from .state import get_conversation_status
            lang = get_conversation_status(hass).get("user_language") or hass.config.language or "en"
            await _emit_frontend_progress(hass, self, f"\n┊ *💭 {display}*")
            fire_live_progress(
                hass,
                conversation_id=getattr(self, "conversation_id", None),
                phase="thinking",
                text=truncated,
                display_text=tool_progress_line("GetLiveContext", {}, lang).strip(),
            )

        tool_calls = getattr(content, "tool_calls", None)
        if tool_calls and _progress_on:
            from .state import get_conversation_status
            lang = get_conversation_status(hass).get("user_language") or hass.config.language or "en"
            for tc in tool_calls:
                if getattr(tc, "external", False):
                    continue
                args = dict(tc.tool_args)
                if tc.tool_name == "NextAgentHandoff":
                    from ..tools.ha_core_tools import _resolve_peer_agents
                    peers = _resolve_peer_agents(hass)
                    others = [p for p in peers if not p.get("is_you")]
                    if others:
                        args["agent_name"] = others[0].get("agent_name", "")
                line = tool_progress_line(tc.tool_name, args, lang, hass=hass)
                await _emit_frontend_progress(hass, self, line.strip())
                fire_live_progress(
                    hass,
                    conversation_id=getattr(self, "conversation_id", None),
                    phase="tool_call",
                    text="",
                    tool_name=tc.tool_name,
                    display_text=line.strip(),
                )

        _had_progress = bool(thinking_text) or bool(
            tool_calls and any(not getattr(tc, "external", False) for tc in tool_calls)
        )
        if _had_progress:
            self._claw_progress_active = False
            self._claw_has_content = False

        async for result in original_async_add_assistant_content(
            self, content, tool_call_tasks=tool_call_tasks
        ):
            yield result

    chat_log_module.ChatLog.async_add_assistant_content = (
        patched_async_add_assistant_content
    )
    setattr(
        chat_log_module.ChatLog,
        _TOOL_PROGRESS_ORIGINAL,
        original_async_add_assistant_content,
    )

    original_delta_stream = chat_log_module.ChatLog.async_add_delta_content_stream

    _STREAM_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

    async def patched_delta_stream(self, agent_id, stream):
        if not getattr(self, "_claw_listener_wrapped", False):
            orig = getattr(self, "delta_listener", None)
            if orig:
                self.delta_listener = _wrap_listener_for_tracking(self, orig)

        async def _filtered_stream(src):
            async for delta in src:
                if isinstance(delta, dict) and "content" in delta and delta["content"]:
                    c = _STREAM_HEADING_RE.sub("", delta["content"])
                    if c != delta["content"]:
                        delta = {**delta, "content": c}
                yield delta

        async for item in original_delta_stream(self, agent_id, _filtered_stream(stream)):
            yield item

    chat_log_module.ChatLog.async_add_delta_content_stream = patched_delta_stream
    setattr(chat_log_module.ChatLog, "_claw_original_delta_stream", original_delta_stream)

    setattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED, True)
    LOGGER.debug("Patched ChatLog.async_add_assistant_content + async_add_delta_content_stream")


def unpatch_tool_progress() -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    original = getattr(
        chat_log_module.ChatLog, _TOOL_PROGRESS_ORIGINAL, None
    )
    if original is None:
        return

    chat_log_module.ChatLog.async_add_assistant_content = original
    delattr(chat_log_module.ChatLog, _TOOL_PROGRESS_ORIGINAL)
    orig_delta = getattr(chat_log_module.ChatLog, "_claw_original_delta_stream", None)
    if orig_delta:
        chat_log_module.ChatLog.async_add_delta_content_stream = orig_delta
        delattr(chat_log_module.ChatLog, "_claw_original_delta_stream")
    if hasattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED):
        delattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED)
    LOGGER.debug("Restored ChatLog.async_add_assistant_content + delta_stream after unload")


def patch_pipeline_timeout(hass: HomeAssistant) -> None:
    """Patch assist_pipeline default timeout to use claw_assistant config."""
    try:
        import homeassistant.components.assist_pipeline.const as pipeline_const
        import homeassistant.components.assist_pipeline.websocket_api as ws_module
        from ..const import CONF_PIPELINE_TIMEOUT, DEFAULT_PIPELINE_TIMEOUT, DOMAIN

        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return
        custom_timeout = entries[0].options.get(CONF_PIPELINE_TIMEOUT, DEFAULT_PIPELINE_TIMEOUT)
        pipeline_const.DEFAULT_PIPELINE_TIMEOUT = custom_timeout
        ws_module.DEFAULT_PIPELINE_TIMEOUT = custom_timeout
        LOGGER.debug("Patched pipeline default timeout to %s", custom_timeout)
    except Exception as exc:
        LOGGER.debug("Pipeline timeout patch failed: %s", exc)


def patch_apiinstance_tool_fallback(hass: HomeAssistant) -> None:

    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import llm as llm_module

    if getattr(llm_module.APIInstance, _TOOL_FALLBACK_PATCHED, False):
        return

    original_async_call_tool = llm_module.APIInstance.async_call_tool

    async def patched_async_call_tool(self, tool_input):
        try:
            return await original_async_call_tool(self, tool_input)
        except HomeAssistantError as err:
            if "not found" not in str(err):
                raise

            try:
                from ..tools.registry import build_tool_map
            except Exception:
                raise err

            tool_cls = build_tool_map().get(tool_input.tool_name)
            if tool_cls is None:
                raise

            tool = tool_cls()
            args = tool_input.tool_args
            if getattr(tool, "parameters", None) is not None:
                try:
                    args = tool.parameters(args)
                except Exception:
                    args = tool_input.tool_args

            from dataclasses import replace as _replace

            try:
                validated_input = _replace(tool_input, tool_args=args)
            except Exception:
                validated_input = tool_input

            return await tool.async_call(
                self.api.hass, validated_input, self.llm_context
            )

    llm_module.APIInstance.async_call_tool = patched_async_call_tool
    setattr(llm_module.APIInstance, _TOOL_FALLBACK_ORIGINAL, original_async_call_tool)
    setattr(llm_module.APIInstance, _TOOL_FALLBACK_PATCHED, True)


async def _maybe_intercept_chat_command(
    hass,
    *,
    text,
    conversation_id,
    context,
    language,
    agent_id,
    device_id,
    satellite_id,
    extra_system_prompt,
):
    if not text or "/" not in text:
        return None
    try:
        from homeassistant.components import conversation as conversation_module
        from ..chat_commands import async_handle_chat_command, parse_chat_command
    except Exception:
        return None
    if parse_chat_command(text) is None:
        return None
    try:
        user_input = conversation_module.ConversationInput(
            text=text,
            context=context,
            conversation_id=conversation_id,
            device_id=device_id,
            language=language,
            agent_id=agent_id,
            satellite_id=satellite_id,
            extra_system_prompt=extra_system_prompt,
        )
    except TypeError:
        try:
            user_input = conversation_module.ConversationInput(
                text=text,
                context=context,
                conversation_id=conversation_id,
                device_id=device_id,
                language=language,
                agent_id=agent_id,
            )
        except Exception:
            return None
    try:
        outcome = await async_handle_chat_command(hass, user_input)
    except Exception as err:
        LOGGER.debug("chat command intercept raised: %s", err)
        return None
    if outcome is None or outcome.result is None:
        return None
    return outcome.result


def patch_global_response_format(hass: HomeAssistant) -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import agent_manager

    if getattr(agent_manager, _GLOBAL_FORMAT_PATCHED, False):
        return

    original_async_converse = agent_manager.async_converse

    async def patched_async_converse(
        hass,
        text,
        conversation_id,
        context,
        language=None,
        agent_id=None,
        device_id=None,
        satellite_id=None,
        extra_system_prompt=None,
    ):
        chat_command_result = await _maybe_intercept_chat_command(
            hass,
            text=text,
            conversation_id=conversation_id,
            context=context,
            language=language,
            agent_id=agent_id,
            device_id=device_id,
            satellite_id=satellite_id,
            extra_system_prompt=extra_system_prompt,
        )
        if chat_command_result is not None:
            return chat_command_result
        result = await original_async_converse(
            hass,
            text,
            conversation_id,
            context,
            language=language,
            agent_id=agent_id,
            device_id=device_id,
            satellite_id=satellite_id,
            extra_system_prompt=extra_system_prompt,
        )
        try:
            _maybe_apply_global_response_format(hass, result, agent_id)
        except Exception:
            LOGGER.debug("global response format failed", exc_info=True)
        return result

    agent_manager.async_converse = patched_async_converse
    setattr(agent_manager, _GLOBAL_FORMAT_ORIGINAL, original_async_converse)
    setattr(agent_manager, _GLOBAL_FORMAT_PATCHED, True)

    if getattr(conv_module, "async_converse", None) is original_async_converse:
        conv_module.async_converse = patched_async_converse


def _maybe_apply_global_response_format(hass, result, agent_id) -> None:

    if not result or not result.response or not result.response.speech:
        LOGGER.debug("global_format: skip (no result/response/speech) agent=%s", agent_id)
        return

    plain = result.response.speech.get("plain")
    if not isinstance(plain, dict):
        LOGGER.debug("global_format: skip (no plain dict) agent=%s", agent_id)
        return
    if plain.get("agent_name"):
        LOGGER.debug("global_format: skip (already stamped agent_name=%s) agent=%s", plain.get("agent_name"), agent_id)
        return
    LOGGER.debug("global_format: applying format, speech=%s agent=%s", str(plain.get("speech", ""))[:80], agent_id)

    from ..const import (
        CONF_CONVERSATION_MODE,
        DEFAULT_CONVERSATION_MODE,
        DOMAIN,
    )
    from .response_format import apply_agent_response_format

    is_ours = False
    agent_name = "Assistant"
    if agent_id:
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(hass)
        entity = registry.async_get(agent_id)
        if entity is not None:
            is_ours = entity.platform == DOMAIN
            if is_ours:
                agent_name = "Claw Assistant"
            else:
                agent_name = entity.name or entity.original_name or entity.platform.replace("_", " ").title()
        else:
            state = hass.states.get(agent_id)
            if state:
                agent_name = state.attributes.get("friendly_name") or agent_id.split(".")[-1].replace("_", " ").title()
            else:
                agent_name = agent_id.split(".")[-1].replace("_", " ").title()

    conversation_mode = DEFAULT_CONVERSATION_MODE
    if is_ours:
        try:
            entries = hass.config_entries.async_entries(DOMAIN)
            if entries:
                conversation_mode = entries[0].options.get(
                    CONF_CONVERSATION_MODE, DEFAULT_CONVERSATION_MODE
                )
        except Exception:
            pass

    apply_agent_response_format(
        result,
        hass=hass,
        agent_name=agent_name,
        agent_id=agent_id or "",
        conversation_mode=conversation_mode,
    )


def unpatch_global_response_format() -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import agent_manager

    original_async_converse = getattr(agent_manager, _GLOBAL_FORMAT_ORIGINAL, None)
    if original_async_converse is None:
        return

    current_patched = agent_manager.async_converse
    agent_manager.async_converse = original_async_converse
    delattr(agent_manager, _GLOBAL_FORMAT_ORIGINAL)
    if hasattr(agent_manager, _GLOBAL_FORMAT_PATCHED):
        delattr(agent_manager, _GLOBAL_FORMAT_PATCHED)

    if getattr(conv_module, "async_converse", None) is current_patched:
        conv_module.async_converse = original_async_converse


def unpatch_apiinstance_tool_fallback() -> None:

    from homeassistant.helpers import llm as llm_module

    original_async_call_tool = getattr(
        llm_module.APIInstance, _TOOL_FALLBACK_ORIGINAL, None
    )
    if original_async_call_tool is None:
        return

    llm_module.APIInstance.async_call_tool = original_async_call_tool
    delattr(llm_module.APIInstance, _TOOL_FALLBACK_ORIGINAL)
    if hasattr(llm_module.APIInstance, _TOOL_FALLBACK_PATCHED):
        delattr(llm_module.APIInstance, _TOOL_FALLBACK_PATCHED)


def unpatch_pipeline_timeout() -> None:
    """Restore assist_pipeline default timeout to HA default (300s)."""
    try:
        import homeassistant.components.assist_pipeline.const as pipeline_const
        import homeassistant.components.assist_pipeline.websocket_api as ws_module
        pipeline_const.DEFAULT_PIPELINE_TIMEOUT = 300
        ws_module.DEFAULT_PIPELINE_TIMEOUT = 300
        LOGGER.debug("Restored pipeline default timeout to 300")
    except Exception as exc:
        LOGGER.debug("Pipeline timeout unpatch failed: %s", exc)


_WS_BINARY_PATCHED = "_claw_assistant_ws_binary_patched"
_WS_BINARY_ORIGINAL = "_claw_assistant_ws_binary_original"


def patch_websocket_binary_handler_noise(hass: HomeAssistant) -> None:
    """Silently drop binary chunks for handlers that were already unregistered.

    Frontend voice/STT clients keep streaming PCM chunks for a short time
    after the pipeline ends (VAD end, STT settled, user cancelled). HA core
    logs every such chunk at ``ERROR`` level, flooding the log. The handler
    was unregistered legitimately — the late packets are harmless protocol
    tail, not an attack or bug. We downgrade this specific case to ``DEBUG``
    while keeping genuinely-unknown handler ids at ``ERROR``.
    """

    from homeassistant.components.websocket_api import connection as ws_conn

    if getattr(ws_conn.ActiveConnection, _WS_BINARY_PATCHED, False):
        return

    original = ws_conn.ActiveConnection.async_handle_binary

    @callback
    def patched_async_handle_binary(
        self: "ws_conn.ActiveConnection", handler_id: int, payload: bytes
    ) -> None:
        index = handler_id - 1
        handlers = self.binary_handlers
        if (
            index < 0
            or index >= len(handlers)
            or (handler := handlers[index]) is None
        ):
            self.logger.debug(
                "Dropping binary chunk for inactive handler %s", handler_id
            )
            return
        try:
            handler(self.hass, self, payload)
        except Exception:
            self.logger.exception("Error handling binary message")
            handlers[index] = None

    setattr(ws_conn.ActiveConnection, _WS_BINARY_ORIGINAL, original)
    ws_conn.ActiveConnection.async_handle_binary = patched_async_handle_binary
    setattr(ws_conn.ActiveConnection, _WS_BINARY_PATCHED, True)

    import logging as _logging

    class _BinaryHandlerNoiseFilter(_logging.Filter):
        def filter(self, record: _logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            return "Received binary message for non-existing handler" not in msg

    noise_logger = _logging.getLogger(
        "homeassistant.components.websocket_api.http.connection"
    )
    if not any(
        isinstance(f, _BinaryHandlerNoiseFilter) for f in noise_logger.filters
    ):
        noise_logger.addFilter(_BinaryHandlerNoiseFilter())
        setattr(ws_conn.ActiveConnection, "_claw_ws_noise_filter_installed", True)
    LOGGER.debug("Silenced websocket 'non-existing handler' ERROR noise")


def unpatch_websocket_binary_handler_noise() -> None:
    try:
        from homeassistant.components.websocket_api import connection as ws_conn
    except Exception:  # pragma: no cover
        return
    original = getattr(ws_conn.ActiveConnection, _WS_BINARY_ORIGINAL, None)
    if original is None:
        return
    ws_conn.ActiveConnection.async_handle_binary = original
    try:
        delattr(ws_conn.ActiveConnection, _WS_BINARY_ORIGINAL)
        delattr(ws_conn.ActiveConnection, _WS_BINARY_PATCHED)
    except AttributeError:
        pass

    import logging as _logging

    noise_logger = _logging.getLogger(
        "homeassistant.components.websocket_api.http.connection"
    )
    for flt in list(noise_logger.filters):
        if type(flt).__name__ == "_BinaryHandlerNoiseFilter":
            noise_logger.removeFilter(flt)
    try:
        delattr(ws_conn.ActiveConnection, "_claw_ws_noise_filter_installed")
    except AttributeError:
        pass
    LOGGER.debug("Restored websocket binary handler logging")


_AIHUB_TIMEOUT_PATCHED = "_claw_aihub_timeout_patched"
_AIHUB_TIMEOUT_ORIGINAL = 60.0


_AIHUB_TIMEOUT_TARGET = 300.0


def patch_aihub_provider_timeout(hass: HomeAssistant) -> None:
    try:
        from custom_components.ai_hub.providers.base import BaseProviderConfig
        if getattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED, False):
            return
        global _AIHUB_TIMEOUT_ORIGINAL
        _AIHUB_TIMEOUT_ORIGINAL = BaseProviderConfig.__dataclass_fields__["timeout"].default
        BaseProviderConfig.__dataclass_fields__["timeout"].default = _AIHUB_TIMEOUT_TARGET
        original_init = BaseProviderConfig.__init__

        def _patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            if self.timeout == _AIHUB_TIMEOUT_ORIGINAL:
                self.timeout = _AIHUB_TIMEOUT_TARGET

        BaseProviderConfig.__init__ = _patched_init
        BaseProviderConfig._claw_original_init = original_init

        _bump_existing_providers(hass)
        setattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED, True)
        LOGGER.debug("Patched ai_hub provider timeout: %s -> %ss", _AIHUB_TIMEOUT_ORIGINAL, _AIHUB_TIMEOUT_TARGET)
    except Exception as exc:
        LOGGER.debug("ai_hub provider timeout patch skipped: %s", exc)


def _bump_existing_providers(hass: HomeAssistant) -> None:
    try:
        data = hass.data.get("ai_hub")
        if not data:
            return
        entities = data if isinstance(data, list) else ([data] if hasattr(data, "_providers") else [])
        for obj in entities:
            providers = getattr(obj, "_providers", None) or {}
            if isinstance(providers, dict):
                providers = providers.values()
            for provider in providers:
                cfg = getattr(provider, "config", None)
                if cfg and getattr(cfg, "timeout", None) == _AIHUB_TIMEOUT_ORIGINAL:
                    cfg.timeout = _AIHUB_TIMEOUT_TARGET
    except Exception:
        pass


def unpatch_aihub_provider_timeout() -> None:
    try:
        from custom_components.ai_hub.providers.base import BaseProviderConfig
        if not getattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED, False):
            return
        original_init = getattr(BaseProviderConfig, "_claw_original_init", None)
        if original_init:
            BaseProviderConfig.__init__ = original_init
            delattr(BaseProviderConfig, "_claw_original_init")
        BaseProviderConfig.__dataclass_fields__["timeout"].default = _AIHUB_TIMEOUT_ORIGINAL
        delattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED)
        LOGGER.debug("Restored ai_hub provider timeout to %s", _AIHUB_TIMEOUT_ORIGINAL)
    except Exception as exc:
        LOGGER.debug("ai_hub provider timeout unpatch skipped: %s", exc)


_AIHUB_MD_FILTER_PATCHED = "_claw_aihub_md_filter_patched"


def _rich_markdown_enabled(hass: HomeAssistant) -> bool:
    from .const import CONF_ENABLE_RICH_MARKDOWN, DOMAIN
    for entry in hass.config_entries.async_entries(DOMAIN):
        return entry.options.get(CONF_ENABLE_RICH_MARKDOWN, True)
    return True


def patch_aihub_markdown_filter(hass: HomeAssistant) -> None:
    try:
        import custom_components.ai_hub.markdown_filter as md_mod
        if not getattr(md_mod, _AIHUB_MD_FILTER_PATCHED, False):
            md_mod._claw_original_filter_content = md_mod.filter_markdown_content
            md_mod._claw_original_filter_streaming = md_mod.filter_markdown_streaming
            setattr(md_mod, _AIHUB_MD_FILTER_PATCHED, True)
        if _rich_markdown_enabled(hass):
            def _passthrough(content: str) -> str:
                return content
            md_mod.filter_markdown_content = _passthrough
            md_mod.filter_markdown_streaming = _passthrough
            LOGGER.debug("Patched ai_hub markdown_filter to passthrough (rich_markdown ON)")
        else:
            md_mod.filter_markdown_content = md_mod._claw_original_filter_content
            md_mod.filter_markdown_streaming = md_mod._claw_original_filter_streaming
            LOGGER.debug("Restored ai_hub markdown_filter (rich_markdown OFF)")
    except Exception as exc:
        LOGGER.debug("ai_hub markdown_filter patch skipped: %s", exc)


def unpatch_aihub_markdown_filter() -> None:
    try:
        import custom_components.ai_hub.markdown_filter as md_mod
        if not getattr(md_mod, _AIHUB_MD_FILTER_PATCHED, False):
            return
        md_mod.filter_markdown_content = md_mod._claw_original_filter_content
        md_mod.filter_markdown_streaming = md_mod._claw_original_filter_streaming
        delattr(md_mod, "_claw_original_filter_content")
        delattr(md_mod, "_claw_original_filter_streaming")
        delattr(md_mod, _AIHUB_MD_FILTER_PATCHED)
        LOGGER.debug("Restored ai_hub markdown_filter")
    except Exception as exc:
        LOGGER.debug("ai_hub markdown_filter unpatch skipped: %s", exc)


_AIHUB_MAXTOKENS_PATCHED = "_claw_aihub_maxtokens_patched"
_DEFAULT_CONTEXT_WINDOW = 196_608


def _estimate_input_tokens(messages: list) -> int:
    try:
        total = len(json.dumps(messages, ensure_ascii=False, default=str))
    except Exception:
        total = sum(len(str(m)) for m in messages)
    return int(((total + 3) // 4) * 1.25)


def _clamp_max_tokens(request: dict, context_window: int = _DEFAULT_CONTEXT_WINDOW) -> dict:
    token_key = next(
        (key for key in ("max_tokens", "max_completion_tokens", "num_predict") if key in request),
        None,
    )
    if not token_key:
        return request
    max_tok = request.get(token_key)
    if not max_tok or max_tok <= 4096:
        return request
    messages = request.get("messages") or []
    input_est = _estimate_input_tokens(messages)
    tools = request.get("tools")
    if tools:
        input_est += (len(str(tools)) + 3) // 4
    system = request.get("system")
    if system:
        input_est += (len(str(system)) + 3) // 4
    headroom = context_window - input_est
    if headroom < max_tok:
        new_max = max(512, headroom - 2048)
        if new_max >= max_tok:
            return request
        LOGGER.debug(
            "Dynamic max tokens clamp: %s %d -> %d (input ~%d, ctx %d)",
            token_key, max_tok, new_max, input_est, context_window,
        )
        request[token_key] = new_max
    return request


def patch_aihub_dynamic_max_tokens(hass: HomeAssistant) -> None:
    patched_classes = []
    try:
        from custom_components.ai_hub.providers.openai_compatible import OpenAICompatibleProvider
        if not getattr(OpenAICompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, False):
            orig = OpenAICompatibleProvider._build_request

            def _patched_openai(self, messages, stream=False, tools=None, **kwargs):
                request = orig(self, messages, stream=stream, tools=tools, **kwargs)
                ctx = getattr(self.config, "context_length", 0) or _DEFAULT_CONTEXT_WINDOW
                return _clamp_max_tokens(request, ctx)

            OpenAICompatibleProvider._build_request = _patched_openai
            OpenAICompatibleProvider._claw_original_build_request = orig
            setattr(OpenAICompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, True)
            patched_classes.append("OpenAI")
    except Exception as exc:
        LOGGER.debug("OpenAI max_tokens patch skipped: %s", exc)

    try:
        from custom_components.ai_hub.providers.anthropic_compatible import AnthropicCompatibleProvider
        if not getattr(AnthropicCompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, False):
            orig_a = AnthropicCompatibleProvider._build_request

            def _patched_anthropic(self, messages, stream=False, tools=None, **kwargs):
                request = orig_a(self, messages, stream=stream, tools=tools, **kwargs)
                ctx = getattr(self.config, "context_length", 0) or _DEFAULT_CONTEXT_WINDOW
                return _clamp_max_tokens(request, ctx)

            AnthropicCompatibleProvider._build_request = _patched_anthropic
            AnthropicCompatibleProvider._claw_original_build_request = orig_a
            setattr(AnthropicCompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, True)
            patched_classes.append("Anthropic")
    except Exception as exc:
        LOGGER.debug("Anthropic max_tokens patch skipped: %s", exc)

    try:
        from custom_components.ai_hub.providers.ollama_compatible import OllamaCompatibleProvider
        if not getattr(OllamaCompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, False):
            orig_o = OllamaCompatibleProvider._build_request

            def _patched_ollama(self, messages, stream=False, tools=None, **kwargs):
                request = orig_o(self, messages, stream=stream, tools=tools, **kwargs)
                ctx = getattr(self.config, "context_length", 0) or _DEFAULT_CONTEXT_WINDOW
                return _clamp_max_tokens(request, ctx)

            OllamaCompatibleProvider._build_request = _patched_ollama
            OllamaCompatibleProvider._claw_original_build_request = orig_o
            setattr(OllamaCompatibleProvider, _AIHUB_MAXTOKENS_PATCHED, True)
            patched_classes.append("Ollama")
    except Exception as exc:
        LOGGER.debug("Ollama max_tokens patch skipped: %s", exc)

    if patched_classes:
        LOGGER.debug("Patched ai_hub dynamic max_tokens: %s", ", ".join(patched_classes))


def unpatch_aihub_dynamic_max_tokens() -> None:
    for cls_path in (
        "custom_components.ai_hub.providers.openai_compatible.OpenAICompatibleProvider",
        "custom_components.ai_hub.providers.anthropic_compatible.AnthropicCompatibleProvider",
        "custom_components.ai_hub.providers.ollama_compatible.OllamaCompatibleProvider",
    ):
        try:
            parts = cls_path.rsplit(".", 1)
            mod = __import__(parts[0], fromlist=[parts[1]])
            cls = getattr(mod, parts[1])
            if not getattr(cls, _AIHUB_MAXTOKENS_PATCHED, False):
                continue
            orig = getattr(cls, "_claw_original_build_request", None)
            if orig:
                cls._build_request = orig
                delattr(cls, "_claw_original_build_request")
            delattr(cls, _AIHUB_MAXTOKENS_PATCHED)
        except Exception:
            pass
    LOGGER.debug("Restored ai_hub _build_request")


_OPENAI_API_KEY_PATCHED = "_claw_openai_apikey_patched"


async def async_patch_openai_allow_empty_key(hass: HomeAssistant) -> None:
    try:
        import importlib
        import voluptuous as vol
        from homeassistant.const import CONF_API_KEY

        def _import_modules():
            cf = importlib.import_module("homeassistant.components.openai_conversation.config_flow")
            init = importlib.import_module("homeassistant.components.openai_conversation")
            return cf, init

        oai_cf, oai_init = await hass.async_add_executor_job(_import_modules)

        if not getattr(oai_cf, _OPENAI_API_KEY_PATCHED, False):
            oai_cf._claw_orig_validate_input = oai_cf.validate_input
            oai_cf._claw_orig_schema = oai_cf.STEP_USER_DATA_SCHEMA
            setattr(oai_cf, _OPENAI_API_KEY_PATCHED, True)

        oai_cf.STEP_USER_DATA_SCHEMA = vol.Schema(
            {vol.Optional(CONF_API_KEY, default=""): str}
        )

        orig_validate = oai_cf._claw_orig_validate_input

        async def _patched_validate(ha, data):
            key = data.get(CONF_API_KEY, "").strip()
            if not key:
                data[CONF_API_KEY] = "sk-no-key"
                return
            return await orig_validate(ha, data)

        oai_cf.validate_input = _patched_validate

        orig_setup = getattr(oai_init, "_claw_orig_setup_entry", None) or oai_init.async_setup_entry

        if not getattr(oai_init, "_claw_setup_patched", False):
            oai_init._claw_orig_setup_entry = orig_setup

            async def _patched_setup(ha, entry):
                if not entry.data.get(CONF_API_KEY, "").strip():
                    hass.config_entries.async_update_entry(
                        entry, data={**entry.data, CONF_API_KEY: "sk-no-key"}
                    )
                return await orig_setup(ha, entry)

            oai_init.async_setup_entry = _patched_setup
            oai_init._claw_setup_patched = True

        LOGGER.debug("Patched openai_conversation to allow empty API key")
    except Exception as exc:
        LOGGER.debug("openai_conversation api_key patch skipped: %s", exc)


def unpatch_openai_allow_empty_key() -> None:
    try:
        import importlib
        oai_cf = importlib.import_module("homeassistant.components.openai_conversation.config_flow")
        oai_init = importlib.import_module("homeassistant.components.openai_conversation")

        if not getattr(oai_cf, _OPENAI_API_KEY_PATCHED, False):
            return
        oai_cf.validate_input = oai_cf._claw_orig_validate_input
        oai_cf.STEP_USER_DATA_SCHEMA = oai_cf._claw_orig_schema
        delattr(oai_cf, "_claw_orig_validate_input")
        delattr(oai_cf, "_claw_orig_schema")
        delattr(oai_cf, _OPENAI_API_KEY_PATCHED)
        if getattr(oai_init, "_claw_setup_patched", False):
            oai_init.async_setup_entry = oai_init._claw_orig_setup_entry
            delattr(oai_init, "_claw_orig_setup_entry")
            delattr(oai_init, "_claw_setup_patched")
        LOGGER.debug("Restored openai_conversation api_key validation")
    except Exception as exc:
        LOGGER.debug("openai_conversation unpatch skipped: %s", exc)


_AIHUB_IMAGE_RETRY_PATCHED = "_claw_aihub_image_retry_patched"
_IMAGE_URL_ERROR_HINTS = ("image_url", "unknown variant", "expected `text`")


_IMAGE_FALLBACK_HINT = "[Image removed - model does not support vision]"

_IMAGE_OCR_INSTRUCTION = (
    "\n\n[SYSTEM NOTICE: Image content was removed because this model cannot process images. "
    "If you need to analyze the image, you MUST use ExecutePython tool with pytesseract OCR:\n"
    "```python\n"
    "import subprocess\n"
    "subprocess.run(['pip', 'install', '-q', 'pytesseract', 'Pillow'], check=True)\n"
    "from PIL import Image\n"
    "import pytesseract\n"
    "text = pytesseract.image_to_string(Image.open('/path/to/image.jpg'))\n"
    "print(text)\n"
    "```\n"
    "Do NOT guess image content. Use Python OCR or tell user you cannot see the image.]"
)


def _strip_image_blocks(messages: list) -> int:
    """Strip image_url content blocks from LLMMessage list. Returns count stripped."""
    stripped = 0
    to_remove = []
    last_user_idx = -1
    for i, msg in enumerate(messages):
        role = getattr(msg, "role", "")
        if role == "user":
            last_user_idx = i
        content = getattr(msg, "content", None)
        if isinstance(content, list):
            new_content = []
            img_count = 0
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    img_count += 1
                    continue
                new_content.append(part)
            if img_count:
                stripped += img_count
                if new_content:
                    msg.content = new_content
                else:
                    if role == "user":
                        to_remove.append(i)
                    else:
                        msg.content = _IMAGE_FALLBACK_HINT
    for i in reversed(to_remove):
        if i == last_user_idx:
            last_user_idx = -1
        elif i < last_user_idx:
            last_user_idx -= 1
        messages.pop(i)
    if stripped and last_user_idx >= 0:
        last_user = messages[last_user_idx]
        content = getattr(last_user, "content", "")
        if isinstance(content, str):
            last_user.content = content + _IMAGE_OCR_INSTRUCTION
        elif isinstance(content, list):
            content.append({"type": "text", "text": _IMAGE_OCR_INSTRUCTION})
    return stripped


def _is_image_url_error(err: Exception) -> bool:
    err_str = str(err).lower()
    return any(hint in err_str for hint in _IMAGE_URL_ERROR_HINTS)


def patch_aihub_image_url_retry(hass: HomeAssistant) -> None:
    patched = []
    try:
        from custom_components.ai_hub.providers.openai_compatible import OpenAICompatibleProvider
        if not getattr(OpenAICompatibleProvider, _AIHUB_IMAGE_RETRY_PATCHED, False):
            orig_complete_stream = OpenAICompatibleProvider.complete_stream

            async def _patched_openai_stream(self, messages, tools=None, **kwargs):
                try:
                    async for chunk in orig_complete_stream(self, messages, tools=tools, **kwargs):
                        yield chunk
                except Exception as err:
                    if not _is_image_url_error(err):
                        raise
                    stripped = _strip_image_blocks(messages)
                    if not stripped:
                        raise
                    LOGGER.info("OpenAI provider hit image_url error; stripped %d images and retrying", stripped)
                    async for chunk in orig_complete_stream(self, messages, tools=tools, **kwargs):
                        yield chunk

            OpenAICompatibleProvider.complete_stream = _patched_openai_stream
            OpenAICompatibleProvider._claw_orig_complete_stream = orig_complete_stream
            setattr(OpenAICompatibleProvider, _AIHUB_IMAGE_RETRY_PATCHED, True)
            patched.append("OpenAI")
    except Exception as exc:
        LOGGER.debug("ai_hub OpenAI image_url retry patch skipped: %s", exc)

    try:
        from custom_components.ai_hub.providers.anthropic_compatible import AnthropicCompatibleProvider
        if not getattr(AnthropicCompatibleProvider, _AIHUB_IMAGE_RETRY_PATCHED, False):
            orig_anthropic_stream = AnthropicCompatibleProvider.complete_stream

            async def _patched_anthropic_stream(self, messages, tools=None, **kwargs):
                try:
                    async for chunk in orig_anthropic_stream(self, messages, tools=tools, **kwargs):
                        yield chunk
                except Exception as err:
                    if not _is_image_url_error(err):
                        raise
                    stripped = _strip_image_blocks(messages)
                    if not stripped:
                        raise
                    LOGGER.info("Anthropic provider hit image_url error; stripped %d images and retrying", stripped)
                    async for chunk in orig_anthropic_stream(self, messages, tools=tools, **kwargs):
                        yield chunk

            AnthropicCompatibleProvider.complete_stream = _patched_anthropic_stream
            AnthropicCompatibleProvider._claw_orig_complete_stream = orig_anthropic_stream
            setattr(AnthropicCompatibleProvider, _AIHUB_IMAGE_RETRY_PATCHED, True)
            patched.append("Anthropic")
    except Exception as exc:
        LOGGER.debug("ai_hub Anthropic image_url retry patch skipped: %s", exc)

    if patched:
        LOGGER.debug("Patched ai_hub complete_stream for image_url retry: %s", ", ".join(patched))


def unpatch_aihub_image_url_retry() -> None:
    for cls_path in (
        "custom_components.ai_hub.providers.openai_compatible.OpenAICompatibleProvider",
        "custom_components.ai_hub.providers.anthropic_compatible.AnthropicCompatibleProvider",
    ):
        try:
            parts = cls_path.rsplit(".", 1)
            mod = __import__(parts[0], fromlist=[parts[1]])
            cls = getattr(mod, parts[1])
            if not getattr(cls, _AIHUB_IMAGE_RETRY_PATCHED, False):
                continue
            orig = getattr(cls, "_claw_orig_complete_stream", None)
            if orig:
                cls.complete_stream = orig
                delattr(cls, "_claw_orig_complete_stream")
            delattr(cls, _AIHUB_IMAGE_RETRY_PATCHED)
        except Exception:
            pass
    LOGGER.debug("Restored ai_hub complete_stream")
