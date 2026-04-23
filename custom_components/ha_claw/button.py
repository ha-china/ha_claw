from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime.custom_entity_store import get_custom_entities_by_platform

_PLATFORM = "button"
_ADD_KEY = "_custom_button_add"
_ENTITIES_KEY = "_custom_button_entities"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data[_ADD_KEY] = async_add_entities
    entities_map: dict[str, DynamicButton] = {}
    data[_ENTITIES_KEY] = entities_map

    defs = get_custom_entities_by_platform(hass, _PLATFORM)
    if defs:
        new_entities = []
        for d in defs:
            ent = DynamicButton(hass, entry, d)
            entities_map[d["uid"]] = ent
            new_entities.append(ent)
        async_add_entities(new_entities)
    return True


class DynamicButton(ButtonEntity):

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, definition: dict) -> None:
        self.hass = hass
        self._entry = entry
        self._definition = definition
        self._attr_unique_id = f"{entry.entry_id}_{definition['uid']}"
        self._attr_name = definition.get("name", definition["uid"])
        if definition.get("icon"):
            self._attr_icon = definition["icon"]
        if definition.get("device_class"):
            self._attr_device_class = definition["device_class"]

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title or DOMAIN,
            manufacturer="claw_assistant",
            model="AI Assistant",
        )

    async def async_press(self) -> None:
        tpl = self._definition.get("press_action", "") or self._definition.get("options", {}).get("press_action", "")
        if not tpl:
            return
        try:
            from homeassistant.helpers.template import Template
            Template(tpl, self.hass).async_render()
        except Exception:
            pass
