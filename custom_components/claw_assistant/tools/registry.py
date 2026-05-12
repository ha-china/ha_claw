

from __future__ import annotations

from typing import Any

from homeassistant.helpers import llm

from .automation_tools import AutomationTool
from .script_tools import ScriptTool
from .ha_core_tools import (
    AgentHandoffTool,
    AreaDevicesTool,
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
    RegistryTool,
    ScriptExecuteTool,
    ServiceCallTool,
    ServiceHelpTool,
    SetConversationStateTool,
    SmartDiscoveryTool,
    ValidateServiceTool,
)
from .custom_entity_tools import CustomEntityManagerTool
from .dashboard_card_tools import DashboardCardTool
from .ha_tools import ConfigEntriesTool, HAControlTool, HACSTool
from .helper_tools import HelperManagerTool
from .misc_tools import (
    CameraCaptureTool,
    MediaAnalyzeTool,
    ConversationMemoryTool,
    ExecutePythonTool,
    ExposeEntityTool,
    GetConversationHistoryTool,
    GetInstalledSkillTool,
    GetMasterPromptTool,
    GetWorkspaceDocTool,
    HeartbeatManagerTool,
    HomeAssistantGuideTool,
    InstallSkillTool,
    ListInstalledSkillsTool,
    ListWorkspaceDocsTool,
    BootstrapControlTool,
    MemoryGraphTool,
    ParallelToolCallTool,
    SetMasterPromptTool,
    SetWorkspaceDocTool,
    SystemControlTool,
    ThinkContinueTool,
)
from .frontend_tools import FrontendInspectTool
from .search_tools import StockQueryTool, UrlFetchTool, WebReadChunkTool, WebSearchTool
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
    "ServiceCall": {"category": "device", "desc": "Call any HA service. Params may be data dict or flat fields. entity_id required/fuzzy-matched. Use real service parameter names, not boolean word keys: light.turn_on uses color_name='white' or rgb_color=[r,g,b] or color_temp_kelvin, brightness/brightness_pct; climate uses temperature/hvac_mode; fan uses percentage; media volume uses volume_level(0-1). Service auto-routed per domain.", "priority": 2},
    "EntityQuery": {"category": "query", "desc": "Query a single entity state. Params: entity_id (supports fuzzy matching such as 'living room light')", "priority": 1},
    "GetLiveContext": {"category": "query", "desc": "Get the real-time state list of all exposed entities. No parameters required.", "priority": 1},
    "CameraCapture": {"category": "device", "desc": "Camera tool: capture snapshots or analyze live camera frames. camera_entity='list'/empty enumerates cameras. mode=snapshot returns snapshot_url+markdown_hint; mode=analyze returns base64 JPEG for vision. For uploaded images/GIFs/videos use MediaAnalyze. Params: camera_entity, mode(snapshot|analyze), max_dim(default 640), target_kb(default 40)", "priority": 1},
    "MediaAnalyze": {"category": "device", "desc": "Analyze uploaded media (images/GIFs/videos). Images→single JPEG. Videos→key frames. First call auto-extracts overview frames with timestamp_sec per frame. For deeper analysis, call again with timestamps=[1.5,3.0,...] to extract at exact seconds. Describe what you see and respond to intent/mood. Params: file_path(required), max_dim(default 640), target_kb(default 40), timestamps(optional list of seconds)", "priority": 1},
    "ThinkContinue": {"category": "core", "desc": "Record internal reasoning steps (optional). Params: thought, next_action", "priority": 0},
    "StockQuery": {"category": "search", "desc": "Query stock or fund quotes. Params: codes (for example 'TSLA,AAPL')", "priority": 1},
    "WebSearch": {"category": "search", "desc": "Web search (bing first, baidu fallback). Returns titles+snippets. Then use UrlFetch(url) to read full page, WebReadChunk(doc_id, position) for more. Params: query, num_results(default 5), engine(google/bing/baidu/bing_cn, leave empty for auto)", "priority": 2},
    "BatchControl": {"category": "device", "desc": "Control multiple devices in one request. Params: entity_ids(list) OR discovery filters domain/area/state/name_contains, action(turn_on/turn_off/toggle), data. Domain-aware: vacuum turn_on=start cleaning, turn_off=return to base; cover turn_on=open, turn_off=close; lock turn_on=unlock, turn_off=lock. For 'turn off all lights', call domain='light', state='on', action='turn_off'. For 'start/open all vacuums', call domain='vacuum', action='turn_on'.", "priority": 2},
    "AreaDevices": {"category": "query", "desc": "Get all devices in a specific area. Params: area", "priority": 2},
    "HistoryQuery": {"category": "query", "desc": "Query entity history. Params: entity_id, hours (default 24)", "priority": 2},
    "Automation": {"category": "system", "desc": "Manage automations via official APIs (NOT shell/ConfigFile!). Params: action (list/get/create/update/delete/trigger/enable/disable), entity_id, automation_id, config, icon, area_id. Workflow: get→modify→update. Always use this tool for automation CRUD.", "priority": 2},
    "Script": {"category": "system", "desc": "Manage scripts via official APIs (NOT shell/ConfigFile!). Params: action (list/get/create/update/delete/run), entity_id, script_id, config, variables, icon, area_id. update merges partial config; run executes with optional variables dict.", "priority": 2},
    "ExecutePython": {"category": "system", "desc": "Execute Python only when native HA tools cannot do the job cleanly. Tool description contains the full routing checklist. Inline(default): HA runtime tasks needing hass/files/frontend artefacts. Sandbox=true: isolated package/heavy/risky code without hass. Inline has OUTPUT_DIR/TMP_DIR, output_url(name), list_outputs(), list_tmp(). Destructive ops need consent. Params: code, sandbox, requirements, timeout", "priority": 2},
    "UrlFetch": {"category": "search", "desc": "Fetch a URL and return chunk 0. Use WebReadChunk to read more. Params: url", "priority": 3},
    "WebReadChunk": {"category": "search", "desc": "Read a specific chunk of a previously fetched page. Params: doc_id, position", "priority": 3},
    "ListServices": {"category": "query", "desc": "List available services for a domain. Params: domain (for example light/switch/climate)", "priority": 2},
    "ScriptExecute": {"category": "system", "desc": "Execute a Home Assistant script. Params: script_id, variables (optional dict)", "priority": 2},
    "Notify": {"category": "device", "desc": "Send a notification. Params: message, title (default 'AI Assistant'), target (default persistent_notification or notify.xxx)", "priority": 2},
    "ConfigEntries": {"category": "system", "desc": "Integration management. Preferred params envelope: {action, params:{...}}. Top-level handler/domain/entry_id/flow_id are also accepted for compatibility. INSTALL: flow/init(handler=domain)→flow/configure. CHECK: get(domain). OPTIONS: options/init→options/configure. DELETE/RELOAD: delete/reload(entry_id). SUBENTRY: subentries/flow/init→configure. Do NOT explore randomly — follow the workflow.", "priority": 3},
    "HAControl": {"category": "system", "desc": "Advanced Home Assistant control + host shell. Preferred params envelope: {action, params:{...}}; top-level command/domain/entry_id are also accepted for compatibility. Actions: shell/check_config/list_integrations/get_integration/list_entities_by_integration/reload_integration/rename_entry/reload_themes/reload_resources/reload_scripts/reload_automations/get_system_log/get_error_log/get_diagnostics. IMPORTANT: before using shell to modify automations.yaml/configuration.yaml/sensors.yaml, politely explain the change and ask the user for confirmation; prefer Automation tool for automations.", "priority": 3},
    "HACS": {"category": "system", "desc": "Manage the HACS store. Params: action (list/search/github_search/info/install/update/uninstall/remove/manage/edit/open_add_integration), repository/source/query/category/params", "priority": 3},
    "SystemControl": {"category": "system", "desc": "System control. Params: action (set_global_inject/set_output_mode/get_status), value. For set_output_mode, value is normal/default/auto/brief/detailed/list/code.", "priority": 3},
    "ConversationMemory": {"category": "misc", "desc": "Manage conversation memory. Params: action (save/get/delete/list), key, value", "priority": 3},
    "MemoryGraph": {"category": "core", "desc": "Long-term graph memory (SQLite + FTS5, BM25 ranked, time decay, dedup, typed edges). Use for durable facts, decisions, bug fixes, and their causal links. Workspace MD is auto-indexed; this tool also writes nodes/edges directly. Params: action(recall/remember/link/pin/forget/get/stats) + action-specific fields. recall: query, kinds?, limit?, expand?. remember: kind, title, body, source_doc?, confidence?, pinned?. link: src_id, dst_id, relation(related_to/caused_by/supersedes/refutes/resolved_by/blocked_by), weight?. pin: id, pinned?. forget: id. get: id. stats: none.", "priority": 1},
    "ParallelToolCall": {"category": "misc", "desc": "Execute multiple independent tools in true parallel and return an aggregated result. Params: tools([{name,args}])", "priority": 3},
    "GetConversationHistory": {"category": "core", "desc": "Inspect/manage conversation history. action=get|recent|clear|stats. `recent` (default 60 min) returns turns across ALL conversations — use when the window/conversation_id was closed/changed. `clear` (scope=current|all) lets you wipe. Params: action, max_turns, include_tools, recent_minutes, conversation_id, scope.", "priority": 2},
    "InstallSkill": {"category": "core", "desc": "Install a Markdown skill into .storage/claw_assistant/skills only. Legacy ~/.openclaw/workspace/skills and config/skills are import-only, never install targets. Params: name, markdown, overwrite", "priority": 2},
    "ListInstalledSkills": {"category": "core", "desc": "List installed skills. No parameters.", "priority": 1},
    "GetInstalledSkill": {"category": "core", "desc": "Read the full content of one installed skill. Params: name", "priority": 1},
    "HomeAssistantGuide": {"category": "core", "desc": "Read the bundled Home Assistant guide. Params: action (overview/list/get/search), name, query, limit", "priority": 1},
    "SetMasterPrompt": {"category": "core", "desc": "Set the global Master Prompt markdown. Params: markdown", "priority": 2},
    "GetMasterPrompt": {"category": "core", "desc": "Read the current Master Prompt markdown. No parameters.", "priority": 1},
    "ListWorkspaceDocs": {"category": "core", "desc": "List workspace markdown documents. No parameters.", "priority": 1},
    "GetWorkspaceDoc": {"category": "core", "desc": "Read one workspace markdown document. Params: name", "priority": 1},
    "SetWorkspaceDoc": {"category": "core", "desc": "Write one workspace markdown document. Params: name, markdown", "priority": 2},
    "HeartbeatManager": {"category": "core", "desc": "Manage heartbeat follow-up tasks instead of blind polling. Params: action(list/upsert/delete/record/clear_state), slug/title/schedule/objective/steps/notes/status/note/enabled/delete_after_success/notify_channel(e.g. wechat:account_id:user_id or qq:user:openid)", "priority": 2},
    "CustomEntityManager": {"category": "system", "desc": "Create/list/edit/delete dynamic AI entities under claw_assistant device (diagnostic). Use this tool (NOT HAControl/shell) to create custom entities. Supports sensor(Jinja2), binary_sensor(Jinja2), switch(toggle), button(press_action). Params: action(create/list/edit/delete), platform, name, entity_id, state_template, icon, device_class, state_class, unit_of_measurement, press_action", "priority": 1},
    "HelperManager": {"category": "system", "desc": "Create/list/delete HA native helpers (input_boolean/input_number/input_text/input_select/input_datetime/input_button/timer/counter/template sensor/binary_sensor). Use this tool (NOT HAControl/shell) to manage helpers. All params are flat (no nested dict). action=create: helper_type+name+type-specific params. action=delete: entity_id or helper_type+name.", "priority": 1},
    "GetSystemIndex": {"category": "query", "desc": "Get the system structure index (areas/domains/device classes/people/automations/scripts overview). Params: force_refresh (default false)", "priority": 2},
    "SetConversationState": {"category": "core", "desc": "Set conversation state ONLY for complex multi-turn interactions. DO NOT use for simple device control or queries — the system auto-detects completion. Params: expecting_response(bool), reason", "priority": 3},
    "AgentHandoff": {"category": "core", "desc": "Consult another AI agent synchronously. You keep control. Params: agent_id(optional), question(required), context(optional), intent(consult|request|review). Reply comes back as tool result.", "priority": 2},
    "NextAgentHandoff": {"category": "core", "desc": "Consult the next available AI agent. Shortcut for AgentHandoff. Params: question(required), context(optional). Reply comes back as tool result.", "priority": 2},
    "ValidateService": {"category": "query", "desc": "Validate service call parameters. Params: domain, service, data. Returns validity, errors, and suggestions.", "priority": 2},
    "ServiceHelp": {"category": "query", "desc": "Get help for a domain or service. Params: domain (required), service (optional)", "priority": 2},
    "SmartDiscovery": {"category": "query", "desc": "Smart entity discovery. Params: area/domain/state/name_contains/name_pattern/device_class/inferred_type/person_name/pet_name/limit", "priority": 2},
    "IntentCall": {"category": "query", "desc": "List or call third-party intent handlers. action=list to discover available intents and their REQUIRED/optional slots; action=call with intent_type and slots dict containing all REQUIRED values.", "priority": 2},
    "ConfigFile": {"category": "system", "desc": "Access the Home Assistant config directory. Params: action(list/read/stage_write/stage_append/stage_mkdir/stage_delete/apply/cancel/list_pending), path/content/approval_id, user_consent(bool, only required for delete apply), consent_quote(str, audit). write/append/mkdir auto-apply on `apply` (reversible). delete is destructive — describe in chat what/why, judge the user's reply yourself (no keyword list), then `apply` with user_consent=true and consent_quote=\"<their words>\". For automations.yaml/configuration.yaml/sensors.yaml, prefer the Automation tool.", "priority": 3},
    "DeleteSkill": {"category": "core", "desc": "Delete an installed Markdown skill (audited in changelog). Params: name, reason", "priority": 2},
    "UpsertGuideDoc": {"category": "core", "desc": "Create or overwrite a runtime Home Assistant guide Markdown. Params: relative_path, markdown, reason", "priority": 2},
    "DeleteGuideDoc": {"category": "core", "desc": "Delete a runtime Home Assistant guide Markdown (source/ is protected). Params: relative_path, reason", "priority": 2},
    "GetSelfChangelog": {"category": "core", "desc": "Read the append-only self-edit audit log. Params: limit(default 20), target_type(skill|guide)", "priority": 2},
    "ReviewSelfSkills": {"category": "core", "desc": "Return a reflection briefing (skills, guide docs, recent changelog, pending proposals) so the AI can self-critique before staging proposals. Params: limit", "priority": 2},
    "ProposeSelfEdit": {"category": "core", "desc": "Stage a self-edit proposal for human approval (never writes directly). Covers skills, guides, and curated memory hygiene (purification/evolution/boundary). Params: target_type(skill|guide|memory), target_id(skill slug | guide relative_path | memory key), action(create|update|delete), markdown(new value for memory), reason", "priority": 2},
    "ListProposals": {"category": "core", "desc": "List pending self-edit proposals. No parameters.", "priority": 2},
    "GetProposal": {"category": "core", "desc": "Read the body of one pending proposal. Params: slug", "priority": 2},
    "DiscardProposal": {"category": "core", "desc": "Remove a pending proposal without applying it. Params: slug", "priority": 2},
    "ApplyProposal": {"category": "core", "desc": "Approve and apply a pending proposal. Params: slug, approved_by", "priority": 2},
    "Registry": {"category": "system", "desc": "Manage HA registries (areas/floors/labels/categories/entities). Use this for: creating/renaming/deleting areas, assigning entities to areas, adding labels, updating labels. Label rename uses action=update with params:{name:new_name}; label action=rename is accepted as update for compatibility. Params: registry(area/floor/label/category/entity), action(list/get/create/update/delete/rename), *_id, params(dict)", "priority": 1},
    "FrontendInspect": {"category": "system", "desc": "Interact with the HA frontend like a real user. action=snapshot reads current page DOM tree. action=navigate smoothly navigates via SPA transition (path e.g. /config, /lovelace/0). action=tap clicks an element by CSS selector or visible text (traverses shadow DOM). action=type types text into an input field (selector or text to find, value to type, clear=true to clear first). action=scroll scrolls page or element (direction=up/down/left/right, amount in px). action=exec_js runs arbitrary JS. Params: action, selector, text, path, value, clear, direction, amount, js_code, depth(default 8)", "priority": 2},
    "DashboardCard": {"category": "system", "desc": "Create/manage Lovelace dashboard views and cards. Supports masonry and sections view types. html-card-pro cards use content; other cards use card_config or card_yaml. Workflow: list_dashboards→get_dashboard→get_card/add_view/add_card/update_card. Run check_dependency only before creating custom:html-pro-card. Params: action, dashboard_url, view_index, card_index, section_index(-1=auto for sections views), title, icon, content(HTML/CSS/JS), card_config, card_yaml. Returns mandatory _action_required instructions.", "priority": 2},
    "ExposeEntity": {"category": "system", "desc": "Expose or unexpose entities to the conversation assistant. ⚠️ PRIVACY: Before exposing, inform user: 'I need to expose [entity] to control it. Data stays local, not sent externally. Proceed?' action=list: list unexposed. action=expose: expose entity. Params: action(expose/list), entity_id, expose(bool), domain", "priority": 1},
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
        "CameraCapture": CameraCaptureTool,
        "MediaAnalyze": MediaAnalyzeTool,
        "StockQuery": StockQueryTool,
        "WebSearch": WebSearchTool,
        "BatchControl": BatchControlTool,
        "AreaDevices": AreaDevicesTool,
        "HistoryQuery": HistoryQueryTool,
        "Automation": AutomationTool,
        "Script": ScriptTool,
        "ExecutePython": ExecutePythonTool,
        "UrlFetch": UrlFetchTool,
        "WebReadChunk": WebReadChunkTool,
        "ListServices": ListServicesTool,
        "ScriptExecute": ScriptExecuteTool,
        "Notify": NotifyTool,
        "ConfigEntries": ConfigEntriesTool,
        "HAControl": HAControlTool,
        "HACS": HACSTool,
        "ThinkContinue": ThinkContinueTool,
        "SystemControl": SystemControlTool,
        "ConversationMemory": ConversationMemoryTool,
        "MemoryGraph": MemoryGraphTool,
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
        "BootstrapControl": BootstrapControlTool,
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
        "Registry": RegistryTool,
        "DashboardCard": DashboardCardTool,
        "FrontendInspect": FrontendInspectTool,
        "ExposeEntity": ExposeEntityTool,
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
