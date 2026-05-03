

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
            pass
        return result

    agent_manager.async_converse = patched_async_converse
    setattr(agent_manager, _GLOBAL_FORMAT_ORIGINAL, original_async_converse)
    setattr(agent_manager, _GLOBAL_FORMAT_PATCHED, True)

    if getattr(conv_module, "async_converse", None) is original_async_converse:
        conv_module.async_converse = patched_async_converse


def _maybe_apply_global_response_format(hass, result, agent_id) -> None:

    if not result or not result.response or not result.response.speech:
        return
    plain = result.response.speech.get("plain")
    if not isinstance(plain, dict):
        return
    if plain.get("agent_name"):
        return

    from homeassistant.helpers import entity_registry as er

    from ..const import (
        CONF_CONVERSATION_MODE,
        DEFAULT_CONVERSATION_MODE,
        DOMAIN,
    )
    from .response_format import apply_agent_response_format

    agent_name = (agent_id or "Assistant").split(".")[-1]
    if agent_id:
        try:
            registry = er.async_get(hass)
            entity = registry.async_get(agent_id)
            if entity is not None:
                agent_name = entity.name or entity.original_name or agent_name
        except Exception:
            pass

    conversation_mode = DEFAULT_CONVERSATION_MODE
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


_AIHUB_TIMEOUT_TARGET = 180.0


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
            md_mod.filter_markdown_content = lambda content: content
            md_mod.filter_markdown_streaming = lambda content: content
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
