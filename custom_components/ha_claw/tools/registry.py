

from __future__ import annotations

from typing import Any

from homeassistant.helpers import llm

from .ha_core_tools import (
    AgentHandoffTool,
    AreaDevicesTool,
    AutomationTool,
    BatchControlTool,
    ConfigFileTool,
    EntityQueryTool,
    GetLiveContextTool,
    GetSystemIndexTool,
    HistoryQueryTool,
    IntentCallTool,
    ListServicesTool,
    NextAgentHandoffTool,
    NotifyTool,
    ScriptExecuteTool,
    ServiceCallTool,
    ServiceHelpTool,
    SetConversationStateTool,
    SmartDiscoveryTool,
    ValidateServiceTool,
)
from .custom_entity_tools import CustomEntityManagerTool
from .ha_tools import ConfigEntriesTool, HAControlTool, HACSTool
from .helper_tools import HelperManagerTool
from .misc_tools import (
    CameraAnalyzeTool,
    ConversationMemoryTool,
    ExecutePythonTool,
    GetConversationHistoryTool,
    GetInstalledSkillTool,
    GetMasterPromptTool,
    GetWorkspaceDocTool,
    HeartbeatManagerTool,
    HomeAssistantGuideTool,
    InstallSkillTool,
    ListInstalledSkillsTool,
    ListWorkspaceDocsTool,
    ParallelToolCallTool,
    SetMasterPromptTool,
    SetWorkspaceDocTool,
    SystemControlTool,
    ThinkContinueTool,
)
from .search_tools import StockQueryTool, UrlFetchTool, WebSearchTool
from .self_edit_tools import (
    ApplyProposalTool,
    DeleteGuideDocTool,
    DeleteSkillTool,
    DiscardProposalTool,
    GetProposalTool,
    GetSelfChangelogTool,
    ListProposalsTool,
    ProposeSelfEditTool,
    ReviewSelfSkillsTool,
    UpsertGuideDocTool,
)

TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "ServiceCall": {"category": "device", "desc": "Call a registered HA service. PREFER native intent tools (HassLightSet, HassTurnOn/Off, HassVacuumStart, HassClimateSetTemperature, etc.) for device control — only use ServiceCall when no matching intent tool exists. Good for: calendar events, todo items, automations/script triggering, input helpers, timers, notifications. NOT for: creating automations(→HAControl), installing integrations(→ConfigEntries), HACS(→HACS), YAML(→ConfigFile), helpers(→HelperManager). Params: domain, service, data(dict). Use ListServices/ServiceHelp if unsure.", "priority": 2},
    "EntityQuery": {"category": "query", "desc": "Query a single entity state. Params: entity_id (supports fuzzy matching such as 'living room light')", "priority": 1},
    "GetLiveContext": {"category": "query", "desc": "Get the real-time state list of all exposed entities. No parameters required.", "priority": 1},
    "CameraAnalyze": {"category": "device", "desc": "Fetch a camera frame as base64 JPEG for vision analysis. Heavy compression (default max_dim=640, target_kb=40) so upstream LLM server can swallow the payload. Bypasses exposure filter. Pass camera_entity='list' or empty to enumerate. Params: camera_entity, max_dim(default 640), target_kb(default 40)", "priority": 1},
    "ThinkContinue": {"category": "core", "desc": "Record internal reasoning steps (optional). Params: thought, next_action", "priority": 0},
    "StockQuery": {"category": "search", "desc": "Query stock or fund quotes. Params: codes (for example 'TSLA,AAPL')", "priority": 1},
    "WebSearch": {"category": "search", "desc": "General-purpose web search for ALL real-time info (news, finance, weather, entertainment, tech). Queries both Baidu and Bing and merges the results. Params: query, num_results (default 3), engine (optional: baidu/bing)", "priority": 2},
    "BatchControl": {"category": "device", "desc": "Control multiple devices in one request. Params: entity_ids (list), action (turn_on/turn_off/toggle), data", "priority": 2},
    "AreaDevices": {"category": "query", "desc": "Get all devices in a specific area. Params: area", "priority": 2},
    "HistoryQuery": {"category": "query", "desc": "Query entity history. Params: entity_id, hours (default 24)", "priority": 2},
    "Automation": {"category": "system", "desc": "Manage automations (creation is not supported). Params: action (list/trigger/enable/disable), entity_id", "priority": 2},
    "ExecutePython": {"category": "system", "desc": "Execute Python code. Default mode: inline with `hass` access. Sandbox mode (sandbox=true or requirements=[...]): isolated child venv + subprocess, can pip install extra deps. Params: code, sandbox, requirements, timeout", "priority": 2},
    "UrlFetch": {"category": "search", "desc": "Fetch readable content from a URL. Params: url, max_length (default 2000)", "priority": 3},
    "ListServices": {"category": "query", "desc": "List available services for a domain. Params: domain (for example light/switch/climate)", "priority": 2},
    "ScriptExecute": {"category": "system", "desc": "Execute a Home Assistant script. Params: script_id, variables (optional dict)", "priority": 2},
    "Notify": {"category": "device", "desc": "Send a notification. Params: message, title (default 'AI Assistant'), target (default persistent_notification or notify.xxx)", "priority": 2},
    "ConfigEntries": {"category": "system", "desc": "Integration management. INSTALL: flow/init(handler=domain)→flow/configure. CHECK: get(domain). OPTIONS: options/init→options/configure. DELETE/RELOAD: delete/reload(entry_id). SUBENTRY: subentries/flow/init→configure. Do NOT explore randomly — follow the workflow.", "priority": 3},
    "HAControl": {"category": "system", "desc": "Advanced Home Assistant control + host shell. Params: action (shell/check_config/list_integrations/get_integration/list_entities_by_integration/reload_integration/rename_entry/reload_themes/reload_resources/reload_scripts/reload_automations/get_system_log/get_error_log/get_diagnostics), params (e.g. {command, timeout, cwd} for shell; {domain} for integration actions; {limit} for system_log; {lines} for error_log)", "priority": 3},
    "HACS": {"category": "system", "desc": "Manage the HACS store. Params: action (list/search/github_search/info/install/update/uninstall/remove/manage/edit/open_add_integration), repository/source/query/category/params", "priority": 3},
    "SystemControl": {"category": "system", "desc": "System control. Params: action (set_global_inject/set_output_mode/get_status)", "priority": 3},
    "ConversationMemory": {"category": "misc", "desc": "Manage conversation memory. Params: action (save/get/delete/list), key, value", "priority": 3},
    "ParallelToolCall": {"category": "misc", "desc": "Execute multiple independent tools in true parallel and return an aggregated result. Params: tools([{name,args}])", "priority": 3},
    "GetConversationHistory": {"category": "core", "desc": "Get current conversation history. Params: limit (default 10)", "priority": 1},
    "InstallSkill": {"category": "core", "desc": "Install a Markdown skill. Params: name, markdown, overwrite", "priority": 2},
    "ListInstalledSkills": {"category": "core", "desc": "List installed skills. No parameters.", "priority": 1},
    "GetInstalledSkill": {"category": "core", "desc": "Read the full content of one installed skill. Params: name", "priority": 1},
    "HomeAssistantGuide": {"category": "core", "desc": "Read the bundled Home Assistant guide. Params: action (overview/list/get/search), name, query, limit", "priority": 1},
    "SetMasterPrompt": {"category": "core", "desc": "Set the global Master Prompt markdown. Params: markdown", "priority": 2},
    "GetMasterPrompt": {"category": "core", "desc": "Read the current Master Prompt markdown. No parameters.", "priority": 1},
    "ListWorkspaceDocs": {"category": "core", "desc": "List workspace markdown documents. No parameters.", "priority": 1},
    "GetWorkspaceDoc": {"category": "core", "desc": "Read one workspace markdown document. Params: name", "priority": 1},
    "SetWorkspaceDoc": {"category": "core", "desc": "Write one workspace markdown document. Params: name, markdown", "priority": 2},
    "HeartbeatManager": {"category": "core", "desc": "Manage heartbeat follow-up tasks instead of blind polling. Params: action(list/upsert/delete/record/clear_state), slug/title/schedule/objective/steps/notes/status/note/enabled/delete_after_success/notify_channel(e.g. wechat:account_id:user_id)", "priority": 2},
    "CustomEntityManager": {"category": "system", "desc": "Create/list/edit/delete dynamic AI entities under claw_assistant device (diagnostic). Use this tool (NOT HAControl/shell) to create custom entities. Supports sensor(Jinja2), binary_sensor(Jinja2), switch(toggle), button(press_action). Params: action(create/list/edit/delete), platform, name, entity_id, state_template, icon, device_class, state_class, unit_of_measurement, press_action", "priority": 1},
    "HelperManager": {"category": "system", "desc": "Create/list/delete HA native helpers (input_boolean/input_number/input_text/input_select/input_datetime/input_button/timer/counter/template sensor/binary_sensor). Use this tool (NOT HAControl/shell) to manage helpers. All params are flat (no nested dict). action=create: helper_type+name+type-specific params. action=delete: entity_id or helper_type+name.", "priority": 1},
    "GetSystemIndex": {"category": "query", "desc": "Get the system structure index (areas/domains/device classes/people/automations/scripts overview). Params: force_refresh (default false)", "priority": 2},
    "SetConversationState": {"category": "core", "desc": "Set the conversation state. Params: expecting_response(bool), reason", "priority": 2},
    "AgentHandoff": {"category": "core", "desc": "Request that another configured AI agent answer this turn. Params: direction(next|previous), reason, reply_content(optional)", "priority": 2},
    "NextAgentHandoff": {"category": "core", "desc": "Request that the next configured AI agent answer this turn. Params: reason, reply_content(optional)", "priority": 2},
    "ValidateService": {"category": "query", "desc": "Validate service call parameters. Params: domain, service, data. Returns validity, errors, and suggestions.", "priority": 2},
    "ServiceHelp": {"category": "query", "desc": "Get help for a domain or service. Params: domain (required), service (optional)", "priority": 2},
    "SmartDiscovery": {"category": "query", "desc": "Smart entity discovery. Params: area/domain/state/name_contains/name_pattern/device_class/inferred_type/person_name/pet_name/limit", "priority": 2},
    "IntentCall": {"category": "query", "desc": "List or call third-party intent handlers (e.g. Holidays, Almanac, TuneFreePlayMusic). action=list to discover; action=call with intent_type and optional slots dict.", "priority": 2},
    "ConfigFile": {"category": "system", "desc": "Access the Home Assistant config directory. Params: action(list/read/stage_write/stage_append/stage_mkdir/stage_delete/apply/cancel/list_pending), path/content/approval_id", "priority": 3},
    "DeleteSkill": {"category": "core", "desc": "Delete an installed Markdown skill (audited in changelog). Params: name, reason", "priority": 2},
    "UpsertGuideDoc": {"category": "core", "desc": "Create or overwrite a runtime Home Assistant guide Markdown. Params: relative_path, markdown, reason", "priority": 2},
    "DeleteGuideDoc": {"category": "core", "desc": "Delete a runtime Home Assistant guide Markdown (source/ is protected). Params: relative_path, reason", "priority": 2},
    "GetSelfChangelog": {"category": "core", "desc": "Read the append-only self-edit audit log. Params: limit(default 20), target_type(skill|guide)", "priority": 2},
    "ReviewSelfSkills": {"category": "core", "desc": "Return a reflection briefing (skills, guide docs, recent changelog, pending proposals) so the AI can self-critique before staging proposals. Params: limit", "priority": 2},
    "ProposeSelfEdit": {"category": "core", "desc": "Stage a self-edit proposal for human approval (never writes directly). Params: target_type(skill|guide), target_id, action(create|update|delete), markdown, reason", "priority": 2},
    "ListProposals": {"category": "core", "desc": "List pending self-edit proposals. No parameters.", "priority": 2},
    "GetProposal": {"category": "core", "desc": "Read the body of one pending proposal. Params: slug", "priority": 2},
    "DiscardProposal": {"category": "core", "desc": "Remove a pending proposal without applying it. Params: slug", "priority": 2},
    "ApplyProposal": {"category": "core", "desc": "Approve and apply a pending proposal. Params: slug, approved_by", "priority": 2},
}

CORE_TOOLS = [
    "ThinkContinue",
    "ServiceCall",
    "EntityQuery",
    "GetLiveContext",
    "StockQuery",
    "WebSearch",
]


def build_tool_map() -> dict[str, type]:

    return {
        "ServiceCall": ServiceCallTool,
        "EntityQuery": EntityQueryTool,
        "CameraAnalyze": CameraAnalyzeTool,
        "StockQuery": StockQueryTool,
        "WebSearch": WebSearchTool,
        "BatchControl": BatchControlTool,
        "AreaDevices": AreaDevicesTool,
        "HistoryQuery": HistoryQueryTool,
        "Automation": AutomationTool,
        "ExecutePython": ExecutePythonTool,
        "UrlFetch": UrlFetchTool,
        "ListServices": ListServicesTool,
        "ScriptExecute": ScriptExecuteTool,
        "Notify": NotifyTool,
        "ConfigEntries": ConfigEntriesTool,
        "HAControl": HAControlTool,
        "HACS": HACSTool,
        "ThinkContinue": ThinkContinueTool,
        "SystemControl": SystemControlTool,
        "ConversationMemory": ConversationMemoryTool,
        "GetConversationHistory": GetConversationHistoryTool,
        "InstallSkill": InstallSkillTool,
        "ListInstalledSkills": ListInstalledSkillsTool,
        "GetInstalledSkill": GetInstalledSkillTool,
        "HomeAssistantGuide": HomeAssistantGuideTool,
        "SetMasterPrompt": SetMasterPromptTool,
        "GetMasterPrompt": GetMasterPromptTool,
        "ListWorkspaceDocs": ListWorkspaceDocsTool,
        "GetWorkspaceDoc": GetWorkspaceDocTool,
        "SetWorkspaceDoc": SetWorkspaceDocTool,
        "HeartbeatManager": HeartbeatManagerTool,
        "ParallelToolCall": ParallelToolCallTool,
        "GetSystemIndex": GetSystemIndexTool,
        "SetConversationState": SetConversationStateTool,
        "AgentHandoff": AgentHandoffTool,
        "NextAgentHandoff": NextAgentHandoffTool,
        "ValidateService": ValidateServiceTool,
        "ServiceHelp": ServiceHelpTool,
        "SmartDiscovery": SmartDiscoveryTool,
        "GetLiveContext": GetLiveContextTool,
        "ConfigFile": ConfigFileTool,
        "DeleteSkill": DeleteSkillTool,
        "UpsertGuideDoc": UpsertGuideDocTool,
        "DeleteGuideDoc": DeleteGuideDocTool,
        "GetSelfChangelog": GetSelfChangelogTool,
        "ReviewSelfSkills": ReviewSelfSkillsTool,
        "ProposeSelfEdit": ProposeSelfEditTool,
        "ListProposals": ListProposalsTool,
        "GetProposal": GetProposalTool,
        "DiscardProposal": DiscardProposalTool,
        "ApplyProposal": ApplyProposalTool,
        "HelperManager": HelperManagerTool,
        "CustomEntityManager": CustomEntityManagerTool,
        "IntentCall": IntentCallTool,
    }


def build_tool_list(
    *,
    include_names: set[str] | None = None,
    exclude_names: set[str] | None = None,
) -> list[llm.Tool]:

    tool_map = build_tool_map()
    tools: list[llm.Tool] = []
    for name, tool_cls in tool_map.items():
        if include_names is not None and name not in include_names:
            continue
        if exclude_names is not None and name in exclude_names:
            continue
        tools.append(tool_cls())
    return tools
