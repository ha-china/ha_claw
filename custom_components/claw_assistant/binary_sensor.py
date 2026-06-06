from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import TrackTemplate, async_track_template_result
from homeassistant.helpers.template import Template, TemplateError

from .const import DOMAIN, VERSION
from .runtime.storage.custom_entity_store import get_custom_entities_by_platform

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
    _attr_should_poll = False
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
            manufacturer="Claw Assistant",
            model="Home Assistant AI",
            sw_version=VERSION,
        )

    @property
    def available(self) -> bool:
        return self._attr_is_on is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        tpl = self._definition.get("state_template", "")
        if not tpl:
            return
        info = async_track_template_result(
            self.hass,
            [TrackTemplate(Template(tpl, self.hass), None)],
            self._handle_template_result,
        )
        self.async_on_remove(info.async_remove)
        info.async_refresh()

    @callback
    def _handle_template_result(self, event, updates) -> None:
        if not updates:
            return
        result = updates.pop().result
        if (
            isinstance(result, TemplateError)
            or result is None
            or str(result).lower() in ("unknown", "unavailable", "none")
        ):
            self._attr_is_on = None
        elif isinstance(result, bool):
            self._attr_is_on = result
        else:
            self._attr_is_on = str(result).lower() in ("true", "on", "1", "yes")
        self.async_write_ha_state()
