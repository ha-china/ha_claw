
from __future__ import annotations
import logging
import asyncio
import time
from typing import Dict, List, Any, Optional
from collections import defaultdict

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er, device_registry as dr
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 60.0
MAX_INDEX_AGE = 300.0
_SKIP_DOMAINS = frozenset(("conversation", "chat", "tts", "stt", "ai_task", "event", "sensor"))


class IndexManager:


    def __init__(self, hass: HomeAssistant, assistant: str = None):
        self.hass = hass
        self.assistant = assistant
        self._index: Optional[Dict[str, Any]] = None
        self._index_timestamp: float = 0
        self._refresh_task: Optional[asyncio.Task] = None
        self._debounce_handle: Optional[asyncio.TimerHandle] = None
        self._unsub_state_changed = None
        self._building = False

    async def async_start(self) -> None:

        @callback
        def _state_changed_listener(event):
            entity_id = event.data.get("entity_id", "")
            domain = entity_id.split(".")[0] if entity_id else ""
            if domain in _SKIP_DOMAINS:
                return
            self._schedule_refresh()

        @callback
        def _on_ha_started(event):
            self._unsub_state_changed = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, _state_changed_listener
            )
            self._schedule_refresh()
            _LOGGER.debug("IndexManager: listening after HA started")

        if self.hass.is_running:
            self._unsub_state_changed = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, _state_changed_listener
            )
        else:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_ha_started)
        _LOGGER.debug("IndexManager started (lazy)")

    async def async_stop(self) -> None:

        if self._unsub_state_changed:
            self._unsub_state_changed()
            self._unsub_state_changed = None

        if self._debounce_handle:
            self._debounce_handle.cancel()
            self._debounce_handle = None

        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        _LOGGER.debug("IndexManager stopped")

    @callback
    def _schedule_refresh(self) -> None:

        if self._debounce_handle:
            self._debounce_handle.cancel()

        self._debounce_handle = self.hass.loop.call_later(
            DEBOUNCE_SECONDS,
            lambda: asyncio.create_task(self._async_refresh_index())
        )

    async def _async_refresh_index(self) -> None:

        if self._building:
            return
        self._building = True
        try:
            self._index = await self.hass.async_add_executor_job(self._build_index_sync)
            self._index_timestamp = time.time()
            _LOGGER.debug("Index refreshed: %d areas, %d domains",
                         len(self._index.get("areas", [])),
                         len(self._index.get("domains", {})))
        except Exception as e:
            _LOGGER.error("Failed to refresh index: %s", e)
        finally:
            self._building = False

    async def get_index(self) -> Dict[str, Any]:

        now = time.time()
        if not self._index or (now - self._index_timestamp) > MAX_INDEX_AGE:
            await self._async_refresh_index()
        return self._index or {}

    def _build_index_sync(self) -> Dict[str, Any]:

        area_reg = ar.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)

        should_expose = None
        if self.assistant:
            from homeassistant.components.homeassistant import async_should_expose
            should_expose = async_should_expose

        entities = list(entity_reg.entities.values())

        areas = self._get_areas(area_reg, entities, device_reg, should_expose)
        domains = self._get_domains(entities, should_expose)
        device_classes = self._get_device_classes(entities)
        people = self._get_states_by_domain("person", include_state=True)
        automations = self._get_states_by_domain("automation", include_state=True, limit=30)
        scripts = self._get_states_by_domain("script", include_state=False, limit=30)

        return {
            "areas": areas,
            "domains": domains,
            "device_classes": device_classes,
            "people": people,
            "automations": automations,
            "scripts": scripts,
            "timestamp": time.time(),
        }

    def _get_areas(self, area_reg, entities, device_reg, should_expose) -> List[Dict[str, Any]]:

        area_entity_counts = defaultdict(int)
        area_device_counts = defaultdict(int)

        for entity in entities:
            if should_expose and not should_expose(self.hass, self.assistant, entity.entity_id):
                continue
            area_id = entity.area_id
            if not area_id and entity.device_id:
                device = device_reg.async_get(entity.device_id)
                if device:
                    area_id = device.area_id
            if area_id:
                area_entity_counts[area_id] += 1

        for device in device_reg.devices.values():
            if device.area_id:
                area_device_counts[device.area_id] += 1

        areas = []
        for area in area_reg.async_list_areas():
            entity_count = area_entity_counts.get(area.id, 0)
            if entity_count > 0:
                areas.append({
                    "id": area.id,
                    "name": area.name,
                    "entities": entity_count,
                    "devices": area_device_counts.get(area.id, 0),
                })

        return sorted(areas, key=lambda x: x["entities"], reverse=True)

    def _get_domains(self, entities, should_expose) -> Dict[str, int]:

        domain_counts = defaultdict(int)
        for entity in entities:
            if should_expose and not should_expose(self.hass, self.assistant, entity.entity_id):
                continue
            domain = entity.entity_id.split(".")[0]
            domain_counts[domain] += 1

        return dict(sorted(domain_counts.items(), key=lambda x: x[1], reverse=True))

    def _get_device_classes(self, entities) -> Dict[str, List[str]]:

        domain_device_classes = defaultdict(set)
        for entity in entities:
            if entity.device_class:
                domain = entity.entity_id.split(".")[0]
                domain_device_classes[domain].add(entity.device_class)

        return {domain: sorted(list(classes)) for domain, classes in domain_device_classes.items()}

    def _get_states_by_domain(self, target_domain: str, *, include_state: bool = True, limit: int = 0) -> List[Dict[str, str]]:

        results = []
        for state in self.hass.states.async_all():
            if state.domain == target_domain:
                entry = {"entity_id": state.entity_id, "name": state.name}
                if include_state:
                    entry["state"] = state.state
                results.append(entry)
                if limit and len(results) >= limit:
                    break
        return results


_INDEX_MANAGERS_KEY = "index_managers"


async def get_index_manager(hass: HomeAssistant, assistant: str = None) -> IndexManager:

    domain_data = hass.data.setdefault(DOMAIN, {})
    managers: dict[str, IndexManager] = domain_data.setdefault(_INDEX_MANAGERS_KEY, {})
    manager_key = assistant or "__default__"

    manager = managers.get(manager_key)
    if manager is None or manager.hass is not hass:
        manager = IndexManager(hass, assistant)
        await manager.async_start()
        managers[manager_key] = manager

    return manager


async def async_cleanup_index_manager(hass: HomeAssistant) -> None:

    domain_data = hass.data.get(DOMAIN, {})
    managers: dict[str, IndexManager] = domain_data.pop(_INDEX_MANAGERS_KEY, {})
    for manager in managers.values():
        await manager.async_stop()
