"""智能实体发现模块 - 借鉴 mcp-assist 的设计
支持按模式、人员、宠物、区域等智能发现关联实体
"""
from __future__ import annotations
import logging
import re
import fnmatch
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, device_registry as dr

_LOGGER = logging.getLogger(__name__)

PERSON_PATTERNS = {
    "presence": ["person.{name}", "device_tracker.{name}*", "binary_sensor.*{name}*presence*"],
    "location": ["sensor.*{name}*location*", "sensor.*{name}*gps*", "device_tracker.{name}*"],
    "activity": ["sensor.*{name}*activity*", "sensor.*{name}*steps*", "sensor.*{name}*sleep*"],
    "device": ["sensor.*{name}*phone*", "sensor.*{name}*battery*", "sensor.*{name}*charging*"],
}

PET_PATTERNS = {
    "tracker": ["device_tracker.*{name}*", "sensor.*{name}*location*"],
    "feeder": ["switch.*{name}*feeder*", "sensor.*{name}*food*", "sensor.*{name}*water*"],
    "activity": ["sensor.*{name}*activity*", "binary_sensor.*{name}*motion*"],
}

INFERRED_TYPE_PATTERNS = {
    "person_detection": ["binary_sensor.*person*", "binary_sensor.*occupancy*", "image.*person*"],
    "motion_detection": ["binary_sensor.*motion*", "binary_sensor.*movement*"],
    "door_window": ["binary_sensor.*door*", "binary_sensor.*window*", "binary_sensor.*contact*"],
    "temperature": ["sensor.*temperature*", "sensor.*temp*"],
    "humidity": ["sensor.*humidity*", "sensor.*humid*"],
    "light_level": ["sensor.*illuminance*", "sensor.*lux*", "sensor.*brightness*"],
    "power_monitoring": ["sensor.*power*", "sensor.*energy*", "sensor.*consumption*"],
    "battery": ["sensor.*battery*"],
    "location_tracking": ["device_tracker.*", "sensor.*location*", "sensor.*gps*"],
}

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@dataclass
class DiscoveryResult:

    entity_id: str
    name: str
    state: str
    domain: str
    area: Optional[str] = None
    device_class: Optional[str] = None
    relationship: str = "primary"
    match_type: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


class SmartDiscovery:

    
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self._area_cache: Dict[str, str] = {}
        self._entity_area_cache: Dict[str, str] = {}
    
    def _refresh_area_cache(self) -> None:

        area_reg = ar.async_get(self.hass)
        self._area_cache = {area.id: area.name for area in area_reg.async_list_areas()}
    
    def _get_entity_area(self, entity_id: str) -> Optional[str]:

        if entity_id in self._entity_area_cache:
            return self._entity_area_cache[entity_id]
        
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)
        
        entry = entity_reg.async_get(entity_id)
        if not entry:
            return None
        
        area_id = entry.area_id
        if not area_id and entry.device_id:
            device = device_reg.async_get(entry.device_id)
            if device:
                area_id = device.area_id
        
        if area_id and area_id in self._area_cache:
            area_name = self._area_cache[area_id]
            self._entity_area_cache[entity_id] = area_name
            return area_name
        
        return None
    
    async def discover_entities(
        self,
        entity_type: str = None,
        area: str = None,
        domain: str = None,
        state: str = None,
        name_contains: str = None,
        device_class: str | List[str] = None,
        name_pattern: str = None,
        inferred_type: str = None,
        limit: int = DEFAULT_LIMIT,
        exposed_only: bool = True,
        assistant: str = None,
    ) -> List[DiscoveryResult]:
        """智能发现实体
        
        参数:
        - entity_type: 实体类型 (如 light, switch)
        - area: 区域名称
        - domain: 域名
        - state: 状态过滤
        - name_contains: 名称包含
        - device_class: 设备类别
        - name_pattern: 通配符模式 (如 *motion*, sensor.*temp*)
        - inferred_type: 推断类型 (如 person_detection, temperature)
        - limit: 返回数量限制
        - exposed_only: 仅返回暴露的实体
        - assistant: 助手ID（用于检查暴露）
        """
        self._refresh_area_cache()
        
        limit = min(limit, MAX_LIMIT)
        results: List[DiscoveryResult] = []
        
        target_area_id = None
        if area:
            area_lower = area.lower()
            for aid, aname in self._area_cache.items():
                if area_lower in aname.lower() or aname.lower() in area_lower:
                    target_area_id = aid
                    break
        
        device_classes: Set[str] = set()
        if device_class:
            if isinstance(device_class, str):
                device_classes.add(device_class.lower())
            else:
                device_classes.update(dc.lower() for dc in device_class)
        
        inferred_patterns: List[str] = []
        if inferred_type and inferred_type in INFERRED_TYPE_PATTERNS:
            inferred_patterns = INFERRED_TYPE_PATTERNS[inferred_type]
        
        entity_reg = er.async_get(self.hass)
        
        for state_obj in self.hass.states.async_all():
            if len(results) >= limit:
                break
            
            eid = state_obj.entity_id
            ename = state_obj.name or eid.split('.')[-1]
            edomain = state_obj.domain
            
            if exposed_only and assistant:
                from homeassistant.components.homeassistant import async_should_expose
                if not async_should_expose(self.hass, assistant, eid):
                    continue
            
            if domain and edomain != domain:
                continue
            if entity_type and edomain != entity_type:
                continue
            
            if state and state_obj.state.lower() != state.lower():
                continue
            
            if name_contains and name_contains.lower() not in ename.lower():
                continue
            
            if name_pattern:
                if not fnmatch.fnmatch(eid.lower(), name_pattern.lower()):
                    continue
            
            match_type = None
            if inferred_patterns:
                matched = False
                for pattern in inferred_patterns:
                    if fnmatch.fnmatch(eid.lower(), pattern.lower()):
                        matched = True
                        match_type = inferred_type
                        break
                if not matched:
                    continue
            
            entry = entity_reg.async_get(eid)
            entity_device_class = None
            if entry and entry.device_class:
                entity_device_class = entry.device_class.lower()
            elif state_obj.attributes.get("device_class"):
                entity_device_class = state_obj.attributes["device_class"].lower()
            
            if device_classes and entity_device_class not in device_classes:
                continue
            
            entity_area = self._get_entity_area(eid)
            if target_area_id:
                if entry and entry.area_id != target_area_id:
                    if entry.device_id:
                        device_reg = dr.async_get(self.hass)
                        device = device_reg.async_get(entry.device_id)
                        if not device or device.area_id != target_area_id:
                            continue
                    else:
                        continue
            
            results.append(DiscoveryResult(
                entity_id=eid,
                name=ename,
                state=state_obj.state,
                domain=edomain,
                area=entity_area,
                device_class=entity_device_class,
                relationship="primary",
                match_type=match_type,
                attributes=dict(state_obj.attributes) if len(state_obj.attributes) < 10 else {},
            ))
        
        return results
    
    async def discover_person_entities(
        self,
        person_name: str,
        limit: int = DEFAULT_LIMIT,
    ) -> List[DiscoveryResult]:

        results: List[DiscoveryResult] = []
        name_lower = person_name.lower().replace(" ", "_")
        
        person_entity = None
        for state in self.hass.states.async_all():
            if state.domain == "person" and name_lower in state.entity_id.lower():
                person_entity = state
                results.append(DiscoveryResult(
                    entity_id=state.entity_id,
                    name=state.name,
                    state=state.state,
                    domain="person",
                    relationship="primary",
                    match_type="person",
                ))
                break
        
        for category, patterns in PERSON_PATTERNS.items():
            for pattern in patterns:
                expanded = pattern.format(name=name_lower)
                for state in self.hass.states.async_all():
                    if len(results) >= limit:
                        break
                    if fnmatch.fnmatch(state.entity_id.lower(), expanded):
                        if state.entity_id not in [r.entity_id for r in results]:
                            results.append(DiscoveryResult(
                                entity_id=state.entity_id,
                                name=state.name,
                                state=state.state,
                                domain=state.domain,
                                area=self._get_entity_area(state.entity_id),
                                relationship="related",
                                match_type=category,
                            ))
        
        return results
    
    async def discover_pet_entities(
        self,
        pet_name: str,
        limit: int = DEFAULT_LIMIT,
    ) -> List[DiscoveryResult]:

        results: List[DiscoveryResult] = []
        name_lower = pet_name.lower().replace(" ", "_")
        
        for category, patterns in PET_PATTERNS.items():
            for pattern in patterns:
                expanded = pattern.format(name=name_lower)
                for state in self.hass.states.async_all():
                    if len(results) >= limit:
                        break
                    if fnmatch.fnmatch(state.entity_id.lower(), expanded):
                        if state.entity_id not in [r.entity_id for r in results]:
                            results.append(DiscoveryResult(
                                entity_id=state.entity_id,
                                name=state.name,
                                state=state.state,
                                domain=state.domain,
                                area=self._get_entity_area(state.entity_id),
                                relationship="related",
                                match_type=category,
                            ))
        
        return results
    
    async def discover_area_entities(
        self,
        area_name: str,
        domains: List[str] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> List[DiscoveryResult]:

        return await self.discover_entities(
            area=area_name,
            domain=domains[0] if domains and len(domains) == 1 else None,
            limit=limit,
        )
    
    def format_results(
        self,
        results: List[DiscoveryResult],
        query_type: str = "general",
        query: str = "",
    ) -> Dict[str, Any]:

        if not results:
            return {"success": True, "count": 0, "entities": [], "message": "未找到匹配的实体"}
        
        primary = [r for r in results if r.relationship == "primary"]
        related = [r for r in results if r.relationship == "related"]
        inferred = [r for r in results if r.relationship == "inferred"]
        
        entities = []
        for r in results:
            entity_data = {
                "entity_id": r.entity_id,
                "name": r.name,
                "state": r.state,
                "domain": r.domain,
            }
            if r.area:
                entity_data["area"] = r.area
            if r.device_class:
                entity_data["device_class"] = r.device_class
            if r.relationship != "primary":
                entity_data["relationship"] = r.relationship
            if r.match_type:
                entity_data["match_type"] = r.match_type
            entities.append(entity_data)
        
        return {
            "success": True,
            "query_type": query_type,
            "query": query,
            "count": len(results),
            "primary_count": len(primary),
            "related_count": len(related),
            "entities": entities,
        }


_discovery: Optional[SmartDiscovery] = None


def get_smart_discovery(hass: HomeAssistant) -> SmartDiscovery:

    global _discovery
    if _discovery is None or _discovery.hass is not hass:
        _discovery = SmartDiscovery(hass)
    return _discovery
