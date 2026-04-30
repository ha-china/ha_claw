

from __future__ import annotations

import asyncio
import logging
import re

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
_RESULT_PATCHED = "_ha_crack_result_patched"
_RESULT_ORIGINAL = "_ha_crack_original_result_extraction"
_STREAM_CLOSURE_PATCHED = "_ha_crack_stream_closure_patched"
_STREAM_CLOSURE_ORIGINAL = "_ha_crack_original_stream_closure"
_PIPELINE_FILTER_PATCHED = "_claw_assistant_tool_filter_patched"
_PIPELINE_FILTER_ORIGINAL = "_claw_assistant_original_process_event"
_TOOL_PROGRESS_PATCHED = "_claw_assistant_tool_progress_patched"
_TOOL_PROGRESS_ORIGINAL = "_claw_assistant_tool_progress_original"


def _pop_empty_trailing_assistant(chat_log) -> None:

    content = getattr(chat_log, "content", None)
    if not content:
        return
    last = content[-1]
    if isinstance(last, AssistantContent) and not last.content:
        content.pop()


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
                if speech_text and conversation_mode != CONVERSATION_MODE_NO_NAME:
                    from .response_format import reply_labels

                    agent_name = "Home Assistant"
                    reply = reply_labels(
                        getattr(user_input, "language", None)
                    )["reply"]
                    if conversation_mode in (
                        CONVERSATION_MODE_ADD_NAME,
                        CONVERSATION_MODE_DETAILED,
                    ):
                        result.speech["plain"]["speech"] = (
                            f"({agent_name}) {reply}: {speech_text}"
                        )

                    result.speech["plain"]["original_speech"] = speech_text
                    result.speech["plain"]["agent_name"] = agent_name

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


def patch_chat_log_result_extraction(hass: HomeAssistant) -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import util as conv_util

    if hasattr(conv_util, _RESULT_PATCHED):
        return

    original_async_get_result_from_chat_log = conv_util.async_get_result_from_chat_log

    @callback
    def patched_async_get_result_from_chat_log(user_input, chat_log):
        synthesized_content = build_synthesized_assistant_from_chat_log(chat_log)
        if synthesized_content is not None:
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
        async for content in original_async_add_delta_content_stream(
            self, agent_id, stream
        ):
            yield content

        synthesized_content = build_synthesized_assistant_from_chat_log(self)
        if synthesized_content is None:
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
    setattr(PipelineRun, _PIPELINE_FILTER_PATCHED, True)
    LOGGER.debug(
        "Patched PipelineRun.process_event to hide tool_calls/tool_result deltas "
        "from the Assist frontend for claw_assistant-owned pipelines"
    )


def unpatch_hide_tool_calls_from_pipeline() -> None:

    from homeassistant.components.assist_pipeline.pipeline import PipelineRun

    original_process_event = getattr(PipelineRun, _PIPELINE_FILTER_ORIGINAL, None)
    if original_process_event is None:
        return

    PipelineRun.process_event = original_process_event
    delattr(PipelineRun, _PIPELINE_FILTER_ORIGINAL)
    if hasattr(PipelineRun, _PIPELINE_FILTER_PATCHED):
        delattr(PipelineRun, _PIPELINE_FILTER_PATCHED)
    LOGGER.debug("Restored PipelineRun.process_event after claw_assistant unload")


def _is_streaming_enabled(hass: HomeAssistant) -> bool:
    from ..const import CONF_ENABLE_STREAMING_EFFECT
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return True
    return entries[0].options.get(CONF_ENABLE_STREAMING_EFFECT, True)


async def _emit_frontend_progress(hass: HomeAssistant, chat_log, text: str) -> None:
    listener = getattr(chat_log, "delta_listener", None)
    if not listener or not text:
        return

    from .state import get_channel_type

    if get_channel_type(getattr(chat_log, "conversation_id", None)) != "ha":
        return

    listener(chat_log, {"role": "assistant"})
    if not _is_streaming_enabled(hass):
        listener(chat_log, {"content": text})
        listener(chat_log, {"content": "\n"})
        return

    await asyncio.sleep(0)
    for ch in text:
        listener(chat_log, {"content": ch})
        await asyncio.sleep(0.01)
    listener(chat_log, {"content": "\n"})


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
        thinking_text = ""
        raw_text = getattr(content, "content", None) or ""
        think_match = _THINK_RE.search(raw_text)
        if think_match:
            thinking_text = think_match.group(1).strip()
            content = _replace(content, content=_THINK_RE.sub("", raw_text).strip() or None)
        native_thinking = getattr(content, "thinking_content", None)
        if native_thinking and native_thinking.strip():
            thinking_text = native_thinking.strip()
        if thinking_text:
            truncated = thinking_text[:120]
            from .state import get_conversation_status
            lang = get_conversation_status(hass).get("user_language") or hass.config.language or "en"
            await _emit_frontend_progress(hass, self, f"┊ {truncated}")
            fire_live_progress(
                hass,
                conversation_id=getattr(self, "conversation_id", None),
                phase="thinking",
                text=truncated,
                display_text=tool_progress_line("GetLiveContext", {}, lang).strip(),
            )

        tool_calls = getattr(content, "tool_calls", None)
        if tool_calls:
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
                line = tool_progress_line(tc.tool_name, args, lang)
                await _emit_frontend_progress(hass, self, line.strip())
                fire_live_progress(
                    hass,
                    conversation_id=getattr(self, "conversation_id", None),
                    phase="tool_call",
                    text="",
                    tool_name=tc.tool_name,
                    display_text=line.strip(),
                )

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
    setattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED, True)
    LOGGER.debug("Patched ChatLog.async_add_assistant_content for tool progress + typewriter")


def unpatch_tool_progress() -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    original = getattr(
        chat_log_module.ChatLog, _TOOL_PROGRESS_ORIGINAL, None
    )
    if original is None:
        return

    chat_log_module.ChatLog.async_add_assistant_content = original
    delattr(chat_log_module.ChatLog, _TOOL_PROGRESS_ORIGINAL)
    if hasattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED):
        delattr(chat_log_module.ChatLog, _TOOL_PROGRESS_PATCHED)
    LOGGER.debug("Restored ChatLog.async_add_assistant_content after claw_assistant unload")


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
