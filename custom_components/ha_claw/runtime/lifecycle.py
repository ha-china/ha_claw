

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .adaptive_memory import async_setup_adaptive_memory
from .coordinator import setup_ai_coordinator
from .data_path import init_storage
from .ha_guide_store import async_setup_homeassistant_guide_store
from .hook import uninstall_conversation_hook
from .internal_llm import async_setup_internal_llm, async_unload_internal_llm
from .official_websocket_hook import (
    install_official_websocket_process_hook,
    uninstall_official_websocket_process_hook,
)
from .patches import (
    patch_chat_log_result_extraction,
    patch_hide_tool_calls_from_pipeline,
    patch_local_intents,
    patch_tool_progress,
    unpatch_chat_log_result_extraction,
    unpatch_hide_tool_calls_from_pipeline,
    unpatch_local_intents,
    unpatch_tool_progress,
)
from .skill_store import async_setup_prompt_store
from .workspace_store import async_setup_workspace_store

LOGGER = logging.getLogger(__name__)


async def async_setup_runtime(hass: HomeAssistant, entry: ConfigEntry) -> None:

    from .hook import install_conversation_hook

    await hass.async_add_executor_job(init_storage, hass)
    await async_setup_workspace_store(hass)
    await async_setup_adaptive_memory(hass)
    await async_setup_homeassistant_guide_store(hass)
    await async_setup_prompt_store(hass)
    await async_setup_internal_llm(hass)
    patch_local_intents(hass)
    patch_chat_log_result_extraction(hass)
    patch_hide_tool_calls_from_pipeline(hass)
    patch_tool_progress(hass)
    install_official_websocket_process_hook(hass)
    setup_ai_coordinator(hass, entry)
    install_conversation_hook(hass, entry)


async def async_unload_runtime(hass: HomeAssistant) -> None:

    from ..index_manager import async_cleanup_index_manager

    await async_cleanup_index_manager(hass)
    uninstall_conversation_hook(hass)
    uninstall_official_websocket_process_hook(hass)
    unpatch_tool_progress()
    unpatch_hide_tool_calls_from_pipeline()
    unpatch_chat_log_result_extraction()
    unpatch_local_intents()
    async_unload_internal_llm(hass)
