from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime.custom_entity_store import get_custom_entities_by_platform

SCAN_INTERVAL = timedelta(seconds=30)

_PLATFORM = "binary_sensor"
_ADD_KEY = "_custom_binary_sensor_add"
_ENTITIES_KEY = "_custom_binary_sensor_entities"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data[_ADD_KEY] = async_add_entities
    entities_map: dict[str, DynamicBinarySensor] = {}
    data[_ENTITIES_KEY] = entities_map

    defs = get_custom_entities_by_platform(hass, _PLATFORM)
    if defs:
        new_entities = []
        for d in defs:
            ent = DynamicBinarySensor(hass, entry, d)
            entities_map[d["uid"]] = ent
            new_entities.append(ent)
        async_add_entities(new_entities)
    return True


class DynamicBinarySensor(BinarySensorEntity):

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, definition: dict) -> None:
        self.hass = hass
        self._entry = entry
        self._definition = definition
        self._attr_unique_id = f"{entry.entry_id}_{definition['uid']}"
        self._attr_name = definition.get("name", definition["uid"])
        self._attr_is_on = None
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

    @property
    def available(self) -> bool:
        return self._attr_is_on is not None

    async def async_update(self) -> None:
        tpl = self._definition.get("state_template", "")
        if not tpl:
            return
        try:
            from homeassistant.helpers.template import Template
            result = Template(tpl, self.hass).async_render()
            if result is None or str(result).lower() in ("unknown", "unavailable", "none"):
                self._attr_is_on = None
            elif isinstance(result, bool):
                self._attr_is_on = result
            else:
                self._attr_is_on = str(result).lower() in ("true", "on", "1", "yes")
        except Exception:
            self._attr_is_on = None
