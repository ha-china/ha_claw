from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime.custom_entity_store import get_custom_entities_by_platform

_PLATFORM = "switch"
_ADD_KEY = "_custom_switch_add"
_ENTITIES_KEY = "_custom_switch_entities"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data[_ADD_KEY] = async_add_entities
    entities_map: dict[str, DynamicSwitch] = {}
    data[_ENTITIES_KEY] = entities_map

    defs = get_custom_entities_by_platform(hass, _PLATFORM)
    if defs:
        new_entities = []
        for d in defs:
            ent = DynamicSwitch(hass, entry, d)
            entities_map[d["uid"]] = ent
            new_entities.append(ent)
        async_add_entities(new_entities)
    return True


class DynamicSwitch(SwitchEntity):

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, definition: dict) -> None:
        self.hass = hass
        self._entry = entry
        self._definition = definition
        self._attr_unique_id = f"{entry.entry_id}_{definition['uid']}"
        self._attr_name = definition.get("name", definition["uid"])
        self._attr_is_on = False
        if definition.get("icon"):
            self._attr_icon = definition["icon"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title or DOMAIN,
            manufacturer="claw_assistant",
            model="AI Assistant",
        )

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()
