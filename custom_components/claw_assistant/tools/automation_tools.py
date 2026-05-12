"""Automation management tools for Home Assistant."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

import voluptuous as vol

from homeassistant.components.automation import DATA_COMPONENT, DOMAIN as AUTOMATION_DOMAIN
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.const import CONF_ID, SERVICE_RELOAD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import category_registry as cr, entity_registry as er, llm
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.json import JsonObjectType
from homeassistant.util.yaml import dump, load_yaml, parse_yaml

from ..runtime.text_patch import PatchError, apply_patches
from ..entity_privacy import entity_is_exposed

_LOGGER = logging.getLogger(__name__)


class AutomationTool(llm.Tool):
    """Manage Home Assistant automations via official APIs."""

    name = "Automation"
    description = (
        "Manage Home Assistant automations via official APIs. "
        "Actions: list, get, create, update, patch, delete, trigger, enable, disable, confirm_draft. "
        "PATCH-FIRST RULE: For any surgical change (tweaking one trigger, swapping one service, fixing a template), "
        "YOU MUST use action=patch with anchor-based ops instead of re-emitting the whole config. "
        "patch params: patches=[{op, anchor, new_text, occurrence?, regex?, count?}, ...], dry_run=true/false. "
        "Ops: replace | insert_before | insert_after | delete | prepend | append | create. "
        "Anchors match against the YAML text of the current config (get the config first to know the exact text). "
        "Limitation: patch cannot remove a top-level key (use action=update for that). "
        "TWO update paths: "
        "1) Metadata only (icon/area/labels/name/category_id): pass empty config + metadata params → applies directly to entity registry, instant. "
        "2) Config change (alias/description/trigger/condition/action): pass config dict → validate → atomic write → reload → post-verify. "
        "IMPORTANT: rename = config.alias (the automation's own internal name, stored in YAML). When user says 'rename', ALWAYS use config.alias, NEVER the name param. "
        "name param = entity registry friendly-name override (a separate display layer on top of alias; rarely needed, null clears back to alias). "
        "For description: config.description. "
        "Params: action, automation_id, entity_id, config (dict or JSON; partial on update, full on create), "
        "icon (emoji or mdi:name; null clears), area_id (must exist; null clears), "
        "labels (list of label_ids; replaces set), name (entity registry display override; null clears back to alias), "
        "category_id (assign to a category; null clears), "
        "page, page_size. "
        "list returns paginated results (default page=1, page_size=10). "
        "create requires full config; if same alias/id exists, auto-promotes to update. "
        "After update, response includes verified=true/false and current_config for self-check; if wrong, do a targeted small fix. "
        "CODING DISCIPLINE: "
        "1) Surgical changes — only modify what the user asked for. Do NOT rewrite or 'improve' unrelated triggers/conditions/actions. Every changed field must trace to the request. "
        "2) Simplicity — minimum config that solves the problem. No speculative error handling, no extra conditions 'just in case'. "
        "3) Think before writing — if the request is ambiguous, ask which interpretation is correct BEFORE sending config. "
        "4) Verify after write — always check verified + current_config in the response. If alias/trigger/action differs from intent, do one targeted fix, not a full rewrite. "
        "5) Jinja2 first — PREFER Jinja2 templates and variables over hardcoded values and repetitive condition branches. "
        "Use value_template / '{{ states(\"sensor.xxx\") }}' for dynamic values. "
        "Use trigger variables + '{{ trigger.to_state.state }}' to avoid duplicating actions per trigger. "
        "Use choose/if with '{{ }}' templates instead of multiple separate automations for related logic. "
        "Use input_number/input_select/input_boolean as variables the user can adjust from the UI instead of hardcoding thresholds. "
        "One smart automation with templates > three dumb automations with hardcoded values."
    )

    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(
                ["list", "get", "trigger", "enable", "disable", "create", "update", "patch", "confirm_draft", "delete"]
            ),
            vol.Optional("entity_id", default=""): str,
            vol.Optional("config", default={}): vol.Any(dict, str),
            vol.Optional("automation_id", default=""): str,
            vol.Optional("page", default=1): vol.Coerce(int),
            vol.Optional("page_size", default=10): vol.Coerce(int),
            vol.Optional("icon"): vol.Any(str, None),
            vol.Optional("area_id"): vol.Any(str, None),
            vol.Optional("labels"): vol.Any(list, None),
            vol.Optional("name"): vol.Any(str, None),
            vol.Optional("category_id"): vol.Any(str, None),
            vol.Optional("patches", default=[]): list,
            vol.Optional("dry_run", default=False): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        """Execute automation tool action."""
        action = tool_input.tool_args.get("action", "list")
        entity_id = tool_input.tool_args.get("entity_id", "")
        automation_id = str(tool_input.tool_args.get("automation_id", "")).strip()
        config = tool_input.tool_args.get("config") or {}
        args = tool_input.tool_args
        _SENTINEL = object()
        icon = args.get("icon", _SENTINEL) if "icon" in args else _SENTINEL
        area_id = args.get("area_id", _SENTINEL) if "area_id" in args else _SENTINEL
        labels = args.get("labels", _SENTINEL) if "labels" in args else _SENTINEL
        name = args.get("name", _SENTINEL) if "name" in args else _SENTINEL
        category_id = args.get("category_id", _SENTINEL) if "category_id" in args else _SENTINEL

        try:
            if action == "list":
                page = max(1, int(tool_input.tool_args.get("page", 1)))
                page_size = max(1, int(tool_input.tool_args.get("page_size", 10)))
                return await self._list_automations(hass, llm_context=llm_context, page=page, page_size=page_size)

            if action == "get":
                return await self._get_automation(hass, entity_id, automation_id)

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
                return await self._create_or_update_automation(
                    hass, config, automation_id,
                    is_update=False, icon=icon, area_id=area_id,
                    labels=labels, name=name, category_id=category_id, sentinel=_SENTINEL,
                )

            if action == "update":
                return await self._inplace_update(
                    hass, config, automation_id, entity_id,
                    icon=icon, area_id=area_id,
                    labels=labels, name=name, category_id=category_id, sentinel=_SENTINEL,
                )

            if action == "patch":
                return await self._patch_automation(
                    hass, automation_id, entity_id,
                    patches=tool_input.tool_args.get("patches", []),
                    dry_run=bool(tool_input.tool_args.get("dry_run", False)),
                    icon=icon, area_id=area_id, labels=labels, name=name,
                    category_id=category_id, sentinel=_SENTINEL,
                )

            if action == "confirm_draft":
                return await self._confirm_draft(hass, automation_id, entity_id)

            if action == "delete":
                return await self._delete_automation(hass, entity_id, automation_id)

            return {"success": False, "error": "Invalid action or missing required parameters"}
        except Exception as err:
            _LOGGER.error("AutomationTool error: %s", err)
            return {"success": False, "error": str(err)}

    def _resolve_config_id(
        self, hass: HomeAssistant, entity_id: str, automation_id: str,
    ) -> tuple[str, str]:
        """Resolve entity_id and the YAML config id (unique_id).

        entity object_id (automation.xxx) != YAML id in many cases.
        The entity's unique_id IS the YAML id field.
        Returns (entity_id, config_id).
        """
        if not entity_id and automation_id:
            entity_id = f"automation.{automation_id}"
        component = hass.data.get(DATA_COMPONENT)
        if component is not None and entity_id:
            auto = component.get_entity(entity_id)
            if auto is not None:
                uid = getattr(auto, "unique_id", None)
                if uid:
                    return entity_id, uid
        if entity_id and not automation_id:
            automation_id = entity_id.removeprefix("automation.")
        return entity_id, automation_id

    async def _list_automations(
        self, hass: HomeAssistant, *, llm_context: llm.LLMContext | None = None, page: int = 1, page_size: int = 10
    ) -> JsonObjectType:
        registry = er.async_get(hass)
        component = hass.data.get(DATA_COMPONENT)
        all_items = []
        for state in hass.states.async_all():
            if not state.entity_id.startswith("automation."):
                continue
            if not entity_is_exposed(hass, state.entity_id, llm_context):
                continue
            auto_id = state.entity_id.removeprefix("automation.")
            if component is not None:
                auto = component.get_entity(state.entity_id)
                if auto is not None:
                    uid = getattr(auto, "unique_id", None)
                    if uid:
                        auto_id = uid
            reg_entry = registry.async_get(state.entity_id)
            cat_id = reg_entry.categories.get("automation") if reg_entry else None
            all_items.append({
                "entity_id": state.entity_id,
                "automation_id": auto_id,
                "name": state.name,
                "state": state.state,
                "icon": reg_entry.icon if reg_entry else None,
                "area_id": reg_entry.area_id if reg_entry else None,
                "category_id": cat_id,
            })
        total = len(all_items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        items = all_items[start : start + page_size]
        return {
            "success": True,
            "automations": items,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total": total,
        }

    async def _get_automation(
        self, hass: HomeAssistant, entity_id: str, automation_id: str
    ) -> JsonObjectType:
        """Get full config of a single automation."""
        if not entity_id and automation_id:
            entity_id = f"automation.{automation_id}"
        if not entity_id:
            return {"success": False, "error": "entity_id or automation_id is required"}

        automation_component = hass.data.get(DATA_COMPONENT)
        if automation_component is None:
            return {"success": False, "error": "Automation component not loaded"}

        automation = automation_component.get_entity(entity_id)
        if automation is None:
            return {"success": False, "error": f"Automation not found: {entity_id}"}

        raw_config = getattr(automation, "raw_config", None)
        if raw_config is None:
            return {"success": False, "error": f"Cannot get config for {entity_id}"}

        config_id = getattr(automation, "unique_id", None) or raw_config.get("id", entity_id.removeprefix("automation."))
        registry = er.async_get(hass)
        reg_entry = registry.async_get(entity_id)
        return {
            "success": True,
            "entity_id": entity_id,
            "automation_id": config_id,
            "config": raw_config,
            "icon": reg_entry.icon if reg_entry else None,
            "area_id": reg_entry.area_id if reg_entry else None,
            "labels": sorted(reg_entry.labels) if reg_entry else [],
            "category_id": reg_entry.categories.get("automation") if reg_entry else None,
        }

    async def _load_existing_config(
        self, hass: HomeAssistant, automation_id: str, *, from_yaml: bool = False,
    ) -> dict | None:
        """Return the current raw config dict for an automation, or None.

        Prefer the in-memory loaded entity (fast, reflects runtime state). Fall
        back to reading automations.yaml directly when the entity is not loaded
        (e.g. disabled or failed to load).

        Set from_yaml=True to skip in-memory cache and read the YAML file
        directly — use this for post-write verification.
        """
        if not from_yaml:
            component = hass.data.get(DATA_COMPONENT)
            if component is not None:
                auto = component.get_entity(f"automation.{automation_id}")
                if auto is not None:
                    raw = getattr(auto, "raw_config", None)
                    if isinstance(raw, dict):
                        return dict(raw)
        path = hass.config.path(AUTOMATION_CONFIG_PATH)
        if not os.path.isfile(path):
            return None
        try:
            loaded = await hass.async_add_executor_job(load_yaml, path)
        except Exception as err:  # pragma: no cover - IO/YAML error path
            _LOGGER.warning("Failed to read %s: %s", path, err)
            return None
        if not isinstance(loaded, list):
            return None
        for item in loaded:
            if isinstance(item, dict) and str(item.get(CONF_ID, "")) == automation_id:
                return dict(item)
        return None

    async def _write_automation(
        self,
        hass: HomeAssistant,
        automation_id: str,
        entry: dict,
    ) -> None:
        """Write automation entry to YAML and reload.

        Mirrors EditAutomationConfigView.post() exactly:
        data_validator → mutation_lock → _write_value → atomic write → post_write_hook.
        """
        from homeassistant.components.config.automation import EditAutomationConfigView
        from homeassistant.helpers import config_validation as cv

        async def hook(action: str, config_key: str) -> None:
            await hass.services.async_call(
                AUTOMATION_DOMAIN, SERVICE_RELOAD, {CONF_ID: config_key}, blocking=True
            )

        view = EditAutomationConfigView(
            AUTOMATION_DOMAIN, "config", AUTOMATION_CONFIG_PATH,
            cv.string, post_write_hook=hook, data_validator=async_validate_config_item,
        )

        await view.data_validator(hass, automation_id, entry)

        path = hass.config.path(AUTOMATION_CONFIG_PATH)
        async with view.mutation_lock:
            current = await view.read_config(hass)
            view._write_value(hass, current, automation_id, entry)
            await hass.async_add_executor_job(
                lambda: write_utf8_file_atomic(path, dump(current))
            )
        await hook("create_update", automation_id)

    async def _apply_registry_meta(
        self, hass: HomeAssistant, entity_id: str,
        *, icon=None, area_id=None, labels=None, name=None, category_id=None, sentinel=None,
    ) -> dict[str, object]:
        """Apply icon/area_id/labels/name/category to entity registry. Returns applied dict."""
        touch_icon = icon is not sentinel
        touch_area = area_id is not sentinel
        touch_labels = labels is not sentinel
        touch_name = name is not sentinel
        touch_category = category_id is not sentinel
        if not (touch_icon or touch_area or touch_labels or touch_name or touch_category):
            return {}
        applied: dict[str, object] = {}
        try:
            registry = er.async_get(hass)
            if registry.async_get(entity_id) is None:
                return {"entity_registry": "entity not yet registered; retry after it exists"}
            kwargs: dict[str, object] = {}
            if touch_icon:
                kwargs["icon"] = icon if icon else None
            if touch_area:
                if area_id:
                    from homeassistant.helpers import area_registry as ar
                    areas = ar.async_get(hass)
                    if areas.async_get_area(area_id) is None:
                        return {"error": f"Area '{area_id}' not found"}
                    kwargs["area_id"] = area_id
                else:
                    kwargs["area_id"] = None
            if touch_labels:
                kwargs["labels"] = set(labels) if labels else set()
            if touch_name:
                kwargs["name"] = name if name else None
            if touch_category:
                reg_ent = registry.async_get(entity_id)
                cats = dict(reg_ent.categories) if reg_ent.categories else {}
                if category_id:
                    cat_reg = cr.async_get(hass)
                    if cat_reg.async_get_category(scope="automation", category_id=category_id) is None:
                        return {"error": f"Category '{category_id}' not found in scope 'automation'"}
                    cats["automation"] = category_id
                else:
                    cats.pop("automation", None)
                kwargs["categories"] = cats
            registry.async_update_entity(entity_id, **kwargs)
            if touch_icon:
                applied["icon"] = kwargs.get("icon")
            if touch_area:
                applied["area_id"] = kwargs.get("area_id")
            if touch_labels:
                applied["labels"] = sorted(kwargs.get("labels") or [])
            if touch_name:
                applied["name"] = kwargs.get("name")
            if touch_category:
                applied["category_id"] = category_id
        except Exception as err:
            _LOGGER.warning("Failed to update entity registry for %s: %s", entity_id, err)
            applied["entity_registry_error"] = str(err)
        return applied

    def _parse_config(self, config) -> dict | str:
        """Parse config from dict or JSON string. Returns dict or error string."""
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                return "config must be a dict (got unparseable string)"
        if not isinstance(config, dict):
            return "config must be a dict"
        return config

    async def _create_or_update_automation(
        self,
        hass: HomeAssistant,
        config: dict,
        automation_id: str,
        *,
        is_update: bool,
        icon=None,
        area_id=None,
        labels=None,
        name=None,
        category_id=None,
        sentinel=None,
    ) -> JsonObjectType:
        """Create a new automation. If same id/alias already exists, auto-promote to in-place update."""
        parsed = self._parse_config(config)
        if isinstance(parsed, str):
            return {"success": False, "error": parsed}
        config = parsed

        if not config:
            return {"success": False, "error": "Missing required parameter: config (dict)"}

        alias = str(config.get("alias", "")).strip()
        if not alias:
            return {"success": False, "error": "config.alias is required"}
        if "trigger" not in config and "triggers" not in config:
            return {"success": False, "error": "config.trigger or config.triggers is required"}
        if "action" not in config and "actions" not in config:
            return {"success": False, "error": "config.action or config.actions is required"}

        if not automation_id:
            slug = re.sub(r"[^a-z0-9_]+", "_", alias.lower()).strip("_")
            automation_id = slug or f"auto_{int(time.time())}"

        dup = await self._load_existing_config(hass, automation_id)
        if dup is not None:
            _LOGGER.info("Auto-promote create→in-place update: id '%s' exists", automation_id)
            return await self._inplace_update(
                hass, config, automation_id, f"automation.{automation_id}",
                icon=icon, area_id=area_id, labels=labels, name=name, category_id=category_id, sentinel=sentinel,
            )
        for state in hass.states.async_all():
            if not state.entity_id.startswith("automation."):
                continue
            if state.attributes.get("friendly_name", "").strip().lower() == alias.lower():
                existing_aid = state.entity_id.removeprefix("automation.")
                _LOGGER.info("Auto-promote create→in-place update: alias '%s' matches %s", alias, state.entity_id)
                return await self._inplace_update(
                    hass, config, existing_aid, state.entity_id,
                    icon=icon, area_id=area_id, labels=labels, name=name, category_id=category_id, sentinel=sentinel,
                )

        entry = dict(config)
        if CONF_ID in entry:
            del entry[CONF_ID]

        try:
            await self._write_automation(hass, automation_id, entry)
        except (vol.Invalid, Exception) as err:
            return {"success": False, "error": f"Invalid automation config: {err}"}

        target_entity_id = f"automation.{automation_id}"
        applied = await self._apply_registry_meta(
            hass, target_entity_id,
            icon=icon, area_id=area_id, labels=labels, name=name, category_id=category_id, sentinel=sentinel,
        )
        if "error" in applied:
            return {"success": False, **applied}

        return {
            "success": True,
            "message": f"Created automation '{alias}' (id={automation_id})",
            "automation_id": automation_id,
            "entity_id": target_entity_id,
            **({"applied_registry": applied} if applied else {}),
        }

    async def _inplace_update(
        self,
        hass: HomeAssistant,
        config,
        automation_id: str,
        entity_id: str,
        *,
        icon=None,
        area_id=None,
        labels=None,
        name=None,
        category_id=None,
        sentinel=None,
    ) -> JsonObjectType:
        """Update automation with safety guarantees.

        - Metadata only (icon/area/labels/name): apply to registry directly.
        - Config changes (alias/description/trigger/condition/action): validate →
          atomic write → reload → post-verify.
        """
        parsed = self._parse_config(config)
        if isinstance(parsed, str):
            return {"success": False, "error": parsed}
        config = parsed

        if not automation_id and entity_id:
            automation_id = entity_id.removeprefix("automation.")
        if not automation_id:
            return {"success": False, "error": "automation_id or entity_id is required for update"}

        target_entity_id = entity_id or f"automation.{automation_id}"
        target_entity_id, automation_id = self._resolve_config_id(hass, target_entity_id, automation_id)
        touch_meta = any(v is not sentinel for v in (icon, area_id, labels, name, category_id))

        if not config:
            if not touch_meta:
                return {"success": False, "error": "Nothing to update (no config and no metadata params)"}
            applied = await self._apply_registry_meta(
                hass, target_entity_id,
                icon=icon, area_id=area_id, labels=labels, name=name, category_id=category_id, sentinel=sentinel,
            )
            if "error" in applied:
                return {"success": False, **applied}
            return {
                "success": True,
                "message": f"Updated metadata for {target_entity_id}",
                "automation_id": automation_id,
                "entity_id": target_entity_id,
                **({"applied_registry": applied} if applied else {}),
            }

        original = await self._load_existing_config(hass, automation_id)
        if original is None:
            return {"success": False, "error": f"Automation '{automation_id}' not found"}

        merged = dict(original)
        merged.update(config)
        final_alias = str(merged.get("alias", "")).strip() or automation_id

        final_entry = dict(merged)
        final_entry.pop(CONF_ID, None)
        final_entry["alias"] = final_alias

        try:
            await self._write_automation(hass, automation_id, final_entry)
        except (vol.Invalid, Exception) as err:
            return {"success": False, "error": f"Failed to write automation: {err}"}
        _LOGGER.info("Automation '%s' updated (frontend-validated + atomic write)", automation_id)

        applied = await self._apply_registry_meta(
            hass, target_entity_id,
            icon=icon, area_id=area_id, labels=labels, name=name, category_id=category_id, sentinel=sentinel,
        )
        if "error" in applied:
            return {"success": False, **applied}

        verified = await self._load_existing_config(hass, automation_id, from_yaml=True)
        verify_ok = verified is not None
        if not verify_ok:
            _LOGGER.warning("Post-update verification: automation '%s' not found after write", automation_id)

        result: dict[str, object] = {
            "success": True,
            "message": f"Updated automation '{final_alias}' (id={automation_id})",
            "automation_id": automation_id,
            "entity_id": target_entity_id,
            "verified": verify_ok,
        }
        if verify_ok and verified:
            result["current_config"] = verified
        if applied:
            result["applied_registry"] = applied
        return result

    async def _patch_automation(
        self,
        hass: HomeAssistant,
        automation_id: str,
        entity_id: str,
        *,
        patches: list,
        dry_run: bool,
        icon=None,
        area_id=None,
        labels=None,
        name=None,
        category_id=None,
        sentinel=None,
    ) -> JsonObjectType:
        """Apply surgical anchor patches to an automation's YAML config.

        Workflow:
        1. Load current config.
        2. Dump to YAML text.
        3. Apply text patches atomically.
        4. Parse patched YAML back to a dict.
        5. Feed the full dict into ``_inplace_update`` (full replacement).
        """
        if not isinstance(patches, list) or not patches:
            return {"success": False, "error": "'patches' must be a non-empty list"}

        if not automation_id and entity_id:
            automation_id = entity_id.removeprefix("automation.")
        if not automation_id:
            return {"success": False, "error": "automation_id or entity_id is required"}

        target_entity_id = entity_id or f"automation.{automation_id}"
        target_entity_id, automation_id = self._resolve_config_id(hass, target_entity_id, automation_id)

        original = await self._load_existing_config(hass, automation_id)
        if original is None:
            return {"success": False, "error": f"Automation '{automation_id}' not found"}

        original_yaml = dump(original)
        label = f"automation/{automation_id}.yaml"

        try:
            report = apply_patches(original_yaml, patches, label=label)
        except PatchError as err:
            return {"success": False, "error": str(err), **err.to_dict()}

        try:
            patched = parse_yaml(report.after)
        except Exception as err:  # noqa: BLE001
            return {
                "success": False,
                "error": f"patched YAML did not parse: {err}",
                "preview_after": report.after[:2000],
                "diff": report.diff,
            }
        if not isinstance(patched, dict):
            return {
                "success": False,
                "error": "patched YAML must be a mapping",
                "preview_after": report.after[:2000],
            }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "report": report.to_dict(),
                "preview_config": patched,
            }

        result = await self._inplace_update(
            hass, patched, automation_id, target_entity_id,
            icon=icon, area_id=area_id, labels=labels, name=name,
            category_id=category_id, sentinel=sentinel,
        )
        if isinstance(result, dict):
            result["patch_report"] = report.to_dict()
        return result

    async def _confirm_draft(
        self, hass: HomeAssistant, automation_id: str, entity_id: str
    ) -> JsonObjectType:
        """Deprecated: update is now atomic in-place. Kept for backward compat.

        If a legacy draft (<id>_draft) exists, promote it via in-place update.
        """
        if not automation_id and entity_id:
            automation_id = entity_id.removeprefix("automation.")
        if not automation_id:
            return {"success": False, "error": "automation_id is required"}

        draft_id = f"{automation_id}_draft"
        draft = await self._load_existing_config(hass, draft_id)
        if draft is None:
            return {
                "success": True,
                "message": f"No pending draft; update is now atomic in-place. Nothing to confirm for '{automation_id}'.",
            }

        draft_alias = str(draft.get("alias", "")).strip()
        final_alias = (
            draft_alias.removeprefix("[Draft] ").strip()
            if draft_alias.startswith("[Draft] ")
            else draft_alias
        )

        final_entry = dict(draft)
        final_entry.pop(CONF_ID, None)
        final_entry["alias"] = final_alias

        await self._write_automation(hass, automation_id, final_entry)
        await self._delete_automation(hass, f"automation.{draft_id}", draft_id)

        return {
            "success": True,
            "message": f"Legacy draft promoted. Automation '{final_alias}' (id={automation_id}) is now live.",
            "automation_id": automation_id,
            "entity_id": f"automation.{automation_id}",
        }

    async def _delete_automation(
        self, hass: HomeAssistant, entity_id: str, automation_id: str
    ) -> JsonObjectType:
        """Delete automation using HA's config view API (same as frontend)."""
        from homeassistant.components.config.automation import EditAutomationConfigView
        from homeassistant.helpers import config_validation as cv

        real_config_id = automation_id
        target_entity_id = entity_id

        if entity_id and not automation_id:
            if not entity_id.startswith("automation."):
                entity_id = f"automation.{entity_id}"
            target_entity_id = entity_id
            automation_component = hass.data.get(DATA_COMPONENT)
            if automation_component:
                automation = automation_component.get_entity(entity_id)
                if automation and hasattr(automation, "raw_config"):
                    raw_config = automation.raw_config
                    if isinstance(raw_config, dict) and "id" in raw_config:
                        real_config_id = raw_config["id"]
                        _LOGGER.info(
                            "Found real config id %s for entity %s",
                            real_config_id, entity_id
                        )
            if not real_config_id:
                real_config_id = entity_id.removeprefix("automation.")

        if not real_config_id:
            return {"success": False, "error": "automation_id or entity_id is required"}

        async def delete_hook(action: str, config_key: str) -> None:
            """Post-delete hook that removes entity from registry."""
            ent_reg = er.async_get(hass)
            reg_entity_id = ent_reg.async_get_entity_id(
                AUTOMATION_DOMAIN, AUTOMATION_DOMAIN, config_key
            )
            if reg_entity_id:
                ent_reg.async_remove(reg_entity_id)

        view = EditAutomationConfigView(
            AUTOMATION_DOMAIN,
            "config",
            AUTOMATION_CONFIG_PATH,
            cv.string,
            post_write_hook=delete_hook,
            data_validator=async_validate_config_item,
        )

        path = hass.config.path(AUTOMATION_CONFIG_PATH)

        try:
            async with view.mutation_lock:
                current = await view.read_config(hass)
                value = view._get_value(hass, current, real_config_id)

                if value is None:
                    all_ids = [
                        item.get(CONF_ID) for item in current if isinstance(item, dict)
                    ]
                    return {
                        "success": False,
                        "error": f"Automation '{real_config_id}' not found. Available IDs: {all_ids}",
                    }

                view._delete_value(hass, current, real_config_id)
                await hass.async_add_executor_job(
                    lambda: write_utf8_file_atomic(path, dump(current))
                )

            await delete_hook("delete", real_config_id)

            return {
                "success": True,
                "message": f"Deleted automation (config_id={real_config_id})",
                "automation_id": real_config_id,
                "entity_id": target_entity_id,
            }
        except Exception as err:
            return {"success": False, "error": f"Failed to delete automation: {err}"}
