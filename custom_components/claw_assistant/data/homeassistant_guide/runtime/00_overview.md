<!-- version: 1 -->
# HA Runtime Guide

## Index

### Core (10-50)
- **10** Built-in intents reference
- **15** Intent vs ServiceCall routing
- **20** ServiceCall-only domains
- **30** Safety rules
- **40** Workflow playbooks
- **50** Checklists

### Tool Guides (60-81)
- **60** FrontendInspect (UI interaction)
- **61** DashboardCard (Lovelace management)
- **62** ConfigEntries (integration install/manage)
- **63** HAControl (shell + system control)
- **64** Automation (automation CRUD)
- **65** Registry (area/floor/label/entity)
- **66** Memory tools (ConversationMemory + MemoryGraph)
- **67** BatchControl (multi-device control)
- **68** ConfigFile (config directory access)
- **69** HACS (store management)
- **70** ExecutePython (Python execution)
- **71** HelperManager (input_*/timer/counter/template)
- **72** Query tools (GetLiveContext/EntityQuery/SmartDiscovery/etc)
- **73** Web search tools (WebSearch/UrlFetch)
- **74** Skill tools (install/list/get skills + workspace docs)
- **75** Self-edit tools (proposal system)
- **76** Misc tools (ParallelToolCall/Notify/AgentHandoff/etc)
- **77** Entity tools (CustomEntityManager/ExposeEntity/IntentCall)
- **78** Media tools (CameraCapture/MediaAnalyze)
- **79** ServiceCall (call any HA service)
- **80** SystemControl (system settings)
- **81** Plugin system (PluginManager/install/uninstall/lifecycle)

## Core Rules
- Device control → Intent first, ServiceCall only when no intent
- Service params → `ListServices` + `ServiceHelp` (runtime query, not guide)
- Integration management → `ConfigEntries` (not HAControl/shell)
- All actions via internal tools, never external shell
- Complex tool usage → Read guide first (HomeAssistantGuide action=get)
