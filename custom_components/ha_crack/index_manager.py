
from __future__ import annotations
import logging
import asyncio
import time
from typing import Dict, List, Any, Optional
from collections import defaultdict

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, entity_registry as er, device_registry as dr
from homeassistant.const import EVENT_STATE_CHANGED

_LOGGER = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 5.0
MAX_INDEX_AGE = 300.0


class IndexManager:
    """系统索引管理器
    
    功能：
    - 生成和缓存系统结构索引
    - 事件驱动刷新（防抖）
    - 提供轻量级系统概览
    """
    
    def __init__(self, hass: HomeAssistant, assistant: str = None):
        self.hass = hass
        self.assistant = assistant
        self._index: Optional[Dict[str, Any]] = None
        self._index_timestamp: float = 0
        self._refresh_task: Optional[asyncio.Task] = None
        self._debounce_handle: Optional[asyncio.TimerHandle] = None
        self._unsub_state_changed = None
    
    async def async_start(self) -> None:

        await self._async_refresh_index()
        
        @callback
        def _state_changed_listener(event):

            self._schedule_refresh()
        
        self._unsub_state_changed = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, _state_changed_listener
        )
        _LOGGER.info("IndexManager started")
    
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
        
        _LOGGER.info("IndexManager stopped")
    
    @callback
    def _schedule_refresh(self) -> None:

        if self._debounce_handle:
            self._debounce_handle.cancel()
        
        self._debounce_handle = self.hass.loop.call_later(
            DEBOUNCE_SECONDS,
            lambda: asyncio.create_task(self._async_refresh_index())
        )
    
    async def _async_refresh_index(self) -> None:

        try:
            self._index = await self._async_build_index()
            self._index_timestamp = time.time()
            _LOGGER.debug("Index refreshed: %d areas, %d domains", 
                         len(self._index.get("areas", [])),
                         len(self._index.get("domains", {})))
        except Exception as e:
            _LOGGER.error("Failed to refresh index: %s", e)
    
    async def get_index(self) -> Dict[str, Any]:

        now = time.time()
        if not self._index or (now - self._index_timestamp) > MAX_INDEX_AGE:
            await self._async_refresh_index()
        return self._index or {}
    
    async def _async_build_index(self) -> Dict[str, Any]:

        area_reg = ar.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)
        
        areas_task = self._async_get_areas(area_reg, entity_reg, device_reg)
        domains_task = self._async_get_domains(entity_reg)
        device_classes_task = self._async_get_device_classes(entity_reg)
        people_task = self._async_get_people()
        automations_task = self._async_get_automations()
        scripts_task = self._async_get_scripts()
        
        areas, domains, device_classes, people, automations, scripts = await asyncio.gather(
            areas_task, domains_task, device_classes_task, 
            people_task, automations_task, scripts_task
        )
        
        return {
            "areas": areas,
            "domains": domains,
            "device_classes": device_classes,
            "people": people,
            "automations": automations,
            "scripts": scripts,
            "timestamp": time.time(),
        }
    
    async def _async_get_areas(
        self, 
        area_reg: ar.AreaRegistry,
        entity_reg: er.EntityRegistry,
        device_reg: dr.DeviceRegistry,
    ) -> List[Dict[str, Any]]:

        area_entity_counts = defaultdict(int)
        area_device_counts = defaultdict(int)
        
        for entity in entity_reg.entities.values():
            if self.assistant:
                from homeassistant.components.homeassistant import async_should_expose
                if not async_should_expose(self.hass, self.assistant, entity.entity_id):
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
    
    async def _async_get_domains(self, entity_reg: er.EntityRegistry) -> Dict[str, int]:

        domain_counts = defaultdict(int)
        
        for entity in entity_reg.entities.values():
            if self.assistant:
                from homeassistant.components.homeassistant import async_should_expose
                if not async_should_expose(self.hass, self.assistant, entity.entity_id):
                    continue
            
            domain = entity.entity_id.split('.')[0]
            domain_counts[domain] += 1
        
        return dict(sorted(domain_counts.items(), key=lambda x: x[1], reverse=True))
    
    async def _async_get_device_classes(self, entity_reg: er.EntityRegistry) -> Dict[str, List[str]]:

        domain_device_classes = defaultdict(set)
        
        for entity in entity_reg.entities.values():
            if entity.device_class:
                domain = entity.entity_id.split('.')[0]
                domain_device_classes[domain].add(entity.device_class)
        
        return {domain: sorted(list(classes)) for domain, classes in domain_device_classes.items()}
    
    async def _async_get_people(self) -> List[Dict[str, str]]:

        people = []
        for state in self.hass.states.async_all():
            if state.domain == "person":
                people.append({
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "state": state.state,
                })
        return people
    
    async def _async_get_automations(self) -> List[Dict[str, str]]:

        automations = []
        for state in self.hass.states.async_all():
            if state.domain == "automation":
                automations.append({
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "state": state.state,
                })
                if len(automations) >= 30:
                    break
        return automations
    
    async def _async_get_scripts(self) -> List[Dict[str, str]]:

        scripts = []
        for state in self.hass.states.async_all():
            if state.domain == "script":
                scripts.append({
                    "entity_id": state.entity_id,
                    "name": state.name,
                })
                if len(scripts) >= 30:
                    break
        return scripts


_index_manager: Optional[IndexManager] = None


async def get_index_manager(hass: HomeAssistant, assistant: str = None) -> IndexManager:

    global _index_manager
    if _index_manager is None or _index_manager.hass is not hass:
        if _index_manager:
            await _index_manager.async_stop()
        _index_manager = IndexManager(hass, assistant)
        await _index_manager.async_start()
    return _index_manager


async def async_cleanup_index_manager() -> None:

    global _index_manager
    if _index_manager:
        await _index_manager.async_stop()
        _index_manager = None
