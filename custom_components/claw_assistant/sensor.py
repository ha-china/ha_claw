from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import TrackTemplate, async_track_template_result
from homeassistant.helpers.template import Template, TemplateError

from .const import DOMAIN, VERSION, CONF_ENTRY_TYPE, ENTRY_TYPE_DASHBOARD
from .runtime.storage.custom_entity_store import get_custom_entities_by_platform
from .runtime.storage.heartbeat_store import async_list_heartbeat_tasks, _next_due_seconds

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
    # ── Dashboard entry: config sensor only ──
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DASHBOARD:
        sensor = ClawConfigSensor(hass)
        async_add_entities([sensor])
        hass.data.setdefault(DOMAIN, {})["claw_config_entity_id"] = sensor.entity_id
        hass.data.setdefault(DOMAIN, {})['_dashboard_sensors'] = [sensor]
        return True

    # ── Core entry: heartbeat + custom sensors ──
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
            manufacturer="Claw Assistant",
            model="Home Assistant AI",
            sw_version=VERSION,
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
    _attr_should_poll = False
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
            manufacturer="Claw Assistant",
            model="Home Assistant AI",
            sw_version=VERSION,
        )

    @property
    def available(self) -> bool:
        return self._attr_native_value is not None

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
            self._attr_native_value = None
        else:
            self._attr_native_value = result
        self.async_write_ha_state()


class ClawConfigSensor(SensorEntity):
    """Expose Claw Assistant config options as sensor attributes for the dashboard."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:robot-happy"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_unique_id = "claw_config"
    _attr_translation_key = "claw_config"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._attr_name = "Claw Config"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "claw_dashboard")},
            name="Claw Dashboard",
            manufacturer="Claw Assistant",
            model="Management Panel",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> str:
        # Return the number of active conversation agents
        agents = [
            e for e in self.hass.states.async_entity_ids("conversation")
            if e != "conversation.home_assistant"
        ]
        return f"{len(agents)} agents"

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {}
        # Read the core claw_assistant config entry
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if not entry.data.get(CONF_ENTRY_TYPE):
                attrs.update(entry.options)
                break
        # Add skills/docs/plugins counts
        try:
            from .runtime.storage.skill_store import list_installed_skills, _INTERNAL_SKILL_SLUGS
            skills = list_installed_skills()
            attrs["skills"] = [s.get("name", s.get("slug", "?")) for s in skills
                               if s.get("slug") not in _INTERNAL_SKILL_SLUGS]
        except Exception:
            attrs["skills"] = []
        try:
            from .runtime.storage.workspace_store import get_workspace_doc
            doc_names = ["AGENTS", "BOOTSTRAP", "HEARTBEAT", "IDENTITY", "MEMORY", "SOUL", "TOOLS", "USER"]
            docs = []
            for name in doc_names:
                doc = get_workspace_doc(name)
                if doc.get("markdown", "").strip():
                    docs.append(name)
            attrs["docs"] = docs
        except Exception:
            attrs["docs"] = []
        try:
            from .runtime.storage.plugin_store import list_installed_plugins
            plugins = list_installed_plugins()
            attrs["plugins"] = [p.get("name", p.get("key", "?")) for p in plugins]
        except Exception:
            attrs["plugins"] = []
        try:
            from .runtime.storage.user_mapping import MappingStore
            mappings = MappingStore.load()
            attrs["user_mappings"] = mappings
        except Exception:
            attrs["user_mappings"] = []
        attrs["skills_count"] = len(attrs.get("skills", []))
        attrs["docs_count"] = len(attrs.get("docs", []))
        attrs["plugins_count"] = len(attrs.get("plugins", []))
        attrs["user_mappings_count"] = len(attrs.get("user_mappings", []))
        return attrs

    async def async_update(self) -> None:
        self.async_write_ha_state()
