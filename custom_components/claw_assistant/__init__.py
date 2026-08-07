from __future__ import annotations
import json
import logging
import os
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView, StaticPathConfig

from .const import (
    DOMAIN,
    CONF_ENTRY_TYPE, ENTRY_TYPE_CORE, ENTRY_TYPE_DASHBOARD,
    DASHBOARD_VFAB_URL, DASHBOARD_API_URL,
    DASHBOARD_PANEL_URL, DASHBOARD_PANEL_ICON, DASHBOARD_PANEL_TITLE,
)
from .runtime.hooks.patches import early_patch_intents
early_patch_intents()
from .runtime import (
    async_setup_runtime,
    async_unload_runtime,
    prime_runtime_state,
)
from .runtime.storage.heartbeat_ticker import async_setup_heartbeat_ticker, async_unload_heartbeat_ticker
from .runtime.utils.im_approval_bridge import (
    async_setup_im_approval_bridge,
    async_unload_im_approval_bridge,
)
from .runtime.storage.user_activity import (
    async_setup_event_listener,
    async_unload_event_listener,
)

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.BUTTON)
DASHBOARD_PLATFORMS = (Platform.SENSOR,)
DATA_AGENT = "agent"

# ── Entry type dispatch ──

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Dispatch to the correct setup based on entry type."""
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DASHBOARD:
        return await _async_setup_dashboard_entry(hass, entry)
    return await _async_setup_core_entry(hass, entry)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_DASHBOARD:
        return await _async_unload_dashboard_entry(hass, entry)
    return await _async_unload_core_entry(hass, entry)

# ── Core entry (existing Claw Assistant runtime) ──

async def _async_setup_core_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    try:
        from homeassistant.loader import async_get_integration
        integration = await async_get_integration(hass, DOMAIN)
        integration.__dict__["quality_scale"] = "None"
        integration.manifest["quality_scale"] = "None"
        integration.manifest["is_built_in"] = True
        integration.manifest["overwrites_built_in"] = False
        integration.manifest["codeowners"] = ["@home-assistant/core"]
        integration.manifest.pop("version", None)
        integration.__dict__.pop("manifest_json_fragment", None)
    except Exception:
        pass
    prime_runtime_state(hass)
    await _async_ensure_bootstrap_on_first_install(hass)
    async_setup_heartbeat_ticker(hass)
    async_setup_im_approval_bridge(hass)
    async_setup_event_listener(hass)
    from .runtime.utils.update_handler import async_setup_update_handler
    async_setup_update_handler(hass)
    from .runtime.hooks.patches import patch_cn_im_hub_interrupt_context
    patch_cn_im_hub_interrupt_context(hass)
    from .runtime.storage.custom_entity_store import async_load_custom_entities
    await async_load_custom_entities(hass)
    from .conversation_utils import async_setup_history_store
    await async_setup_history_store(hass)
    await async_setup_runtime(hass, entry)
    from .runtime.storage.plugin_store import load_all_plugins
    from .runtime.llm.internal_llm import invalidate_runtime_tool_cache
    loaded_plugins = await hass.async_add_executor_job(load_all_plugins, hass)
    if loaded_plugins:
        enabled = [p.manifest.name for p in loaded_plugins if p.enabled]
        failed = [p.manifest.name for p in loaded_plugins if not p.enabled]
        LOGGER.info("Plugins loaded: %d enabled, %d failed. Enabled: %s", len(enabled), len(failed), enabled)
        invalidate_runtime_tool_cache()
    from .delegation import register_delegation_system
    register_delegation_system(hass)
    hass.data[DOMAIN]["entry"] = entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    LOGGER.info("claw_assistant initialized with backend-only runtime")
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    from .runtime.hooks.patches import patch_pipeline_timeout, patch_aihub_markdown_filter
    from .runtime.history.continuous_conversation import continuous_conversation_enabled
    from .runtime.hooks.official_websocket_hook import context_status_bar_enabled, file_upload_enabled, sidebar_dock_enabled
    patch_pipeline_timeout(hass)
    patch_aihub_markdown_filter(hass)

    cc_enabled = continuous_conversation_enabled(hass)
    prev_cc = hass.data.get(f"{DOMAIN}_prev_continuous_conversation")
    if prev_cc is not None and prev_cc != cc_enabled:
        from .chat_commands import _clear_conversation_runtime
        from .runtime.core.state import get_active_conversation_state
        conv_id = get_active_conversation_state(hass).get("id")
        _clear_conversation_runtime(hass, conv_id)
    hass.data[f"{DOMAIN}_prev_continuous_conversation"] = cc_enabled

    hass.bus.async_fire(
        "ha_crack_settings_changed",
        {
            "continuous_conversation": cc_enabled,
            "enable_context_status_bar": context_status_bar_enabled(hass),
            "enable_file_upload": file_upload_enabled(hass),
            "enable_sidebar_dock": sidebar_dock_enabled(hass),
            "enable_sound_notifications": entry.options.get("enable_sound_notifications", True),
            "enable_tool_details": entry.options.get("enable_tool_details", False),
        },
    )

async def _async_unload_core_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    if not hass.data[DOMAIN]:
        async_unload_heartbeat_ticker(hass)
        async_unload_im_approval_bridge(hass)
        async_unload_event_listener(hass)
        from .runtime.utils.update_handler import async_unload_update_handler
        async_unload_update_handler(hass)
        from .conversation_utils import async_flush_history_store
        await async_flush_history_store(hass)
        from .delegation import unregister_delegation_system
        unregister_delegation_system()
        await async_unload_runtime(hass)
    return True

async def _async_ensure_bootstrap_on_first_install(hass: HomeAssistant) -> None:
    import json
    from .runtime.utils.data_path import get_data_dir

    state_path = get_data_dir() / "workspace" / ".workspace_state.json"

    def _materialize() -> bool:
        if state_path.exists():
            return False
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"bootstrap_active": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True

    try:
        created = await hass.async_add_executor_job(_materialize)
    except OSError as err:
        LOGGER.warning("Failed to initialize bootstrap state on first install: %s", err)
        return
    if created:
        LOGGER.info("claw_assistant first install detected; bootstrap_active=true")


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    import json
    from .runtime.utils.data_path import get_data_dir

    state_path = get_data_dir() / "workspace" / ".workspace_state.json"

    def _reset_bootstrap() -> None:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"bootstrap_active": True}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as err:
            LOGGER.warning("Failed to reset bootstrap flag on remove: %s", err)

    await hass.async_add_executor_job(_reset_bootstrap)
    LOGGER.info("claw_assistant removed; bootstrap_active reset to true for next install")


async def async_migrate_entry(hass, config_entry: ConfigEntry):
    if config_entry.version == 1:
        return True

    return False


# ═══════════════════════════════════════════════════════════════
# Dashboard entry — management panel, Voice FAB, config sensor, services
# ═══════════════════════════════════════════════════════════════

_SERVICE_SET_OPTION = "set_option"
_SERVICE_LIST_WORKSPACE = "list_workspace"
_SERVICE_READ_FILE = "read_workspace_file"
_SERVICE_WRITE_FILE = "write_workspace_file"
_SERVICE_GET_RULES = "get_rules"
_SERVICE_TOGGLE_RULE = "toggle_rule"
_SERVICE_ADD_RULE = "add_rule"
_SERVICE_DELETE_RULE = "delete_rule"
# ── G5 member permission / approval / audit services ──
_SERVICE_GET_MEMBERS = "get_members"
_SERVICE_ADD_MEMBER = "add_member"
_SERVICE_UPDATE_MEMBER = "update_member"
_SERVICE_REMOVE_MEMBER = "remove_member"
_SERVICE_GET_APPROVALS = "get_approvals"
_SERVICE_RESOLVE_APPROVAL = "resolve_approval"
_SERVICE_LIST_AUDIT = "list_audit"
_SERVICE_LIST_WHITELIST = "list_whitelist"
_SERVICE_ADD_WHITELIST = "add_whitelist"
_SERVICE_REMOVE_WHITELIST = "remove_whitelist"
# ── G2 scheduled task panel services ──
_SERVICE_GET_TASKS = "get_tasks"
_SERVICE_SET_TASK_ENABLED = "set_task_enabled"
_SERVICE_RUN_TASK_NOW = "run_task_now"
_SERVICE_DELETE_TASK = "delete_task"
_SERVICE_CREATE_TASK = "create_task"
# ── G3 passive learning confirmation services ──
_SERVICE_GET_PENDING_INSIGHTS = "get_pending_insights"
_SERVICE_CONFIRM_INSIGHT = "confirm_insight"
_SERVICE_DISMISS_INSIGHT = "dismiss_insight"
_SERVICE_BLOCK_INSIGHT = "block_insight"
_SERVICE_LIST_LEARNED = "list_learned"

_SET_OPTION_SCHEMA = vol.Schema({
    vol.Required("key"): cv.string,
    vol.Required("value"): vol.Any(bool, int, float, str, None),
})

_LIST_WORKSPACE_SCHEMA = vol.Schema({
    vol.Optional("category"): vol.In(["skills", "docs", "plugins", "all"]),
})

_READ_FILE_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,
})

_WRITE_FILE_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,
    vol.Required("content"): cv.string,
})

_RULE_CATEGORIES = ("always", "never", "reply", "custom")

_TOGGLE_RULE_SCHEMA = vol.Schema({
    vol.Required("rule_id"): cv.string,
    vol.Required("enabled"): bool,
})

_ADD_RULE_SCHEMA = vol.Schema({
    vol.Required("category"): vol.In(_RULE_CATEGORIES),
    vol.Required("description"): cv.string,
})

_DELETE_RULE_SCHEMA = vol.Schema({
    vol.Required("rule_id"): cv.string,
})

_MEMBER_ROLES = ("owner", "member")

_ADD_MEMBER_SCHEMA = vol.Schema({
    vol.Required("provider"): cv.string,
    vol.Required("ext_id"): cv.string,
    vol.Required("ha_user_id"): cv.string,
    vol.Optional("role", default="member"): vol.In(_MEMBER_ROLES),
    vol.Optional("allowed_areas", default=[]): vol.Any(list, None),
    vol.Optional("label", default=""): cv.string,
})

_UPDATE_MEMBER_SCHEMA = vol.Schema({
    vol.Optional("provider"): cv.string,
    vol.Optional("ext_id"): cv.string,
    vol.Optional("ha_user_id"): cv.string,
    vol.Optional("role"): vol.In(_MEMBER_ROLES),
    vol.Optional("allowed_areas"): vol.Any(list, None),
    vol.Optional("label"): cv.string,
})

_REMOVE_MEMBER_SCHEMA = vol.Schema({
    vol.Optional("provider"): cv.string,
    vol.Optional("ext_id"): cv.string,
    vol.Optional("ha_user_id"): cv.string,
})

_GET_APPROVALS_SCHEMA = vol.Schema({})

_RESOLVE_APPROVAL_SCHEMA = vol.Schema({
    vol.Required("approval_id"): cv.string,
    vol.Required("approved"): bool,
    vol.Optional("approver", default=""): cv.string,
})

_LIST_AUDIT_SCHEMA = vol.Schema({
    vol.Optional("limit", default=100): vol.Coerce(int),
})

_ADD_WHITELIST_SCHEMA = vol.Schema({
    vol.Required("user_key"): cv.string,
    vol.Required("action"): cv.string,
    vol.Optional("entity_id", default=""): cv.string,
    vol.Optional("area", default=""): cv.string,
})

_REMOVE_WHITELIST_SCHEMA = vol.Schema({
    vol.Required("user_key"): cv.string,
    vol.Required("action"): cv.string,
    vol.Optional("entity_id", default=""): cv.string,
    vol.Optional("area", default=""): cv.string,
})

_GET_TASKS_SCHEMA = vol.Schema({})

_SET_TASK_ENABLED_SCHEMA = vol.Schema({
    vol.Required("slug"): cv.string,
    vol.Required("enabled"): bool,
})

_RUN_TASK_NOW_SCHEMA = vol.Schema({
    vol.Required("slug"): cv.string,
})

_DELETE_TASK_SCHEMA = vol.Schema({
    vol.Required("slug"): cv.string,
})

_CREATE_TASK_SCHEMA = vol.Schema({
    vol.Required("title"): cv.string,
    vol.Optional("schedule", default=""): cv.string,
})

_GET_PENDING_INSIGHTS_SCHEMA = vol.Schema({})

_CONFIRM_INSIGHT_SCHEMA = vol.Schema({
    vol.Required("insight_id"): cv.string,
    vol.Optional("user_key", default=""): cv.string,
})

_DISMISS_INSIGHT_SCHEMA = vol.Schema({
    vol.Required("insight_id"): cv.string,
})

_BLOCK_INSIGHT_SCHEMA = vol.Schema({
    vol.Required("insight_id"): cv.string,
})

_LIST_LEARNED_SCHEMA = vol.Schema({})


def _get_claw_base(hass: HomeAssistant) -> str | None:
    """Find Claw Assistant base directory."""
    config_dir = hass.config.config_dir
    base = os.path.join(config_dir, "custom_components", "claw_assistant")
    if os.path.isdir(base):
        return base
    return None


def _list_skills(base: str) -> list[dict]:
    skills_dir = os.path.join(base, "data", "skills")
    items = []
    if not os.path.isdir(skills_dir):
        return items
    for entry in sorted(os.scandir(skills_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "skills/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            skill_md = os.path.join(entry.path, "SKILL.md")
            items.append({
                "name": entry.name,
                "path": "skills/" + entry.name,
                "type": "directory",
                "has_skill_md": os.path.isfile(skill_md),
            })
    return items


def _list_docs(base: str) -> list[dict]:
    ws_dir = os.path.join(base, "data", "workspace")
    items = []
    if not os.path.isdir(ws_dir):
        return items
    for entry in sorted(os.scandir(ws_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "workspace/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            for sub in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if sub.is_file() and sub.name.endswith('.md'):
                    items.append({
                        "name": entry.name + "/" + sub.name,
                        "path": "workspace/" + entry.name + "/" + sub.name,
                        "size": sub.stat().st_size,
                    })
    return items


def _list_plugins(base: str) -> list[dict]:
    plugins_dir = os.path.join(base, "plugins")
    items = []
    if not os.path.isdir(plugins_dir):
        return items
    for entry in sorted(os.scandir(plugins_dir), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith('.py') and not entry.name.startswith('__'):
            items.append({"name": entry.name[:-3], "path": "plugins/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "plugins/" + entry.name, "type": "directory"})
    return items


def _read_file(base: str, rel_path: str) -> dict:
    actual_rel = rel_path
    if rel_path.startswith("skills/"):
        actual_rel = "data/" + rel_path
    elif rel_path.startswith("workspace/"):
        actual_rel = "data/" + rel_path
    file_path = os.path.normpath(os.path.join(base, actual_rel))
    if not file_path.startswith(os.path.normpath(base)):
        return {"error": "path_traversal_denied"}
    if not os.path.isfile(file_path):
        return {"error": "file_not_found", "path": rel_path}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"path": rel_path, "content": content, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "path": rel_path}


def _write_file(base: str, rel_path: str, content: str) -> dict:
    actual_rel = rel_path
    if rel_path.startswith("skills/"):
        actual_rel = "data/" + rel_path
    elif rel_path.startswith("workspace/"):
        actual_rel = "data/" + rel_path
    file_path = os.path.normpath(os.path.join(base, actual_rel))
    if not file_path.startswith(os.path.normpath(base)):
        return {"error": "path_traversal_denied"}
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        LOGGER.info("Dashboard wrote file: %s (%d bytes)", rel_path, len(content))
        return {"success": True, "path": rel_path, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "path": rel_path}


async def _async_setup_dashboard_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setup dashboard entry: panel, Voice FAB, sensor, services."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, DASHBOARD_PLATFORMS)

    # ── Voice FAB static path ──
    js_path = Path(__file__).parent / "www" / "voice_fab.js"
    if js_path.is_file():
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/api/{DOMAIN}/voice_fab.js", str(js_path), cache_headers=False),
        ])

    # ── Service: set_option ──
    async def handle_set_option(call: ServiceCall) -> None:
        key = call.data["key"]
        value = call.data["value"]
        LOGGER.debug("Dashboard set_option: %s = %s", key, value)

        # Voice FAB toggle → inject/remove JS
        if key == "enable_voice_fab":
            vf_entries = hass.config_entries.async_entries("voice_fab")
            from homeassistant.config_entries import ConfigEntryState
            if any(e.state is ConfigEntryState.LOADED for e in vf_entries):
                await hass.services.async_call("voice_fab", "set_fab_enabled", {"enabled": bool(value)})
            else:
                if bool(value):
                    frontend.add_extra_js_url(hass, DASHBOARD_VFAB_URL)
                else:
                    frontend.remove_extra_js_url(hass, DASHBOARD_VFAB_URL)

        # Write option to claw_assistant config entry
        claw_entries = hass.config_entries.async_entries(DOMAIN)
        if not claw_entries:
            LOGGER.warning("No claw_assistant config entry found")
            return
        claw_entry = claw_entries[0]
        new_options = {**claw_entry.options, key: value}
        hass.config_entries.async_update_entry(claw_entry, options=new_options)
        for sensor in hass.data.get(DOMAIN, {}).get('_dashboard_sensors', []):
            sensor.async_write_ha_state()

    hass.services.async_register(DOMAIN, _SERVICE_SET_OPTION, handle_set_option, schema=_SET_OPTION_SCHEMA)

    # ── Service: refresh_sensor ──
    async def handle_refresh_sensor(call: ServiceCall) -> None:
        for sensor in hass.data.get(DOMAIN, {}).get('_dashboard_sensors', []):
            await sensor.async_update()
            sensor.async_write_ha_state()

    hass.services.async_register(DOMAIN, "refresh_sensor", handle_refresh_sensor)

    # ── Service: list_workspace ──
    async def handle_list_workspace(call: ServiceCall) -> ServiceResponse:
        category = call.data.get("category", "all")
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        result = {}
        if category in ("skills", "all"):
            result["skills"] = await hass.async_add_executor_job(_list_skills, base)
        if category in ("docs", "all"):
            result["docs"] = await hass.async_add_executor_job(_list_docs, base)
        if category in ("plugins", "all"):
            result["plugins"] = await hass.async_add_executor_job(_list_plugins, base)
        result["base_path"] = base
        return result

    hass.services.async_register(DOMAIN, _SERVICE_LIST_WORKSPACE, handle_list_workspace,
                                  schema=_LIST_WORKSPACE_SCHEMA, supports_response=True)

    # ── Service: read_workspace_file ──
    async def handle_read_file(call: ServiceCall) -> ServiceResponse:
        rel_path = call.data["path"]
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        return await hass.async_add_executor_job(_read_file, base, rel_path)

    hass.services.async_register(DOMAIN, _SERVICE_READ_FILE, handle_read_file,
                                  schema=_READ_FILE_SCHEMA, supports_response=True)

    # ── Service: write_workspace_file ──
    async def handle_write_file(call: ServiceCall) -> ServiceResponse:
        rel_path = call.data["path"]
        content = call.data["content"]
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        return await hass.async_add_executor_job(_write_file, base, rel_path, content)

    hass.services.async_register(DOMAIN, _SERVICE_WRITE_FILE, handle_write_file,
                                  schema=_WRITE_FILE_SCHEMA, supports_response=True)

    # ── Service: get_rules ──
    async def handle_get_rules(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.rules_store import list_rules
        rules = await hass.async_add_executor_job(list_rules)
        return {"rules": rules}

    hass.services.async_register(DOMAIN, _SERVICE_GET_RULES, handle_get_rules,
                                  supports_response=True)

    # ── Service: toggle_rule ──
    async def handle_toggle_rule(call: ServiceCall) -> ServiceResponse:
        rule_id = call.data["rule_id"]
        enabled = call.data["enabled"]
        from .runtime.storage.rules_store import list_rules, toggle_rule
        ok = await hass.async_add_executor_job(toggle_rule, rule_id, enabled)
        rules = await hass.async_add_executor_job(list_rules)
        return {"ok": ok, "rules": rules}

    hass.services.async_register(DOMAIN, _SERVICE_TOGGLE_RULE, handle_toggle_rule,
                                  schema=_TOGGLE_RULE_SCHEMA, supports_response=True)

    # ── Service: add_rule ──
    async def handle_add_rule(call: ServiceCall) -> ServiceResponse:
        category = call.data["category"]
        description = call.data["description"]
        from .runtime.storage.rules_store import add_rule, list_rules
        rule = await hass.async_add_executor_job(add_rule, category, description)
        rules = await hass.async_add_executor_job(list_rules)
        return {"ok": rule is not None, "rule": rule, "rules": rules}

    hass.services.async_register(DOMAIN, _SERVICE_ADD_RULE, handle_add_rule,
                                  schema=_ADD_RULE_SCHEMA, supports_response=True)

    # ── Service: delete_rule ──
    async def handle_delete_rule(call: ServiceCall) -> ServiceResponse:
        rule_id = call.data["rule_id"]
        from .runtime.storage.rules_store import delete_rule, list_rules
        ok = await hass.async_add_executor_job(delete_rule, rule_id)
        rules = await hass.async_add_executor_job(list_rules)
        return {"ok": ok, "rules": rules}

    hass.services.async_register(DOMAIN, _SERVICE_DELETE_RULE, handle_delete_rule,
                                  schema=_DELETE_RULE_SCHEMA, supports_response=True)

    # ── Service: get_members ──
    async def handle_get_members(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.user_mapping import MappingStore
        members = await hass.async_add_executor_job(MappingStore.load)
        return {"members": members}

    hass.services.async_register(DOMAIN, _SERVICE_GET_MEMBERS, handle_get_members,
                                  supports_response=True)

    # ── Service: add_member ──
    async def handle_add_member(call: ServiceCall) -> ServiceResponse:
        provider = call.data["provider"]
        ext_id = call.data["ext_id"]
        ha_user_id = call.data["ha_user_id"]
        role = call.data.get("role", "member")
        allowed_areas = call.data.get("allowed_areas") or []
        label = call.data.get("label", "")
        from .runtime.storage.user_mapping import MappingStore
        ok = await hass.async_add_executor_job(
            MappingStore.set, provider, ext_id, ha_user_id,
            role=role, allowed_areas=allowed_areas, label=label,
        )
        members = await hass.async_add_executor_job(MappingStore.load)
        return {"ok": bool(ok), "members": members}

    hass.services.async_register(DOMAIN, _SERVICE_ADD_MEMBER, handle_add_member,
                                  schema=_ADD_MEMBER_SCHEMA, supports_response=True)

    # ── Service: update_member ──
    async def handle_update_member(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.user_mapping import MappingStore
        kwargs = {
            "provider": call.data.get("provider"),
            "ext_id": call.data.get("ext_id"),
            "ha_user_id": call.data.get("ha_user_id"),
        }
        if "role" in call.data:
            kwargs["role"] = call.data["role"]
        if "allowed_areas" in call.data:
            kwargs["allowed_areas"] = call.data["allowed_areas"] or []
        if "label" in call.data:
            kwargs["label"] = call.data["label"]
        ok = await hass.async_add_executor_job(MappingStore.update_member, **kwargs)
        members = await hass.async_add_executor_job(MappingStore.load)
        return {"ok": bool(ok), "members": members}

    hass.services.async_register(DOMAIN, _SERVICE_UPDATE_MEMBER, handle_update_member,
                                  schema=_UPDATE_MEMBER_SCHEMA, supports_response=True)

    # ── Service: remove_member ──
    async def handle_remove_member(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.user_mapping import MappingStore
        provider = call.data.get("provider")
        ext_id = call.data.get("ext_id")
        ha_user_id = call.data.get("ha_user_id")
        if provider and ext_id:
            ok = await hass.async_add_executor_job(MappingStore.remove, provider, ext_id)
        elif ha_user_id:
            ok = await hass.async_add_executor_job(MappingStore.remove_by_user_key, ha_user_id)
        else:
            return {"ok": False, "error": "需要 provider+ext_id 或 ha_user_id"}
        members = await hass.async_add_executor_job(MappingStore.load)
        return {"ok": bool(ok), "members": members}

    hass.services.async_register(DOMAIN, _SERVICE_REMOVE_MEMBER, handle_remove_member,
                                  schema=_REMOVE_MEMBER_SCHEMA, supports_response=True)

    # ── Service: get_approvals ──
    async def handle_get_approvals(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage import approval_store
        pending = await hass.async_add_executor_job(approval_store.list_pending, hass)
        history = await hass.async_add_executor_job(approval_store.history, hass, 20)
        return {"pending": pending, "history": history}

    hass.services.async_register(DOMAIN, _SERVICE_GET_APPROVALS, handle_get_approvals,
                                  schema=_GET_APPROVALS_SCHEMA, supports_response=True)

    # ── Service: resolve_approval ──
    async def handle_resolve_approval(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage import approval_store
        approval_id = call.data["approval_id"]
        approved = call.data["approved"]
        approver = call.data.get("approver", "")
        ok = await hass.async_add_executor_job(
            approval_store.resolve, hass, approval_id, approved, approver or None
        )
        pending = await hass.async_add_executor_job(approval_store.list_pending, hass)
        return {"ok": bool(ok), "pending": pending}

    hass.services.async_register(DOMAIN, _SERVICE_RESOLVE_APPROVAL, handle_resolve_approval,
                                  schema=_RESOLVE_APPROVAL_SCHEMA, supports_response=True)

    # ── Service: list_audit ──
    async def handle_list_audit(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.audit_store import list_recent
        limit = call.data.get("limit", 100)
        entries = await hass.async_add_executor_job(list_recent, hass, limit)
        return {"entries": entries}

    hass.services.async_register(DOMAIN, _SERVICE_LIST_AUDIT, handle_list_audit,
                                  schema=_LIST_AUDIT_SCHEMA, supports_response=True)

    # ── Service: list_whitelist ──
    async def handle_list_whitelist(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.authorization import PolicyGate
        entries = await hass.async_add_executor_job(PolicyGate.list_whitelist)
        return {"entries": entries}

    hass.services.async_register(DOMAIN, _SERVICE_LIST_WHITELIST, handle_list_whitelist,
                                  supports_response=True)

    # ── Service: add_whitelist ──
    async def handle_add_whitelist(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.authorization import PolicyGate
        ok = await hass.async_add_executor_job(
            PolicyGate.add_whitelist_entry,
            call.data["user_key"], call.data["action"],
            call.data.get("entity_id") or None,
            call.data.get("area") or None,
        )
        entries = await hass.async_add_executor_job(PolicyGate.list_whitelist)
        return {"ok": bool(ok), "entries": entries}

    hass.services.async_register(DOMAIN, _SERVICE_ADD_WHITELIST, handle_add_whitelist,
                                  schema=_ADD_WHITELIST_SCHEMA, supports_response=True)

    # ── Service: remove_whitelist ──
    async def handle_remove_whitelist(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.authorization import PolicyGate
        ok = await hass.async_add_executor_job(
            PolicyGate.remove_whitelist_entry,
            call.data["user_key"], call.data["action"],
            call.data.get("entity_id") or None,
            call.data.get("area") or None,
        )
        entries = await hass.async_add_executor_job(PolicyGate.list_whitelist)
        return {"ok": bool(ok), "entries": entries}

    hass.services.async_register(DOMAIN, _SERVICE_REMOVE_WHITELIST, handle_remove_whitelist,
                                  schema=_REMOVE_WHITELIST_SCHEMA, supports_response=True)

    # ── Service: get_tasks (G2) ──
    async def handle_get_tasks(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.heartbeat_store import async_list_heartbeat_tasks
        tasks = await async_list_heartbeat_tasks(hass)
        return {"tasks": tasks}

    hass.services.async_register(DOMAIN, _SERVICE_GET_TASKS, handle_get_tasks,
                                  schema=_GET_TASKS_SCHEMA, supports_response=True)

    # ── Service: set_task_enabled (G2) ──
    async def handle_set_task_enabled(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.heartbeat_store import (
            async_list_heartbeat_tasks,
            async_set_enabled,
        )
        slug = call.data["slug"]
        enabled = call.data["enabled"]
        result = await async_set_enabled(hass, slug=slug, enabled=enabled)
        tasks = await async_list_heartbeat_tasks(hass)
        result["tasks"] = tasks
        return result

    hass.services.async_register(DOMAIN, _SERVICE_SET_TASK_ENABLED, handle_set_task_enabled,
                                  schema=_SET_TASK_ENABLED_SCHEMA, supports_response=True)

    # ── Service: run_task_now (G2) ──
    async def handle_run_task_now(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.heartbeat_store import (
            async_list_heartbeat_tasks,
            async_run_now,
        )
        slug = call.data["slug"]
        result = await async_run_now(hass, slug=slug)
        tasks = await async_list_heartbeat_tasks(hass)
        result["tasks"] = tasks
        return result

    hass.services.async_register(DOMAIN, _SERVICE_RUN_TASK_NOW, handle_run_task_now,
                                  schema=_RUN_TASK_NOW_SCHEMA, supports_response=True)

    # ── Service: delete_task (G2) ──
    async def handle_delete_task(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.heartbeat_store import (
            async_delete_heartbeat_task,
            async_list_heartbeat_tasks,
        )
        slug = call.data["slug"]
        await async_delete_heartbeat_task(hass, slug)
        tasks = await async_list_heartbeat_tasks(hass)
        return {"ok": True, "tasks": tasks}

    hass.services.async_register(DOMAIN, _SERVICE_DELETE_TASK, handle_delete_task,
                                  schema=_DELETE_TASK_SCHEMA, supports_response=True)

    # ── Service: create_task (G2, simplified) ──
    async def handle_create_task(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.heartbeat_store import (
            async_list_heartbeat_tasks,
            async_upsert_heartbeat_task,
        )
        title = call.data["title"]
        schedule = call.data.get("schedule", "")
        path = await async_upsert_heartbeat_task(
            hass,
            title=title,
            schedule=schedule,
            objective=title,
            steps=title,
        )
        tasks = await async_list_heartbeat_tasks(hass)
        return {"ok": True, "path": str(path), "tasks": tasks}

    hass.services.async_register(DOMAIN, _SERVICE_CREATE_TASK, handle_create_task,
                                  schema=_CREATE_TASK_SCHEMA, supports_response=True)

    # ── Service: get_pending_insights (G3) ──
    async def handle_get_pending_insights(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.pending_insights import InsightQueue
        pending = await hass.async_add_executor_job(InsightQueue.list_pending)
        learned = await hass.async_add_executor_job(InsightQueue.list_learned)
        return {"pending": pending, "learned": learned}

    hass.services.async_register(DOMAIN, _SERVICE_GET_PENDING_INSIGHTS, handle_get_pending_insights,
                                  schema=_GET_PENDING_INSIGHTS_SCHEMA, supports_response=True)

    # ── Service: confirm_insight (G3) ──
    async def handle_confirm_insight(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.pending_insights import InsightQueue
        insight_id = call.data["insight_id"]
        user_key = call.data.get("user_key", "")
        result = await InsightQueue.confirm(hass, insight_id, user_key=user_key)
        pending = await hass.async_add_executor_job(InsightQueue.list_pending)
        learned = await hass.async_add_executor_job(InsightQueue.list_learned)
        result["pending"] = pending
        result["learned"] = learned
        return result

    hass.services.async_register(DOMAIN, _SERVICE_CONFIRM_INSIGHT, handle_confirm_insight,
                                  schema=_CONFIRM_INSIGHT_SCHEMA, supports_response=True)

    # ── Service: dismiss_insight (G3) ──
    async def handle_dismiss_insight(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.pending_insights import InsightQueue
        insight_id = call.data["insight_id"]
        ok = await hass.async_add_executor_job(InsightQueue.dismiss, insight_id)
        pending = await hass.async_add_executor_job(InsightQueue.list_pending)
        return {"ok": ok, "pending": pending}

    hass.services.async_register(DOMAIN, _SERVICE_DISMISS_INSIGHT, handle_dismiss_insight,
                                  schema=_DISMISS_INSIGHT_SCHEMA, supports_response=True)

    # ── Service: block_insight (G3) ──
    async def handle_block_insight(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.pending_insights import InsightQueue
        insight_id = call.data["insight_id"]
        ok = await hass.async_add_executor_job(InsightQueue.block, insight_id)
        pending = await hass.async_add_executor_job(InsightQueue.list_pending)
        return {"ok": ok, "pending": pending}

    hass.services.async_register(DOMAIN, _SERVICE_BLOCK_INSIGHT, handle_block_insight,
                                  schema=_BLOCK_INSIGHT_SCHEMA, supports_response=True)

    # ── Service: list_learned (G3) ──
    async def handle_list_learned(call: ServiceCall) -> ServiceResponse:
        from .runtime.storage.pending_insights import InsightQueue
        learned = await hass.async_add_executor_job(InsightQueue.list_learned)
        return {"learned": learned}

    hass.services.async_register(DOMAIN, _SERVICE_LIST_LEARNED, handle_list_learned,
                                  schema=_LIST_LEARNED_SCHEMA, supports_response=True)

    entry.async_on_unload(entry.add_update_listener(_async_dashboard_update_listener))

    # ── Register dashboard panel ──
    await _register_dashboard_panel(hass)

    # ── Initial Voice FAB injection ──
    if js_path.is_file():
        async def _inject_voice_fab(_event=None):
            frontend.add_extra_js_url(hass, DASHBOARD_VFAB_URL)
            LOGGER.warning("Dashboard Voice FAB JS injected (deferred)")

        if hass.is_running:
            await _inject_voice_fab()
        else:
            hass.bus.async_listen_once("homeassistant_started", _inject_voice_fab)
            LOGGER.warning("Dashboard Voice FAB injection deferred to homeassistant_started")

    LOGGER.info("claw_assistant dashboard entry initialized")
    return True


async def _async_unload_dashboard_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload dashboard entry."""
    # Remove Voice FAB JS
    frontend.remove_extra_js_url(hass, DASHBOARD_VFAB_URL)
    vf_entries = hass.config_entries.async_entries("voice_fab")
    from homeassistant.config_entries import ConfigEntryState
    if any(e.state is ConfigEntryState.LOADED for e in vf_entries):
        await hass.services.async_call("voice_fab", "set_fab_enabled", {"enabled": False})

    unload_ok = await hass.config_entries.async_unload_platforms(entry, DASHBOARD_PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_dashboard_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _register_dashboard_panel(hass: HomeAssistant) -> None:
    """Register the built-in panel and API view for Claw dashboard."""
    from homeassistant.components import frontend as fe

    # Register dashboard JSON API view
    hass.http.register_view(ClawDashboardView)

    # Register sidebar panel (iframe)
    fe.async_register_built_in_panel(
        hass,
        "iframe",
        DASHBOARD_PANEL_TITLE,
        DASHBOARD_PANEL_ICON,
        "claw",
        config={"url": DASHBOARD_PANEL_URL},
        require_admin=True,
    )

    # Best-effort static path registration
    www_path = Path(__file__).parent / "www"
    if www_path.is_dir():
        try:
            await hass.http.async_register_static_paths([
                StaticPathConfig(f"/api/{DOMAIN}/www", str(www_path), True),
            ])
        except Exception as err:
            LOGGER.debug("Dashboard static path registration skipped: %s", err)

    LOGGER.info("Dashboard panel registered")


# ── Continue-conversation API (panel chat modal) ──
# These actions reuse the existing resume-history mechanism
# (runtime/history/chat_history_api.py) so the panel chat writes into and
# reads from the same conversation history as the parent-window dock.

_CONVERSATION_RESUME_SCHEMA = vol.Schema({
    vol.Required("conversation_id"): cv.string,
    vol.Required("window_id"): cv.string,
}, extra=vol.ALLOW_EXTRA)

_CONVERSATION_PROCESS_SCHEMA = vol.Schema({
    vol.Required("text"): cv.string,
    vol.Required("conversation_id"): cv.string,
    vol.Optional("agent_id"): vol.Any(cv.string, None),
    vol.Optional("language"): vol.Any(cv.string, None),
    vol.Optional("user_id"): vol.Any(cv.string, None),
}, extra=vol.ALLOW_EXTRA)

_CONVERSATION_RELEASE_SCHEMA = vol.Schema({}, extra=vol.ALLOW_EXTRA)


def _resolve_claw_agent_entity(hass) -> str | None:
    """Resolve the Claw conversation-agent entity id at runtime.

    The entity name is not stable (it can be ``conversation.claw_assistant``,
    ``conversation.claw_assistant_ai`` or ``conversation.<entry_id>``), so we
    match the ``entity == "claw_assistant.ai"`` attribute marker first (same
    convention as config_flow._get_agent_options), then fall back to the
    per-entry ``conversation.{entry_id}`` id, then to any conversation entity
    whose id contains "claw".
    """
    from homeassistant.components.conversation import async_get_agent

    # 1. Attribute marker set by FallbackConversationAgent.state_attributes.
    for entity_id in hass.states.async_entity_ids("conversation"):
        try:
            state = hass.states.get(entity_id)
            if state and state.attributes.get("entity") == "claw_assistant.ai":
                if async_get_agent(hass, entity_id) is not None:
                    return entity_id
        except Exception:
            continue

    # 2. Per-config-entry fallback id (same convention as delegation/executor).
    for entry in hass.config_entries.async_entries(DOMAIN):
        agent_id = f"conversation.{entry.entry_id}"
        try:
            if async_get_agent(hass, agent_id) is not None:
                return agent_id
        except Exception:
            continue

    # 3. Last-resort heuristic: any conversation entity containing "claw".
    for entity_id in hass.states.async_entity_ids("conversation"):
        if "claw" in entity_id.lower():
            try:
                if async_get_agent(hass, entity_id) is not None:
                    return entity_id
            except Exception:
                continue
    return None


class ClawDashboardView(HomeAssistantView):
    """Serve and handle the Claw dashboard (GET = HTML, POST = JSON API)."""

    url = DASHBOARD_API_URL
    name = f"{DOMAIN}:dashboard"
    requires_auth = False

    async def _get_sensor_data(self, hass):
        entity_id = hass.data.get(DOMAIN, {}).get(
            "claw_config_entity_id",
            f"sensor.{DOMAIN}_claw_config",
        )
        state = hass.states.get(entity_id)
        attrs = dict(state.attributes) if state else {}

        agents = sorted(
            e.split(".", 1)[1] for e in hass.states.async_entity_ids("conversation")
            if e != "conversation.claw_assistant"
        )

        return {
            "attrs": attrs,
            "agents": agents,
            "skills": attrs.get("skills", []),
            "docs": attrs.get("docs", []),
            "plugins": attrs.get("plugins", []),
            "user_mappings": attrs.get("user_mappings", []),
        }

    async def get(self, request):
        from aiohttp import web
        hass = request.app["hass"]

        www_file = Path(__file__).parent / "www" / "claw_control.html"
        if www_file.is_file():
            try:
                text = www_file.read_text(encoding="utf-8")
            except Exception:
                text = "<h1>Claw dashboard file could not be read</h1>"
            return web.Response(
                text=text,
                content_type="text/html",
                charset="utf-8",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        data = await self._get_sensor_data(hass)
        return web.json_response(data)

    async def post(self, request):
        """API endpoint: read state or set an option."""
        hass = request.app["hass"]
        from aiohttp import web

        try:
            body = await request.json()
        except Exception:
            data = await self._get_sensor_data(hass)
            return web.json_response(data)

        action = body.get("action", "read")

        if action == "set_option":
            key = body.get("key")
            value = body.get("value")
            if key:
                await hass.services.async_call(
                    DOMAIN, _SERVICE_SET_OPTION,
                    {"key": key, "value": value},
                    blocking=True,
                )
            data = await self._get_sensor_data(hass)
            return web.json_response({"ok": True, **data})

        if action == "read":
            data = await self._get_sensor_data(hass)
            return web.json_response(data)

        if action == "read_file":
            path = body.get("path", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_READ_FILE, {"path": path},
                    blocking=True, return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "write_file":
            path = body.get("path", "")
            content = body.get("content", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_WRITE_FILE, {"path": path, "content": content},
                    blocking=True, return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_workspace":
            category = body.get("category", "all")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_LIST_WORKSPACE, {"category": category},
                    blocking=True, return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "get_rules":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_GET_RULES, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"rules": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "toggle_rule":
            rule_id = body.get("rule_id", "")
            enabled = str(body.get("enabled", False)).lower() not in ("false", "0", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_TOGGLE_RULE,
                    {"rule_id": rule_id, "enabled": enabled},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "rules": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "add_rule":
            category = body.get("category", "always")
            description = body.get("description", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_ADD_RULE,
                    {"category": category, "description": description},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "rules": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "delete_rule":
            rule_id = body.get("rule_id", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_DELETE_RULE, {"rule_id": rule_id},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "rules": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── G5 members / approvals / audit / whitelist proxy ──
        if action == "get_members":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_GET_MEMBERS, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"members": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "add_member":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_ADD_MEMBER,
                    {
                        "provider": body.get("provider", ""),
                        "ext_id": body.get("ext_id", ""),
                        "ha_user_id": body.get("ha_user_id", ""),
                        "role": body.get("role", "member"),
                        "allowed_areas": body.get("allowed_areas") or [],
                        "label": body.get("label", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "members": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "update_member":
            try:
                payload = {
                    "provider": body.get("provider"),
                    "ext_id": body.get("ext_id"),
                    "ha_user_id": body.get("ha_user_id"),
                }
                if "role" in body:
                    payload["role"] = body.get("role")
                if "allowed_areas" in body:
                    payload["allowed_areas"] = body.get("allowed_areas") or []
                if "label" in body:
                    payload["label"] = body.get("label")
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_UPDATE_MEMBER, payload,
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "members": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "remove_member":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_REMOVE_MEMBER,
                    {
                        "provider": body.get("provider"),
                        "ext_id": body.get("ext_id"),
                        "ha_user_id": body.get("ha_user_id"),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "members": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "get_approvals":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_GET_APPROVALS, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"pending": [], "history": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "resolve_approval":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_RESOLVE_APPROVAL,
                    {
                        "approval_id": body.get("approval_id", ""),
                        "approved": str(body.get("approved", False)).lower()
                        not in ("false", "0", ""),
                        "approver": body.get("approver", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "pending": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_audit":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_LIST_AUDIT,
                    {"limit": int(body.get("limit", 100))},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"entries": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_whitelist":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_LIST_WHITELIST, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"entries": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "add_whitelist":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_ADD_WHITELIST,
                    {
                        "user_key": body.get("user_key", ""),
                        "action": body.get("action", ""),
                        "entity_id": body.get("entity_id", ""),
                        "area": body.get("area", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "entries": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "remove_whitelist":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_REMOVE_WHITELIST,
                    {
                        "user_key": body.get("user_key", ""),
                        "action": body.get("action", ""),
                        "entity_id": body.get("entity_id", ""),
                        "area": body.get("area", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "entries": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── G2 scheduled task panel proxy ──
        if action == "get_tasks":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_GET_TASKS, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"tasks": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "set_task_enabled":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_SET_TASK_ENABLED,
                    {
                        "slug": body.get("slug", ""),
                        "enabled": str(body.get("enabled", False)).lower()
                        not in ("false", "0", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "tasks": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "run_task_now":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_RUN_TASK_NOW,
                    {"slug": body.get("slug", "")},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "tasks": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "delete_task":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_DELETE_TASK,
                    {"slug": body.get("slug", "")},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "tasks": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "create_task":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_CREATE_TASK,
                    {
                        "title": body.get("title", ""),
                        "schedule": body.get("schedule", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "tasks": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── G3 passive learning proxy ──
        if action == "get_pending_insights":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_GET_PENDING_INSIGHTS, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"pending": [], "learned": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "confirm_insight":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_CONFIRM_INSIGHT,
                    {
                        "insight_id": body.get("insight_id", ""),
                        "user_key": body.get("user_key", ""),
                    },
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "pending": [], "learned": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "dismiss_insight":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_DISMISS_INSIGHT,
                    {"insight_id": body.get("insight_id", "")},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "pending": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "block_insight":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_BLOCK_INSIGHT,
                    {"insight_id": body.get("insight_id", "")},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"ok": False, "pending": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_learned":
            try:
                result = await hass.services.async_call(
                    DOMAIN, _SERVICE_LIST_LEARNED, {},
                    blocking=True, return_response=True,
                )
                return web.json_response(result or {"learned": []})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "reload_conversation":
            try:
                await hass.services.async_call("conversation", "reload", {}, blocking=True)
                data = await self._get_sensor_data(hass)
                return web.json_response({"ok": True, **data})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_users":
            users = []
            for user in await hass.auth.async_get_users():
                users.append({
                    "id": user.id,
                    "name": user.name or user.id[:12],
                    "is_active": user.is_active,
                })
            im_identities = []
            ih_state = hass.states.get("select.cn_im_hub_im_hub")
            if ih_state:
                im_identities = list(ih_state.attributes.get("options", []))
            for state in hass.states.async_all("select"):
                if "cn_im_hub" in state.entity_id and "channel" in state.entity_id:
                    im_identities.extend(list(state.attributes.get("options", [])))
            data = await self._get_sensor_data(hass)
            return web.json_response({
                "ok": True, "users": users,
                "im_identities": sorted(set(im_identities)) if im_identities else [],
                **data,
            })

        if action == "get_labels":
            import json as _json, os as _os
            trans_path = _os.path.join(
                hass.config.config_dir,
                "custom_components", "claw_assistant",
                "translations", "zh-Hans.json",
            )
            labels = {}
            if _os.path.isfile(trans_path):
                try:
                    with open(trans_path, "r", encoding="utf-8") as f:
                        trans = _json.load(f)
                    for step_name, step_data in trans.get("options", {}).get("step", {}).items():
                        d = step_data.get("data", {})
                        desc = step_data.get("data_description", {})
                        if d:
                            labels[step_name] = {"data": d, "desc": desc}
                    sel = trans.get("selector", {})
                    if sel:
                        labels["_selectors"] = sel
                except Exception:
                    pass
            return web.json_response({"ok": True, "labels": labels})

        if action == "get_option_schema":
            # Full schema generation — adapted from claw_plus
            import json as _json, os as _os
            import re as _re

            entity_id = hass.data.get(DOMAIN, {}).get(
                "claw_config_entity_id",
                f"sensor.{DOMAIN}_claw_config",
            )
            state = hass.states.get(entity_id)
            attrs = dict(state.attributes) if state else {}

            agents = sorted(
                e.split(".", 1)[1] for e in hass.states.async_entity_ids("conversation")
                if e != "conversation.claw_assistant"
            )
            agent_ids = [f"conversation.{a}" for a in agents]

            # Fallback structures
            _DOC_ORDER = ["AGENTS", "BOOTSTRAP", "HEARTBEAT", "IDENTITY", "MEMORY", "SOUL", "TOOLS", "USER"]
            _DOC_INFO = {
                "AGENTS": "定义各工作区文档的职责分工和 AI 的操作约束规则",
                "BOOTSTRAP": "首次引导流程，引导 AI 完成初始化对话",
                "HEARTBEAT": "定时跟进任务，AI 按设定周期自动执行跟进",
                "IDENTITY": "助手身份设定：名称、生物类型、性格标签",
                "MEMORY": "用户偏好记忆，长期记住的用户偏好和习惯",
                "SOUL": "语气与风格，定义 AI 的说话方式和性格基调",
                "TOOLS": "环境与工具备注，记录设备/服务信息",
                "USER": "用户基本信息，AI 据此个性化回复",
            }
            _SECTION_DEFS = {
                "conv_dialog": [
                    {"key": "reply_policy", "title": "回复策略", "collapsed": False,
                     "fields": ["conversation_mode", "enable_web_search"]},
                ],
                "conv_display": [
                    {"key": "chat_window", "title": "聊天窗口", "collapsed": False,
                     "fields": ["enable_sidebar_dock", "continuous_conversation", "enable_sound_notifications"]},
                    {"key": "message_display", "title": "消息显示", "collapsed": True,
                     "fields": ["enable_file_upload", "enable_rich_markdown", "enable_activity_tracking"]},
                    {"key": "diagnostics", "title": "诊断与工具", "collapsed": False,
                     "fields": ["enable_tool_details", "enable_tool_progress", "enable_context_status_bar"]},
                    {"key": "voice_fab", "title": "悬浮按钮", "collapsed": False,
                     "fields": ["enable_voice_fab"]},
                ],
                "conv_runtime": [
                    {"key": "tool_loop", "title": "工具循环", "collapsed": False,
                     "fields": ["max_tool_repeat", "identical_call_warn", "identical_call_stop"]},
                    {"key": "pipeline", "title": "流水线", "collapsed": True,
                     "fields": ["pipeline_timeout"]},
                ],
            }
            _FALLBACK_OPT_STEPS = {
                "init": {
                    "title": "Claw 配置",
                    "description": "配置 Claw Assistant 的各项能力",
                    "menu_options": {
                        "agent_settings": "配置智能代理 ｜ 首要、后备、总结链路",
                        "conversation_settings": "调整对话风格 ｜ 显示、流式与维度",
                        "conversation_manager": "对话管理 ｜ 历史记录与统计",
                        "workspace_editor": "编辑工作文档 ｜ 主提示词与技能资料",
                        "skill_editor": "管理安装技能 ｜ 动态查看与编辑",
                        "plugin_manager": "管理安装插件 ｜ Hermes 兼容插件",
                        "rules_editor": "硬边界规则 ｜ 强制约束与安全红线",
                        "members": "成员与权限 ｜ 角色分级与操作确认",
                        "scheduled_tasks": "定时任务 ｜ 自动跟进任务管理",
                        "learning": "学习 ｜ 被动学习与规律确认",
                    },
                },
                "scheduled_tasks": {
                    "title": "定时任务",
                    "description": "查看与管理自动跟进任务：暂停/恢复、立即运行、删除或新建。",
                    "data": {},
                },
                "learning": {
                    "title": "学习",
                    "description": "查看 AI 发现的规律候选，确认后写入长期记忆。",
                    "data": {},
                },
                "agent_settings": {
                    "title": "配置智能代理",
                    "description": "配置 AI 对话的调度链路，系统按顺序尝试各代理直到获得有效回复。",
                    "data": {
                        "primary_agent": "首要 AI 实体",
                        "fallback_agent": "后备 AI 实体",
                        "secondary_fallback_agent": "总结 AI 实体（可选）",
                    },
                    "data_description": {
                        "primary_agent": "默认优先响应的对话代理实体。",
                        "fallback_agent": "首要 AI 失败时自动接管的对话代理实体。",
                        "secondary_fallback_agent": "可选；配置后前两个 AI 分别回答，由该 AI 汇总。",
                    },
                },
                "conversation_settings": {"title": "调整对话风格", "menu_options": {
                    "conv_dialog": "对话策略 ｜ 回复如何生成",
                    "conv_display": "聊天体验 ｜ 窗口如何使用",
                    "conv_runtime": "执行控制 ｜ 任务如何运行",
                    "user_mapping": "用户关联 ｜ 已统一至成员与权限",
                }},
                "conversation_manager": {"title": "对话管理", "data": {}},
                "conv_dialog": {
                    "title": "回复策略",
                    "description": "设置 AI 生成回复时使用的策略。",
                    "data": {"conversation_mode": "对话模式", "enable_web_search": "联网搜索"},
                    "data_description": {
                        "conversation_mode": "精简模式只显示最终回复；标注来源会在回复前标注 AI 名称；多模型对比让所有 AI 同时回答。",
                        "enable_web_search": "启用后 AI 可在需要时自动补充联网搜索结果。",
                    },
                },
                "conv_display": {
                    "title": "显示设置",
                    "data": {
                        "enable_sidebar_dock": "AI 侧边栏",
                        "continuous_conversation": "连续聊天窗口",
                        "enable_sound_notifications": "提示音",
                        "enable_file_upload": "文件上传",
                        "enable_rich_markdown": "富文本增强",
                        "enable_activity_tracking": "操作感知",
                        "enable_tool_details": "工具调用详情",
                        "enable_tool_progress": "工具调用进度",
                        "enable_context_status_bar": "上下文状态栏",
                    },
                },
                "conv_runtime": {
                    "title": "运行时",
                    "data": {
                        "max_tool_repeat": "工具重复上限",
                        "identical_call_warn": "相同调用警告阈值",
                        "identical_call_stop": "相同调用终止阈值",
                        "pipeline_timeout": "等待时长 (分钟)",
                    },
                },
                "workspace_editor": {"title": "编辑工作文档", "menu_options": {
                    "ws_agents": "AGENTS ｜ 智能体协作指南",
                    "ws_bootstrap": "BOOTSTRAP ｜ 启动引导",
                    "ws_heartbeat": "HEARTBEAT ｜ 周期心跳",
                    "ws_identity": "IDENTITY ｜ 身份设定",
                    "ws_memory": "MEMORY ｜ 长期记忆",
                    "ws_soul": "SOUL ｜ 性格灵魂",
                    "ws_tools": "TOOLS ｜ 工具清单",
                    "ws_user": "USER ｜ 用户档案",
                }},
                "ws_agents": {"title": "AGENTS", "description": "定义各工作区文档的职责分工和 AI 的操作约束规则。", "data": {"content": "Markdown 编辑器"}},
                "ws_bootstrap": {"title": "BOOTSTRAP", "description": "首次引导流程，引导 AI 完成初始化对话。", "data": {"bootstrap_active": "启用引导流程", "content": "Markdown 编辑器"}, "data_description": {"bootstrap_active": "开启后 AI 下次对话重新执行引导流程。"}},
                "ws_heartbeat": {"title": "HEARTBEAT", "description": "定时跟进任务，AI 按设定周期自动执行。", "data": {"content": "Markdown 编辑器"}},
                "ws_identity": {"title": "IDENTITY", "description": "助手身份设定：名称、生物类型、性格标签和代表 Emoji。", "data": {"content": "Markdown 编辑器"}},
                "ws_memory": {"title": "MEMORY", "description": "用户偏好记忆，长期记住的用户偏好和习惯。", "data": {"content": "Markdown 编辑器"}},
                "ws_soul": {"title": "SOUL", "description": "语气与风格，定义 AI 的说话方式和性格基调。", "data": {"content": "Markdown 编辑器"}},
                "ws_tools": {"title": "TOOLS", "description": "环境与工具备注，记录设备/服务信息。", "data": {"content": "Markdown 编辑器"}},
                "ws_user": {"title": "USER", "description": "用户基本信息，AI 据此个性化回复。", "data": {"content": "Markdown 编辑器"}},
                "skill_editor": {"title": "管理安装技能", "data": {}},
                "plugin_manager": {"title": "管理安装插件", "data": {}},
                "rules_editor": {"title": "硬边界规则", "data": {}},
                "members": {"title": "成员与权限", "description": "成员角色分级、操作确认队列、审计与白名单", "data": {}},
                "user_mapping": {"title": "用户关联（已统一至成员与权限）", "description": "用户关联已统一到「成员与权限」页面，点击下方按钮前往管理", "menu_options": {
                    "um_pick_channel": "选择通道",
                    "um_pick_identity": "选择外部用户",
                    "um_pick_member": "选择 HA 成员",
                }},
                "um_pick_channel": {"title": "选择通道", "description": "第 1 步：选择已接入的 IM 通道。", "data": {"provider": "通道平台"}},
                "um_pick_identity": {"title": "选择外部用户", "description": "第 2 步：选择该通道下的外部用户。", "data": {"ext_id": "外部用户", "ext_id_manual": "手动填写 ID"}, "data_description": {"ext_id": "从 cn_im_hub 与近期对话自动识别", "ext_id_manual": "选手动输入时填写，不含通道前缀"}},
                "um_pick_member": {"title": "选择 HA 成员", "description": "第 3 步：选择要绑定的 HA 家庭成员。绑定后可到面板「成员与权限」完善角色和区域设置。", "data": {"ha_user": "关联到", "role": "角色"}, "data_description": {"ha_user": "选择已创建的 HA 用户账号", "role": "owner 全部放行；member 分级确认"}},
                "um_remove": {"title": "删除关联", "description": "解除外部身份与家庭成员的绑定。如需管理成员权限，请到面板「成员与权限」。", "data": {"remove_key": "要删除的映射"}},
            }
            _FALLBACK_SELECTORS = {
                "conversation_mode": {
                    "options": {
                        "no_name": "精简模式",
                        "add_name": "标注来源",
                        "detailed": "多模型对比",
                    }
                }
            }
            FIELD_TYPE_HINTS = {
                "primary_agent": "agent_select",
                "fallback_agent": "agent_select",
                "secondary_fallback_agent": "agent_select",
                "conversation_mode": "mode_select",
                "enable_web_search": "toggle",
                "enable_sidebar_dock": "toggle",
                "continuous_conversation": "toggle",
                "enable_sound_notifications": "toggle",
                "enable_context_status_bar": "toggle",
                "enable_file_upload": "toggle",
                "enable_rich_markdown": "toggle",
                "enable_activity_tracking": "toggle",
                "enable_tool_details": "toggle",
                "enable_tool_progress": "toggle",
                "bootstrap_active": "toggle",
                "max_tool_repeat": "slider",
                "identical_call_warn": "slider",
                "identical_call_stop": "slider",
                "pipeline_timeout": "slider",
                "enable_voice_fab": "toggle",
            }
            _PAGE_DESC = {
                "agent_settings": "配置 AI 对话的调度链路，系统按顺序尝试各代理直到获得有效回复。",
                "conversation_settings": "调整 AI 的对话策略、聊天体验、执行控制与用户关联。",
                "conversation_manager": "查看所有对话历史记录，搜索、浏览和删除对话。",
                "workspace_editor": "**使用说明：**\n这些文档定义了 AI 助手的**核心人格与行为**，使用 Markdown 格式编写。",
                "skill_editor": "选择一个技能查看其 Markdown 全文，或直接在下一步进行编辑/删除。",
                "plugin_manager": "感谢 Hermes Agent 项目，本功能特别支持 **Hermes 兼容的扩展模块**。",
                "rules_editor": "**硬边界规则**\n规则会注入 AI 的 system prompt，启用后 AI 必须无条件遵守。\n「始终遵循」是正向要求；「绝对禁止」是安全红线；「回复要求」约束回复格式。",
                "members": "**成员与权限**\nowner 无感放行全部操作；member/shadow 的 R0/R1 只读与可逆控制在允许区域内放行，R2 系统变更需人工确认，R3 破坏性操作默认拒绝。所有决策写入审计日志。",
                "scheduled_tasks": "**定时任务**\n查看与管理自动跟进任务：暂停/恢复、立即运行、删除或新建。任务由心跳 Ticker 按周期自动执行。",
                "learning": "**被动学习**\n查看 AI 从日常对话中发现的规律候选（如定时关灯），确认后写入长期记忆；含实体/操作的建议同时加入免确认白名单。",
                "dynamic": "**操作历史与审计日志**\n记录每次 AI 工具调用的策略决策（ALLOW / CONFIRM / DENY）与人工批准结果，最新在前，可按决策类型筛选。",
                "user_mapping": "用户关联已统一到「成员与权限」页面。将飞书、微信、QQ 等 IM 通道里的外部身份，绑定到 HA 家庭成员。",
                "conv_dialog": "设置 AI 生成回复时使用的策略。",
                "conv_display": "设置聊天界面的交互和显示方式。",
                "conv_runtime": "设置 AI 执行任务时的控制规则。",
                "ws_agents": "**文件角色与操作规范**\n定义各工作区文档的职责分工和 AI 的操作约束规则。",
                "ws_bootstrap": "**首次引导流程**\n仅在首次启动时生效，引导 AI 完成初始化对话。",
                "ws_heartbeat": "**定时跟进任务**\n定义心跳机制的行为规则，AI 会按设定周期自动执行跟进任务。",
                "ws_identity": "**助手身份设定**\n定义 AI 助手的名称、生物类型、性格标签和代表 Emoji 等身份信息。",
                "ws_memory": "**用户偏好记忆**\n存储 AI 需要长期记住的用户偏好和习惯，每行一条。",
                "ws_soul": "**语气与风格**\n定义 AI 助手的说话方式和性格基调。",
                "ws_tools": "**环境与工具备注**\n记录设备信息、服务名称等，AI 执行操作时自动参考。",
                "ws_user": "**用户基本信息**\n存储用户的基本资料，AI 会据此个性化回复。",
                "um_pick_channel": "第 1 步 / 共 3 步：选择已在 cn_im_hub 中接入的 IM 通道。",
                "um_pick_identity": "第 2 步 / 共 3 步：选择该通道下的外部用户。",
                "um_pick_member": "第 3 步 / 共 3 步：选择要绑定的 HA 家庭成员。",
                "um_remove": "删除通道绑定，解除外部身份与家庭成员的关联。",
            }
            _CUSTOM_FIELD_DESC = {
                "enable_voice_fab": ("语音助手悬浮按钮", "在 HA 所有页面显示可拖动的语音助手按钮，轻触唤醒语音对话。"),
            }

            trans = {}
            trans_path = ""
            trans_found = False

            def _read_trans():
                nonlocal trans_path, trans_found
                cands = [
                    _os.path.join(hass.config.config_dir, "custom_components",
                                  "claw_assistant", "translations", "zh-Hans.json"),
                    "/config/custom_components/claw_assistant/translations/zh-Hans.json",
                ]
                for p in cands:
                    if _os.path.isfile(p):
                        trans_path = p
                        trans_found = True
                        with open(p, encoding="utf-8") as _f:
                            return _json.loads(_f.read())
                trans_path = cands[0]
                return {}

            try:
                trans = await hass.async_add_executor_job(_read_trans)
            except Exception:
                trans = {}

            opt_steps = trans.get("options", {}).get("step", {})
            if not opt_steps:
                opt_steps = _FALLBACK_OPT_STEPS

            field_meta = {}
            for step_name, step_data in opt_steps.items():
                d = step_data.get("data", {})
                desc = step_data.get("data_description", {})
                if d:
                    field_meta[step_name] = {"data": d, "desc": desc}
            field_meta["_selectors"] = trans.get("selector", {}) or _FALLBACK_SELECTORS

            for fkey, (label, desc_text) in _CUSTOM_FIELD_DESC.items():
                for step_name, meta in list(field_meta.items()):
                    if fkey in meta.get("data", {}):
                        if desc_text:
                            meta["desc"][fkey] = desc_text
                        break
                else:
                    for step_name in _SECTION_DEFS:
                        for sec in _SECTION_DEFS[step_name]:
                            if fkey in sec.get("fields", []):
                                fm = field_meta.setdefault(step_name, {"data": {}, "desc": {}})
                                fm["data"].setdefault(fkey, label)
                                fm["desc"][fkey] = desc_text
                                break

            NAV_KEYS = {"back", "save_and_exit", "next_step",
                        "provider", "remove_key", "skill_slug", "plugin_key"}

            def _split_menu(val):
                if "｜" in val:
                    t, s = val.split("｜", 1)
                    return t.strip(), s.strip()
                return val.strip(), ""

            def _build_node(key, menu_val=None):
                s = opt_steps.get(key, {})
                mo = s.get("menu_options")
                node = {"key": key}
                if menu_val is not None:
                    title, subtitle = _split_menu(menu_val)
                    node["title"] = title
                    node["subtitle"] = subtitle
                else:
                    node["title"] = s.get("title", key)
                    node["subtitle"] = ""
                native_desc = s.get("description")
                desc = native_desc or _PAGE_DESC.get(key, "")
                if _re.search(r"\{[a-zA-Z_]+\}", desc or ""):
                    fb = _PAGE_DESC.get(key, "")
                    if fb:
                        desc = fb
                node["description"] = desc
                if mo:
                    node["children"] = {k: _build_node(k, mo[k]) for k in mo}
                else:
                    data = s.get("data", {})
                    node["fields"] = [k for k in data if k not in NAV_KEYS]
                return node

            menu_tree = _build_node("init")

            option_types = {}
            slider_config = {}
            SKIP_KEYS = {"friendly_name", "icon", "skills", "docs", "plugins",
                         "user_mappings", "skills_count", "docs_count",
                         "plugins_count", "user_mappings_count"}

            for key, value in attrs.items():
                if key in SKIP_KEYS:
                    continue
                if isinstance(value, bool):
                    otype = "toggle"
                elif isinstance(value, str) and key == "conversation_mode":
                    otype = "mode_select"
                elif isinstance(value, str) and value in agent_ids:
                    otype = "agent_select"
                elif isinstance(value, str):
                    otype = "display"
                elif isinstance(value, (int, float)):
                    otype = "slider"
                    if key == "pipeline_timeout":
                        slider_config[key] = {"min": 5, "max": 360, "div60": True}
                    elif key == "max_tool_repeat":
                        slider_config[key] = {"min": 3, "max": 50}
                    elif isinstance(value, int) and value > 0:
                        slider_config[key] = {"min": 1, "max": max(value * 3, 30)}
                    else:
                        slider_config[key] = {"min": 0, "max": 100}
                else:
                    continue
                option_types[key] = otype

            all_fields = []
            def _collect(node):
                if "fields" in node:
                    all_fields.extend(node["fields"])
                for c in (node.get("children") or {}).values():
                    _collect(c)
            _collect(menu_tree)
            for _secs in _SECTION_DEFS.values():
                for _sec in _secs:
                    all_fields.extend(_sec.get("fields", []))

            for key in all_fields:
                if key in option_types:
                    continue
                hint = FIELD_TYPE_HINTS.get(key)
                if hint:
                    option_types[key] = hint
                    if hint == "slider" and key not in slider_config:
                        slider_config[key] = {"min": 1, "max": 50}
                else:
                    option_types[key] = "display"

            _ca_version = "unknown"
            _ca_path = _os.path.join(hass.config.config_dir,
                                     "custom_components", "claw_assistant", "manifest.json")
            if _os.path.isfile(_ca_path):
                try:
                    with open(_ca_path, encoding="utf-8") as _f:
                        _ca_version = _json.load(_f).get("version", "unknown")
                except Exception:
                    pass

            return web.json_response({
                "ok": True,
                "menu_tree": menu_tree,
                "field_meta": field_meta,
                "labels": field_meta,
                "option_types": option_types,
                "slider_config": slider_config,
                "sections": _SECTION_DEFS,
                "agents": agents,
                "selectors": trans.get("selector", {}) or _FALLBACK_SELECTORS,
                "claw_assistant_version": _ca_version,
                "_debug_trans_path": trans_path,
                "_debug_trans_found": trans_found,
            })

        # ── Skills ──
        if action == "get_skills":
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import (
                    list_installed_skills, _INTERNAL_SKILL_SLUGS,
                )
                skills = await hass.async_add_executor_job(list_installed_skills)
                items = []
                for s in skills:
                    slug = s.get("slug") or s.get("file") or s.get("name") or ""
                    if slug in _INTERNAL_SKILL_SLUGS:
                        continue
                    items.append({
                        "name": s.get("name", slug),
                        "slug": slug,
                        "file": s.get("file", slug + ".md"),
                        "chars": int(s.get("chars", 0) or 0),
                        "description": s.get("description", ""),
                        "version": s.get("version", ""),
                        "category": s.get("category", ""),
                    })
                return web.json_response({"ok": True, "skills": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "read_skill":
            slug = body.get("slug", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import (
                    async_get_installed_skill, async_read_skill_markdown,
                )
                meta = await async_get_installed_skill(hass, slug)
                raw = await async_read_skill_markdown(hass, slug)
                return web.json_response({
                    "ok": True, "slug": slug,
                    "name": meta.get("name", slug),
                    "file": meta.get("file", slug + ".md"),
                    "description": meta.get("description", ""),
                    "chars": len(raw), "content": raw,
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "save_skill":
            slug = body.get("slug", "")
            content = body.get("content", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import async_install_skill
                await async_install_skill(hass, slug, content, overwrite=True,
                                          actor="dashboard", reason="edited via control panel")
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "delete_skill":
            slug = body.get("slug", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import async_delete_skill
                await async_delete_skill(hass, slug, actor="dashboard", reason="deleted via control panel")
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── Plugins ──
        if action == "get_plugins":
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import list_installed_plugins
                plugins = await hass.async_add_executor_job(list_installed_plugins)
                items = []
                for p in plugins:
                    valid = p.get("valid", True)
                    loaded = p.get("loaded", False)
                    load_error = p.get("load_error")
                    if not valid:
                        status = "INVALID"
                    elif loaded:
                        status = "RUNNING"
                    elif load_error:
                        status = "FAILED"
                    else:
                        status = "STOPPED"
                    items.append({
                        "name": p.get("name", ""),
                        "key": p.get("key", ""),
                        "version": p.get("version", ""),
                        "description": p.get("description", ""),
                        "author": p.get("author", ""),
                        "valid": valid, "loaded": loaded,
                        "load_error": load_error,
                        "tools_count": p.get("tools_count", 0),
                        "status": status,
                    })
                return web.json_response({"ok": True, "plugins": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_enable":
            key = body.get("key", "")
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_load_plugin
                r = await hass.async_add_executor_job(hot_load_plugin, hass, key)
                return web.json_response({"ok": bool(r.get("success", False)), "result": r})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_disable":
            key = body.get("key", "")
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_unload_plugin
                r = await hass.async_add_executor_job(hot_unload_plugin, hass, key)
                return web.json_response({"ok": bool(r.get("success", False)), "result": r})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_delete":
            key = body.get("key", "")
            try:
                import shutil
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_unload_plugin
                from custom_components.claw_assistant.plugins import plugins_dir
                await hass.async_add_executor_job(hot_unload_plugin, hass, key)
                pp = await hass.async_add_executor_job(plugins_dir)
                await hass.async_add_executor_job(shutil.rmtree, str(pp / key), True)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── Workspace docs ──
        if action == "get_docs":
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import get_workspace_doc
                items = []
                for name in _DOC_ORDER:
                    doc = await hass.async_add_executor_job(get_workspace_doc, name)
                    md = doc.get("markdown") or ""
                    items.append({
                        "name": name,
                        "title": name.capitalize(),
                        "desc": _DOC_INFO.get(name, ""),
                        "has_content": bool(md.strip()),
                        "chars": len(md),
                        "active": doc.get("active", True),
                    })
                return web.json_response({"ok": True, "docs": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "read_doc":
            name = body.get("name", "")
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import get_workspace_doc
                doc = await hass.async_add_executor_job(get_workspace_doc, name)
                return web.json_response({
                    "ok": True, "name": name,
                    "content": doc.get("markdown", ""),
                    "active": doc.get("active", True),
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "save_doc":
            name = body.get("name", "")
            content = body.get("content", "")
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import async_save_workspace_doc
                await async_save_workspace_doc(hass, name, content)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "set_bootstrap":
            active = bool(body.get("active", False))
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import async_set_bootstrap_active
                await async_set_bootstrap_active(hass, active)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── User mappings ──
        if action == "get_mappings":
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                mappings = await hass.async_add_executor_job(MappingStore.load)
                return web.json_response({"ok": True, "mappings": mappings})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "add_mapping":
            provider = body.get("provider", "")
            ext_id = body.get("ext_id", "")
            ha_user_id = body.get("ha_user_id", "")
            role = body.get("role", "member")
            allowed_areas = body.get("allowed_areas") or []
            label = body.get("label", "")
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                ok = await hass.async_add_executor_job(
                    MappingStore.set, provider, ext_id, ha_user_id,
                    role=role, allowed_areas=allowed_areas, label=label,
                )
                return web.json_response({"ok": bool(ok)})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "remove_mapping":
            provider = body.get("provider", "")
            ext_id = body.get("ext_id", "")
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                ok = await hass.async_add_executor_job(MappingStore.remove, provider, ext_id)
                return web.json_response({"ok": bool(ok)})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "get_mapping_options":
            provider = body.get("provider") or None
            try:
                from custom_components.claw_assistant.runtime.storage.im_channel_helpers import (
                    get_configured_provider_keys, collect_provider_targets, build_ext_id_options,
                )
                provider_keys = await get_configured_provider_keys(hass)
                targets = await collect_provider_targets(hass)
                ext_options = {}
                if provider:
                    ext_options = await hass.async_add_executor_job(
                        lambda: build_ext_id_options(hass, provider, targets, manual_label="手动填写 ID"))
                users = []
                for u in await hass.auth.async_get_users():
                    users.append({"id": u.id, "name": u.name or u.id[:12], "is_active": u.is_active})
                return web.json_response({
                    "ok": True, "provider_keys": provider_keys,
                    "targets": targets, "ext_options": ext_options, "users": users,
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── Conversation management ──
        if action == "list_conversations":
            try:
                from .conversation_utils import get_conversation_history
                history = get_conversation_history()
                conv_ids = history.list_conversation_ids()
                conversations = []
                for cid in conv_ids:
                    turns = history.get_history(cid)
                    title = history.get_conversation_title(cid)
                    conversations.append({
                        "id": cid,
                        "title": title,
                        "turn_count": len(turns),
                        "last_touched": history._last_touched.get(cid, 0),
                        "first_message": turns[0].user_message[:120] if turns else "",
                    })
                conversations.sort(key=lambda c: c["last_touched"], reverse=True)
                stats = history.get_stats()
                return web.json_response({"ok": True, "conversations": conversations, "stats": stats})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "get_conversation":
            conv_id = body.get("conversation_id", "")
            try:
                from .conversation_utils import get_conversation_history
                history = get_conversation_history()
                turns = history.get_history(conv_id)
                title = history.get_conversation_title(conv_id)
                return web.json_response({
                    "ok": True,
                    "conversation_id": conv_id,
                    "title": title,
                    "turn_count": len(turns),
                    "turns": [
                        {
                            "user_message": t.user_message,
                            "assistant_response": (t.assistant_response[:2000] + "...")
                                if len(t.assistant_response) > 2000 else t.assistant_response,
                            "timestamp": t.timestamp,
                            "tool_calls": t.tool_calls,
                            "metadata": t.metadata,
                        }
                        for t in turns
                    ],
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "delete_conversation":
            conv_id = body.get("conversation_id", "")
            try:
                from .conversation_utils import get_conversation_history
                history = get_conversation_history()
                removed = history.clear(conv_id)
                return web.json_response({"ok": True, "removed": removed})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "clear_all_conversations":
            try:
                from .conversation_utils import get_conversation_history
                history = get_conversation_history()
                removed = history.clear()
                return web.json_response({"ok": True, "removed": removed})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ── Continue conversation (panel chat modal) ──
        if action == "conversation_resume":
            try:
                body = _CONVERSATION_RESUME_SCHEMA(body)
            except Exception as e:
                return web.json_response({"ok": False, "error": f"invalid_request: {e}"})
            conversation_id = str(body.get("conversation_id", "") or "")
            window_id = str(body.get("window_id", "") or "")
            try:
                from .conversation_utils import get_conversation_history
                from .runtime.core.state import get_conversation_status
                from .runtime.history.chat_history_api import (
                    _HISTORY_WINDOW_ID_KEY,
                    _RESUME_HISTORY_ID_KEY,
                    _RESUME_HISTORY_WINDOW_ID_KEY,
                )
                history = get_conversation_history()
                turns = history.get_history(conversation_id)
                if not turns:
                    return web.json_response({"ok": False, "error": "No history found for this conversation"})
                # Bind the panel window to this conversation so the next
                # conversation_process write is anchored to this history id.
                status = get_conversation_status(hass)
                status[_HISTORY_WINDOW_ID_KEY] = window_id
                status[_RESUME_HISTORY_ID_KEY] = conversation_id
                status[_RESUME_HISTORY_WINDOW_ID_KEY] = window_id
                return web.json_response({
                    "ok": True,
                    "conversation_id": conversation_id,
                    "turn_count": len(turns),
                })
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})

        if action == "conversation_process":
            try:
                body = _CONVERSATION_PROCESS_SCHEMA(body)
            except Exception as e:
                return web.json_response({"ok": False, "error": f"invalid_request: {e}"})
            text = str(body.get("text", "") or "").strip()
            conversation_id = str(body.get("conversation_id", "") or "")
            agent_id = body.get("agent_id") or None
            language = body.get("language") or None
            user_id = body.get("user_id") or None
            if not text:
                return web.json_response({"ok": False, "error": "empty_text"})
            try:
                from homeassistant.components import conversation as ha_conversation
                from homeassistant.core import Context

                resolved_agent_id = agent_id or _resolve_claw_agent_entity(hass)
                if not resolved_agent_id:
                    return web.json_response({"ok": False, "error": "agent_not_found"})
                if language is None:
                    language = hass.config.language
                context = Context(user_id=user_id) if user_id else Context()
                result = await ha_conversation.async_converse(
                    hass,
                    text=text,
                    conversation_id=conversation_id,
                    context=context,
                    language=language,
                    agent_id=resolved_agent_id,
                    device_id=None,
                    satellite_id=None,
                )
                reply = ""
                if result and result.response:
                    resp = result.response
                    speech = getattr(resp, "speech", None)
                    if isinstance(speech, dict):
                        plain = speech.get("plain") or {}
                        reply = str(plain.get("speech") or "")
                return web.json_response({
                    "ok": True,
                    "reply": reply,
                    "conversation_id": conversation_id,
                    "agent_id": resolved_agent_id,
                })
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})

        if action == "conversation_release":
            try:
                _CONVERSATION_RELEASE_SCHEMA(body)
            except Exception as e:
                return web.json_response({"ok": False, "error": f"invalid_request: {e}"})
            try:
                from .runtime.history.chat_history_api import clear_resume_history_binding
                clear_resume_history_binding(hass)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"ok": False, "error": str(e)})

        return web.json_response({"error": "unknown_action"})
