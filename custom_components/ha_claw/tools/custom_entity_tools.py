from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, llm
from homeassistant.util.json import JsonObjectType

from ..const import DOMAIN
from ..runtime.custom_entity_store import (
    SUPPORTED_PLATFORMS,
    async_delete_custom_entity,
    async_list_custom_entities,
    async_upsert_custom_entity,
    get_custom_entities,
)

_LOGGER = logging.getLogger(__name__)

_PLATFORM_ADD_KEYS = {
    "sensor": "_custom_sensor_add",
    "binary_sensor": "_custom_binary_sensor_add",
    "switch": "_custom_switch_add",
    "button": "_custom_button_add",
}
_PLATFORM_ENTITIES_KEYS = {
    "sensor": "_custom_sensor_entities",
    "binary_sensor": "_custom_binary_sensor_entities",
    "switch": "_custom_switch_entities",
    "button": "_custom_button_entities",
}


def _sanitize_entity(entry: dict) -> dict:
    safe = {k: v for k, v in entry.items() if k not in ("state_template", "press_action", "options")}
    if entry.get("state_template"):
        safe["state_template"] = "(set)"
    if entry.get("press_action") or (entry.get("options") or {}).get("press_action"):
        safe["press_action"] = "(set)"
    return safe


class CustomEntityManagerTool(llm.Tool):
    name = "CustomEntityManager"
    description = """Create, list, edit, or delete dynamic entities under claw_assistant device (diagnostic category).
These are custom runtime entities managed by AI, with Jinja2 template support.

IMPORTANT: When user asks to create an entity or helper, ALWAYS ask which kind they want:
  1. HA Helper (input_boolean/input_number/timer/template sensor etc, independent, use HelperManager)
  2. AI Custom Entity (under claw_assistant device, diagnostic, sensor/binary_sensor/switch/button, use this tool)
  Both work. Helpers are more standard, custom entities are more flexible. You can handle either.

action=create: platform + name required.
action=list: optional platform filter.
action=edit: entity_id(uid) + fields to update.
action=delete: entity_id(uid).

platform choices & params:
  sensor         — state_template(Jinja2, REQUIRED), unit_of_measurement, device_class, state_class, icon
  binary_sensor  — state_template(Jinja2 returning true/false, REQUIRED), device_class, icon
  switch         — icon (stateful toggle, no template needed)
  button         — icon, press_action(Jinja2 template executed on press)

"""
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(["create", "list", "edit", "delete"]),
            vol.Optional("platform", default=""): str,
            vol.Optional("name", default=""): str,
            vol.Optional("entity_id", default=""): str,
            vol.Optional("state_template", default=""): str,
            vol.Optional("icon", default=""): str,
            vol.Optional("device_class", default=""): str,
            vol.Optional("state_class", default=""): str,
            vol.Optional("unit_of_measurement", default=""): str,
            vol.Optional("press_action", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        args = tool_input.tool_args
        action = args.get("action", "")
        platform = args.get("platform", "").strip()
        name = args.get("name", "").strip()
        entity_id = args.get("entity_id", "").strip()

        if action == "list":
            entities = await async_list_custom_entities(hass)
            if platform:
                entities = [e for e in entities if e.get("platform") == platform]
            return {"success": True, "entities": [_sanitize_entity(e) for e in entities], "count": len(entities)}

        if action == "create":
            if not platform:
                return {"success": False, "error": f"platform is required. Choose from: {list(SUPPORTED_PLATFORMS)}"}
            if platform not in SUPPORTED_PLATFORMS:
                return {"success": False, "error": f"Unsupported platform: {platform}. Choose from: {list(SUPPORTED_PLATFORMS)}"}
            if not name:
                return {"success": False, "error": "name is required"}
            opts: dict[str, Any] = {}
            if args.get("press_action", "").strip():
                opts["press_action"] = args["press_action"].strip()
            entry = await async_upsert_custom_entity(
                hass,
                platform=platform,
                name=name,
                state_template=args.get("state_template", "").strip(),
                icon=args.get("icon", "").strip(),
                device_class=args.get("device_class", "").strip(),
                state_class=args.get("state_class", "").strip(),
                unit_of_measurement=args.get("unit_of_measurement", "").strip(),
                options=opts or None,
            )
            await self._add_entity_to_platform(hass, entry)
            return {
                "success": True,
                "message": f"Created {platform} entity: {name}",
                "uid": entry["uid"],
                "entity": _sanitize_entity(entry),
            }

        if action == "edit":
            if not entity_id:
                return {"success": False, "error": "entity_id (uid) is required"}
            entities = get_custom_entities(hass)
            existing = next((e for e in entities if e["uid"] == entity_id), None)
            if not existing:
                return {"success": False, "error": f"Entity not found: {entity_id}"}
            entry = await async_upsert_custom_entity(
                hass,
                entity_id=entity_id,
                platform=existing["platform"],
                name=name or existing.get("name", ""),
                state_template=args.get("state_template", "").strip() or existing.get("state_template", ""),
                icon=args.get("icon", "").strip() or existing.get("icon", ""),
                device_class=args.get("device_class", "").strip() or existing.get("device_class", ""),
                state_class=args.get("state_class", "").strip() or existing.get("state_class", ""),
                unit_of_measurement=args.get("unit_of_measurement", "").strip() or existing.get("unit_of_measurement", ""),
            )
            self._update_entity_definition(hass, entry)
            return {
                "success": True,
                "message": f"Updated entity: {entity_id}",
                "entity": _sanitize_entity(entry),
            }

        if action == "delete":
            if not entity_id:
                return {"success": False, "error": "entity_id (uid) is required"}
            removed = await async_delete_custom_entity(hass, entity_id)
            if not removed:
                return {"success": False, "error": f"Entity not found: {entity_id}"}
            await self._remove_entity_from_platform(hass, removed)
            return {"success": True, "message": f"Deleted entity: {entity_id}"}

        return {"success": False, "error": f"Unknown action: {action}"}

    async def _add_entity_to_platform(self, hass: HomeAssistant, entry: dict) -> None:
        data = hass.data.get(DOMAIN, {})
        platform = entry["platform"]
        add_key = _PLATFORM_ADD_KEYS.get(platform)
        entities_key = _PLATFORM_ENTITIES_KEYS.get(platform)
        if not add_key or not entities_key:
            return
        add_fn = data.get(add_key)
        entities_map = data.get(entities_key)
        if add_fn is None or entities_map is None:
            return

        config_entry = data.get(next(
            (k for k in data if not k.startswith("_")), None
        ))
        if not hasattr(config_entry, "entry_id"):
            for v in data.values():
                if hasattr(v, "entry_id"):
                    config_entry = v
                    break
        if not hasattr(config_entry, "entry_id"):
            return

        uid = entry["uid"]
        if uid in entities_map:
            return

        if platform == "sensor":
            from ..sensor import DynamicSensor
            ent = DynamicSensor(hass, config_entry, entry)
        elif platform == "binary_sensor":
            from ..binary_sensor import DynamicBinarySensor
            ent = DynamicBinarySensor(hass, config_entry, entry)
        elif platform == "switch":
            from ..switch import DynamicSwitch
            ent = DynamicSwitch(hass, config_entry, entry)
        elif platform == "button":
            from ..button import DynamicButton
            ent = DynamicButton(hass, config_entry, entry)
        else:
            return

        add_fn([ent])
        entities_map[uid] = ent

    def _update_entity_definition(self, hass: HomeAssistant, entry: dict) -> None:
        data = hass.data.get(DOMAIN, {})
        platform = entry["platform"]
        entities_key = _PLATFORM_ENTITIES_KEYS.get(platform)
        if not entities_key:
            return
        entities_map = data.get(entities_key, {})
        ent = entities_map.get(entry["uid"])
        if ent is not None:
            ent._definition = entry
            if entry.get("name"):
                ent._attr_name = entry["name"]
            if entry.get("icon"):
                ent._attr_icon = entry["icon"]
            ent.async_write_ha_state()

    async def _remove_entity_from_platform(self, hass: HomeAssistant, entry: dict) -> None:
        data = hass.data.get(DOMAIN, {})
        platform = entry["platform"]
        entities_key = _PLATFORM_ENTITIES_KEYS.get(platform)
        uid = entry["uid"]

        if entities_key:
            entities_map = data.get(entities_key, {})
            ent = entities_map.pop(uid, None)
            if ent is not None:
                await ent.async_remove()

        registry = er.async_get(hass)
        suffix = f"_{uid}"
        to_remove = [
            e.entity_id for e in registry.entities.values()
            if e.platform == DOMAIN and e.unique_id.endswith(suffix)
        ]
        for eid in to_remove:
            registry.async_remove(eid)
