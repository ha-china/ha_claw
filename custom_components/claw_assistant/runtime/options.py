

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from ..const import (
    CONF_CONVERSATION_MODE,
    CONF_ENABLE_WEB_SEARCH,
    CONF_FALLBACK_AGENT,
    CONF_PRIMARY_AGENT,
    CONF_SECONDARY_FALLBACK_AGENT,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
)
from .state import get_global_state, get_output_state


@dataclass(slots=True)
class ConversationRuntimeConfig:


    fallback_agents: list[str]
    summary_agents: list[str]
    summary_agent: str
    conversation_mode: str
    enable_ai_summary: bool
    enable_web_search: bool
    global_inject: str
    output_mode: str


def build_conversation_runtime_config(
    entry: ConfigEntry, global_state: dict, output_state: dict
) -> ConversationRuntimeConfig:

    options = entry.options
    conversation_mode = options.get(
        CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME
    )
    primary_agent = str(options.get(CONF_PRIMARY_AGENT, "") or "")
    fallback_agent = str(options.get(CONF_FALLBACK_AGENT, "") or "")
    secondary_agent = str(options.get(CONF_SECONDARY_FALLBACK_AGENT, "") or "")

    fallback_agents = [
        agent_id for agent_id in (primary_agent, fallback_agent) if agent_id
    ]

    summary_requested = conversation_mode == CONVERSATION_MODE_DETAILED

    summary_agents: list[str] = []
    if summary_requested:
        if primary_agent and fallback_agent and secondary_agent:
            summary_agents = [primary_agent, fallback_agent, secondary_agent]
        elif len(fallback_agents) >= 2:
            summary_agents = list(fallback_agents)

    return ConversationRuntimeConfig(
        fallback_agents=fallback_agents,
        summary_agents=summary_agents,
        summary_agent=secondary_agent if primary_agent and fallback_agent and secondary_agent else "",
        conversation_mode=conversation_mode,
        enable_ai_summary=bool(summary_agents),
        enable_web_search=bool(options.get(CONF_ENABLE_WEB_SEARCH, False)),
        global_inject=str(global_state.get("inject", "")),
        output_mode=str(output_state.get("mode", "")),
    )


def build_conversation_runtime_config_for_hass(
    entry: ConfigEntry, hass
) -> ConversationRuntimeConfig:
    return build_conversation_runtime_config(
        entry,
        get_global_state(hass),
        get_output_state(hass),
    )
