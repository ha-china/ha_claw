
AI Sidecar (kadermanager) is a Home Assistant custom integration that provides a **unified multi-AI conversation dispatch center** for smart homes. It is not yet another conversation agent—it is the **coordination layer** for all conversation agents, responsible for routing, fallback, and summarization between multiple AIs, and empowering AIs to truly understand and operate your home environment.

Developed and maintained independently by [@knoop7](https://github.com/knoop7).

---

## Core Architecture

### Multi-AI Dispatch Engine

The system adopts a **three-tier agent chain** architecture:

- **Primary AI** — Handles all conversation requests by default
- **Backup AI** — Automatically takes over when the primary AI fails, seamless switching
- **Summary AI** (optional) — After the first two AIs answer separately, a third AI summarizes them into one final reply

The dispatch core is driven by `orchestrator.py`, working with `agent_fallback.py` for fault tolerance, `loop_controller.py` to prevent infinite recursion, and `turn_kernel.py` to manage the complete lifecycle of a single conversation turn.

### Workspace Personality System

The AI's identity, memory, and behavioral rules are not hardcoded—they are all stored in **8 Markdown workspace documents** that users can edit directly through the configuration interface:

| Document | Responsibility |
|----------|----------------|
| `AGENTS.md` | File role division and operation constraints |
| `BOOTSTRAP.md` | First-run bootstrap process |
| `HEARTBEAT.md` | Scheduled follow-up task rules |
| `IDENTITY.md` | Assistant name, personality, Emoji |
| `MEMORY.md` | User preference long-term memory |
| `SOUL.md` | Tone style and personality base |
| `TOOLS.md` | Environment device information notes |
| `USER.md` | User basic information |

All changes are **saved and take effect immediately**, no need to restart Home Assistant. `workspace_store.py` handles hot reload and signature verification.

### Tool System

The integration includes a complete LLM toolchain, enabling AIs to not just chat but also **truly operate**:

- **ha_tools** — Call Home Assistant services, control devices
- **ha_core_tools** — Read/write config files, Shell execution, system checks
- **helper_tools** — Create and manage input_boolean / template and other auxiliary entities
- **custom_entity_tools** — Dynamically create sensors, switches, buttons
- **search_tools** — Web search, trafilatura extracts webpage body text
- **self_edit_tools** — AI autonomously edits workspace documents (self-evolution)
- **skill_tools** — Skill library management, reusable operation templates
- **misc_tools** — Stock quotes, heartbeat management and other misc items

### Intelligent Runtime

- **adaptive_memory** — Adaptive memory, dynamically matches relevant memory entries based on conversation context
- **heartbeat_ticker** — Heartbeat scheduler, AI automatically executes follow-up tasks on a periodic basis
- **signal_capture** — Intercept Home Assistant events, trigger AI proactive response
- **internal_llm** — Built-in lightweight LLM invocation layer, supports local models
- **patches** — Pipeline patch system, controls frontend visibility of tool call information by conversation mode

---

## Dependencies

- Home Assistant 2025.1+
- Python 3.12+
- trafilatura 2.0.0 (webpage body text extraction)
