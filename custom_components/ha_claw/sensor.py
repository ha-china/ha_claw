from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime.custom_entity_store import get_custom_entities_by_platform
from .runtime.heartbeat_store import async_list_heartbeat_tasks, _next_due_seconds

SCAN_INTERVAL = timedelta(seconds=30)
_SENSOR_KEY = "_heartbeat_sensor"
_ADD_KEY = "_heartbeat_add_entities"
_ENTRY_KEY = "_heartbeat_entry"
_CUSTOM_ADD_KEY = "_custom_sensor_add"
_CUSTOM_ENTITIES_KEY = "_custom_sensor_entities"

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    data[_ADD_KEY] = async_add_entities
    data[_ENTRY_KEY] = entry
    data[_SENSOR_KEY] = None

    tasks = await async_list_heartbeat_tasks(hass)
    if tasks:
        sensor = HeartbeatSensor(hass, entry)
        async_add_entities([sensor])
        data[_SENSOR_KEY] = sensor

    data[_CUSTOM_ADD_KEY] = async_add_entities
    entities_map: dict[str, DynamicSensor] = {}
    data[_CUSTOM_ENTITIES_KEY] = entities_map
    defs = get_custom_entities_by_platform(hass, "sensor")
    if defs:
        new_ents = []
        for d in defs:
            ent = DynamicSensor(hass, entry, d)
            entities_map[d["uid"]] = ent
            new_ents.append(ent)
        async_add_entities(new_ents)
    return True


async def async_sync_heartbeat_sensor(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    add_fn = data.get(_ADD_KEY)
    entry = data.get(_ENTRY_KEY)
    if not add_fn or not entry:
        return

    tasks = await async_list_heartbeat_tasks(hass)
    sensor = data.get(_SENSOR_KEY)

    if tasks and sensor is None:
        sensor = HeartbeatSensor(hass, entry)
        add_fn([sensor])
        data[_SENSOR_KEY] = sensor
    elif not tasks and sensor is not None:
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, sensor.unique_id
        )
        if entity_id:
            registry.async_remove(entity_id)
        data[_SENSOR_KEY] = None


class HeartbeatSensor(TextEntity):

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart-pulse"
    _attr_should_poll = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_translation_key = "heartbeat"
        self._attr_unique_id = f"{entry.entry_id}_heartbeat"
        self._active_count = 0
        self._total_count = 0
        self._next_due_value: int | None = None
        self._next_due_unit: str = "min"
        self._tasks: list[dict] = []
        self._last_updated = datetime.now(UTC)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title or DOMAIN,
            manufacturer="claw_assistant",
            model="AI Assistant",
        )

    @property
    def native_value(self) -> str:
        if self._total_count == 0:
            return "0 tasks"
        if self._next_due_value is None:
            return "Unknown"
        return f"{self._next_due_value} {self._next_due_unit}"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "total_tasks": self._total_count,
            "active_tasks": self._active_count,
            "tasks": self._tasks,
            "last_updated": self._last_updated.strftime("%B %d, %Y at %I:%M %p"),
        }

    async def async_added_to_hass(self) -> None:
        await self.async_update()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        self._last_updated = datetime.now(UTC)
        tasks = await async_list_heartbeat_tasks(self.hass)
        self._total_count = len(tasks)
        self._active_count = sum(1 for t in tasks if t.get("enabled", False))
        self._tasks = tasks
        now = datetime.now(UTC)
        soonest = None
        for t in tasks:
            if not t.get("enabled"):
                continue
            remaining = _next_due_seconds(
                t.get("schedule", "") or t.get("when", ""),
                t.get("last_checked_at", ""),
                now,
            )
            if remaining is None:
                continue
            if soonest is None or remaining < soonest:
                soonest = remaining
        if soonest is None:
            self._next_due_value = None
            self._next_due_unit = "s"
        elif soonest >= 86400:
            self._next_due_value = soonest // 86400
            self._next_due_unit = "d"
        elif soonest >= 3600:
            self._next_due_value = soonest // 3600
            self._next_due_unit = "h"
        elif soonest >= 60:
            self._next_due_value = soonest // 60
            self._next_due_unit = "min"
        else:
            self._next_due_value = max(0, soonest)
            self._next_due_unit = "s"


class DynamicSensor(SensorEntity):

    _attr_has_entity_name = True
    _attr_should_poll = True
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
        if definition.get("state_class"):
            self._attr_state_class = definition["state_class"]
        if definition.get("unit_of_measurement"):
            self._attr_native_unit_of_measurement = definition["unit_of_measurement"]

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
        return self._attr_native_value is not None

    async def async_update(self) -> None:
        tpl = self._definition.get("state_template", "")
        if not tpl:
            return
        try:
            from homeassistant.helpers.template import Template
            result = Template(tpl, self.hass).async_render()
            if result is None or str(result).lower() in ("unknown", "unavailable", "none"):
                self._attr_native_value = None
            else:
                self._attr_native_value = result
        except Exception:
            self._attr_native_value = None
