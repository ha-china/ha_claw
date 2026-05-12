
from __future__ import annotations
import logging
import re
import fnmatch
from typing import Dict, List, Any, Optional, Set, Union
from enum import Enum

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er, device_registry as dr
from homeassistant.components.homeassistant import async_should_expose

_LOGGER = logging.getLogger(__name__)

MAX_ENTITIES_PER_DISCOVERY = 50


class QueryType(Enum):

    PERSON = "person"
    PET = "pet"
    DEVICE = "device"
    AREA = "area"
    STATE = "state"
    AGGREGATE = "aggregate"
    GENERAL = "general"


class EntityPattern:


    PERSON_PATTERNS = [
        (r"^person\.{name}$", "primary", "Person entity"),
        (r"^device_tracker\..*{name}.*", "device_tracking", "Device tracker"),
        (r"^sensor\..*{name}.*_ble_area$", "ble_tracking", "BLE area sensor"),
        (r"^sensor\..*{name}.*_ble_room_presence$", "ble_tracking", "BLE room presence"),
        (r"^input_text\.room_{name}$", "room_tracking", "Room tracker"),
        (r"^input_text\.{name}_room$", "room_tracking", "Room tracker"),
        (r"^input_boolean\.{name}_home$", "presence", "Home presence"),
        (r"^input_boolean\.{name}_inside$", "presence", "Inside tracker"),
        (r"^binary_sensor\.{name}_home$", "presence", "Home sensor"),
    ]

    PET_PATTERNS = [
        (r"^person\.{name}$", "primary", "Pet as person entity"),
        (r"^binary_sensor\.{name}$", "primary", "Pet sensor"),
        (r"^sensor\..*{name}.*_ble_area$", "ble_tracking", "BLE area sensor"),
        (r"^input_text\.room_{name}$", "room_tracking", "Room tracker"),
        (r"^input_text\.{name}_room$", "room_tracking", "Room tracker"),
        (r"^input_boolean\.{name}_inside$", "presence", "Inside tracker"),
        (r"^input_boolean\.{name}_home$", "presence", "Home presence"),
    ]

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


class SmartDiscovery:


    def __init__(self, hass: HomeAssistant) -> None:

        self.hass = hass
        self._entity_cache = None
        self._cache_time = None

    @staticmethod
    def _stringify_name(value: Any) -> str:

        if value in (None, ""):
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @classmethod
    def _normalize_name(cls, value: Any) -> str:

        return cls._stringify_name(value).lower()

    async def discover_entities(
        self,
        entity_type: Optional[str] = None,
        area: Optional[str] = None,
        domain: Optional[str] = None,
        state: Optional[str] = None,
        name_contains: Optional[str] = None,
        limit: int = 20,
        device_class: Optional[Union[str, List[str]]] = None,
        name_pattern: Optional[str] = None,
        inferred_type: Optional[str] = None,
        assistant: Optional[str] = None,
        skip_expose_check: bool = False,
    ) -> List[Dict[str, Any]]:

        query_type = self._detect_query_type(entity_type, area, domain, state, name_contains)
        _LOGGER.debug(f"Detected query type: {query_type}, name_contains: {name_contains}")

        if query_type == QueryType.PERSON and name_contains and not device_class and not name_pattern and not inferred_type:
            return await self._discover_person_entities(name_contains, limit)
        elif query_type == QueryType.PET and name_contains and not device_class and not name_pattern and not inferred_type:
            return await self._discover_pet_entities(name_contains, limit)
        elif query_type == QueryType.AGGREGATE and not device_class and not name_pattern and not inferred_type:
            return await self._discover_aggregate_entities(domain, state, limit)
        elif area and not device_class and not name_pattern and not inferred_type:
            return await self._discover_area_entities(area, domain, state, limit)
        else:
            return await self._discover_general_entities(
                entity_type, area, domain, state, name_contains, limit, device_class, name_pattern, inferred_type, assistant, skip_expose_check
            )

    def _detect_query_type(
        self,
        entity_type: Optional[str],
        area: Optional[str],
        domain: Optional[str],
        state: Optional[str],
        name_contains: Optional[str]
    ) -> QueryType:

        if name_contains:
            name_lower = name_contains.lower()
            if self._is_likely_person_name(name_lower):
                if self._is_likely_pet_name(name_lower):
                    return QueryType.PET
                return QueryType.PERSON
            if any(word in name_lower for word in ["who", "anyone", "everyone", "all", "谁", "所有人"]):
                return QueryType.AGGREGATE

        if area and not name_contains:
            return QueryType.AREA
        if state and not name_contains and not area:
            return QueryType.STATE
        if domain and not name_contains:
            return QueryType.DEVICE

        return QueryType.GENERAL

    def _is_likely_person_name(self, name: str) -> bool:

        person_entity = f"person.{name}"
        if self.hass.states.get(person_entity):
            return True

        for entity_id in self.hass.states.async_entity_ids():
            if "device_tracker" in entity_id and name in entity_id.lower():
                return True

        for entity_id in self.hass.states.async_entity_ids():
            if re.match(rf"input_text\.room_{name}", entity_id):
                return True
            if re.match(rf"sensor\..*{name}.*_ble_area", entity_id):
                return True

        return False

    def _is_likely_pet_name(self, name: str) -> bool:

        name_lower = name.lower()

        if name_lower in ["cat", "dog", "pet", "puppy", "kitten", "bird", "fish", "猫", "狗", "宠物"]:
            return True

        has_pet_entities = False
        has_person_entity = self.hass.states.get(f"person.{name_lower}") is not None
        has_device_tracker = False

        for entity_id in self.hass.states.async_entity_ids():
            entity_lower = entity_id.lower()
            if name_lower in entity_lower:
                if "inside" in entity_lower or "room" in entity_lower:
                    has_pet_entities = True
                if "device_tracker" in entity_lower:
                    has_device_tracker = True

        return has_pet_entities and not has_person_entity and not has_device_tracker

    async def _discover_person_entities(self, name: str, limit: int) -> List[Dict[str, Any]]:

        name_lower = name.lower()
        results = {
            "query": name,
            "query_type": "person",
            "primary_entities": [],
            "related_entities": {
                "device_tracking": [],
                "ble_tracking": [],
                "room_tracking": [],
                "presence": [],
                "other": []
            }
        }

        entity_registry = er.async_get(self.hass)

        for state_obj in self.hass.states.async_all():
            entity_id = state_obj.entity_id
            entity_id_lower = entity_id.lower()

            if not async_should_expose(self.hass, "conversation", entity_id):
                continue

            matched = False
            for pattern, category, description in EntityPattern.PERSON_PATTERNS:
                pattern_regex = pattern.replace("{name}", name_lower)
                if re.match(pattern_regex, entity_id_lower):
                    entity_info = self._create_entity_info(state_obj, description)
                    if category == "primary":
                        results["primary_entities"].append(entity_info)
                    else:
                        results["related_entities"][category].append(entity_info)
                    matched = True
                    break

            if not matched:
                state_name = self._normalize_name(state_obj.name)
                if name_lower in state_name:
                    entity_info = self._create_entity_info(state_obj)
                    results["related_entities"]["other"].append(entity_info)
                else:
                    entity_entry = entity_registry.async_get(entity_id)
                    if entity_entry and entity_entry.aliases:
                        for alias in entity_entry.aliases:
                            alias_text = self._stringify_name(alias)
                            if name_lower in alias_text.lower():
                                entity_info = self._create_entity_info(state_obj)
                                entity_info["matched_alias"] = alias_text
                                results["related_entities"]["other"].append(entity_info)
                                break

        return self._format_smart_results(results, limit)

    async def _discover_pet_entities(self, name: str, limit: int) -> List[Dict[str, Any]]:

        name_lower = name.lower()
        results = {
            "query": name,
            "query_type": "pet",
            "primary_entities": [],
            "related_entities": {
                "ble_tracking": [],
                "room_tracking": [],
                "presence": [],
                "other": []
            }
        }

        entity_registry = er.async_get(self.hass)

        for state_obj in self.hass.states.async_all():
            entity_id = state_obj.entity_id
            entity_id_lower = entity_id.lower()

            if not async_should_expose(self.hass, "conversation", entity_id):
                continue

            matched = False
            for pattern, category, description in EntityPattern.PET_PATTERNS:
                pattern_regex = pattern.replace("{name}", name_lower)
                if re.match(pattern_regex, entity_id_lower):
                    entity_info = self._create_entity_info(state_obj, description)
                    if category == "primary":
                        results["primary_entities"].append(entity_info)
                    else:
                        results["related_entities"][category].append(entity_info)
                    matched = True
                    break

            if not matched:
                state_name = self._normalize_name(state_obj.name)
                if name_lower in state_name:
                    entity_info = self._create_entity_info(state_obj)
                    results["related_entities"]["other"].append(entity_info)
                else:
                    entity_entry = entity_registry.async_get(entity_id)
                    if entity_entry and entity_entry.aliases:
                        for alias in entity_entry.aliases:
                            alias_text = self._stringify_name(alias)
                            if name_lower in alias_text.lower():
                                entity_info = self._create_entity_info(state_obj)
                                entity_info["matched_alias"] = alias_text
                                results["related_entities"]["other"].append(entity_info)
                                break

        return self._format_smart_results(results, limit)

    async def _discover_aggregate_entities(
        self, domain: Optional[str], state: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:

        results = {
            "query": f"{'all ' + domain if domain else 'entities'} {'with state ' + state if state else ''}",
            "query_type": "aggregate",
            "primary_entities": [],
            "related_entities": {}
        }

        target_domains = ["person", "device_tracker", "binary_sensor"]
        if domain:
            target_domains = [domain]

        for state_obj in self.hass.states.async_all():
            entity_id = state_obj.entity_id
            entity_domain = entity_id.split(".")[0]

            if not async_should_expose(self.hass, "conversation", entity_id):
                continue

            if entity_domain not in target_domains:
                continue

            if state and state_obj.state.lower() != state.lower():
                continue

            entity_info = self._create_entity_info(state_obj)

            if entity_domain == "person":
                results["primary_entities"].append(entity_info)
            else:
                category = entity_domain.replace("_", " ").title()
                results["related_entities"].setdefault(category, []).append(entity_info)

        return self._format_smart_results(results, limit)

    async def _discover_area_entities(
        self, area: str, domain: Optional[str], state: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:

        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        if isinstance(area, list):
            if not area:
                return []
            area = area[0]

        area_id = None
        area_entry = None
        area_lower = area.lower()
        for a in area_registry.areas.values():
            if area_lower in a.name.lower() or a.name.lower() in area_lower:
                area_id = a.id
                area_entry = a
                break

        if not area_id:
            return []

        results = {
            "query": area,
            "query_type": "area",
            "area_name": area_entry.name if area_entry else area,
            "primary_entities": [],
            "related_entities": {}
        }

        for state_obj in self.hass.states.async_all():
            entity_id = state_obj.entity_id
            entity_domain = entity_id.split(".")[0]

            if not async_should_expose(self.hass, "conversation", entity_id):
                continue

            if domain and entity_domain != domain:
                continue

            if state and state_obj.state.lower() != state.lower():
                continue

            entity_entry = entity_registry.async_get(entity_id)
            entity_area_id = None

            if entity_entry:
                if entity_entry.area_id:
                    entity_area_id = entity_entry.area_id
                elif entity_entry.device_id:
                    device_entry = device_registry.async_get(entity_entry.device_id)
                    if device_entry and device_entry.area_id:
                        entity_area_id = device_entry.area_id

            if entity_area_id != area_id:
                continue

            entity_info = self._create_entity_info(state_obj)
            results["related_entities"].setdefault(entity_domain, []).append(entity_info)

        return self._format_smart_results(results, limit)

    async def _discover_general_entities(
        self,
        entity_type: Optional[str],
        area: Optional[str],
        domain: Optional[str],
        state: Optional[str],
        name_contains: Optional[str],
        limit: int,
        device_class: Optional[Union[str, List[str]]],
        name_pattern: Optional[str],
        inferred_type: Optional[str],
        assistant: Optional[str] = None,
        skip_expose_check: bool = False,
    ) -> List[Dict[str, Any]]:

        entities = []
        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        area_id = None
        if area:
            area_lower = area.lower()
            for a in area_registry.areas.values():
                if area_lower in a.name.lower() or a.name.lower() in area_lower:
                    area_id = a.id
                    break

        inferred_patterns = []
        if inferred_type and inferred_type in INFERRED_TYPE_PATTERNS:
            inferred_patterns = INFERRED_TYPE_PATTERNS[inferred_type]

        limit = min(limit, MAX_ENTITIES_PER_DISCOVERY)

        for state_obj in self.hass.states.async_all():
            entity_id = state_obj.entity_id

            if not skip_expose_check:
                check_assistant = assistant or "conversation"
                if not async_should_expose(self.hass, check_assistant, entity_id):
                    continue

            entity_domain = entity_id.split(".")[0]

            if domain and entity_domain != domain:
                continue
            if entity_type and entity_domain != entity_type:
                continue
            if state and state_obj.state.lower() != state.lower():
                continue

            entity_entry = entity_registry.async_get(entity_id)

            if name_contains:
                search_term = name_contains.lower()
                state_name = self._normalize_name(state_obj.name)
                friendly_name = self._normalize_name(
                    state_obj.attributes.get("friendly_name", "")
                )
                found_match = (
                    search_term in entity_id.lower() or
                    search_term in state_name or
                    search_term in friendly_name
                )
                if not found_match and entity_entry and entity_entry.aliases:
                    for alias in entity_entry.aliases:
                        if search_term in self._normalize_name(alias):
                            found_match = True
                            break
                if not found_match:
                    continue

            if device_class:
                entity_device_class = state_obj.attributes.get('device_class')
                device_class_list = [device_class] if isinstance(device_class, str) else device_class
                if entity_device_class not in device_class_list:
                    continue

            if name_pattern:
                if not fnmatch.fnmatch(entity_id, name_pattern):
                    continue

            if inferred_patterns:
                matched = False
                for pattern in inferred_patterns:
                    if fnmatch.fnmatch(entity_id.lower(), pattern.lower()):
                        matched = True
                        break
                if not matched:
                    continue

            entity_area_id = None
            entity_area_name = None

            if entity_entry:
                if entity_entry.area_id:
                    entity_area_id = entity_entry.area_id
                elif entity_entry.device_id:
                    device_entry = device_registry.async_get(entity_entry.device_id)
                    if device_entry and device_entry.area_id:
                        entity_area_id = device_entry.area_id

                if entity_area_id:
                    area_entry = area_registry.async_get_area(entity_area_id)
                    if area_entry:
                        entity_area_name = area_entry.name

            if area_id and entity_area_id != area_id:
                continue

            entity_info = self._create_entity_info(state_obj)
            entity_info["area"] = entity_area_name
            entity_info["area_id"] = entity_area_id

            entities.append(entity_info)

            if len(entities) >= limit:
                break

        _LOGGER.debug(
            f"General discovery found {len(entities)} entities with filters: "
            f"type={entity_type}, area={area}, domain={domain}, "
            f"state={state}, name_contains={name_contains}"
        )

        return entities

    def _create_entity_info(self, state_obj: Any, description: Optional[str] = None) -> Dict[str, Any]:

        entity_info = {
            "entity_id": state_obj.entity_id,
            "name": self._stringify_name(state_obj.name),
            "domain": state_obj.domain,
            "state": state_obj.state,
        }

        if description:
            entity_info["type"] = description

        entity_registry = er.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        entity_entry = entity_registry.async_get(state_obj.entity_id)
        if entity_entry:
            area_id = entity_entry.area_id
            if not area_id and entity_entry.device_id:
                device_entry = device_registry.async_get(entity_entry.device_id)
                if device_entry:
                    area_id = device_entry.area_id

            if area_id:
                area_entry = area_registry.async_get_area(area_id)
                if area_entry:
                    entity_info["area"] = self._stringify_name(area_entry.name)

            if entity_entry.aliases:
                entity_info["aliases"] = [
                    self._stringify_name(alias) for alias in entity_entry.aliases
                ]

        if state_obj.attributes:
            useful_attrs = {}
            for attr in ["brightness", "temperature", "humidity", "unit_of_measurement",
                        "device_class", "friendly_name"]:
                if attr in state_obj.attributes:
                    useful_attrs[attr] = state_obj.attributes[attr]
            if useful_attrs:
                entity_info["attributes"] = useful_attrs

        return entity_info

    def _format_smart_results(self, results: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:

        formatted = []

        for entity in results.get("primary_entities", [])[:limit]:
            entity["relationship"] = "primary"
            formatted.append(entity)

        remaining_limit = limit - len(formatted)
        for category, entities in results.get("related_entities", {}).items():
            if remaining_limit <= 0:
                break
            for entity in entities[:remaining_limit]:
                entity["relationship"] = category
                formatted.append(entity)
                remaining_limit -= 1
                if remaining_limit <= 0:
                    break

        if formatted:
            summary = {
                "entity_id": "_summary",
                "query_type": results.get("query_type", "general"),
                "query": results.get("query", ""),
                "total_found": len(results.get("primary_entities", [])) +
                              sum(len(v) for v in results.get("related_entities", {}).values()),
                "primary_count": len(results.get("primary_entities", [])),
                "related_count": sum(len(v) for v in results.get("related_entities", {}).values()),
            }
            formatted.insert(0, summary)

        return formatted

    async def get_entity_details(self, entity_ids: List[str]) -> Dict[str, Any]:

        details = {}
        entity_registry = er.async_get(self.hass)
        area_registry = ar.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        for entity_id in entity_ids:
            if not async_should_expose(self.hass, "conversation", entity_id):
                details[entity_id] = {"error": "Entity not exposed to conversation"}
                continue

            state_obj = self.hass.states.get(entity_id)
            if not state_obj:
                details[entity_id] = {"error": "Entity not found"}
                continue

            entity_entry = entity_registry.async_get(entity_id)
            area_name = None
            device_name = None

            if entity_entry:
                area_id = entity_entry.area_id
                if not area_id and entity_entry.device_id:
                    device_entry = device_registry.async_get(entity_entry.device_id)
                    if device_entry:
                        area_id = device_entry.area_id
                        device_name = self._stringify_name(device_entry.name)

                if area_id:
                    area_entry = area_registry.async_get_area(area_id)
                    if area_entry:
                        area_name = self._stringify_name(area_entry.name)

            entity_details = {
                "entity_id": entity_id,
                "name": self._stringify_name(state_obj.name),
                "domain": state_obj.domain,
                "state": state_obj.state,
                "attributes": dict(state_obj.attributes),
                "area": area_name,
                "device": device_name,
                "last_changed": state_obj.last_changed.isoformat(),
                "last_updated": state_obj.last_updated.isoformat(),
            }

            if entity_entry:
                entity_details.update({
                    "unique_id": entity_entry.unique_id,
                    "entity_category": entity_entry.entity_category,
                    "disabled": entity_entry.disabled_by is not None,
                })

            details[entity_id] = entity_details

        return details

    async def list_areas(self) -> List[Dict[str, Any]]:

        area_registry = ar.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        areas = []
        for area_entry in area_registry.areas.values():
            entity_count = 0

            for entity_entry in entity_registry.entities.values():
                if entity_entry.area_id == area_entry.id:
                    if async_should_expose(self.hass, "conversation", entity_entry.entity_id):
                        entity_count += 1

            for device_entry in device_registry.devices.values():
                if device_entry.area_id == area_entry.id:
                    for entity_entry in entity_registry.entities.values():
                        if (entity_entry.device_id == device_entry.id
                            and not entity_entry.area_id
                            and async_should_expose(self.hass, "conversation", entity_entry.entity_id)):
                            entity_count += 1

            areas.append({
                "id": area_entry.id,
                "name": area_entry.name,
                "entity_count": entity_count
            })

        areas.sort(key=lambda x: x["name"])
        return areas

    async def list_domains(self) -> List[Dict[str, Any]]:

        domain_counts = {}

        for state_obj in self.hass.states.async_all():
            if async_should_expose(self.hass, "conversation", state_obj.entity_id):
                domain = state_obj.domain
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

        domains = [
            {"domain": domain, "count": count}
            for domain, count in domain_counts.items()
        ]

        domains.sort(key=lambda x: (-x["count"], x["domain"]))
        return domains

    async def get_entities_by_area(self, area_id: str) -> List[Dict[str, Any]]:

        entities = []
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)

        for state in self.hass.states.async_all():
            entity_entry = entity_registry.async_get(state.entity_id)
            entity_area_id = None

            if entity_entry:
                if entity_entry.area_id:
                    entity_area_id = entity_entry.area_id
                elif entity_entry.device_id:
                    device_entry = device_registry.async_get(entity_entry.device_id)
                    if device_entry and device_entry.area_id:
                        entity_area_id = device_entry.area_id

            if entity_area_id == area_id:
                if async_should_expose(self.hass, "conversation", state.entity_id):
                    entities.append({
                        "entity_id": state.entity_id,
                        "name": state.attributes.get("friendly_name", state.entity_id),
                        "state": state.state,
                        "domain": state.entity_id.split(".")[0]
                    })

        _LOGGER.debug(f"Found {len(entities)} entities in area '{area_id}'")
        return entities

    def format_results(self, results: List[Dict[str, Any]], query_type: str = "general", query: str = "") -> Dict[str, Any]:

        if not results:
            return {"success": True, "count": 0, "entities": [], "message": "No matching entities found"}

        return {
            "success": True,
            "query_type": query_type,
            "query": query,
            "count": len(results),
            "entities": results,
        }


_discovery: Optional[SmartDiscovery] = None


def get_smart_discovery(hass: HomeAssistant) -> SmartDiscovery:

    global _discovery
    if _discovery is None or _discovery.hass is not hass:
        _discovery = SmartDiscovery(hass)
    return _discovery


EntityDiscovery = SmartDiscovery
