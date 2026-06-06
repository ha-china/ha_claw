

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ..storage.adaptive_memory import async_setup_adaptive_memory
from ..utils.coordinator import setup_ai_coordinator
from ..utils.data_path import init_storage, output_dir_path
from ..utils.frontend_loader import (
    async_setup_frontend_loader,
    async_unload_frontend_loader,
)
from ..storage.graph_service import async_setup_graph_store, async_unload_graph_store
from ..storage.ha_guide_store import async_setup_homeassistant_guide_store
from ..hooks.hook import uninstall_conversation_hook
from ..llm.internal_llm import async_setup_internal_llm, async_unload_internal_llm
from ..hooks.official_websocket_hook import (
    install_official_websocket_process_hook,
    uninstall_official_websocket_process_hook,
)
from ..utils.output_cleanup import async_setup_output_cleanup, async_unload_output_cleanup
from ..hooks.patches import (
    async_downgrade_intents_package,
    patch_apiinstance_tool_fallback,
    patch_chat_log_result_extraction,
    patch_global_response_format,
    patch_hide_tool_calls_from_pipeline,
    patch_local_intents,
    patch_pipeline_timeout,
    patch_strip_thinking_content_serialization,
    patch_tool_progress,
    patch_websocket_binary_handler_noise,
    unpatch_apiinstance_tool_fallback,
    unpatch_chat_log_result_extraction,
    unpatch_global_response_format,
    unpatch_hide_tool_calls_from_pipeline,
    unpatch_local_intents,
    unpatch_pipeline_timeout,
    unpatch_strip_thinking_content_serialization,
    unpatch_tool_progress,
    unpatch_websocket_binary_handler_noise,
    patch_aihub_provider_timeout,
    unpatch_aihub_provider_timeout,
    patch_aihub_markdown_filter,
    unpatch_aihub_markdown_filter,
    patch_aihub_dynamic_max_tokens,
    unpatch_aihub_dynamic_max_tokens,
    async_patch_openai_allow_empty_key,
    unpatch_openai_allow_empty_key,
    patch_aihub_image_url_retry,
    unpatch_aihub_image_url_retry,
    patch_pipeline_websocket_detach,
    unpatch_pipeline_websocket_detach,
)
from ..storage.skill_store import async_setup_prompt_store
from ..utils.tmp_cleanup import async_setup_tmp_cleanup, async_unload_tmp_cleanup
from ..storage.curator import async_setup_curator, async_unload_curator
from ..storage.workspace_store import async_setup_workspace_store

LOGGER = logging.getLogger(__name__)


async def async_setup_runtime(hass: HomeAssistant, entry: ConfigEntry) -> None:

    from ..hooks.hook import install_conversation_hook

    await hass.async_add_executor_job(init_storage, hass)
    hass.config.allowlist_external_dirs.add(str(output_dir_path(hass)))
    await async_setup_workspace_store(hass)
    await async_setup_graph_store(hass)
    await async_setup_adaptive_memory(hass)
    await async_setup_homeassistant_guide_store(hass)
    await async_setup_prompt_store(hass)
    await async_setup_internal_llm(hass)
    await async_setup_output_cleanup(hass)
    await async_setup_tmp_cleanup(hass)
    await async_setup_curator(hass)
    await async_setup_frontend_loader(hass)
    await async_downgrade_intents_package(hass)
    patch_local_intents(hass)
    patch_websocket_binary_handler_noise(hass)
    patch_chat_log_result_extraction(hass)
    patch_hide_tool_calls_from_pipeline(hass)
    patch_strip_thinking_content_serialization(hass)
    patch_tool_progress(hass)
    patch_apiinstance_tool_fallback(hass)
    patch_global_response_format(hass)
    patch_pipeline_timeout(hass)
    patch_aihub_provider_timeout(hass)
    patch_aihub_markdown_filter(hass)
    patch_aihub_dynamic_max_tokens(hass)
    patch_aihub_image_url_retry(hass)
    patch_pipeline_websocket_detach(hass)
    await async_patch_openai_allow_empty_key(hass)
    install_official_websocket_process_hook(hass)
    setup_ai_coordinator(hass, entry)
    install_conversation_hook(hass, entry)
    _patch_ai_hub_intent_bypass()


def _patch_ai_hub_intent_bypass() -> None:
    try:
        from custom_components.ai_hub.conversation import AIHubConversationAgent
        original = getattr(AIHubConversationAgent, "_async_handle_local_and_builtin_intents", None)
        if original is None or getattr(original, "_patched_for_consult", False):
            return

        async def _patched(self, user_input, chat_log):
            if "[PEER-CONSULT]" in getattr(user_input, "text", ""):
                LOGGER.debug("ai_hub intent bypass active for peer consult")
                return None
            return await original(self, user_input, chat_log)

        _patched._patched_for_consult = True
        AIHubConversationAgent._async_handle_local_and_builtin_intents = _patched
        LOGGER.debug("ai_hub intent bypass hook installed")
    except Exception as exc:
        LOGGER.debug("ai_hub intent bypass hook skipped: %s", exc)


async def async_unload_runtime(hass: HomeAssistant) -> None:

    from ...index_manager import async_cleanup_index_manager

    await async_cleanup_index_manager(hass)
    await async_unload_graph_store(hass)
    await async_unload_output_cleanup(hass)
    await async_unload_tmp_cleanup(hass)
    await async_unload_curator(hass)
    async_unload_frontend_loader(hass)
    uninstall_conversation_hook(hass)
    uninstall_official_websocket_process_hook(hass)
    unpatch_tool_progress()
    unpatch_global_response_format()
    unpatch_apiinstance_tool_fallback()
    unpatch_strip_thinking_content_serialization()
    unpatch_hide_tool_calls_from_pipeline()
    unpatch_chat_log_result_extraction()
    unpatch_local_intents()
    unpatch_websocket_binary_handler_noise()
    unpatch_pipeline_timeout()
    unpatch_aihub_provider_timeout()
    unpatch_aihub_dynamic_max_tokens()
    unpatch_aihub_image_url_retry()
    unpatch_pipeline_websocket_detach(hass)
    unpatch_openai_allow_empty_key()
    unpatch_aihub_markdown_filter()
    async_unload_internal_llm(hass)
