from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback, HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)

from .const import (
    CONF_CONVERSATION_MODE,
    CONF_ENABLE_AI_SUMMARY,
    CONF_ENABLE_WEB_SEARCH,
    CONF_ERROR_RESPONSES,
    CONF_FALLBACK_AGENT,
    CONF_PRIMARY_AGENT,
    CONF_SECONDARY_FALLBACK_AGENT,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
    DEFAULT_CONVERSATION_MODE,
    DEFAULT_FALLBACK_AGENT,
    DEFAULT_PRIMARY_AGENT,
    DOMAIN,
)

LOGGER = logging.getLogger(__name__)
_REMOVED_OPTION_KEYS = (
    CONF_ERROR_RESPONSES,
    CONF_ENABLE_AI_SUMMARY,
    "speaker_entity",
    "speaker_type",
    "tts_service",
)

@callback
def get_conversation_agents(hass: HomeAssistant) -> list[dict[str, str]]:
    entity_registry = er.async_get(hass)
    own_config_entry_ids = {
        entry.entry_id for entry in hass.config_entries.async_entries(DOMAIN)
    }
    agents = []
    for entity_id in hass.states.async_entity_ids("conversation"):
        try:
            state = hass.states.get(entity_id)
            registry_entry = entity_registry.async_get(entity_id)
            if registry_entry and (
                registry_entry.platform == DOMAIN
                or registry_entry.config_entry_id in own_config_entry_ids
            ):
                continue
            if state and state.attributes.get("entity") != "kadermanager.ai":
                label = state.attributes.get("friendly_name")
                if label in (None, ""):
                    label = entity_id.split('.')[-1]
                agents.append({
                    "value": entity_id,
                    "label": str(label),
                })
        except:
            continue
    return agents if agents else [{"value": "no_agents", "label": "无可用对话代理"}]

class KaderManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._title: str = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        if user_input is None:
            integration = await async_get_integration(self.hass, DOMAIN)
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_NAME, default=integration.name): str,
                    }
                ),
            )
        self._title = user_input[CONF_NAME]
        return await self.async_step_agent_settings()

    async def async_step_agent_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        available_agents = get_conversation_agents(self.hass)
        available_agent_ids = [a["value"] for a in available_agents]

        if user_input is not None:
            if not available_agent_ids or "no_agents" in available_agent_ids:
                errors["base"] = "no_agents_available"
            elif user_input.get(CONF_PRIMARY_AGENT) not in available_agent_ids:
                errors[CONF_PRIMARY_AGENT] = "invalid_agent"
            else:
                return self.async_create_entry(
                    title=self._title,
                    data={},
                    options={
                        CONF_PRIMARY_AGENT: user_input[CONF_PRIMARY_AGENT],
                        CONF_FALLBACK_AGENT: user_input.get(CONF_FALLBACK_AGENT, user_input[CONF_PRIMARY_AGENT]),
                        CONF_SECONDARY_FALLBACK_AGENT: user_input.get(CONF_SECONDARY_FALLBACK_AGENT),
                    },
                )

        default_agent = available_agent_ids[0] if available_agent_ids and available_agent_ids[0] != "no_agents" else ""

        schema = vol.Schema({
            vol.Required(CONF_PRIMARY_AGENT, description={"suggested_value": default_agent}): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_FALLBACK_AGENT, description={"suggested_value": default_agent}): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_SECONDARY_FALLBACK_AGENT): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
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
            conversation_keys = [CONF_CONVERSATION_MODE, CONF_ENABLE_WEB_SEARCH]

            if not allow_agent_changes:
                for key in agent_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

            if not allow_conversation_changes:
                for key in conversation_keys:
                    if key in current_options:
                        self._user_input[key] = current_options[key]

            bool_keys = [CONF_ENABLE_WEB_SEARCH]

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
                        elif value not in (None, ""):
                            self._user_input[key] = value
                    elif key in bool_keys:
                        self._user_input[key] = value
                    elif value not in (None, ""):
                        self._user_input[key] = value
                    elif key in current_options and key not in self._user_input:
                        self._user_input[key] = current_options[key]

            if not allow_agent_changes:
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
            menu_options=["agent_settings", "conversation_settings", "workspace_editor"],
            description_placeholders={"integration_title": "integration_title", "current_config": "current_config"},
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
        from .runtime.workspace_store import async_save_workspace_doc, get_workspace_doc

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
        return await self._workspace_edit("BOOTSTRAP", user_input)

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

    async def async_step_agent_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        available_agents = get_conversation_agents(self.hass)
        available_agent_ids = [agent["value"] for agent in available_agents]
        errors = {}

        if not available_agents or (len(available_agents) == 1 and available_agents[0]["value"] == "no_agents"):
            errors["base"] = "no_agents_available"

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()

            if not available_agent_ids or "no_agents" in available_agent_ids:
                errors["base"] = "no_agents_available"
            else:
                if CONF_PRIMARY_AGENT in user_input and user_input[CONF_PRIMARY_AGENT] not in available_agent_ids:
                    errors[CONF_PRIMARY_AGENT] = "invalid_agent"
                if CONF_FALLBACK_AGENT in user_input and user_input[CONF_FALLBACK_AGENT] not in available_agent_ids:
                    errors[CONF_FALLBACK_AGENT] = "invalid_agent"
                if (CONF_SECONDARY_FALLBACK_AGENT in user_input and
                    user_input[CONF_SECONDARY_FALLBACK_AGENT] not in (None, "") and
                    user_input[CONF_SECONDARY_FALLBACK_AGENT] not in available_agent_ids):
                    errors[CONF_SECONDARY_FALLBACK_AGENT] = "invalid_agent"

            if not errors:
                self._process_user_input(user_input, exclude_keys=["back", "next_step", "save_and_exit"],
                                      allow_agent_changes=True)
                return self.async_create_entry(title="", data=self._user_input)

        current_primary = self._config_entry.options.get(CONF_PRIMARY_AGENT, DEFAULT_PRIMARY_AGENT)
        if current_primary not in available_agent_ids and available_agent_ids and available_agent_ids[0] != "no_agents":
            current_primary = available_agent_ids[0]

        current_fallback = self._config_entry.options.get(CONF_FALLBACK_AGENT, DEFAULT_FALLBACK_AGENT)
        if current_fallback not in available_agent_ids and available_agent_ids and available_agent_ids[0] != "no_agents":
            current_fallback = available_agent_ids[0]

        current_secondary = self._config_entry.options.get(CONF_SECONDARY_FALLBACK_AGENT, None)

        schema = vol.Schema({
            vol.Required(CONF_PRIMARY_AGENT, description={"suggested_value": current_primary}): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_FALLBACK_AGENT, description={"suggested_value": current_fallback}): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional(CONF_SECONDARY_FALLBACK_AGENT, description={"suggested_value": current_secondary}): SelectSelector(
                SelectSelectorConfig(options=available_agents, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="agent_settings",
            data_schema=schema,
            errors=errors,
            description_placeholders={}
        )

    async def async_step_conversation_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}

        if user_input is not None:
            if user_input.get("back"):
                return await self.async_step_init()

            if not user_input.get(CONF_CONVERSATION_MODE):
                errors[CONF_CONVERSATION_MODE] = "invalid_conversation_mode"

            if not errors:
                self._process_user_input(user_input, exclude_keys=["back", "next_step", "save_and_exit"],
                                      allow_conversation_changes=True)
                return self.async_create_entry(title="", data=self._user_input)

        current_mode = self._config_entry.options.get(CONF_CONVERSATION_MODE, DEFAULT_CONVERSATION_MODE)
        if not current_mode:
            current_mode = DEFAULT_CONVERSATION_MODE
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
                    mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(CONF_ENABLE_WEB_SEARCH, default=current_enable_web_search): BooleanSelector(),
            vol.Optional("back", default=False): bool,
        })

        return self.async_show_form(
            step_id="conversation_settings",
            data_schema=schema,
            errors=errors,
            description_placeholders={}
        )
