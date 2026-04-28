from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)

_COLLECTION_DOMAINS = {
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
    "input_button",
    "timer",
    "counter",
    "schedule",
}

_TEMPLATE_TYPES = {
    "sensor",
    "binary_sensor",
}


def _storage_path(hass: HomeAssistant, domain: str) -> Path:
    return Path(hass.config.config_dir) / ".storage" / domain


def _read_storage(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "minor_version": 1, "key": path.name, "data": {"items": []}}
    return json.loads(path.read_text("utf-8"))


def _write_storage(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower().strip())
    return slug.strip("_") or "helper"


def _next_id(items: list[dict]) -> str:
    existing = {item.get("id", "") for item in items}
    for i in range(1, 10000):
        if str(i) not in existing:
            return str(i)
    return uuid.uuid4().hex[:8]


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _coerce_csv_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _helper_runtime_status(hass: HomeAssistant, domain: str, name: str) -> dict[str, Any]:
    expected_entity_id = f"{domain}.{_slugify(name)}"
    registry = er.async_get(hass)
    registry_entry = next(
        (
            entry
            for entry in registry.entities.values()
            if entry.entity_id.startswith(f"{domain}.")
            and entry.original_name == name
        ),
        None,
    )
    entity_id = registry_entry.entity_id if registry_entry else expected_entity_id
    state = hass.states.get(entity_id)
    return {
        "expected_entity_id": entity_id,
        "registry_present": registry_entry is not None,
        "state_present": state is not None,
        "sync_status": "ready" if state is not None else "storage_only",
    }


def _helper_delete_status(hass: HomeAssistant, domain: str, name: str) -> dict[str, Any]:
    runtime_status = _helper_runtime_status(hass, domain, name)
    return {
        **runtime_status,
        "sync_status": (
            "delete_pending"
            if runtime_status["registry_present"] or runtime_status["state_present"]
            else "removed"
        ),
    }


class HelperManagerTool(llm.Tool):
    name = "HelperManager"
    description = """Create, list, or delete HA native helper entities (visible in Settings > Helpers).

IMPORTANT: When user asks to create an entity or helper, ALWAYS ask which kind they want:
  1. HA Helper (input_boolean/input_number/timer/template sensor etc, independent, use HelperManager)
  2. AI Custom Entity (under claw_assistant device, diagnostic, sensor/binary_sensor/switch/button, use CustomEntityManager)
  Both work. Helpers are more standard, custom entities are more flexible. You can handle either.

action=create: helper_type + name required. Fill type-specific params directly (flat, not nested).
action=list: optional helper_type filter.
action=delete: entity_id or (helper_type + name).

helper_type & params:
  input_boolean  — initial(bool), icon
  input_number   — min(float), max(float), step(float), initial(float), mode("slider"|"box"), unit_of_measurement, icon
  input_text     — min(int), max(int), initial, mode("text"|"password"), pattern, icon
  input_select   — options(comma-separated e.g. "a,b,c"), initial, icon
  input_datetime — has_date(bool), has_time(bool), icon
  input_button   — icon
  timer          — duration("H:MM:SS"), restore(bool), icon
  counter        — initial(int), step(int), minimum(int), maximum(int), restore(bool), icon
  sensor         — state(Jinja2 REQUIRED), unit_of_measurement, device_class, state_class, icon
  binary_sensor  — state(Jinja2 REQUIRED), device_class, icon

Examples:
  action=create helper_type=timer name=Kitchen duration=0:05:00
  action=create helper_type=sensor name=LightsOn state={{ states.light|selectattr('state','eq','on')|list|count }} unit_of_measurement=pcs
  action=create helper_type=input_boolean name=NightMode
  action=delete entity_id=input_boolean.night_mode"""
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(["create", "list", "delete"]),
            vol.Optional("helper_type", default=""): str,
            vol.Optional("name", default=""): str,
            vol.Optional("entity_id", default=""): str,
            vol.Optional("state", default=""): str,
            vol.Optional("icon", default=""): str,
            vol.Optional("initial", default=""): str,
            vol.Optional("min", default=""): str,
            vol.Optional("max", default=""): str,
            vol.Optional("step", default=""): str,
            vol.Optional("mode", default=""): str,
            vol.Optional("unit_of_measurement", default=""): str,
            vol.Optional("options", default=""): str,
            vol.Optional("duration", default=""): str,
            vol.Optional("restore", default=""): str,
            vol.Optional("has_date", default=""): str,
            vol.Optional("has_time", default=""): str,
            vol.Optional("pattern", default=""): str,
            vol.Optional("minimum", default=""): str,
            vol.Optional("maximum", default=""): str,
            vol.Optional("device_class", default=""): str,
            vol.Optional("state_class", default=""): str,
            vol.Optional("availability", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        args = tool_input.tool_args
        action = args.get("action", "")
        helper_type = args.get("helper_type", "").strip()
        name = args.get("name", "").strip()
        entity_id = args.get("entity_id", "").strip()
        config: dict[str, Any] = {}
        for k in ("state", "icon", "initial", "min", "max", "step", "mode",
                   "unit_of_measurement", "options", "duration", "restore",
                   "has_date", "has_time", "pattern", "minimum", "maximum",
                   "device_class", "state_class", "availability"):
            v = args.get(k, "")
            if isinstance(v, str):
                v = v.strip()
            if v not in ("", None):
                config[k] = v

        if action == "list":
            return await self._list(hass, helper_type)
        if action == "create":
            if not helper_type:
                return {"success": False, "error": "helper_type is required"}
            if not name:
                return {"success": False, "error": "name is required"}
            if helper_type in _COLLECTION_DOMAINS:
                return await self._create_collection(hass, helper_type, name, config)
            if helper_type in _TEMPLATE_TYPES:
                return await self._create_template(hass, helper_type, name, config)
            return {"success": False, "error": f"Unsupported helper_type: {helper_type}. Supported: {sorted(_COLLECTION_DOMAINS | _TEMPLATE_TYPES)}"}
        if action == "delete":
            if not entity_id and not name:
                return {"success": False, "error": "entity_id or name is required"}
            return await self._delete(hass, helper_type, entity_id, name)
        return {"success": False, "error": f"Unknown action: {action}"}

    async def _list(self, hass: HomeAssistant, helper_type: str) -> JsonObjectType:
        domains = [helper_type] if helper_type and helper_type in _COLLECTION_DOMAINS else sorted(_COLLECTION_DOMAINS)
        result: dict[str, list] = {}
        for domain in domains:
            path = _storage_path(hass, domain)
            data = await hass.async_add_executor_job(_read_storage, path)
            items = data.get("data", {}).get("items", [])
            if items:
                result[domain] = [
                    item | _helper_runtime_status(hass, domain, item.get("name", ""))
                    for item in items
                ]

        template_entries = []
        for entry in hass.config_entries.async_entries("template"):
            template_entries.append({
                "entry_id": entry.entry_id,
                "title": entry.title,
                "template_type": entry.options.get("template_type", ""),
                "state": entry.options.get("state", ""),
                "name": entry.options.get("name", ""),
            })
        if template_entries and (not helper_type or helper_type in _TEMPLATE_TYPES):
            result["template"] = template_entries

        return {"success": True, "helpers": result, "count": sum(len(v) for v in result.values())}

    async def _create_collection(
        self, hass: HomeAssistant, domain: str, name: str, config: dict
    ) -> JsonObjectType:
        path = _storage_path(hass, domain)
        data = await hass.async_add_executor_job(_read_storage, path)
        items = data.setdefault("data", {}).setdefault("items", [])

        for item in items:
            if item.get("name", "").lower() == name.lower():
                return {"success": False, "error": f"Helper '{name}' already exists in {domain}"}

        item: dict[str, Any] = {"id": _next_id(items), "name": name}

        if domain == "input_boolean":
            if "initial" in config:
                item["initial"] = _coerce_bool(config["initial"])
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "input_number":
            item["min"] = float(config.get("min", 0))
            item["max"] = float(config.get("max", 100))
            item["step"] = float(config.get("step", 1))
            item["mode"] = config.get("mode", "slider")
            if "initial" in config:
                item["initial"] = float(config["initial"])
            if "unit_of_measurement" in config:
                item["unit_of_measurement"] = config["unit_of_measurement"]
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "input_text":
            item["min"] = int(config.get("min", 0))
            item["max"] = int(config.get("max", 100))
            item["mode"] = config.get("mode", "text")
            if "initial" in config:
                item["initial"] = str(config["initial"])
            if "pattern" in config:
                item["pattern"] = config["pattern"]
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "input_select":
            item["options"] = _coerce_csv_list(config.get("options", []))
            if "initial" in config:
                item["initial"] = config["initial"]
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "input_datetime":
            item["has_date"] = _coerce_bool(config.get("has_date", True), default=True)
            item["has_time"] = _coerce_bool(config.get("has_time", True), default=True)
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "input_button":
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "timer":
            item["duration"] = config.get("duration", "0:00:00")
            item["restore"] = _coerce_bool(config.get("restore", False))
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "counter":
            item["initial"] = int(config.get("initial", 0))
            item["step"] = int(config.get("step", 1))
            if "minimum" in config:
                item["minimum"] = int(config["minimum"])
            if "maximum" in config:
                item["maximum"] = int(config["maximum"])
            if "restore" in config:
                item["restore"] = _coerce_bool(config["restore"])
            if "icon" in config:
                item["icon"] = config["icon"]

        elif domain == "schedule":
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                if day in config:
                    item[day] = config[day]
            if "icon" in config:
                item["icon"] = config["icon"]

        items.append(item)
        await hass.async_add_executor_job(_write_storage, path, data)

        try:
            await hass.services.async_call(domain, "reload", blocking=True)
        except Exception:
            _LOGGER.warning("Failed to reload %s after creating helper", domain)

        runtime_status = _helper_runtime_status(hass, domain, name)
        return {
            "success": True,
            "message": f"Created {domain} helper: {name}",
            **runtime_status,
            "item": item,
        }

    async def _create_template(
        self, hass: HomeAssistant, template_type: str, name: str, config: dict
    ) -> JsonObjectType:
        flow_result = await hass.config_entries.flow.async_init(
            "template", context={"source": "user"}
        )
        if flow_result.get("type") != "menu":
            return {"success": False, "error": f"Unexpected flow init result: {flow_result.get('type')}"}

        flow_id = flow_result["flow_id"]
        flow_result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": template_type}
        )
        if flow_result.get("type") not in ("form", "create_entry"):
            return {"success": False, "error": f"Unexpected flow step result: {flow_result}"}

        user_input: dict[str, Any] = {"name": name}
        if "state" in config:
            user_input["state"] = config["state"]
        if "device_class" in config:
            user_input["device_class"] = config["device_class"]
        if "state_class" in config:
            user_input["state_class"] = config["state_class"]
        if "unit_of_measurement" in config:
            user_input["unit_of_measurement"] = config["unit_of_measurement"]
        if "availability" in config:
            user_input["availability"] = config["availability"]

        flow_result = await hass.config_entries.flow.async_configure(flow_id, user_input)

        if flow_result.get("type") == "create_entry":
            return {
                "success": True,
                "message": f"Created template {template_type}: {name}",
                "entry_id": flow_result.get("result", {}).entry_id if hasattr(flow_result.get("result", {}), "entry_id") else "",
                "title": flow_result.get("title", name),
            }

        if flow_result.get("type") == "form" and flow_result.get("errors"):
            return {"success": False, "error": f"Validation errors: {flow_result['errors']}"}

        return {"success": False, "error": f"Unexpected result: {flow_result.get('type')}", "details": str(flow_result)[:500]}

    async def _delete(
        self, hass: HomeAssistant, helper_type: str, entity_id: str, name: str
    ) -> JsonObjectType:
        if entity_id and "." in entity_id:
            domain = entity_id.split(".")[0]
        elif helper_type:
            domain = helper_type
        else:
            return {"success": False, "error": "Cannot determine domain. Provide entity_id or helper_type."}

        if domain == "template" or domain in _TEMPLATE_TYPES:
            for entry in hass.config_entries.async_entries("template"):
                entry_name = entry.options.get("name", entry.title)
                if entry.entry_id == entity_id or entry_name.lower() == name.lower():
                    await hass.config_entries.async_remove(entry.entry_id)
                    return {"success": True, "message": f"Deleted template helper: {entry_name}"}
            return {"success": False, "error": f"Template helper not found: {entity_id or name}"}

        if domain in _COLLECTION_DOMAINS:
            path = _storage_path(hass, domain)
            data = await hass.async_add_executor_job(_read_storage, path)
            items = data.get("data", {}).get("items", [])
            slug = entity_id.split(".", 1)[1] if "." in entity_id else ""

            new_items = []
            removed = None
            for item in items:
                item_slug = _slugify(item.get("name", ""))
                if (slug and item_slug == slug) or (name and item.get("name", "").lower() == name.lower()) or (entity_id and item.get("id") == entity_id):
                    removed = item
                else:
                    new_items.append(item)

            if not removed:
                return {"success": False, "error": f"Helper not found: {entity_id or name}"}

            data["data"]["items"] = new_items
            await hass.async_add_executor_job(_write_storage, path, data)
            try:
                await hass.services.async_call(domain, "reload", blocking=True)
            except Exception:
                _LOGGER.warning("Failed to reload %s after deleting helper", domain)

            target_eid = entity_id if "." in (entity_id or "") else f"{domain}.{_slugify(removed.get('name', ''))}"
            registry = er.async_get(hass)
            if registry.async_get(target_eid):
                registry.async_remove(target_eid)

            runtime_status = _helper_delete_status(hass, domain, removed.get("name", ""))
            return {
                "success": True,
                "message": f"Deleted {domain} helper: {removed.get('name', '')}",
                **runtime_status,
            }

        return {"success": False, "error": f"Unsupported domain: {domain}"}
