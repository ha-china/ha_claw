"""Script management tool for Home Assistant.

Mirrors the official frontend pipeline used by the config panel:
- YAML source of truth is scripts.yaml (a dict keyed by object_id).
- EditScriptConfigView is used to rewrite entries under the mutation lock.
- async_validate_config_item validates a single script's config.
- icon / area_id are entity registry fields, not YAML fields.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import voluptuous as vol

from homeassistant.components.script import DOMAIN as SCRIPT_DOMAIN
from homeassistant.components.script.config import async_validate_config_item
from homeassistant.config import SCRIPT_CONFIG_PATH
from homeassistant.const import SERVICE_RELOAD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, llm
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.json import JsonObjectType
from homeassistant.util.yaml import dump, load_yaml

_LOGGER = logging.getLogger(__name__)


class ScriptTool(llm.Tool):
    """Manage Home Assistant scripts via official APIs."""

    name = "Script"
    description = (
        "Manage Home Assistant scripts via official APIs. "
        "Actions: list, get, create, update, delete, run. "
        "TWO update paths: "
        "1) Metadata only (icon/area/labels/name): pass empty config + metadata params → applies directly to entity registry, instant. "
        "2) Config change (alias/description/sequence): pass config dict → validate → atomic write → reload → post-verify. "
        "IMPORTANT: rename = config.alias (the script's own internal name, stored in YAML). When user says 'rename', ALWAYS use config.alias, NEVER the name param. "
        "name param = entity registry friendly-name override (a separate display layer on top of alias; rarely needed, null clears back to alias). "
        "For description: config.description. "
        "Params: action, script_id, entity_id, config (dict; partial on update, full on create), "
        "variables (for run), icon (emoji or mdi:name; null clears), area_id (must exist; null clears), "
        "labels (list of label_ids; replaces set), name (entity registry display override; null clears back to alias), "
        "page, page_size. "
        "list returns paginated results (default page=1, page_size=10). "
        "create requires config with sequence; duplicates are rejected. "
        "run executes the script with optional variables dict. "
        "After update, response includes verified=true/false and current_config for self-check; if wrong, do a targeted small fix."
    )

    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(
                ["list", "get", "create", "update", "delete", "run"]
            ),
            vol.Optional("entity_id", default=""): str,
            vol.Optional("script_id", default=""): str,
            vol.Optional("config", default={}): vol.Any(dict, str),
            vol.Optional("variables", default={}): dict,
            vol.Optional("page", default=1): vol.Coerce(int),
            vol.Optional("page_size", default=10): vol.Coerce(int),
            vol.Optional("icon"): vol.Any(str, None),
            vol.Optional("area_id"): vol.Any(str, None),
            vol.Optional("labels"): vol.Any(list, None),
            vol.Optional("name"): vol.Any(str, None),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "list")
        entity_id = str(tool_input.tool_args.get("entity_id", "")).strip()
        script_id = str(tool_input.tool_args.get("script_id", "")).strip()
        config = tool_input.tool_args.get("config") or {}
        variables = tool_input.tool_args.get("variables") or {}
        args = tool_input.tool_args
        _SENTINEL = object()
        icon = args.get("icon", _SENTINEL) if "icon" in args else _SENTINEL
        area_id = args.get("area_id", _SENTINEL) if "area_id" in args else _SENTINEL
        labels = args.get("labels", _SENTINEL) if "labels" in args else _SENTINEL
        name = args.get("name", _SENTINEL) if "name" in args else _SENTINEL

        try:
            if action == "list":
                page = max(1, int(tool_input.tool_args.get("page", 1)))
                page_size = max(1, int(tool_input.tool_args.get("page_size", 10)))
                return await self._list_scripts(hass, page=page, page_size=page_size)

            if action == "get":
                return await self._get_script(hass, entity_id, script_id)

            if action == "run":
                return await self._run_script(hass, entity_id, script_id, variables)

            if action == "create":
                return await self._create_or_update(
                    hass, config, script_id,
                    is_update=False, icon=icon, area_id=area_id,
                    labels=labels, name=name, sentinel=_SENTINEL,
                )

            if action == "update":
                return await self._create_or_update(
                    hass, config, script_id,
                    is_update=True, icon=icon, area_id=area_id,
                    labels=labels, name=name, sentinel=_SENTINEL,
                )

            if action == "delete":
                return await self._delete_script(hass, entity_id, script_id)

            return {"success": False, "error": "Invalid action or missing required parameters"}
        except Exception as err:
            _LOGGER.error("ScriptTool error: %s", err)
            return {"success": False, "error": str(err)}

    # ---------- helpers ----------

    @staticmethod
    def _resolve_script_id(entity_id: str, script_id: str) -> str:
        """Derive the object_id (= unique_id / YAML key) from user input."""
        if script_id:
            return script_id
        if entity_id:
            return entity_id.removeprefix("script.") if entity_id.startswith("script.") else entity_id
        return ""

    def _get_component(self, hass: HomeAssistant) -> EntityComponent | None:
        return hass.data.get(SCRIPT_DOMAIN)

    async def _load_existing_config(
        self, hass: HomeAssistant, object_id: str
    ) -> dict | None:
        """Return the current raw config dict for a script, or None.

        Prefer in-memory entity (runtime truth); fall back to scripts.yaml.
        """
        component = self._get_component(hass)
        if component is not None:
            entity = component.get_entity(f"script.{object_id}")
            if entity is not None:
                raw = getattr(entity, "raw_config", None)
                if isinstance(raw, dict):
                    return dict(raw)
        path = hass.config.path(SCRIPT_CONFIG_PATH)
        if not os.path.isfile(path):
            return None
        try:
            loaded = await hass.async_add_executor_job(load_yaml, path)
        except Exception as err:  # pragma: no cover
            _LOGGER.warning("Failed to read %s: %s", path, err)
            return None
        if not isinstance(loaded, dict):
            return None
        item = loaded.get(object_id)
        return dict(item) if isinstance(item, dict) else None

    # ---------- actions ----------

    async def _list_scripts(
        self, hass: HomeAssistant, *, page: int = 1, page_size: int = 10
    ) -> JsonObjectType:
        """List scripts with pagination."""
        registry = er.async_get(hass)
        all_items = []
        for state in hass.states.async_all():
            if not state.entity_id.startswith("script."):
                continue
            obj_id = state.entity_id.removeprefix("script.")
            reg_entry = registry.async_get(state.entity_id)
            all_items.append({
                "entity_id": state.entity_id,
                "script_id": obj_id,
                "name": state.name,
                "state": state.state,
                "icon": reg_entry.icon if reg_entry else None,
                "area_id": reg_entry.area_id if reg_entry else None,
            })
        total = len(all_items)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        items = all_items[start : start + page_size]
        return {
            "success": True,
            "scripts": items,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total": total,
        }

    async def _get_script(
        self, hass: HomeAssistant, entity_id: str, script_id: str
    ) -> JsonObjectType:
        object_id = self._resolve_script_id(entity_id, script_id)
        if not object_id:
            return {"success": False, "error": "entity_id or script_id is required"}
        target_entity_id = f"script.{object_id}"

        component = self._get_component(hass)
        if component is None:
            return {"success": False, "error": "Script component not loaded"}
        entity = component.get_entity(target_entity_id)
        if entity is None:
            return {"success": False, "error": f"Script not found: {target_entity_id}"}

        raw_config = getattr(entity, "raw_config", None)
        if raw_config is None:
            return {"success": False, "error": f"Cannot get config for {target_entity_id}"}

        registry = er.async_get(hass)
        reg_entry = registry.async_get(target_entity_id)
        return {
            "success": True,
            "entity_id": target_entity_id,
            "script_id": object_id,
            "config": raw_config,
            "icon": reg_entry.icon if reg_entry else None,
            "area_id": reg_entry.area_id if reg_entry else None,
            "labels": sorted(reg_entry.labels) if reg_entry else [],
        }

    async def _run_script(
        self, hass: HomeAssistant, entity_id: str, script_id: str, variables: dict
    ) -> JsonObjectType:
        object_id = self._resolve_script_id(entity_id, script_id)
        if not object_id:
            return {"success": False, "error": "entity_id or script_id is required"}
        target_entity_id = f"script.{object_id}"
        svc_data: dict = {"entity_id": target_entity_id}
        if variables:
            svc_data["variables"] = variables
        await hass.services.async_call(
            SCRIPT_DOMAIN, "turn_on", svc_data, blocking=True
        )
        return {"success": True, "message": f"Executed {target_entity_id}"}

    async def _apply_registry_meta(
        self, hass: HomeAssistant, entity_id: str,
        *, icon=None, area_id=None, labels=None, name=None, sentinel=None,
    ) -> dict[str, object]:
        """Apply icon/area_id/labels/name to entity registry."""
        touch_icon = icon is not sentinel
        touch_area = area_id is not sentinel
        touch_labels = labels is not sentinel
        touch_name = name is not sentinel
        if not (touch_icon or touch_area or touch_labels or touch_name):
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
            registry.async_update_entity(entity_id, **kwargs)
            if touch_icon:
                applied["icon"] = kwargs.get("icon")
            if touch_area:
                applied["area_id"] = kwargs.get("area_id")
            if touch_labels:
                applied["labels"] = sorted(kwargs.get("labels") or [])
            if touch_name:
                applied["name"] = kwargs.get("name")
        except Exception as err:
            _LOGGER.warning("Failed to update entity registry for %s: %s", entity_id, err)
            applied["entity_registry_error"] = str(err)
        return applied

    async def _create_or_update(
        self,
        hass: HomeAssistant,
        config: dict,
        script_id: str,
        *,
        is_update: bool,
        icon=None,
        area_id=None,
        labels=None,
        name=None,
        sentinel=None,
    ) -> JsonObjectType:
        """Create or update a script via the same pipeline as the frontend."""
        from homeassistant.components.config.script import EditScriptConfigView
        from homeassistant.helpers import config_validation as cv

        if isinstance(config, str):
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "error": "config must be a dict (got unparseable string)"}
        if not isinstance(config, dict):
            return {"success": False, "error": "config must be a dict"}

        if is_update and not script_id:
            return {"success": False, "error": "script_id is required for update"}

        config_patch_empty = is_update and not config
        touch_meta = any(v is not sentinel for v in (icon, area_id, labels, name))

        target_entity_id = f"script.{script_id}" if script_id else ""

        if config_patch_empty:
            if not touch_meta:
                return {"success": False, "error": "Nothing to update (no config and no metadata params)"}
            if not script_id:
                return {"success": False, "error": "script_id is required for metadata update"}
            applied = await self._apply_registry_meta(
                hass, target_entity_id,
                icon=icon, area_id=area_id, labels=labels, name=name, sentinel=sentinel,
            )
            if "error" in applied:
                return {"success": False, **applied}
            return {
                "success": True,
                "message": f"Updated metadata for {target_entity_id}",
                "script_id": script_id,
                "entity_id": target_entity_id,
                **({"applied_registry": applied} if applied else {}),
            }

        if is_update:
            existing = await self._load_existing_config(hass, script_id)
            if existing is None:
                return {
                    "success": False,
                    "error": f"Script '{script_id}' not found",
                }
            merged = dict(existing)
            merged.update(config)
            config = merged

        if not config:
            return {"success": False, "error": "Missing required parameter: config (dict)"}

        if "sequence" not in config:
            return {"success": False, "error": "config.sequence is required"}

        alias = str(config.get("alias", "")).strip()

        if not script_id:
            base = alias or "script"
            slug = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
            script_id = slug or f"script_{int(time.time())}"
            target_entity_id = f"script.{script_id}"

        if not is_update:
            dup = await self._load_existing_config(hass, script_id)
            if dup is not None:
                return {
                    "success": False,
                    "error": f"Script '{script_id}' already exists. Use update instead.",
                    "existing_id": script_id,
                }
            if alias:
                for state in hass.states.async_all():
                    if not state.entity_id.startswith("script."):
                        continue
                    if state.attributes.get("friendly_name", "").strip().lower() == alias.lower():
                        return {
                            "success": False,
                            "error": f"A script with the same name '{alias}' already exists: {state.entity_id}. Use update to modify it.",
                            "existing_entity_id": state.entity_id,
                        }

        entry = dict(config)

        async def hook(action: str, config_key: str) -> None:
            await hass.services.async_call(
                SCRIPT_DOMAIN, SERVICE_RELOAD, {}, blocking=True
            )

        view = EditScriptConfigView(
            SCRIPT_DOMAIN,
            "config",
            SCRIPT_CONFIG_PATH,
            cv.slug,
            post_write_hook=hook,
            data_validator=async_validate_config_item,
        )

        try:
            await view.data_validator(hass, script_id, entry)
        except (vol.Invalid, Exception) as err:
            return {"success": False, "error": f"Invalid script config: {err}"}

        path = hass.config.path(SCRIPT_CONFIG_PATH)

        async with view.mutation_lock:
            current = await view.read_config(hass)
            if not isinstance(current, dict):
                current = {}
            view._write_value(hass, current, script_id, entry)
            await hass.async_add_executor_job(
                lambda: write_utf8_file_atomic(path, dump(current))
            )
        await hook("create_update", script_id)
        if is_update:
            _LOGGER.info("Script '%s' updated (frontend-validated + atomic write)", script_id)

        if not target_entity_id:
            target_entity_id = f"script.{script_id}"
        applied = await self._apply_registry_meta(
            hass, target_entity_id,
            icon=icon, area_id=area_id, labels=labels, name=name, sentinel=sentinel,
        )
        if "error" in applied:
            return {"success": False, **applied}

        verified_config = await self._load_existing_config(hass, script_id)
        verify_ok = verified_config is not None
        if not verify_ok:
            _LOGGER.warning("Post-update verification: script '%s' not found after write", script_id)

        action_word = "Updated" if is_update else "Created"
        display = str(config.get("alias") or script_id)
        result: dict[str, object] = {
            "success": True,
            "message": f"{action_word} script '{display}' (id={script_id})",
            "script_id": script_id,
            "entity_id": target_entity_id,
            "verified": verify_ok,
        }
        if verify_ok and verified_config:
            result["current_config"] = verified_config
        if applied:
            result["applied_registry"] = applied
        return result

    async def _delete_script(
        self, hass: HomeAssistant, entity_id: str, script_id: str
    ) -> JsonObjectType:
        from homeassistant.components.config.script import EditScriptConfigView
        from homeassistant.helpers import config_validation as cv

        object_id = self._resolve_script_id(entity_id, script_id)
        if not object_id:
            return {"success": False, "error": "entity_id or script_id is required"}

        existing = await self._load_existing_config(hass, object_id)
        if existing is None:
            return {"success": False, "error": f"Script '{object_id}' not found"}

        async def hook(action: str, config_key: str) -> None:
            # Reload, then remove the entity registry entry — matches the
            # frontend behaviour in homeassistant.components.config.script.
            await hass.services.async_call(
                SCRIPT_DOMAIN, SERVICE_RELOAD, {}, blocking=True
            )
            ent_reg = er.async_get(hass)
            eid = ent_reg.async_get_entity_id(SCRIPT_DOMAIN, SCRIPT_DOMAIN, config_key)
            if eid is not None:
                ent_reg.async_remove(eid)

        view = EditScriptConfigView(
            SCRIPT_DOMAIN,
            "config",
            SCRIPT_CONFIG_PATH,
            cv.slug,
            post_write_hook=hook,
            data_validator=async_validate_config_item,
        )

        path = hass.config.path(SCRIPT_CONFIG_PATH)
        async with view.mutation_lock:
            current = await view.read_config(hass)
            if isinstance(current, dict) and object_id in current:
                del current[object_id]
                await hass.async_add_executor_job(
                    lambda: write_utf8_file_atomic(path, dump(current))
                )
            else:
                return {"success": False, "error": f"Script '{object_id}' not in scripts.yaml"}

        # Use the ACTION_DELETE constant value ("delete") so the hook mirrors
        # the HA config view semantics for removal.
        await hook("delete", object_id)

        return {
            "success": True,
            "message": f"Deleted script '{object_id}'",
            "script_id": object_id,
            "entity_id": f"script.{object_id}",
        }
