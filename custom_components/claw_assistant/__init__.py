from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .runtime.hooks.patches import early_patch_intents
early_patch_intents()
from .runtime import (
    async_setup_runtime,
    async_unload_runtime,
    prime_runtime_state,
)
from .runtime.storage.heartbeat_ticker import async_setup_heartbeat_ticker, async_unload_heartbeat_ticker
from .runtime.utils.im_approval_bridge import (
    async_setup_im_approval_bridge,
    async_unload_im_approval_bridge,
)
from .runtime.storage.user_activity import (
    async_setup_event_listener,
    async_unload_event_listener,
)

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.BUTTON)
DATA_AGENT = "agent"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    try:
        from homeassistant.loader import async_get_integration
        integration = await async_get_integration(hass, DOMAIN)
        integration.__dict__["quality_scale"] = "None"
        integration.manifest["quality_scale"] = "None"
        integration.manifest["is_built_in"] = True
        integration.manifest["overwrites_built_in"] = False
        integration.manifest["codeowners"] = ["@home-assistant/core"]
        integration.manifest.pop("version", None)
        integration.__dict__.pop("manifest_json_fragment", None)
    except Exception:
        pass
    prime_runtime_state(hass)
    await _async_ensure_bootstrap_on_first_install(hass)
    async_setup_heartbeat_ticker(hass)
    async_setup_im_approval_bridge(hass)
    async_setup_event_listener(hass)
    from .runtime.utils.update_handler import async_setup_update_handler
    async_setup_update_handler(hass)
    from .runtime.hooks.patches import patch_cn_im_hub_interrupt_context
    patch_cn_im_hub_interrupt_context(hass)
    from .runtime.storage.custom_entity_store import async_load_custom_entities
    await async_load_custom_entities(hass)
    from .conversation_utils import async_setup_history_store
    await async_setup_history_store(hass)
    await async_setup_runtime(hass, entry)
    from .runtime.storage.plugin_store import load_all_plugins
    from .runtime.llm.internal_llm import invalidate_runtime_tool_cache
    loaded_plugins = await hass.async_add_executor_job(load_all_plugins, hass)
    if loaded_plugins:
        enabled = [p.manifest.name for p in loaded_plugins if p.enabled]
        failed = [p.manifest.name for p in loaded_plugins if not p.enabled]
        LOGGER.info("Plugins loaded: %d enabled, %d failed. Enabled: %s", len(enabled), len(failed), enabled)
        invalidate_runtime_tool_cache()
    from .delegation import register_delegation_system
    register_delegation_system(hass)
    hass.data[DOMAIN]["entry"] = entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    LOGGER.info("claw_assistant initialized with backend-only runtime")
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from .runtime.hooks.patches import patch_pipeline_timeout, patch_aihub_markdown_filter
    from .runtime.history.continuous_conversation import continuous_conversation_enabled
    from .runtime.hooks.official_websocket_hook import context_status_bar_enabled, file_upload_enabled, sidebar_dock_enabled
    patch_pipeline_timeout(hass)
    patch_aihub_markdown_filter(hass)

    cc_enabled = continuous_conversation_enabled(hass)
    prev_cc = hass.data.get(f"{DOMAIN}_prev_continuous_conversation")
    if prev_cc is not None and prev_cc != cc_enabled:
        from .chat_commands import _clear_conversation_runtime
        from .runtime.core.state import get_active_conversation_state
        conv_id = get_active_conversation_state(hass).get("id")
        _clear_conversation_runtime(hass, conv_id)
    hass.data[f"{DOMAIN}_prev_continuous_conversation"] = cc_enabled

    hass.bus.async_fire(
        "ha_crack_settings_changed",
        {
            "continuous_conversation": cc_enabled,
            "enable_context_status_bar": context_status_bar_enabled(hass),
            "enable_file_upload": file_upload_enabled(hass),
            "enable_sidebar_dock": sidebar_dock_enabled(hass),
            "enable_sound_notifications": entry.options.get("enable_sound_notifications", True),
            "enable_tool_details": entry.options.get("enable_tool_details", False),
        },
    )

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        async_unload_heartbeat_ticker(hass)
        async_unload_im_approval_bridge(hass)
        async_unload_event_listener(hass)
        from .runtime.utils.update_handler import async_unload_update_handler
        async_unload_update_handler(hass)
        from .conversation_utils import async_flush_history_store
        await async_flush_history_store(hass)
        from .delegation import unregister_delegation_system
        unregister_delegation_system()
        await async_unload_runtime(hass)
    return True

async def _async_ensure_bootstrap_on_first_install(hass: HomeAssistant) -> None:
    import json
    from .runtime.utils.data_path import get_data_dir

    state_path = get_data_dir() / "workspace" / ".workspace_state.json"

    def _materialize() -> bool:
        if state_path.exists():
            return False
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"bootstrap_active": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True

    try:
        created = await hass.async_add_executor_job(_materialize)
    except OSError as err:
        LOGGER.warning("Failed to initialize bootstrap state on first install: %s", err)
        return
    if created:
        LOGGER.info("claw_assistant first install detected; bootstrap_active=true")


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    import json
    from .runtime.utils.data_path import get_data_dir

    state_path = get_data_dir() / "workspace" / ".workspace_state.json"

    def _reset_bootstrap() -> None:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"bootstrap_active": True}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as err:
            LOGGER.warning("Failed to reset bootstrap flag on remove: %s", err)

    await hass.async_add_executor_job(_reset_bootstrap)
    LOGGER.info("claw_assistant removed; bootstrap_active reset to true for next install")


async def async_migrate_entry(hass, config_entry: ConfigEntry):
    if config_entry.version == 1:
        return True

    return False
