

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .orchestrator import execute_conversation_turn
from .state import (
    get_active_conversation_state,
    get_conversation_status,
    get_runtime_store,
    get_task_loop_state,
)

LOGGER = logging.getLogger(__name__)

_PIPELINE_PATCHED = False
_PIPELINE_ORIGINAL = "_kadermanager_final_content_original_process_event"


def _update_injection_tracking(run: Any, delta: dict[str, Any]) -> None:

    if (role := delta.get("role")) is not None:
        setattr(run, "_km_current_role", role)
        if role == "tool_result":
            setattr(run, "_km_seen_tool_phase", True)
            setattr(run, "_km_assistant_content_after_tool", False)

    if (
        getattr(run, "_km_current_role", None) == "assistant"
        and delta.get("content")
    ):
        setattr(run, "_km_any_assistant_content_seen", True)
        if getattr(run, "_km_seen_tool_phase", False):
            setattr(run, "_km_assistant_content_after_tool", True)


def _should_inject_final_assistant(run: Any) -> bool:

    if getattr(run, "_km_seen_tool_phase", False):
        return not getattr(run, "_km_assistant_content_after_tool", False)
    return not getattr(run, "_km_any_assistant_content_seen", False)


def patch_pipeline_for_final_content(hass: HomeAssistant) -> None:

    global _PIPELINE_PATCHED
    if _PIPELINE_PATCHED:
        return

    try:
        from homeassistant.components.assist_pipeline.pipeline import (
            PipelineEvent,
            PipelineEventType,
            PipelineRun,
        )

        original_process_event = PipelineRun.process_event

        def patched_process_event(self, event):
            if event.type == PipelineEventType.INTENT_PROGRESS:
                delta = (event.data or {}).get("chat_log_delta") or {}
                _update_injection_tracking(self, delta)

            if event.type == PipelineEventType.INTENT_END:
                if _should_inject_final_assistant(self):
                    intent_output = (event.data or {}).get("intent_output") or {}
                    response = intent_output.get("response") or {}
                    speech = response.get("speech") or {}
                    plain = speech.get("plain") or {}
                    final_text = plain.get("speech") or ""

                    if final_text:
                        from .patches import _is_streaming_enabled
                        original_process_event(
                            self,
                            PipelineEvent(
                                PipelineEventType.INTENT_PROGRESS,
                                {"chat_log_delta": {"role": "assistant"}},
                            ),
                        )
                        if _is_streaming_enabled(hass):
                            for ch in final_text:
                                original_process_event(
                                    self,
                                    PipelineEvent(
                                        PipelineEventType.INTENT_PROGRESS,
                                        {"chat_log_delta": {"content": ch}},
                                    ),
                                )
                        else:
                            original_process_event(
                                self,
                                PipelineEvent(
                                    PipelineEventType.INTENT_PROGRESS,
                                    {"chat_log_delta": {"content": final_text}},
                                ),
                            )

            return original_process_event(self, event)

        setattr(PipelineRun, _PIPELINE_ORIGINAL, original_process_event)
        PipelineRun.process_event = patched_process_event
        _PIPELINE_PATCHED = True
        LOGGER.debug("Pipeline patched (conditional final-content injection)")
    except Exception as err:
        LOGGER.warning("Failed to patch pipeline: %s", err)


def unpatch_pipeline_for_final_content() -> None:

    global _PIPELINE_PATCHED

    if not _PIPELINE_PATCHED:
        return

    from homeassistant.components.assist_pipeline.pipeline import PipelineRun

    original_process_event = getattr(PipelineRun, _PIPELINE_ORIGINAL, None)
    if original_process_event is None:
        _PIPELINE_PATCHED = False
        return

    PipelineRun.process_event = original_process_event
    delattr(PipelineRun, _PIPELINE_ORIGINAL)
    _PIPELINE_PATCHED = False
    LOGGER.debug("Pipeline final-content injection restored after kadermanager unload")


def install_conversation_hook(hass: HomeAssistant, entry: ConfigEntry) -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import agent_manager
    from homeassistant.components.conversation import http as conv_http

    if get_conversation_status(hass).get("hook_installed"):
        return

    patch_pipeline_for_final_content(hass)
    get_task_loop_state(hass)

    get_active_conversation_state(hass)
    runtime_store = get_runtime_store(hass)
    runtime_store["original_async_converse"] = agent_manager.async_converse
    runtime_store["config_entry"] = entry
    original_async_converse = agent_manager.async_converse

    async def hooked_async_converse(
        hass: HomeAssistant,
        text: str,
        conversation_id,
        context,
        language=None,
        agent_id=None,
        device_id=None,
        satellite_id=None,
        extra_system_prompt=None,
    ):
        frontend_lang = language
        try:
            user_id = getattr(context, "user_id", None)
            if user_id:
                from homeassistant.components.frontend.storage import async_user_store
                user_store = await async_user_store(hass, user_id)
                lang_data = user_store.data.get("language")
                if isinstance(lang_data, dict) and lang_data.get("language"):
                    frontend_lang = lang_data["language"]
        except Exception:
            pass
        get_conversation_status(hass)["user_language"] = frontend_lang
        if agent_id is not None and agent_id != entry.entry_id:
            return await original_async_converse(
                hass, text, conversation_id, context, language,
                agent_id, device_id, satellite_id, extra_system_prompt,
            )
        return await execute_conversation_turn(
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

    agent_manager.async_converse = hooked_async_converse
    conv_http.async_converse = hooked_async_converse
    conv_module.async_converse = hooked_async_converse
    get_conversation_status(hass)["hook_installed"] = True
    LOGGER.debug("Conversation hook installed to agent_manager, http, and conversation module")


def uninstall_conversation_hook(hass: HomeAssistant) -> None:

    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import agent_manager
    from homeassistant.components.conversation import http as conv_http

    runtime_store = get_runtime_store(hass)
    original_async_converse = runtime_store.pop("original_async_converse", None)
    if callable(original_async_converse):
        agent_manager.async_converse = original_async_converse
        conv_http.async_converse = original_async_converse
        conv_module.async_converse = original_async_converse

    unpatch_pipeline_for_final_content()
    get_conversation_status(hass).pop("hook_installed", None)
    LOGGER.debug("Conversation hook restored to agent_manager, http, and conversation module")
