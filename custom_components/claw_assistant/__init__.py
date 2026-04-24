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

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.BUTTON)
DATA_AGENT = "agent"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    prime_runtime_state(hass)
    async_setup_heartbeat_ticker(hass)
    from .runtime.custom_entity_store import async_load_custom_entities
    await async_load_custom_entities(hass)
    from .conversation_utils import async_setup_history_store
    await async_setup_history_store(hass)
    await async_setup_runtime(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.info("claw_assistant initialized with backend-only runtime")
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        async_unload_heartbeat_ticker(hass)
        from .conversation_utils import async_flush_history_store
        await async_flush_history_store(hass)
        await async_unload_runtime(hass)
    return True

async def async_migrate_entry(hass, config_entry: ConfigEntry):
    if config_entry.version == 1:
        return True

    return False
