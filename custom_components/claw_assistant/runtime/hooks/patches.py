

from __future__ import annotations

import asyncio
import logging
import re
import time

from typing import Any

from homeassistant.core import HomeAssistant, callback

from ...const import (
    CONF_CONVERSATION_MODE,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
    DOMAIN,
)
from homeassistant.components.conversation.chat_log import AssistantContent

from ..tools.tool_result_summary import build_synthesized_assistant_from_chat_log
from ..core.state import get_conversation_status, get_runtime_store

LOGGER = logging.getLogger(__name__)

_EARLY_PATCH_DONE = "_claw_early_intent_patch_done"
_LOCAL_INTENTS_PATCHED = "_ha_crack_intents_patched"
_CN_COMMAND_PATCH_KEY = "_claw_cn_command_original"
_CN_WECHAT_DETACH_PATCH_KEY = "_claw_cn_wechat_detach_original"
_CN_WECHAT_TASKS_KEY = "_claw_cn_wechat_message_tasks"
_CN_RECENT_KEY = "_claw_cn_recent_messages"
_CN_COMMAND_LINE_RE = re.compile(r"(^|\n)\s*/[a-zA-Z][\w\-]*\b", re.MULTILINE)


def patch_cn_im_hub_interrupt_context(hass: HomeAssistant) -> None:
    if not hass.data.get(_CN_COMMAND_PATCH_KEY):
        try:
            from custom_components.cn_im_hub.core import command as command_module
        except ImportError:
            command_module = None
        if command_module is not None:
            original_execute = command_module.execute_command

            async def _patched_execute_command(
                cmd_hass,
                command,
                *,
                conversation_id,
                agent_id,
                extra_system_prompt=None,
                user_id="",
            ):
                if getattr(command, "kind", None) != "conversation":
                    return await original_execute(
                        cmd_hass,
                        command,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        extra_system_prompt=extra_system_prompt,
                        user_id=user_id,
                    )
                text = str(getattr(command, "target", "") or "").strip()
                is_command_like = False
                if text:
                    try:
                        from ...chat_commands import parse_chat_command as _parse_chat_command

                        is_command_like = _parse_chat_command(text) is not None
                    except Exception:
                        is_command_like = bool(re.search(r"(?<![\\w/])/[a-zA-Z][\\w\\-]*", text))
                recent_map = cmd_hass.data.setdefault(_CN_RECENT_KEY, {})
                if text == "/new":
                    recent_map.pop(conversation_id, None)
                elif text and is_command_like:
                    # Command turns must not pollute stitched "recent" context.
                    recent_map.pop(conversation_id, None)
                elif text and not is_command_like:
                    recent = recent_map.setdefault(conversation_id, [])
                    recent.append(text)
                    # Keep command messages out of stitched "recent" context to
                    # prevent stale slash commands from being replayed later.
                    filtered_recent: list[str] = []
                    for item in recent:
                        item_text = str(item or "").strip()
                        if not item_text:
                            continue
                        try:
                            from ...chat_commands import parse_chat_command as _parse_chat_command

                            if _parse_chat_command(item_text) is not None:
                                continue
                        except Exception:
                            if re.search(r"(?<![\\w/])/[a-zA-Z][\\w\\-]*", item_text):
                                continue
                        if _CN_COMMAND_LINE_RE.search(item_text):
                            continue
                        filtered_recent.append(item_text)
                    recent[:] = filtered_recent
                    del recent[:-3]
                    if len(recent) > 1:
                        combined = "\n".join(
                            f"[Recent user message {idx + 1}] {item}"
                            for idx, item in enumerate(recent)
                        )
                        command = command_module.command_factory(
                            "conversation",
                            f"{combined}\n\n[Current request] {text}",
                        )
                return await original_execute(
                    cmd_hass,
                    command,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    extra_system_prompt=extra_system_prompt,
                    user_id=user_id,
                )

            command_module.execute_command = _patched_execute_command
            import sys
            for module_name in (
                "custom_components.cn_im_hub.providers.qq.client",
                "custom_components.cn_im_hub.providers.wechat.client",
                "custom_components.cn_im_hub.providers.feishu.client",
                "custom_components.cn_im_hub.providers.wecom.client",
                "custom_components.cn_im_hub.providers.dingtalk.client",
                "custom_components.cn_im_hub.providers.xiaoyi.client",
                "custom_components.cn_im_hub.providers.custom.client",
            ):
                provider_module = sys.modules.get(module_name)
                if provider_module is not None and hasattr(provider_module, "execute_command"):
                    provider_module.execute_command = _patched_execute_command
            hass.data[_CN_COMMAND_PATCH_KEY] = original_execute

    if hass.data.get(_CN_WECHAT_DETACH_PATCH_KEY):
        return
    try:
        from custom_components.cn_im_hub.providers.wechat.client import WeixinClient
    except ImportError:
        return

    original = WeixinClient._handle_message

    async def _detached_handle_message(self, message):
        from_user_id = str(message.get("from_user_id") or "").strip()
        task_key = f"{getattr(self, '_account_id', '')}:{from_user_id}"
        tasks = self._hass.data.setdefault(_CN_WECHAT_TASKS_KEY, {})
        existing = tasks.get(task_key)
        if existing is not None and not existing.done():
            existing.cancel("Interrupted by newer WeChat message")

        async def _run_message():
            try:
                await original(self, message)
            except asyncio.CancelledError:
                raise
            finally:
                if tasks.get(task_key) is asyncio.current_task():
                    tasks.pop(task_key, None)

        task = self._hass.async_create_task(
            _run_message(),
            f"claw_cn_wechat_message_{task_key}",
        )
        tasks[task_key] = task

    WeixinClient._handle_message = _detached_handle_message
    hass.data[_CN_WECHAT_DETACH_PATCH_KEY] = original
    LOGGER.info("Installed cn_im_hub WeChat detached receive hook")
_LOCAL_INTENTS_ORIGINAL = "_ha_crack_original_async_handle_intents"

_ALLOWED_INTENTS = {
    "HassBroadcast",
    "HassTurnOn",
    "HassTurnOff",
    "HassLightSet",
    "HassSetPosition",
    "HassOpenCover",
    "HassCloseCover",
    "GetDateTime",
    "GetSkillIndex",
    "HassGetDateTime",
}


def early_patch_intents() -> None:
    """Patch async_handle_intents at module load time, before ai_hub imports it."""
    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation.default_agent import DefaultAgent

    if hasattr(conv_module, _EARLY_PATCH_DONE):
        return

    original = conv_module.async_handle_intents

    async def _early_patched_async_handle_intents(
        hass,
        user_input,
        chat_log,
        *,
        intent_filter=None,
    ):
        text = getattr(user_input, "text", "") or ""
        if len(text) > 200:
            return None

        def combined_filter(result):
            intent_name = getattr(result.intent, "name", "") if result.intent else ""
            if intent_name not in _ALLOWED_INTENTS:
                return True
            if intent_filter is not None:
                return intent_filter(result)
            return False

        return await original(hass, user_input, chat_log, intent_filter=combined_filter)

    conv_module.async_handle_intents = _early_patched_async_handle_intents
    setattr(conv_module, _EARLY_PATCH_DONE, original)

    original_recognize = DefaultAgent.async_recognize_intent

    async def _patched_recognize_intent(self, user_input, strict_intents_only=False):
        text = getattr(user_input, "text", "") or ""
        if len(text) > 200:
            return None
        result = await original_recognize(self, user_input, strict_intents_only)
        if result is None:
            return None
        intent_name = getattr(result.intent, "name", "") if result.intent else ""
        if intent_name not in _ALLOWED_INTENTS:
            return None
        return result

    DefaultAgent.async_recognize_intent = _patched_recognize_intent

    LOGGER.info("Early intent patch installed (allows %d intents)", len(_ALLOWED_INTENTS))
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

    def _claw_intent_filter(result) -> bool:
        """Return True to block the intent."""
        intent_name = getattr(result.intent, "name", "") if result.intent else ""
        return intent_name not in _ALLOWED_INTENTS

    async def patched_async_handle_intents(
        hass_arg,
        user_input,
        chat_log,
        *,
        intent_filter=None,
    ):
        text = getattr(user_input, "text", "") or ""
        if len(text) > 200:
            return None

        def combined_filter(result):
            if _claw_intent_filter(result):
                return True
            if intent_filter is not None:
                return intent_filter(result)
            return False

        result = await original_async_handle_intents(
            hass_arg, user_input, chat_log, intent_filter=combined_filter
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
                    from ..output.reply_formatter import stamp_plain
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
    import os
    import sys

    if os.environ.get(_INTENTS_DOWNGRADE_DONE) == "1":
        return

    def _check_current_version() -> str | None:
        try:
            from importlib.metadata import version
            return version("home-assistant-intents")
        except Exception:
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
        except Exception as exc:
            return -1, "", str(exc)

    LOGGER.info("Downgrading home-assistant-intents %s -> 2026.3.3", current)
    code, _stdout, stderr = await hass.async_add_executor_job(_pip_install)
    if code != 0:
        LOGGER.warning("home-assistant-intents downgrade failed (rc=%s): %s", code, stderr.strip())
        return

    os.environ[_INTENTS_DOWNGRADE_DONE] = "1"
    LOGGER.info("home-assistant-intents downgraded to 2026.3.3")

    try:
        for entry in list(hass.data.values()):
            if not isinstance(entry, dict):
                continue
            for value in list(entry.values()):
                cache = getattr(value, "_lang_intents", None)
                if isinstance(cache, dict):
                    for key in [k for k in cache if isinstance(k, str) and k.lower().startswith("zh")]:
                        cache.pop(key, None)
    except Exception:
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

    from ..llm.internal_llm import patch_chatlog_tools as patch_internal_llm_chatlog_tools

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
        if get_conversation_status(hass).get("is_internal_llm"):
            return
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
            if not cleaned:
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
            and not delta
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
    from ...const import CONF_ENABLE_STREAMING_EFFECT
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return True
    return entries[0].options.get(CONF_ENABLE_STREAMING_EFFECT, True)


def _is_tool_progress_enabled(hass: HomeAssistant) -> bool:
    from ...const import CONF_ENABLE_TOOL_PROGRESS
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return True
    return entries[0].options.get(CONF_ENABLE_TOOL_PROGRESS, True)


def _is_tool_details_enabled(hass: HomeAssistant) -> bool:
    from ...const import CONF_ENABLE_TOOL_DETAILS
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return False
    return entries[0].options.get(CONF_ENABLE_TOOL_DETAILS, False)


def _get_current_token_stats(hass: HomeAssistant, chat_log) -> tuple[int, int]:
    """Get current token usage and context window size."""
    try:
        from ..llm.context_compressor import _estimate_total_tokens
        content = getattr(chat_log, "content", []) or []
        tokens_used = _estimate_total_tokens(content)
        return tokens_used, 262144
    except Exception:
        return 0, 262144


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
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CLAW_MEDIA_TAG_RE = re.compile(r"\[(?:IMAGE|GIF|VIDEO|FILE):[^\]]+\]", re.IGNORECASE)
_CLAW_TOOL_COMMENT_RE = re.compile(r"<!--\s*CLAW_[\s\S]*?-->", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|/local/\S+|/config/www/\S+")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_ANY_NEWLINE_RE = re.compile(r"[\r\n]+")
_SPOKEN_PUNCT_SPACING_RE = re.compile(r"\s*([，。！？；：,.!?;:])\s*")
_MULTI_PUNCT_RE = re.compile(r"([，。！？；：,.!?;:]){2,}")


def _clean_for_tts(text: str) -> str:
    if not text:
        return text
    t = _MD_CODE_BLOCK_RE.sub("", text)
    t = _CLAW_TOOL_COMMENT_RE.sub("", t)
    t = _CLAW_MEDIA_TAG_RE.sub("", t)
    t = _MD_IMAGE_RE.sub(r"\1", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _MD_BOLD_ITALIC_RE.sub(r"\1", t)
    t = _MD_STRIKE_RE.sub(r"\1", t)
    t = _MD_INLINE_CODE_RE.sub(r"\1", t)
    t = _HTML_TAG_RE.sub("", t)
    t = _URL_RE.sub("", t)
    t = _MD_HEADING_RE.sub("", t)
    t = _MD_BLOCKQUOTE_RE.sub("", t)
    t = _MD_HR_RE.sub("", t)
    t = _MD_TABLE_SEP_RE.sub("", t)
    t = _MD_TABLE_PIPE_RE.sub(" ", t)
    t = _MD_LIST_RE.sub("", t)
    t = _MD_ORDERED_LIST_RE.sub("", t)
    t = t.replace("*", "").replace("_", "").replace("#", "")
    t = t.replace("[", "").replace("]", "").replace("{", "").replace("}", "")
    t = _ANY_NEWLINE_RE.sub(" ", t)
    t = _SPOKEN_PUNCT_SPACING_RE.sub(r"\1", t)
    t = _MULTI_PUNCT_RE.sub(r"\1", t)
    t = _MULTI_SPACE_RE.sub(" ", t)
    return t.strip()


def _wrap_listener_for_tracking(chat_log, original_listener):
    """Wrap delta_listener to track the last emitted character, strip headings,
    and separate TTS content (cleaned) from frontend display content (original)."""
    if getattr(chat_log, "_claw_listener_wrapped", False):
        return original_listener

    thinking_marker_id = f"think_{id(chat_log)}"
    if not hasattr(chat_log, "_claw_thinking_buffer"):
        chat_log._claw_thinking_buffer = []
    
    def tracked_listener(log, delta):
        if not isinstance(delta, dict):
            return original_listener(log, delta)
        
        thinking_content = delta.pop("thinking_content", None)
        if thinking_content:
            log._claw_thinking_buffer.append(thinking_content)
        
        skip_tts = delta.pop("_claw_skip_tts", False)
        content = delta.get("content")
        
        if content:
            try:
                from homeassistant.core import async_get_hass
                from ..core.state import get_runtime_store
                hass = async_get_hass()
                conv_id = getattr(log, "conversation_id", None) or "default"
                runtime_store = get_runtime_store(hass)
                parts = runtime_store.setdefault("live_response_parts", {}).setdefault(conv_id, [])
                parts.append(content)
                if len(parts) > 50:
                    parts[:] = parts[-30:]
            except Exception:
                pass
        
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


async def _emit_frontend_progress(hass: HomeAssistant, chat_log, text: str, *, extra: dict | None = None) -> None:
    listener = getattr(chat_log, "delta_listener", None)
    if not listener or not text:
        return

    from ..core.state import get_channel_type

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

    _extra = extra or {}

    if not _is_streaming_enabled(hass):
        listener(chat_log, {"content": full, "_claw_skip_tts": True, **_extra})
        listener(chat_log, {"content": "\n", "_claw_skip_tts": True})
        return

    chunks = _PROGRESS_CHUNK_RE.findall(full)
    if not chunks:
        listener(chat_log, {"content": full, "_claw_skip_tts": True, **_extra})
        listener(chat_log, {"content": "\n", "_claw_skip_tts": True})
        return

    await asyncio.sleep(0)
    first_chunk = True
    for chunk in chunks:
        d = {"content": chunk, "_claw_skip_tts": True}
        if first_chunk:
            d.update(_extra)
            first_chunk = False
        listener(chat_log, d)
        await asyncio.sleep(0.02)
    listener(chat_log, {"content": "\n", "_claw_skip_tts": True})


def patch_tool_progress(hass: HomeAssistant) -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module
    from ..core.events import fire_live_progress
    from ..tools.tool_progress import tool_progress_line, thinking_progress_line

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
        _details_on = _is_tool_details_enabled(hass)
        if thinking_text and _progress_on:
            from ..core.state import get_channel_type as _gct2
            if _gct2(getattr(self, "conversation_id", None)) != "ha":
                truncated = thinking_text[:120].replace("#", "").replace(">", "").replace("<", "").replace("|", "")
                lines = [l.strip() for l in truncated.splitlines() if l.strip()]
                display = " ".join(lines)
                await _emit_frontend_progress(hass, self, f"\n┊ *💭 {display}*")
                from ..core.state import get_conversation_status
                lang = get_conversation_status(hass).get("user_language") or hass.config.language or "en"
                _tk_used, _ctx_win = _get_current_token_stats(hass, self)
                fire_live_progress(
                    hass,
                    conversation_id=getattr(self, "conversation_id", None),
                    phase="thinking",
                    text=truncated,
                    display_text=thinking_progress_line(truncated, lang),
                    tokens_used=_tk_used,
                    context_window=_ctx_win,
                )

        tool_calls = getattr(content, "tool_calls", None)
        if tool_calls and (_progress_on or _details_on):
            from ..core.state import get_conversation_status
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
                from ..core.state import get_channel_type as _gct, get_conversation_status as _gcs
                _is_ha_frontend = _gct(getattr(self, "conversation_id", None)) == "ha"
                _is_voice = _gcs(hass).get("is_voice_pipeline", False)
                _pending = getattr(self, "_claw_pending_tools", 0) + 1
                self._claw_pending_tools = _pending
                _tool_info_payload = {"_claw_tool_info": {
                    "tool_call_id": getattr(tc, "id", ""),
                    "marker_id": getattr(tc, "id", ""),
                    "tool_name": tc.tool_name,
                    "tool_args": dict(tc.tool_args),
                }, "_claw_pending_tools": _pending}
                listener = self.delta_listener
                if listener:
                    if _is_ha_frontend and not _is_voice:
                        if _details_on:
                            listener(self, {"content": f"\n\n<!--CLAW_TOOL:{getattr(tc, 'id', '')}-->\n\n", "_claw_skip_tts": True, **_tool_info_payload})
                        elif _progress_on:
                            line = tool_progress_line(tc.tool_name, args, lang, hass=hass)
                            listener(self, {"content": f"\n\n{line}\n\n", "_claw_skip_tts": True})
                    elif _progress_on:
                        line = tool_progress_line(tc.tool_name, args, lang, hass=hass)
                        listener(self, {"content": f"\n\n{line}\n\n", "_claw_skip_tts": True, **_tool_info_payload})
                line = tool_progress_line(tc.tool_name, args, lang, hass=hass) if _progress_on else ""
                if line:
                    _tk_used, _ctx_win = _get_current_token_stats(hass, self)
                    fire_live_progress(
                        hass,
                        conversation_id=getattr(self, "conversation_id", None),
                        phase="tool_call",
                        text="",
                        tool_name=tc.tool_name,
                        display_text=line.strip(),
                        tokens_used=_tk_used,
                        context_window=_ctx_win,
                    )

        streamed_thinking = getattr(self, "_claw_thinking_buffer", [])
        final_thinking = ""
        if streamed_thinking:
            full_thinking = "".join(streamed_thinking)
            cleaned = full_thinking.replace("#", "").replace(">", "").replace("<", "").replace("|", "")
            lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
            final_thinking = " ".join(lines)
            self._claw_thinking_buffer = []
        elif thinking_text:
            cleaned = thinking_text.replace("#", "").replace(">", "").replace("<", "").replace("|", "")
            lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
            final_thinking = " ".join(lines)
        if final_thinking and _progress_on:
            listener = getattr(self, "delta_listener", None)
            if listener:
                from ..core.state import get_channel_type as _gct2, get_conversation_status as _gcs2
                _is_ha2 = _gct2(getattr(self, "conversation_id", None)) == "ha"
                _is_voice2 = _gcs2(hass).get("is_voice_pipeline", False)
                if _details_on and _is_ha2 and not _is_voice2:
                    listener(self, {
                        "_claw_thinking": final_thinking,
                        "_claw_marker_id": f"think_{id(self)}",
                        "_claw_skip_tts": True,
                    })
                else:
                    truncated = final_thinking[:80]
                    listener(self, {
                        "content": f"\n┊ *💭 {truncated}{'...' if len(final_thinking) > 80 else ''}*\n",
                        "_claw_skip_tts": True,
                    })

        _had_progress = bool(thinking_text) or bool(streamed_thinking) or bool(
            tool_calls and any(not getattr(tc, "external", False) for tc in tool_calls)
        )
        if _had_progress:
            self._claw_progress_active = False
            self._claw_has_content = False

        _gen = original_async_add_assistant_content(
            self, content, tool_call_tasks=tool_call_tasks
        )
        import inspect
        if inspect.iscoroutine(_gen):
            _gen = await _gen
            if _gen is None:
                return
            if not hasattr(_gen, "__aiter__"):
                yield _gen
                return
        async for result in _gen:
            _rl = getattr(self, "delta_listener", None)
            if _rl and hasattr(result, "tool_call_id"):
                from ..core.state import get_channel_type as _gct2
                if _gct2(getattr(self, "conversation_id", None)) == "ha" and _details_on:
                    _pending = max(0, getattr(self, "_claw_pending_tools", 1) - 1)
                    self._claw_pending_tools = _pending
                    _rl(self, {
                        "_claw_tool_result": {
                            "tool_call_id": result.tool_call_id,
                            "tool_name": getattr(result, "tool_name", ""),
                            "tool_result": getattr(result, "tool_result", None),
                        },
                        "_claw_pending_tools": _pending,
                        "_claw_skip_tts": True,
                    })
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
        orig = getattr(self, "delta_listener", None)
        if orig and not getattr(self, "_claw_listener_wrapped", False):
            self.delta_listener = _wrap_listener_for_tracking(self, orig)

        async def _filtered_stream(src):
            async for delta in src:
                if isinstance(delta, dict):
                    if "content" in delta and delta["content"]:
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
        from ...const import CONF_PIPELINE_TIMEOUT, DEFAULT_PIPELINE_TIMEOUT, DOMAIN

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
                from ...tools.registry import build_tool_map
            except Exception:
                raise err

            tool_cls = build_tool_map().get(tool_input.tool_name)
            if tool_cls is not None:
                tool = tool_cls()
            else:
                from ..storage.plugin_store import get_plugin_tools
                plugin_tool = next(
                    (t for t in get_plugin_tools() if t.name == tool_input.tool_name),
                    None,
                )
                if plugin_tool is None:
                    raise
                tool = plugin_tool
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
    if not text:
        return None

    command_text = str(text)
    marker = "[Current request]"
    if marker in command_text:
        command_text = command_text.rsplit(marker, 1)[-1].strip()

    if "/" not in command_text:
        return None
    try:
        from homeassistant.components import conversation as conversation_module
        from ...chat_commands import async_handle_chat_command, parse_chat_command
    except Exception:
        return None
    if parse_chat_command(command_text) is None:
        return None
    try:
        user_input = conversation_module.ConversationInput(
            text=command_text,
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
                text=command_text,
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
        if conversation_id:
            try:
                from ..llm.context_compressor import sanitize_tool_pairs
                from ..agent.agent_fallback import _get_chat_log_content
                _cl = _get_chat_log_content(hass, conversation_id)
                if _cl:
                    _fixed = sanitize_tool_pairs(_cl)
                    if _fixed is not _cl:
                        _cl.clear()
                        _cl.extend(_fixed)
            except Exception:
                pass
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

    from ...const import (
        CONF_CONVERSATION_MODE,
        DEFAULT_CONVERSATION_MODE,
        DOMAIN,
    )
    from ..llm.response_format import apply_agent_response_format

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
_AIHUB_TIMEOUT_CURRENT = "_claw_aihub_timeout_current"
_AIHUB_TIMEOUT_ORIGINAL = 60.0


def _aihub_timeout_target(hass: HomeAssistant) -> float:
    from ...const import CONF_PIPELINE_TIMEOUT, DEFAULT_PIPELINE_TIMEOUT, DOMAIN

    minimum_timeout = 300.0
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return max(minimum_timeout, float(DEFAULT_PIPELINE_TIMEOUT))
    configured_timeout = entries[0].options.get(
        CONF_PIPELINE_TIMEOUT,
        DEFAULT_PIPELINE_TIMEOUT,
    )
    return max(minimum_timeout, float(configured_timeout))


def patch_aihub_provider_timeout(hass: HomeAssistant) -> None:
    try:
        from custom_components.ai_hub.providers.base import BaseProviderConfig
        target_timeout = _aihub_timeout_target(hass)
        if getattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED, False):
            BaseProviderConfig.__dataclass_fields__["timeout"].default = target_timeout
            _bump_existing_providers(hass)
            setattr(BaseProviderConfig, _AIHUB_TIMEOUT_CURRENT, target_timeout)
            return
        global _AIHUB_TIMEOUT_ORIGINAL
        _AIHUB_TIMEOUT_ORIGINAL = BaseProviderConfig.__dataclass_fields__["timeout"].default
        BaseProviderConfig.__dataclass_fields__["timeout"].default = target_timeout
        original_init = BaseProviderConfig.__init__

        def _patched_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            if self.timeout == _AIHUB_TIMEOUT_ORIGINAL:
                self.timeout = BaseProviderConfig.__dataclass_fields__["timeout"].default

        BaseProviderConfig.__init__ = _patched_init
        BaseProviderConfig._claw_original_init = original_init

        _bump_existing_providers(hass)
        setattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED, True)
        setattr(BaseProviderConfig, _AIHUB_TIMEOUT_CURRENT, target_timeout)
        LOGGER.debug("Patched ai_hub provider timeout: %s -> %ss", _AIHUB_TIMEOUT_ORIGINAL, target_timeout)
    except Exception as exc:
        LOGGER.debug("ai_hub provider timeout patch skipped: %s", exc)


def _bump_existing_providers(hass: HomeAssistant) -> None:
    try:
        from custom_components.ai_hub.providers.base import BaseProviderConfig
        target_timeout = _aihub_timeout_target(hass)
        previous_timeout = getattr(
            BaseProviderConfig,
            _AIHUB_TIMEOUT_CURRENT,
            BaseProviderConfig.__dataclass_fields__["timeout"].default,
        )
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
                if cfg and getattr(cfg, "timeout", None) in (
                    _AIHUB_TIMEOUT_ORIGINAL,
                    previous_timeout,
                ):
                    cfg.timeout = target_timeout
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
        if hasattr(BaseProviderConfig, _AIHUB_TIMEOUT_CURRENT):
            delattr(BaseProviderConfig, _AIHUB_TIMEOUT_CURRENT)
        delattr(BaseProviderConfig, _AIHUB_TIMEOUT_PATCHED)
        LOGGER.debug("Restored ai_hub provider timeout to %s", _AIHUB_TIMEOUT_ORIGINAL)
    except Exception as exc:
        LOGGER.debug("ai_hub provider timeout unpatch skipped: %s", exc)


_AIHUB_MD_FILTER_PATCHED = "_claw_aihub_md_filter_patched"


def _rich_markdown_enabled(hass: HomeAssistant) -> bool:
    from ...const import CONF_ENABLE_RICH_MARKDOWN, DOMAIN
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


_PIPELINE_WS_DETACH_PATCHED = "_claw_pipeline_ws_detach_patched"


def patch_pipeline_websocket_detach(hass: HomeAssistant) -> None:
    """Hook assist_pipeline/run so the pipeline task is NOT cancelled when
    the WebSocket connection drops (browser refresh). Instead, the task
    continues in the background, and its events are buffered for replay."""
    from homeassistant.components.assist_pipeline import websocket_api as ap_ws

    if getattr(ap_ws, _PIPELINE_WS_DETACH_PATCHED, False):
        return

    original_wrapped = ap_ws.websocket_run
    original_unwrapped = getattr(ap_ws.websocket_run, "__wrapped__", None)

    from homeassistant.components import websocket_api as ws_api

    @ws_api.async_response
    async def detached_websocket_run(hass_inner, connection, msg):
        from homeassistant.components.assist_pipeline.pipeline import (
            PipelineInput,
            PipelineRun,
            PipelineEvent,
            PipelineEventType,
            PipelineStage,
        )
        from homeassistant.components.assist_pipeline import async_get_pipeline
        from homeassistant.components.assist_pipeline.const import DEFAULT_PIPELINE_TIMEOUT
        from homeassistant.helpers import chat_session
        from .official_websocket_hook import _buffer_live_event, _domain_data

        async def _call_original():
            if original_unwrapped is not None:
                return await original_unwrapped(hass_inner, connection, msg)
            result = original_wrapped(hass_inner, connection, msg)
            if result is not None and hasattr(result, "__await__"):
                return await result
            return result

        pipeline_id = msg.get("pipeline")
        try:
            pipeline = async_get_pipeline(hass_inner, pipeline_id=pipeline_id)
        except Exception:
            return await _call_original()

        start_stage = PipelineStage(msg["start_stage"])
        if start_stage != PipelineStage.INTENT:
            return await _call_original()

        timeout = msg.get("timeout", DEFAULT_PIPELINE_TIMEOUT)
        conversation_id = msg.get("conversation_id") or ""

        dd = _domain_data(hass_inner)
        detached_runs = dd.setdefault("_claw_detached_pipeline_runs", {})
        
        runtime_store = get_runtime_store(hass_inner)
        _now = time.time()
        runtime_store.setdefault("turn_start_times", {})[conversation_id] = _now
        _window_starts = runtime_store.setdefault("window_start_times", {})
        if conversation_id not in _window_starts:
            _window_starts[conversation_id] = _now

        def resilient_event_callback(event):
            evt_dict = {"type": event.type, "data": event.data}
            _buffer_live_event(hass_inner, conversation_id, {
                "conversation_id": conversation_id,
                "event_type": event.type,
                "data": event.data or {},
            })
            try:
                connection.send_event(msg["id"], evt_dict)
            except Exception:
                pass

        run = PipelineRun(
            hass_inner,
            context=connection.context(msg),
            pipeline=pipeline,
            start_stage=start_stage,
            end_stage=PipelineStage(msg["end_stage"]),
            event_callback=resilient_event_callback,
            runner_data={"timeout": timeout},
        )

        with chat_session.async_get_chat_session(hass_inner, msg.get("conversation_id")) as session:
            pipeline_input = PipelineInput(
                intent_input=msg["input"]["text"],
                device_id=msg.get("device_id"),
                run=run,
                session=session,
            )
            try:
                await pipeline_input.validate()
            except Exception as err:
                connection.send_error(msg["id"], "pipeline-error", str(err))
                return

            connection.send_result(msg["id"])

            run_task = hass_inner.async_create_background_task(
                pipeline_input.execute(),
                name=f"claw_pipeline_{conversation_id}",
            )
            detached_runs[conversation_id] = run_task

            from ...chat_commands import _task_registry
            if conversation_id:
                _task_registry(hass_inner)[conversation_id] = run_task

            def _finalize_detached_run(_finished_task):
                detached_runs.pop(conversation_id, None)
                from ...chat_commands import _task_registry as _tr
                registry = _tr(hass_inner)
                if registry.get(conversation_id) is run_task:
                    registry.pop(conversation_id, None)
                _buffer_live_event(hass_inner, conversation_id, {
                    "conversation_id": conversation_id,
                    "event_type": "stream_end",
                    "data": {},
                })
                from .official_websocket_hook import _clear_live_event_buffer
                async def _delayed_clear():
                    await asyncio.sleep(30)
                    _clear_live_event_buffer(hass_inner, conversation_id)
                hass_inner.async_create_background_task(_delayed_clear(), name=f"claw_clear_buf_{conversation_id}")

            run_task.add_done_callback(_finalize_detached_run)

            def _on_unsub():
                LOGGER.info("Pipeline WS unsubscribed for %s — task continues detached", conversation_id)

            connection.subscriptions[msg["id"]] = _on_unsub

            try:
                async with asyncio.timeout(timeout):
                    await asyncio.shield(run_task)
            except asyncio.CancelledError:
                LOGGER.info("Pipeline WS cancelled for %s — task continues", conversation_id)
            except TimeoutError:
                resilient_event_callback(PipelineEvent(
                    PipelineEventType.ERROR,
                    {"code": "timeout", "message": "Timeout running pipeline"},
                ))
            except Exception:
                LOGGER.exception("Pipeline detached run error for %s", conversation_id)

    wrapped = detached_websocket_run
    for attr in ("_ws_command",):
        if hasattr(original_wrapped, attr):
            setattr(wrapped, attr, getattr(original_wrapped, attr))

    ap_ws.websocket_run = wrapped

    handlers = hass.data.get("websocket_api", {})
    if "assist_pipeline/run" in handlers:
        old_entry = handlers["assist_pipeline/run"]
        if isinstance(old_entry, tuple) and len(old_entry) == 2:
            handlers["assist_pipeline/run"] = (wrapped, old_entry[1])
        else:
            handlers["assist_pipeline/run"] = wrapped

    setattr(ap_ws, _PIPELINE_WS_DETACH_PATCHED, True)
    setattr(ap_ws, "_claw_original_ws_run", original_wrapped)
    LOGGER.info("Patched assist_pipeline/run for detached execution on WS disconnect")


def unpatch_pipeline_websocket_detach(hass: HomeAssistant) -> None:
    from homeassistant.components.assist_pipeline import websocket_api as ap_ws

    if not getattr(ap_ws, _PIPELINE_WS_DETACH_PATCHED, False):
        return
    original = getattr(ap_ws, "_claw_original_ws_run", None)
    if original:
        ap_ws.websocket_run = original
        handlers = hass.data.get("websocket_api", {})
        if "assist_pipeline/run" in handlers:
            old_entry = handlers["assist_pipeline/run"]
            if isinstance(old_entry, tuple) and len(old_entry) == 2:
                handlers["assist_pipeline/run"] = (original, old_entry[1])
            else:
                handlers["assist_pipeline/run"] = original
    if hasattr(ap_ws, _PIPELINE_WS_DETACH_PATCHED):
        delattr(ap_ws, _PIPELINE_WS_DETACH_PATCHED)
    LOGGER.info("Restored assist_pipeline/run to original")
