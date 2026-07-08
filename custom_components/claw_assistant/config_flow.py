from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)

from .const import (
    CONF_CONVERSATION_MODE,
    CONF_CONTINUOUS_CONVERSATION,
    CONF_ENABLE_ACTIVITY_TRACKING,
    CONF_ENABLE_AI_SUMMARY,
    CONF_ENABLE_CONTEXT_STATUS_BAR,
    CONF_ENABLE_FILE_UPLOAD,
    CONF_ENABLE_RICH_MARKDOWN,
    CONF_ENABLE_SIDEBAR_DOCK,
    CONF_ENABLE_SOUND_NOTIFICATIONS,
    CONF_ENABLE_STREAMING_EFFECT,
    CONF_ENABLE_TOOL_DETAILS,
    CONF_ENABLE_TOOL_PROGRESS,
    CONF_ENABLE_WEB_SEARCH,
    CONF_ERROR_RESPONSES,
    CONF_FALLBACK_AGENT,
    CONF_IDENTICAL_CALL_STOP,
    CONF_IDENTICAL_CALL_WARN,
    CONF_MAX_TOOL_REPEAT,
    CONF_PIPELINE_TIMEOUT,
    CONF_PRIMARY_AGENT,
    CONF_SECONDARY_FALLBACK_AGENT,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
    DEFAULT_CONVERSATION_MODE,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_IDENTICAL_CALL_STOP,
    DEFAULT_IDENTICAL_CALL_WARN,
    DEFAULT_MAX_TOOL_REPEAT,
    DEFAULT_PIPELINE_TIMEOUT,
    DEFAULT_PRIMARY_AGENT,
    DOMAIN,
)

LOGGER = logging.getLogger(__name__)

_HTML_BLOCK_RE = re.compile(
    r"<(script|style|head|noscript|svg|iframe)[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>]*>", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _sanitize_skill_markdown_for_display(text: str) -> str:
    if not text:
        return ""
    cleaned = _HTML_COMMENT_RE.sub("", text)
    cleaned = _HTML_DOCTYPE_RE.sub("", cleaned)
    cleaned = _HTML_BLOCK_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    return cleaned.strip()


_REMOVED_OPTION_KEYS = (
    CONF_ERROR_RESPONSES,
    CONF_ENABLE_AI_SUMMARY,
    "speaker_entity",
    "speaker_type",
    "tts_service",
)

@callback
def _get_agent_options(hass: HomeAssistant) -> list[dict[str, str]]:
    ent_reg = er.async_get(hass)
    own_entry_ids = {
        e.entry_id for e in hass.config_entries.async_entries(DOMAIN)
    }
    agents: list[dict[str, str]] = []
    for entity_id in hass.states.async_entity_ids("conversation"):
        try:
            if entity_id == "conversation.home_assistant":
                continue
            reg = ent_reg.async_get(entity_id)
            if reg and (reg.platform == DOMAIN or reg.config_entry_id in own_entry_ids):
                continue
            state = hass.states.get(entity_id)
            if state and state.attributes.get("entity") == "claw_assistant.ai":
                continue
            label = (state.attributes.get("friendly_name") if state else None) or entity_id.split('.')[-1]
            agents.append({"value": entity_id, "label": str(label)})
        except Exception:
            continue
    return agents


@callback
def _get_own_entity_ids(hass: HomeAssistant) -> set[str]:
    ent_reg = er.async_get(hass)
    own_entry_ids = {
        e.entry_id for e in hass.config_entries.async_entries(DOMAIN)
    }
    own: set[str] = set()
    for entity_id in hass.states.async_entity_ids("conversation"):
        reg = ent_reg.async_get(entity_id)
        if reg and (reg.platform == DOMAIN or reg.config_entry_id in own_entry_ids):
            own.add(entity_id)
    return own


def _agent_selector(hass: HomeAssistant) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=_get_agent_options(hass), mode=SelectSelectorMode.DROPDOWN)
    )

_IM_PREFIXES = ("feishu:", "wechat:", "dingtalk:", "qq:", "wecom:", "xiaoyi:")


@callback
def _get_known_im_identifiers(hass: HomeAssistant) -> dict[str, str]:
    """Collect known external IM user identifiers.

    Sources: existing mappings + active conversation history.
    Returns dict {ext_id: display_label} for vol.In().
    Labels are language-neutral (just the raw identifiers) because the
    provider is already selected in a separate dropdown above.
    """
    from .runtime.storage.user_mapping import MappingStore
    from .conversation_utils import get_conversation_history

    seen: dict[str, str] = {}

    # 1) Already-mapped identifiers — key by ext_id directly
    for m in MappingStore._load():
        e = m.get("ext_id", "")
        if e and e not in seen:
            seen[e] = e

    # 2) Active conversation IDs with IM prefix
    history = get_conversation_history()
    for conv_id in list(history._histories.keys()):
        conv_id_lower = conv_id.lower()
        for prefix in _IM_PREFIXES:
            if conv_id_lower.startswith(prefix):
                rest = conv_id[len(prefix):]
                parts = rest.split(":", 1)
                ext_id = parts[-1]  # last segment is the user identifier
                if ext_id and ext_id not in seen:
                    seen[ext_id] = ext_id
                break

    if not seen:
        seen["__manual__"] = "__manual_input__"
    return seen


class ClawAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._title: str = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        self._title = "Claw Assistant"
        if user_input is not None:
            return await self.async_step_agent_settings()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )

    async def async_step_agent_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            primary = user_input.get(CONF_PRIMARY_AGENT)
            if not primary:
                errors[CONF_PRIMARY_AGENT] = "invalid_agent"
            if not errors:
                return self.async_create_entry(
                    title=self._title,
                    data={},
                    options={
                        CONF_PRIMARY_AGENT: primary,
                        CONF_FALLBACK_AGENT: user_input.get(CONF_FALLBACK_AGENT) or primary,
                        CONF_SECONDARY_FALLBACK_AGENT: user_input.get(CONF_SECONDARY_FALLBACK_AGENT),
                    },
                )

        sel = _agent_selector(self.hass)
        schema = vol.Schema({
            vol.Required(CONF_PRIMARY_AGENT): sel,
            vol.Required(CONF_FALLBACK_AGENT): sel,
            vol.Optional(CONF_SECONDARY_FALLBACK_AGENT): sel,
        })

        return self.async_show_form(
            step_id="agent_settings",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        self._config_entry = config_entry
        self._user_input: dict[str, Any] = {
            key: value
            for key, value in config_entry.options.items()
            if key not in _REMOVED_OPTION_KEYS
        }

    def _process_user_input(self, user_input: dict[str, Any], exclude_keys: list[str] = ["back"],
                           allow_agent_changes: bool = False, allow_conversation_changes: bool = False) -> None:
        if user_input:
            current_options = dict(self._config_entry.options)

            agent_keys = [CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT]
            conversation_keys = [CONF_CONVERSATION_MODE, CONF_ENABLE_WEB_SEARCH, CONF_ENABLE_STREAMING_EFFECT, CONF_ENABLE_TOOL_DETAILS, CONF_ENABLE_TOOL_PROGRESS, CONF_CONTINUOUS_CONVERSATION, CONF_ENABLE_SOUND_NOTIFICATIONS, CONF_ENABLE_CONTEXT_STATUS_BAR, CONF_ENABLE_FILE_UPLOAD, CONF_ENABLE_RICH_MARKDOWN, CONF_ENABLE_ACTIVITY_TRACKING, CONF_ENABLE_SIDEBAR_DOCK, CONF_MAX_TOOL_REPEAT, CONF_IDENTICAL_CALL_WARN, CONF_IDENTICAL_CALL_STOP, CONF_PIPELINE_TIMEOUT]

            if not allow_agent_changes:
                for key in agent_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

            if not allow_conversation_changes:
                for key in conversation_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

            bool_keys = [CONF_ENABLE_WEB_SEARCH, CONF_ENABLE_STREAMING_EFFECT, CONF_ENABLE_TOOL_DETAILS, CONF_ENABLE_TOOL_PROGRESS, CONF_CONTINUOUS_CONVERSATION, CONF_ENABLE_SOUND_NOTIFICATIONS, CONF_ENABLE_CONTEXT_STATUS_BAR, CONF_ENABLE_FILE_UPLOAD, CONF_ENABLE_RICH_MARKDOWN, CONF_ENABLE_ACTIVITY_TRACKING, CONF_ENABLE_SIDEBAR_DOCK]

            for key, value in user_input.items():
                if key not in exclude_keys:
                    if key in agent_keys and allow_agent_changes:
                        if key == CONF_SECONDARY_FALLBACK_AGENT:
                            if value in (None, ""):
                                if key in self._user_input:
                                    self._user_input.pop(key)
                            else:
                                self._user_input[key] = value
                        else:
                            if value not in (None, ""):
                                self._user_input[key] = value
                    elif key in conversation_keys and allow_conversation_changes:
                        if key in bool_keys:
                            self._user_input[key] = value
                        elif key == CONF_PIPELINE_TIMEOUT and isinstance(value, (int, float)):
                            self._user_input[key] = int(value) * 60
                        elif value not in (None, ""):
                            self._user_input[key] = value
                    elif key in bool_keys:
                        self._user_input[key] = value
                    elif value not in (None, ""):
                        self._user_input[key] = value
                    elif key in current_options and key not in self._user_input:
                        self._user_input[key] = current_options[key]

            if allow_agent_changes:
                if CONF_SECONDARY_FALLBACK_AGENT not in user_input or user_input.get(CONF_SECONDARY_FALLBACK_AGENT) in (None, ""):
                    self._user_input.pop(CONF_SECONDARY_FALLBACK_AGENT, None)
            else:
                for key in agent_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

            if not allow_conversation_changes:
                for key in conversation_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "agent_settings",
                "conversation_settings",
                "user_mapping",
                "workspace_editor",
                "skill_editor",
                "plugin_manager",
            ],
            description_placeholders={"integration_title": "integration_title", "current_config": "current_config"},
        )

    async def async_step_user_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage external IM → HA user identity mapping."""
        from .runtime.storage.user_mapping import MappingStore

        if user_input is not None:
            action = user_input.get("action")
            if action == "add":
                provider = user_input.get("provider", "")
                ext_id = user_input.get("ext_id", "")
                ha_user = user_input.get("ha_user", "")
                # Handle manual input: strip prefix if user typed full id
                if ext_id == "__manual__":
                    ext_id = user_input.get("ext_id_manual", "").strip()
                if provider and ext_id and ha_user:
                    # ha_user format: "name (id...)"
                    ha_user_id = ha_user.split("(")[-1].rstrip(")") if "(" in ha_user else ha_user
                    MappingStore.set(provider, ext_id, ha_user_id)
            elif action == "remove":
                remove_key = user_input.get("remove_key", "")
                if remove_key:
                    parts = remove_key.split(":", 1)
                    if len(parts) == 2:
                        MappingStore.remove(parts[0], parts[1])
            return await self.async_step_user_mapping()

        # Build current mappings display
        mappings = MappingStore._load()
        current_lines = []
        remove_options = {}
        if mappings:
            for m in mappings:
                p = m.get("provider", "?")
                e = m.get("ext_id", "?")
                h = m.get("ha_user_id", "?")[:12]
                label = f"{p}: {e[:30]} → {h}..."
                current_lines.append(label)
                remove_options[f"{p}:{e}"] = label
            current_text = "\n".join(current_lines) if current_lines else "暂无"
        else:
            current_text = "暂无"
            remove_options[""] = "-- 无映射可删 --"

        # Load HA users for dropdown
        users = await self.hass.auth.async_get_users()
        user_options = {"": "-- 选择用户 --"}
        for u in users:
            if not u.system_generated and u.is_active:
                user_options[u.id] = f"{u.name}"

        # Load known IM identifiers for dropdown
        im_ids = _get_known_im_identifiers(self.hass)

        return self.async_show_form(
            step_id="user_mapping",
            data_schema=vol.Schema({
                vol.Required("action", default="add"): vol.In({
                    "add": "➕ 添加映射",
                    "remove": "🗑️ 删除映射",
                }),
                # Add fields
                vol.Optional("provider", default="feishu"): vol.In({
                    "feishu": "飞书 Feishu",
                    "wechat": "微信 WeChat",
                    "dingtalk": "钉钉 DingTalk",
                    "qq": "QQ",
                    "wecom": "企业微信 WeCom",
                    "xiaoyi": "小艺 XiaoYi",
                }),
                vol.Optional("ext_id", default=""): vol.In(im_ids),
                vol.Optional("ext_id_manual", default=""): str,
                vol.Optional("ha_user", default=""): vol.In(user_options),
                # Remove fields
                vol.Optional("remove_key", default=""): vol.In(remove_options),
            }),
            description_placeholders={
                "current_mappings": current_text,
            },
        )

    async def async_step_workspace_editor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="workspace_editor",
            menu_options=[
                "ws_agents",
                "ws_bootstrap",
                "ws_heartbeat",
                "ws_identity",
                "ws_memory",
                "ws_soul",
                "ws_tools",
                "ws_user",
            ],
        )

    async def _workspace_edit(self, doc_name: str, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.workspace_store import async_save_workspace_doc, get_workspace_doc

        step_id = f"ws_{doc_name.lower()}"

        if user_input is not None:
            content = user_input.get("content", "")
            await async_save_workspace_doc(self.hass, doc_name, content)
            return await self.async_step_workspace_editor()

        doc = get_workspace_doc(doc_name)
        current_content = doc.get("markdown", "")

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({
                vol.Required("content", default=current_content): TemplateSelector(),
            }),
            description_placeholders={"doc_name": f"{doc_name}.md"},
        )

    async def async_step_ws_agents(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("AGENTS", user_input)

    async def async_step_ws_bootstrap(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.workspace_store import (
            async_save_workspace_doc,
            async_set_bootstrap_active,
            get_workspace_doc,
        )

        if user_input is not None:
            active = user_input.get("bootstrap_active", False)
            content = user_input.get("content", "")
            await async_set_bootstrap_active(self.hass, active)
            await async_save_workspace_doc(self.hass, "BOOTSTRAP", content)
            return await self.async_step_workspace_editor()

        doc = get_workspace_doc("BOOTSTRAP")
        current_content = doc.get("markdown", "")
        current_active = doc.get("active", False)

        return self.async_show_form(
            step_id="ws_bootstrap",
            data_schema=vol.Schema({
                vol.Required("bootstrap_active", default=current_active): BooleanSelector(),
                vol.Required("content", default=current_content): TemplateSelector(),
            }),
            description_placeholders={"doc_name": "BOOTSTRAP.md"},
        )

    async def async_step_ws_heartbeat(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("HEARTBEAT", user_input)

    async def async_step_ws_identity(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("IDENTITY", user_input)

    async def async_step_ws_memory(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("MEMORY", user_input)

    async def async_step_ws_soul(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("SOUL", user_input)

    async def async_step_ws_tools(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("TOOLS", user_input)

    async def async_step_ws_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._workspace_edit("USER", user_input)

    async def async_step_skill_editor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.skill_store import async_refresh_prompt_store, list_installed_skills

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            slug = str(user_input.get("skill_slug") or "").strip()
            if slug:
                self._skill_editor_target = slug
                return await self.async_step_skill_edit()

        await async_refresh_prompt_store(self.hass, force=True)

        from .runtime.storage.skill_store import _INTERNAL_SKILL_SLUGS

        skills = await self.hass.async_add_executor_job(list_installed_skills)
        options = []
        for skill in skills:
            value = str(skill.get("slug") or skill.get("file") or skill.get("name") or "")
            if not value or value in _INTERNAL_SKILL_SLUGS:
                continue
            name = str(skill.get("name") or skill.get("slug") or "skill")
            version = str(skill.get("version") or "").strip()
            label = f"{name} · v{version}" if version else name
            options.append({"value": value, "label": label})

        if not options:
            return self.async_show_form(
                step_id="skill_editor",
                data_schema=vol.Schema({vol.Optional("back", default=True): bool}),
                description_placeholders={"skill_count": "0"},
                errors={"base": "no_installed_skills"},
            )

        schema = vol.Schema({
            vol.Required("skill_slug"): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("back", default=False): bool,
        })
        return self.async_show_form(
            step_id="skill_editor",
            data_schema=schema,
            description_placeholders={"skill_count": str(len(options))},
        )

    async def async_step_skill_edit(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.skill_store import (
            async_delete_skill,
            async_get_installed_skill,
            async_install_skill,
            async_read_skill_markdown,
        )

        slug = getattr(self, "_skill_editor_target", "") or ""
        if not slug:
            return await self.async_step_skill_editor()

        if user_input is not None:
            if user_input.get("back"):
                self._skill_editor_target = ""
                return await self.async_step_skill_editor()
            if user_input.get("delete"):
                try:
                    await async_delete_skill(
                        self.hass, slug, actor="options_flow", reason="user deleted via options"
                    )
                except (FileNotFoundError, ValueError) as err:
                    LOGGER.warning("Skill delete failed: %s", err)
                self._skill_editor_target = ""
                return await self.async_step_skill_editor()
            content = str(user_input.get("content") or "")
            if content.strip():
                try:
                    await async_install_skill(
                        self.hass,
                        slug,
                        content,
                        overwrite=True,
                        actor="options_flow",
                        reason="user edited via options",
                    )
                except Exception as err:
                    LOGGER.warning("Skill save failed: %s", err)
            self._skill_editor_target = ""
            return await self.async_step_skill_editor()

        try:
            meta = await async_get_installed_skill(self.hass, slug)
        except ValueError:
            meta = {"name": slug, "slug": slug}
        raw_content = await async_read_skill_markdown(self.hass, slug)
        current_content = _sanitize_skill_markdown_for_display(raw_content)

        schema = vol.Schema({
            vol.Required("content", default=current_content): TemplateSelector(),
            vol.Optional("delete", default=False): bool,
            vol.Optional("back", default=False): bool,
        })
        return self.async_show_form(
            step_id="skill_edit",
            data_schema=schema,
            description_placeholders={
                "skill_name": str(meta.get("name") or slug),
                "skill_slug": slug,
                "skill_file": str(meta.get("file") or f"{slug}.md"),
                "skill_chars": str(meta.get("chars") or len(current_content)),
                "skill_description": str(meta.get("description") or ""),
            },
        )

    async def async_step_plugin_manager(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.plugin_store import list_installed_plugins, plugins_dir

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()
            plugin_key = str(user_input.get("plugin_key") or "").strip()
            if plugin_key:
                self._plugin_editor_target = plugin_key
                return await self.async_step_plugin_detail()

        plugins = await self.hass.async_add_executor_job(list_installed_plugins)
        plugins_path = str(plugins_dir())

        options = []
        for p in plugins:
            name = str(p.get("name") or "")
            key = str(p.get("key") or name)
            version = str(p.get("version") or "")
            loaded = p.get("loaded", False)
            valid = p.get("valid", True)
            load_error = p.get("load_error")

            if not valid:
                status = "INVALID"
            elif loaded:
                status = "RUNNING"
            elif load_error:
                status = "FAILED"
            else:
                status = "STOPPED"

            ver_str = f" v{version}" if version else ""
            label = f"[{status}] {name}{ver_str}"
            options.append({"value": key, "label": label})

        if not options:
            return self.async_show_form(
                step_id="plugin_manager",
                data_schema=vol.Schema({vol.Optional("back", default=True): bool}),
                description_placeholders={"plugin_count": "0", "plugins_path": plugins_path},
                errors={"base": "no_installed_plugins"},
            )

        schema = vol.Schema({
            vol.Required("plugin_key"): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("back", default=False): bool,
        })
        return self.async_show_form(
            step_id="plugin_manager",
            data_schema=schema,
            description_placeholders={
                "plugin_count": str(len(options)),
                "plugins_path": plugins_path,
            },
        )

    async def async_step_plugin_detail(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.plugin_store import (
            get_plugin_install_guide,
            hot_load_plugin,
            hot_unload_plugin,
            list_installed_plugins,
            plugins_dir,
        )

        plugin_key = getattr(self, "_plugin_editor_target", "") or ""
        if not plugin_key:
            return await self.async_step_plugin_manager()

        if user_input is not None:
            if user_input.get("back"):
                self._plugin_editor_target = ""
                return await self.async_step_plugin_manager()
            if user_input.get("delete"):
                return await self.async_step_plugin_delete_confirm()
            if user_input.get("enable"):
                await self.hass.async_add_executor_job(hot_load_plugin, self.hass, plugin_key)
                self._plugin_editor_target = ""
                return await self.async_step_plugin_manager()
            if user_input.get("disable"):
                await self.hass.async_add_executor_job(hot_unload_plugin, self.hass, plugin_key)
                self._plugin_editor_target = ""
                return await self.async_step_plugin_manager()

        guide = await self.hass.async_add_executor_job(get_plugin_install_guide, plugin_key)
        plugins = await self.hass.async_add_executor_job(list_installed_plugins)
        plugin_info = next((p for p in plugins if p.get("key") == plugin_key), {})
        is_loaded = plugin_info.get("loaded", False)
        tools_count = plugin_info.get("tools_count", 0)

        desc_parts = []
        if guide.get("description"):
            desc_parts.append(guide["description"])
        if guide.get("pip_dependencies"):
            desc_parts.append(f"Dependencies: {', '.join(guide['pip_dependencies'])}")
        if guide.get("provides_tools"):
            desc_parts.append(f"Tools: {', '.join(guide['provides_tools'])}")
        if guide.get("errors"):
            desc_parts.append(f"Errors: {'; '.join(guide['errors'])}")

        description = "\n".join(desc_parts) if desc_parts else ""

        toggle_label = "disable" if is_loaded else "enable"
        schema = vol.Schema({
            vol.Optional(toggle_label, default=False): bool,
            vol.Optional("delete", default=False): bool,
            vol.Optional("back", default=False): bool,
        })
        return self.async_show_form(
            step_id="plugin_detail",
            data_schema=schema,
            description_placeholders={
                "plugin_name": guide.get("name", plugin_key),
                "plugin_version": guide.get("version", ""),
                "plugin_status": "ENABLED" if is_loaded else "DISABLED",
                "plugin_tools": str(tools_count),
                "plugin_path": guide.get("path", ""),
                "plugin_description": description,
            },
        )

    async def async_step_plugin_delete_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        from .runtime.storage.plugin_store import (
            hot_unload_plugin,
            plugins_dir,
        )
        import shutil

        plugin_key = getattr(self, "_plugin_editor_target", "") or ""
        if not plugin_key:
            return await self.async_step_plugin_manager()

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_plugin_detail()
            if user_input.get("confirm_delete"):
                plugin_path = plugins_dir() / plugin_key
                if plugin_path.exists():
                    await self.hass.async_add_executor_job(hot_unload_plugin, self.hass, plugin_key)
                    await self.hass.async_add_executor_job(shutil.rmtree, str(plugin_path))
                self._plugin_editor_target = ""
                return await self.async_step_plugin_manager()

        schema = vol.Schema({
            vol.Optional("confirm_delete", default=False): bool,
            vol.Optional("back", default=False): bool,
        })
        return self.async_show_form(
            step_id="plugin_delete_confirm",
            data_schema=schema,
            description_placeholders={
                "plugin_name": plugin_key,
            },
        )

    async def async_step_agent_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()

            primary = user_input.get(CONF_PRIMARY_AGENT)
            fallback = user_input.get(CONF_FALLBACK_AGENT)
            secondary = user_input.get(CONF_SECONDARY_FALLBACK_AGENT)
            if not primary:
                errors[CONF_PRIMARY_AGENT] = "invalid_agent"
            own_ids = _get_own_entity_ids(self.hass)
            for key, val in ((CONF_PRIMARY_AGENT, primary), (CONF_FALLBACK_AGENT, fallback), (CONF_SECONDARY_FALLBACK_AGENT, secondary)):
                if val and val in own_ids:
                    errors[key] = "invalid_agent"

            if not errors:
                self._process_user_input(user_input, exclude_keys=["back", "next_step", "save_and_exit"],
                                      allow_agent_changes=True)
                return self.async_create_entry(title="", data=self._user_input)

        current_primary = self._config_entry.options.get(CONF_PRIMARY_AGENT, DEFAULT_PRIMARY_AGENT)
        current_fallback = self._config_entry.options.get(CONF_FALLBACK_AGENT, DEFAULT_FALLBACK_AGENT)
        current_secondary = self._config_entry.options.get(CONF_SECONDARY_FALLBACK_AGENT, None)

        conv_sel = _agent_selector(self.hass)
        schema = vol.Schema({
            vol.Required(CONF_PRIMARY_AGENT, description={"suggested_value": current_primary}): conv_sel,
            vol.Required(CONF_FALLBACK_AGENT, description={"suggested_value": current_fallback}): conv_sel,
            vol.Optional(CONF_SECONDARY_FALLBACK_AGENT, description={"suggested_value": current_secondary}): conv_sel,
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="agent_settings",
            data_schema=schema,
            errors=errors,
            description_placeholders={}
        )

    async def async_step_conversation_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="conversation_settings",
            menu_options=["conv_dialog", "conv_display", "conv_runtime"],
        )

    def _save_conversation_subform(self, user_input: dict[str, Any]) -> FlowResult:
        self._process_user_input(
            user_input,
            exclude_keys=["back", "next_step", "save_and_exit"],
            allow_conversation_changes=True,
        )
        return self.async_create_entry(title="", data=self._user_input)

    async def async_step_conv_dialog(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_conversation_settings()
            if not user_input.get(CONF_CONVERSATION_MODE):
                errors[CONF_CONVERSATION_MODE] = "invalid_conversation_mode"
            if not errors:
                return self._save_conversation_subform(user_input)

        current_mode = self._config_entry.options.get(CONF_CONVERSATION_MODE, DEFAULT_CONVERSATION_MODE) or DEFAULT_CONVERSATION_MODE
        current_enable_web_search = self._config_entry.options.get(CONF_ENABLE_WEB_SEARCH, True)

        schema = vol.Schema({
            vol.Required(CONF_CONVERSATION_MODE, description={"suggested_value": current_mode}): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        CONVERSATION_MODE_NO_NAME,
                        CONVERSATION_MODE_ADD_NAME,
                        CONVERSATION_MODE_DETAILED,
                    ],
                    translation_key="conversation_mode",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_ENABLE_WEB_SEARCH, default=current_enable_web_search): BooleanSelector(),
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="conv_dialog",
            data_schema=schema,
            errors=errors,
            description_placeholders={},
        )

    async def async_step_conv_display(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_conversation_settings()
            user_input = {
                key: value
                for section_input in user_input.values()
                if isinstance(section_input, dict)
                for key, value in section_input.items()
            }
            return self._save_conversation_subform(user_input)

        current_tool_details = self._config_entry.options.get(CONF_ENABLE_TOOL_DETAILS, False)
        current_tool_progress = self._config_entry.options.get(CONF_ENABLE_TOOL_PROGRESS, True)
        current_continuous_conversation = self._config_entry.options.get(CONF_CONTINUOUS_CONVERSATION, False)
        current_sound_notifications = self._config_entry.options.get(CONF_ENABLE_SOUND_NOTIFICATIONS, True)
        current_context_status_bar = self._config_entry.options.get(CONF_ENABLE_CONTEXT_STATUS_BAR, True)
        current_file_upload = self._config_entry.options.get(CONF_ENABLE_FILE_UPLOAD, True)
        current_rich_markdown = self._config_entry.options.get(CONF_ENABLE_RICH_MARKDOWN, True)
        current_activity_tracking = self._config_entry.options.get(CONF_ENABLE_ACTIVITY_TRACKING, True)
        current_sidebar_dock = self._config_entry.options.get(CONF_ENABLE_SIDEBAR_DOCK, True)

        schema = vol.Schema({
            vol.Required("chat_window"): section(
                vol.Schema({
                    vol.Optional(CONF_ENABLE_SIDEBAR_DOCK, default=current_sidebar_dock): BooleanSelector(),
                    vol.Optional(CONF_CONTINUOUS_CONVERSATION, default=current_continuous_conversation): BooleanSelector(),
                    vol.Optional(CONF_ENABLE_SOUND_NOTIFICATIONS, default=current_sound_notifications): BooleanSelector(),
                }),
                {"collapsed": False},
            ),
            vol.Required("message_display"): section(
                vol.Schema({
                    vol.Optional(CONF_ENABLE_FILE_UPLOAD, default=current_file_upload): BooleanSelector(),
                    vol.Optional(CONF_ENABLE_RICH_MARKDOWN, default=current_rich_markdown): BooleanSelector(),
                    vol.Optional(CONF_ENABLE_ACTIVITY_TRACKING, default=current_activity_tracking): BooleanSelector(),
                }),
                {"collapsed": True},
            ),
            vol.Required("diagnostics"): section(
                vol.Schema({
                    vol.Optional(CONF_ENABLE_TOOL_DETAILS, default=current_tool_details): BooleanSelector(),
                    vol.Optional(CONF_ENABLE_TOOL_PROGRESS, default=current_tool_progress): BooleanSelector(),
                    vol.Optional(CONF_ENABLE_CONTEXT_STATUS_BAR, default=current_context_status_bar): BooleanSelector(),
                }),
                {"collapsed": True},
            ),
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="conv_display",
            data_schema=schema,
            description_placeholders={},
        )

    async def async_step_conv_runtime(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_conversation_settings()
            return self._save_conversation_subform(user_input)

        current_max_tool_repeat = self._config_entry.options.get(CONF_MAX_TOOL_REPEAT, DEFAULT_MAX_TOOL_REPEAT)
        current_identical_warn = self._config_entry.options.get(CONF_IDENTICAL_CALL_WARN, DEFAULT_IDENTICAL_CALL_WARN)
        current_identical_stop = self._config_entry.options.get(CONF_IDENTICAL_CALL_STOP, DEFAULT_IDENTICAL_CALL_STOP)
        current_pipeline_timeout = self._config_entry.options.get(CONF_PIPELINE_TIMEOUT, DEFAULT_PIPELINE_TIMEOUT)
        display_timeout = current_pipeline_timeout // 60 if current_pipeline_timeout else 5

        schema = vol.Schema({
            vol.Optional(CONF_MAX_TOOL_REPEAT, default=current_max_tool_repeat): NumberSelector(
                NumberSelectorConfig(min=3, max=50, step=1, unit_of_measurement="loop", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_IDENTICAL_CALL_WARN, default=current_identical_warn): NumberSelector(
                NumberSelectorConfig(min=5, max=30, step=1, unit_of_measurement="times", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_IDENTICAL_CALL_STOP, default=current_identical_stop): NumberSelector(
                NumberSelectorConfig(min=5, max=30, step=1, unit_of_measurement="times", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Optional(CONF_PIPELINE_TIMEOUT, default=display_timeout): NumberSelector(
                NumberSelectorConfig(min=5, max=360, step=5, unit_of_measurement="min", mode=NumberSelectorMode.SLIDER)
            ),
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="conv_runtime",
            data_schema=schema,
            description_placeholders={},
        )
