from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .runtime import (
    async_setup_runtime,
    async_unload_runtime,
    prime_runtime_state,
)
from .runtime.heartbeat_ticker import async_setup_heartbeat_ticker, async_unload_heartbeat_ticker
from .runtime.im_approval_bridge import (
    async_setup_im_approval_bridge,
    async_unload_im_approval_bridge,
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
    from .runtime.custom_entity_store import async_load_custom_entities
    await async_load_custom_entities(hass)
    from .conversation_utils import async_setup_history_store
    await async_setup_history_store(hass)
    await async_setup_runtime(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    LOGGER.info("claw_assistant initialized with backend-only runtime")
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from .runtime.patches import patch_pipeline_timeout, patch_aihub_markdown_filter
    from .runtime.continuous_conversation import continuous_conversation_enabled
    from .runtime.official_websocket_hook import context_status_bar_enabled, file_upload_enabled, sidebar_dock_enabled
    patch_pipeline_timeout(hass)
    patch_aihub_markdown_filter(hass)
    hass.bus.async_fire(
        "ha_crack_settings_changed",
        {
            "continuous_conversation": continuous_conversation_enabled(hass),
            "enable_context_status_bar": context_status_bar_enabled(hass),
            "enable_file_upload": file_upload_enabled(hass),
            "enable_sidebar_dock": sidebar_dock_enabled(hass),
        },
    )

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        async_unload_heartbeat_ticker(hass)
        async_unload_im_approval_bridge(hass)
        from .conversation_utils import async_flush_history_store
        await async_flush_history_store(hass)
        await async_unload_runtime(hass)
    return True

async def _async_ensure_bootstrap_on_first_install(hass: HomeAssistant) -> None:
    """On first-ever install, materialize state.json with bootstrap_active=true.

    Detects "first install" by the absence of the workspace state file. Without
    this, the flag only exists implicitly (default-when-missing) and downstream
    consumers that read the file directly see stale data.
    """
    import json
    from .runtime.data_path import get_data_dir

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
    """Reset bootstrap flag so the next install re-runs first-run setup."""
    import json
    from .runtime.data_path import get_data_dir

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
