from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from homeassistant.components import conversation
from homeassistant.util import ulid
from home_assistant_intents import get_languages
from homeassistant.components.conversation.default_agent import (
    DATA_DEFAULT_ENTITY,
    DefaultAgent,
)

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION,)
DATA_AGENT = "agent"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    return True

async def async_migrate_entry(hass, config_entry: ConfigEntry):
    if config_entry.version == 1:
        return False

    return True


class FallbackConversationAgent:


    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:

        self.hass = hass
        self.entry = entry
        self.name = "聚合AI"

    @property
    def supported_languages(self) -> list[str]:

        return get_languages()

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:

        agent_manager = conversation.get_agent_manager(self.hass)

        default_agent = self.hass.data[DATA_DEFAULT_ENTITY]
        

        for agent in agent_manager.async_get_agent_preferences():
            if agent.id == self.entry.entry_id:
                continue
            try:
                result = await agent.async_process(user_input)
                if result.response.response_type != conversation.ConversationResponseType.ERROR:
                    return result
            except Exception:
                LOGGER.exception("%s", agent.id)

        return await default_agent.async_process(user_input)

    async def async_tear_down(self) -> None:
        pass