from __future__ import annotations

import json
import logging
import time

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers import service as service_helper
from homeassistant.util.json import JsonObjectType

from ..runtime import (
    request_agent_handoff,
    request_next_agent_handoff,
    set_conversation_state,
)
from ..runtime.config_file_store import (
    apply_staged_operation_sync,
    cancel_staged_operation,
    list_config_entries_sync,
    list_pending_operations,
    read_config_file_sync,
    stage_config_operation,
)

_LOGGER = logging.getLogger(__name__)
_TARGET_PARAMS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")


def _extract_discovered_entity_id(results: list[object]) -> str | None:

    for item in results:
        if isinstance(item, dict):
            entity_id = item.get("entity_id")
        else:
            entity_id = getattr(item, "entity_id", None)
        if entity_id and entity_id != "_summary":
            return str(entity_id)
    return None


def _extract_service_target(data: dict) -> tuple[dict, dict]:

    if not isinstance(data, dict):
        return {}, {}

    service_data = dict(data)
    raw_target = service_data.pop("target", None)
    target = dict(raw_target) if isinstance(raw_target, dict) else {}
    return service_data, target


def _has_explicit_target(service_data: dict, target: dict) -> bool:

    return any(param in service_data or param in target for param in _TARGET_PARAMS)


async def _service_requires_explicit_target(
    hass: HomeAssistant, domain: str, service: str
) -> bool:

    description = service_helper.async_get_cached_service_description(
        hass, domain, service
    )
    if description is None:
        descriptions = await service_helper.async_get_all_descriptions(hass)
        description = descriptions.get(domain, {}).get(service)

    if not isinstance(description, dict):
        return True

    return "target" in description


def _build_missing_target_response(
    *,
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    domain: str,
    service: str,
    data: dict,
    tool: "ServiceCallTool",
) -> JsonObjectType:

    from homeassistant.helpers.llm import _get_exposed_entities

    exposed_entities = (
        _get_exposed_entities(hass, llm_context.assistant)
        if llm_context.assistant
        else {}
    )
    available_entities = tool._get_exposed_entities_list(domain, exposed_entities)[:10]
    return {
        "success": False,
        "error": f"Service call requires at least one of: {', '.join(_TARGET_PARAMS)}",
        "retryable": True,
        "missing_target": True,
        "domain": domain,
        "service": service,
        "data": data,
        "available_entities": available_entities,
        "recovery_hint": (
            "Resolve a concrete target entity first, then call the same service again."
        ),
        "suggested_next_tools": [
            {
                "tool": "SmartDiscovery",
                "args": {"domain": domain, "limit": 10},
                "reason": "Find candidate entity IDs for this service domain.",
            },
            {
                "tool": "GetLiveContext",
                "args": {"domain": domain, "limit": 20},
                "reason": "Inspect available exposed entities and their current states.",
            },
        ],
    }


class GetSystemIndexTool(llm.Tool):
    name = "GetSystemIndex"
    description = """Get the cached system structure index. Returns a lightweight overview including:
- areas: area list with entity/device counts
- domains: domains with entity counts
- device_classes: device classes grouped by domain
- people: people entities and states
- automations: automation list
- scripts: script list

The index is cached for 5 minutes and refreshes automatically when state changes."""
    parameters = vol.Schema({vol.Optional("force_refresh", default=False): bool})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..index_manager import get_index_manager

        force_refresh = tool_input.tool_args.get("force_refresh", False)
        manager = await get_index_manager(hass, llm_context.assistant)
        if force_refresh:
            await manager._async_refresh_index()
        index = await manager.get_index()
        return {"success": True, "cached": not force_refresh, **index}


class SetConversationStateTool(llm.Tool):
    name = "SetConversationState"
    description = """Set the conversation state to indicate whether a user reply is expected.

- expecting_response=true: you are waiting for a user reply, such as a question or confirmation
- expecting_response=false: the task is complete and no reply is needed

Call this tool after finishing a task so the system can manage the conversation lifecycle correctly."""
    parameters = vol.Schema(
        {
            vol.Required("expecting_response"): bool,
            vol.Optional("reason", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        expecting = tool_input.tool_args.get("expecting_response", False)
        reason = tool_input.tool_args.get("reason", "")
        set_conversation_state(hass, expecting_response=expecting, reason=reason)
        _LOGGER.debug("SetConversationState: expecting=%s, reason=%s", expecting, reason)
        return {
            "success": True,
            "expecting_response": expecting,
            "message": "Conversation state updated" if expecting else "Task completed; conversation may end",
        }


class AgentHandoffTool(llm.Tool):
    name = "AgentHandoff"
    description = """Request that another configured AI agent answer this turn.

Use direction="next" when the next AI should answer.
Use direction="previous" when the previous AI should continue.
Optionally include reply_content so the target AI can continue without making
the user repeat themselves."""
    parameters = vol.Schema(
        {
            vol.Optional("direction", default="next"): vol.In(["next", "previous"]),
            vol.Optional("reason", default=""): str,
            vol.Optional("reply_content", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        direction = str(tool_input.tool_args.get("direction", "next") or "next").strip()
        reason = str(tool_input.tool_args.get("reason", "") or "").strip()
        reply_content = str(tool_input.tool_args.get("reply_content", "") or "").strip()
        request_agent_handoff(
            hass,
            direction=direction,
            reason=reason,
            reply_content=reply_content,
        )
        _LOGGER.debug(
            "AgentHandoff requested: direction=%s, reason=%s, reply_chars=%s",
            direction,
            reason,
            len(reply_content),
        )
        return {
            "success": True,
            "requested": True,
            "direction": direction,
            "reason": reason,
            "message": "Agent handoff requested",
        }


class NextAgentHandoffTool(llm.Tool):
    name = "NextAgentHandoff"
    description = """Backward-compatible alias for AgentHandoff(direction='next')."""
    parameters = vol.Schema(
        {
            vol.Optional("reason", default=""): str,
            vol.Optional("reply_content", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        reason = str(tool_input.tool_args.get("reason", "") or "").strip()
        reply_content = str(tool_input.tool_args.get("reply_content", "") or "").strip()
        request_next_agent_handoff(
            hass,
            reason=reason,
            reply_content=reply_content,
        )
        return {
            "success": True,
            "requested": True,
            "direction": "next",
            "reason": reason,
            "message": "Next AI handoff requested",
        }


class ConfigFileTool(llm.Tool):
    name = "ConfigFile"
    description = """Access the Home Assistant config directory.

Available actions:
- action=list: list a directory
- action=read: read a file
- action=stage_write: stage a write and wait for confirmation
- action=stage_append: stage an append and wait for confirmation
- action=stage_mkdir: stage directory creation and wait for confirmation
- action=stage_delete: stage file or directory deletion and wait for confirmation
- action=apply: apply a staged operation after user confirmation
- action=cancel: cancel a staged operation
- action=list_pending: list pending operations

Rules:
- Only paths inside the Home Assistant config directory are allowed
- Any write must be staged first, then applied after user confirmation"""
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(
                [
                    "list",
                    "read",
                    "stage_write",
                    "stage_append",
                    "stage_mkdir",
                    "stage_delete",
                    "apply",
                    "cancel",
                    "list_pending",
                ]
            ),
            vol.Optional("path", default=""): str,
            vol.Optional("content", default=""): str,
            vol.Optional("approval_id", default=""): str,
            vol.Optional("create_dirs", default=False): bool,
            vol.Optional("include_hidden", default=False): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        path = tool_input.tool_args.get("path", "")
        content = tool_input.tool_args.get("content", "")
        approval_id = tool_input.tool_args.get("approval_id", "")
        create_dirs = bool(tool_input.tool_args.get("create_dirs", False))
        include_hidden = bool(tool_input.tool_args.get("include_hidden", False))

        try:
            if action == "list":
                result = await hass.async_add_executor_job(
                    list_config_entries_sync, hass, path, include_hidden
                )
                return {"success": True, **result}

            if action == "read":
                result = await hass.async_add_executor_job(
                    read_config_file_sync, hass, path
                )
                return {"success": True, **result}

            if action == "list_pending":
                return {
                    "success": True,
                    "count": len(list_pending_operations(hass)),
                    "pending": list_pending_operations(hass),
                }

            if action in {"stage_write", "stage_append", "stage_mkdir", "stage_delete"}:
                stage_action = action.removeprefix("stage_")
                operation = stage_config_operation(
                    hass,
                    action=stage_action,
                    relative_path=path,
                    content=content,
                    create_dirs=create_dirs,
                )
                return {
                    "success": True,
                    "message": "Created a staged operation and waiting for user confirmation",
                    **operation,
                }

            if action == "apply":
                result = await hass.async_add_executor_job(
                    apply_staged_operation_sync, hass, approval_id
                )
                return {"success": True, **result}

            if action == "cancel":
                result = cancel_staged_operation(hass, approval_id)
                return {"success": True, **result}

            return {"success": False, "error": f"Unknown action: {action}"}
        except PermissionError as err:
            return {"success": False, "error": str(err), "requires_confirmation": True}
        except Exception as err:
            return {"success": False, "error": str(err)}


class ValidateServiceTool(llm.Tool):
    name = "ValidateService"
    description = "Validate service call parameters before using ServiceCall. Returns validity, errors, and suggestions."
    parameters = vol.Schema(
        {
            vol.Required("domain"): str,
            vol.Required("service"): str,
            vol.Optional("data", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..domain_registry import validate_service_call

        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})
        result = validate_service_call(domain, service, data)
        return {
            "success": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "suggestions": result["suggestions"],
            "normalized_service": result["normalized_service"],
        }


class ServiceHelpTool(llm.Tool):
    name = "ServiceHelp"
    description = "Get help for a domain or service, including available services, parameter guidance, and accepted values."
    parameters = vol.Schema(
        {vol.Required("domain"): str, vol.Optional("service", default=""): str}
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..domain_registry import get_service_help

        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        help_text = get_service_help(domain, service if service else None)
        return {"success": True, "help": help_text}


class SmartDiscoveryTool(llm.Tool):
    name = "SmartDiscovery"
    description = """Smart entity discovery with multiple matching modes:
- by pattern: name_pattern="*motion*" or "*temperature*"
- by inferred type: inferred_type="person_detection"/"temperature"/"door_window" and more
- by person: person_name to discover related entities
- by pet: pet_name to discover related entities
- combined filtering: area + domain + device_class + state

Available inferred types: person_detection, motion_detection, door_window, temperature, humidity, light_level, power_monitoring, battery, location_tracking"""
    parameters = vol.Schema(
        {
            vol.Optional("area", default=""): str,
            vol.Optional("domain", default=""): str,
            vol.Optional("state", default=""): str,
            vol.Optional("name_contains", default=""): str,
            vol.Optional("name_pattern", default=""): str,
            vol.Optional("device_class", default=""): str,
            vol.Optional("inferred_type", default=""): str,
            vol.Optional("person_name", default=""): str,
            vol.Optional("pet_name", default=""): str,
            vol.Optional("limit", default=20): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..smart_discovery import get_smart_discovery

        discovery = get_smart_discovery(hass)
        args = tool_input.tool_args
        person_name = args.get("person_name", "")
        pet_name = args.get("pet_name", "")
        limit = min(args.get("limit", 20), 50)

        if person_name:
            results = await discovery.discover_person_entities(person_name, limit)
            return discovery.format_results(results, "person", person_name)

        if pet_name:
            results = await discovery.discover_pet_entities(pet_name, limit)
            return discovery.format_results(results, "pet", pet_name)

        results = await discovery.discover_entities(
            area=args.get("area") or None,
            domain=args.get("domain") or None,
            state=args.get("state") or None,
            name_contains=args.get("name_contains") or None,
            name_pattern=args.get("name_pattern") or None,
            device_class=args.get("device_class") or None,
            inferred_type=args.get("inferred_type") or None,
            limit=limit,
            assistant=llm_context.assistant,
        )

        query_type = "general"
        query = ""
        if args.get("inferred_type"):
            query_type = "inferred"
            query = args["inferred_type"]
        elif args.get("area"):
            query_type = "area"
            query = args["area"]
        elif args.get("name_pattern"):
            query_type = "pattern"
            query = args["name_pattern"]

        return discovery.format_results(results, query_type, query)


class EntityQueryTool(llm.Tool):
    name = "EntityQuery"
    description = "Query a Home Assistant entity state. Use this to get current device state, sensor values, and similar information. Supports entity IDs and fuzzy name matching."
    parameters = vol.Schema({vol.Required("entity_id"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        entity_id = tool_input.tool_args.get("entity_id", "")
        state = hass.states.get(entity_id)
        if state:
            return {
                "success": True,
                "entity_id": entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
                "name": state.name,
            }

        from ..smart_discovery import get_smart_discovery

        discovery = get_smart_discovery(hass)
        results = await discovery.discover_entities(
            name_contains=entity_id,
            limit=5,
            assistant=llm_context.assistant if llm_context else None,
        )

        if results:
            matched_entity_id = _extract_discovered_entity_id(results)
            state = hass.states.get(matched_entity_id) if matched_entity_id else None
            if state:
                return {
                    "success": True,
                    "entity_id": matched_entity_id,
                    "state": state.state,
                    "attributes": dict(state.attributes),
                    "name": state.name,
                    "matched_from": entity_id,
                }

        return {"success": False, "error": f"Entity {entity_id} not found"}


class ServiceCallTool(llm.Tool):
    name = "ServiceCall"
    description = """Call a registered Home Assistant service. Params: domain, service, data (dict).
PREFER native intent tools (HassLightSet, HassTurnOn/Off, HassVacuumStart, HassClimateSetTemperature, HassMediaPause, etc.) for device control — they handle entity matching and error reporting better. Only use ServiceCall when no matching intent tool exists.
Good for: calendar events, todo items, triggering automations/scripts, notifications, input helpers, timers, and any service not covered by intent tools.
Do NOT use for: creating automations (use HAControl), installing integrations (use ConfigEntries), managing HACS (use HACS tool), editing YAML config (use ConfigFile), creating helpers (use HelperManager).
Use ListServices to discover available services, ServiceHelp for parameter details."""
    parameters = vol.Schema(
        {
            vol.Required("domain"): str,
            vol.Required("service"): str,
            vol.Optional("data", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        if "json" in data and len(data) == 1:
            try:
                inner = json.loads(data["json"]) if isinstance(data["json"], str) else data["json"]
                data = inner if isinstance(inner, dict) else {}
            except Exception:
                data = {}

        if not isinstance(data, dict):
            data = {}
        sanitized: dict[str, object] = {}
        for k, v in data.items():
            if isinstance(k, str):
                sanitized[k] = v
        data = sanitized

        service_data, target = _extract_service_target(data)
        requires_target = await _service_requires_explicit_target(hass, domain, service)
        has_target = _has_explicit_target(service_data, target)
        if requires_target and not has_target:
            return _build_missing_target_response(
                hass=hass,
                llm_context=llm_context,
                domain=domain,
                service=service,
                data=data,
                tool=self,
            )

        if "entity_id" in service_data or "entity_id" in target:
            entity_id = service_data.get("entity_id", target.get("entity_id"))
            if isinstance(entity_id, list):
                entity_id = entity_id[0] if entity_id else None
                if "entity_id" in service_data:
                    service_data["entity_id"] = entity_id
                else:
                    target["entity_id"] = entity_id
            if entity_id and not hass.states.get(str(entity_id)):
                from ..smart_discovery import get_smart_discovery
                discovery = get_smart_discovery(hass)

                results = await discovery.discover_entities(
                    name_contains=entity_id,
                    domain=domain if domain else None,
                    limit=5,
                    assistant=llm_context.assistant if llm_context else None,
                )

                matched_entity_id = None
                if results:
                    matched_entity_id = _extract_discovered_entity_id(results)
                    if matched_entity_id:
                        if "entity_id" in service_data:
                            service_data["entity_id"] = matched_entity_id
                        else:
                            target["entity_id"] = matched_entity_id
                if not matched_entity_id:
                    from homeassistant.helpers.llm import _get_exposed_entities

                    exposed_entities = (
                        _get_exposed_entities(hass, llm_context.assistant)
                        if llm_context.assistant
                        else {}
                    )
                    exposed = self._get_exposed_entities_list(domain, exposed_entities)
                    return {
                        "success": False,
                        "error": f"Entity not found: {entity_id}",
                        "available_entities": exposed[:10],
                    }

        try:
            await hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=True,
                target=target or None,
            )
            return {
                "success": True,
                "message": f"Successfully called {domain}.{service}",
                "domain": domain,
                "service": service,
                "data": service_data,
                "target": target,
            }
        except Exception as err:
            _LOGGER.error("ServiceCall failed: %s.%s - %s", domain, service, err)
            return {"success": False, "error": str(err)}

    def _get_exposed_entities_list(self, domain: str, exposed_entities: dict) -> list:
        entities = exposed_entities.get("entities", {}) if exposed_entities else {}
        result = []
        for entity_id, info in entities.items():
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            result.append({"entity_id": entity_id, "names": info.get("names", "")})
        return result


class GetLiveContextTool(llm.Tool):
    name = "GetLiveContext"
    description = """Get the real-time state of exposed entities. Use this to answer questions about current device state.

Parameters:
- domain: optional domain filter (for example light, switch, sensor)
- area: optional area filter
- limit: optional maximum result count (default 50, max 100)"""
    parameters = vol.Schema(
        {
            vol.Optional("domain", default=""): str,
            vol.Optional("area", default=""): str,
            vol.Optional("limit", default=50): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from homeassistant.components.homeassistant import async_should_expose
        from homeassistant.helpers import area_registry as ar, entity_registry as er

        domain_filter = tool_input.tool_args.get("domain", "")
        area_filter = tool_input.tool_args.get("area", "").lower()
        limit = min(tool_input.tool_args.get("limit", 50), 100)

        area_reg = ar.async_get(hass)
        entity_reg = er.async_get(hass)

        target_area_id = None
        if area_filter:
            for area in area_reg.async_list_areas():
                if area_filter in area.name.lower():
                    target_area_id = area.id
                    break

        entities = {}
        count = 0
        for state in hass.states.async_all():
            if count >= limit:
                break
            if llm_context.assistant and not async_should_expose(
                hass, llm_context.assistant, state.entity_id
            ):
                continue
            if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
                continue
            if target_area_id:
                entity_entry = entity_reg.async_get(state.entity_id)
                if entity_entry and entity_entry.area_id != target_area_id:
                    continue
            entities[state.entity_id] = {
                "name": state.name,
                "state": state.state,
                "domain": state.domain,
            }
            count += 1

        return {
            "success": True,
            "entities": entities,
            "count": count,
            "limited": count >= limit,
        }


class ListServicesTool(llm.Tool):
    name = "ListServices"
    description = "List all available services for a domain. Use this to inspect what can be called on a given domain."
    parameters = vol.Schema({vol.Required("domain"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        domain = tool_input.tool_args.get("domain", "")
        services = hass.services.async_services().get(domain, {})
        if services:
            service_list = []
            for name, service_obj in services.items():
                description = (
                    getattr(service_obj, "description", "")
                    if hasattr(service_obj, "description")
                    else ""
                )
                service_list.append(
                    f"- {domain}.{name}: {description[:100]}"
                    if description
                    else f"- {domain}.{name}"
                )
            return {
                "success": True,
                "domain": domain,
                "services": service_list,
                "count": len(service_list),
            }
        return {"success": False, "error": f"Domain {domain} does not exist or has no services"}


class AutomationTool(llm.Tool):
    name = "Automation"
    description = """Manage Home Assistant automations: list, trigger, enable, disable, or create.

For create:
- `config` must be a full automation config dict: {alias, trigger, action, [condition, mode, description]}.
- Optional `automation_id` pins the storage id; otherwise a slug of alias is used.
- Writes to `<config>/automations.yaml` (creates the file if missing) and reloads automations.
- Does NOT overwrite an existing entry with the same id unless `overwrite=true`."""
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(["list", "trigger", "enable", "disable", "create"]),
            vol.Optional("entity_id", default=""): str,
            vol.Optional("config", default={}): dict,
            vol.Optional("automation_id", default=""): str,
            vol.Optional("overwrite", default=False): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "list")
        entity_id = tool_input.tool_args.get("entity_id", "")

        try:
            if action == "list":
                automations = [
                    state for state in hass.states.async_all()
                    if state.entity_id.startswith("automation.")
                ]
                return {
                    "success": True,
                    "automations": [
                        {
                            "entity_id": automation.entity_id,
                            "name": automation.name,
                            "state": automation.state,
                        }
                        for automation in automations
                    ],
                }

            if action == "trigger" and entity_id:
                await hass.services.async_call(
                    "automation", "trigger", {"entity_id": entity_id}, blocking=True
                )
                return {"success": True, "message": f"Triggered {entity_id}"}

            if action == "enable" and entity_id:
                await hass.services.async_call(
                    "automation", "turn_on", {"entity_id": entity_id}, blocking=True
                )
                return {"success": True, "message": f"Enabled {entity_id}"}

            if action == "disable" and entity_id:
                await hass.services.async_call(
                    "automation", "turn_off", {"entity_id": entity_id}, blocking=True
                )
                return {"success": True, "message": f"Disabled {entity_id}"}

            if action == "create":
                return await self._create_automation(
                    hass,
                    config=tool_input.tool_args.get("config") or {},
                    automation_id=str(tool_input.tool_args.get("automation_id", "")).strip(),
                    overwrite=bool(tool_input.tool_args.get("overwrite", False)),
                )

            return {"success": False, "error": "Invalid action or missing required parameters"}
        except Exception as err:
            _LOGGER.error("AutomationTool error: %s", err)
            return {"success": False, "error": str(err)}

    async def _create_automation(
        self,
        hass: HomeAssistant,
        *,
        config: dict,
        automation_id: str,
        overwrite: bool,
    ) -> JsonObjectType:
        import re

        from homeassistant.components.automation.config import (
            async_validate_config_item,
        )
        from homeassistant.components.config.view import _read, _write
        from homeassistant.config import AUTOMATION_CONFIG_PATH
        from homeassistant.const import CONF_ID, SERVICE_RELOAD

        if not isinstance(config, dict) or not config:
            return {"success": False, "error": "Missing required parameter: config (dict)"}
        alias = str(config.get("alias", "")).strip()
        if not alias:
            return {"success": False, "error": "config.alias is required"}
        if "trigger" not in config:
            return {"success": False, "error": "config.trigger is required"}
        if "action" not in config:
            return {"success": False, "error": "config.action is required"}

        if not automation_id:
            slug = re.sub(r"[^a-z0-9_]+", "_", alias.lower()).strip("_")
            automation_id = slug or f"auto_{int(time.time())}"

        entry = dict(config)
        entry[CONF_ID] = automation_id

        try:
            await async_validate_config_item(hass, automation_id, entry)
        except vol.Invalid as err:
            return {"success": False, "error": f"Invalid automation config: {err}"}

        path = hass.config.path(AUTOMATION_CONFIG_PATH)
        current = await hass.async_add_executor_job(_read, path)
        if current is None:
            current = []
        if not isinstance(current, list):
            return {
                "success": False,
                "error": (
                    f"{path} is not a list of automations; this file is managed "
                    "by Home Assistant's UI. Aborting to preserve user data."
                ),
            }

        replaced = False
        for idx, item in enumerate(current):
            if isinstance(item, dict) and str(item.get(CONF_ID, "")) == automation_id:
                if not overwrite:
                    return {
                        "success": False,
                        "error": (
                            f"automation id '{automation_id}' already exists; "
                            "retry with overwrite=true to replace it."
                        ),
                    }
                merged = dict(item)
                merged.update(entry)
                current[idx] = merged
                replaced = True
                break
        if not replaced:
            current.append(entry)

        await hass.async_add_executor_job(_write, path, current)

        try:
            await hass.services.async_call(
                "automation", SERVICE_RELOAD, {}, blocking=True
            )
        except Exception as err:
            return {
                "success": False,
                "error": f"wrote {path} but reload failed: {err}",
                "automation_id": automation_id,
                "path": path,
            }

        return {
            "success": True,
            "message": (
                f"{'Updated' if replaced else 'Created'} automation "
                f"'{alias}' (id={automation_id})"
            ),
            "automation_id": automation_id,
            "entity_id": f"automation.{automation_id}",
            "path": path,
        }


class ScriptExecuteTool(llm.Tool):
    name = "ScriptExecute"
    description = "Execute a Home Assistant script."
    parameters = vol.Schema(
        {
            vol.Required("script_id"): str,
            vol.Optional("variables", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        script_id = tool_input.tool_args.get("script_id", "")
        variables = tool_input.tool_args.get("variables", {})
        try:
            if not script_id.startswith("script."):
                script_id = f"script.{script_id}"
            await hass.services.async_call(
                "script",
                "turn_on",
                {"entity_id": script_id, "variables": variables},
                blocking=True,
            )
            return {"success": True, "message": f"Executed {script_id}"}
        except Exception as err:
            return {"success": False, "error": str(err)}


class HistoryQueryTool(llm.Tool):
    name = "HistoryQuery"
    description = "Query entity history."
    parameters = vol.Schema(
        {
            vol.Required("entity_id"): str,
            vol.Optional("hours", default=24): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from datetime import datetime, timedelta

        entity_id = tool_input.tool_args.get("entity_id", "")
        hours = tool_input.tool_args.get("hours", 24)
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                state_changes_during_period,
            )

            start = datetime.now() - timedelta(hours=hours)
            history = await get_instance(hass).async_add_executor_job(
                state_changes_during_period, hass, start, None, entity_id
            )
            if entity_id in history:
                states = [
                    {"state": state.state, "time": state.last_changed.isoformat()}
                    for state in history[entity_id][-20:]
                ]
                return {"success": True, "entity_id": entity_id, "history": states}
            return {"success": False, "error": "No history found"}
        except Exception as err:
            return {"success": False, "error": str(err)}


class AreaDevicesTool(llm.Tool):
    name = "AreaDevices"
    description = "Get all devices in a specific area."
    parameters = vol.Schema({vol.Required("area"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from homeassistant.helpers import (
            area_registry as ar,
            device_registry as dr,
            entity_registry as er,
        )

        area_name = tool_input.tool_args.get("area", "").lower()
        area_reg = ar.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_reg = er.async_get(hass)

        target_area = None
        for area in area_reg.async_list_areas():
            if area_name in area.name.lower():
                target_area = area
                break

        if not target_area:
            return {
                "success": False,
                "error": f"Area '{area_name}' not found",
                "available_areas": [area.name for area in area_reg.async_list_areas()],
            }

        devices = []
        for device in device_reg.devices.values():
            if device.area_id == target_area.id:
                entities = [
                    entity.entity_id
                    for entity in entity_reg.entities.values()
                    if entity.device_id == device.id
                ]
                devices.append({"name": device.name, "entities": entities})

        return {"success": True, "area": target_area.name, "devices": devices}


class BatchControlTool(llm.Tool):
    name = "BatchControl"
    description = "Control multiple devices in one batch."
    parameters = vol.Schema(
        {
            vol.Required("entity_ids"): list,
            vol.Required("action"): vol.In(["turn_on", "turn_off", "toggle"]),
            vol.Optional("data", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        entity_ids = tool_input.tool_args.get("entity_ids", [])
        action = tool_input.tool_args.get("action", "turn_on")
        data = tool_input.tool_args.get("data", {})
        results = []
        for entity_id in entity_ids:
            try:
                domain = entity_id.split(".")[0]
                await hass.services.async_call(
                    domain,
                    action,
                    {"entity_id": entity_id, **data},
                    blocking=True,
                )
                results.append({"entity_id": entity_id, "success": True})
            except Exception as err:
                results.append(
                    {"entity_id": entity_id, "success": False, "error": str(err)}
                )
        return {"success": True, "results": results}


class NotifyTool(llm.Tool):
    name = "Notify"
    description = "Send a notification."
    parameters = vol.Schema(
        {
            vol.Required("message"): str,
            vol.Optional("title", default="AI Assistant"): str,
            vol.Optional("target", default="persistent_notification"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        message = tool_input.tool_args.get("message", "")
        title = tool_input.tool_args.get("title", "AI Assistant")
        target = tool_input.tool_args.get("target", "persistent_notification")
        try:
            if target == "persistent_notification":
                await hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {"message": message, "title": title},
                    blocking=True,
                )
            else:
                await hass.services.async_call(
                    "notify",
                    target,
                    {"message": message, "title": title},
                    blocking=True,
                )
            return {"success": True, "message": "Notification sent"}
        except Exception as err:
            return {"success": False, "error": str(err)}
