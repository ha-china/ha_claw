<img width="2004" height="362" alt="Mac_2026-05-05 01 07 01" src="https://github.com/user-attachments/assets/65292c6e-8f15-423b-8c44-26e3a0c22527" />

---

## Why Did I Build This?

Honestly, I never set out to "hack" Home Assistant.

HA's native conversation system has a fatal flaw: **the single-shot mechanism**. One AI Agent, one tool call, one result. In real-world scenarios this is practically useless — when you say "set the living room to movie mode," the AI needs to dim the lights, adjust brightness, turn on the TV, switch the input source, and close the curtains. A single tool call simply can't handle that.

So Claw Assistant was born. I **deeply transformed HA's official conversation pipeline via a Hook mechanism**, injecting multi-turn tool call loops, Agent cascading, adaptive memory, streaming output, and other modern AI Agent architectures — all without breaking existing functionality.

> **Important Disclaimer: This integration is NOT officially supported or endorsed by Home Assistant.** This is a community-driven, independently developed third-party project. The Hook mechanism means it has deep intrusion into HA's internal pipeline — this is both the reason for its power and a risk you need to understand. The entire codebase is 100% open source; every line can be audited. If it's not for you, you can uninstall it at any time from `Settings → Integrations` with one click, leaving no residue behind.

---



<p align="center">
  <a href="https://buymeacoffee.com/knoop7"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee" /></a>
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Home%20Assistant-2025.12+-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant" />
</p>

## What Can It Do?

### Multi-Turn Tool Call Loop

This is the fundamental difference between Claw Assistant and other solutions.

HA native: User speaks → AI calls one tool → Done.
Claw Assistant: User speaks → AI calls a tool → Examines result → Decides whether to continue → Calls another tool → … → Until the task is complete.

This means the AI can accomplish truly complex task chains: query state → analyze data → create automation → verify results → notify user.

### Multi-Agent Cascading Failover

| Tier | Role | Scenario |
|------|------|----------|
| First tier | Primary Agent | All AI |
| Second tier | Fallback Agent | Primary timeout / error / unavailable |
| Third tier | Tertiary fallback (optional) | Last resort for extreme cases |

The system includes a built-in Adaptive Memory module that automatically tracks each Agent's success/failure rate and intelligently skips known-incompatible models. After extensive testing, the following models have been verified to work effectively: OpenAI, Claude, Google Gemini, DeepSeek, Qwen, local Ollama (to be further verified).

### 50+ Built-in Tools

This is the most comprehensive AI tool coverage in the HA ecosystem. Every tool is designed for real-world scenarios:

**Device & Control**
- **ServiceCall** — Call any HA service (lights, HVAC, curtains, locks, valves… all categories)
- **BatchControl** — Control multiple devices in a single command
- **CameraCapture** — Camera snapshot / live frame analysis (supports vision AI)
- **MediaAnalyze** — Upload images/GIFs/videos for direct AI analysis
- **IntentCall** — Invoke third-party intent handlers

**Automation & Scripts**
- **Automation** — Full automation CRUD (create / read / update / delete / trigger / enable / disable)
- **Script** — Script management and execution
- **HelperManager** — Create/delete native HA Helpers (input_boolean, timer, counter, etc.)

**Dashboard**
- **DashboardCard** — Dynamically create/edit Lovelace dashboard views and cards

**System Management**
- **HAControl** — Advanced control: Shell commands, integration reload, system logs, diagnostics
- **ConfigEntries** — Full integration install / configure / delete workflow
- **HACS** — Directly manage the HACS store (search / install / update / uninstall)
- **ConfigFile** — Read/write HA configuration files (with staging + confirmation to prevent accidents)
- **Registry** — Area / floor / label / category / entity registry management
- **ExecutePython** — Execute Python code online (with sandbox isolation support)

**Information & Search**
- **WebSearch + UrlFetch** — Web search and webpage content extraction
- **StockQuery** — Real-time stock/fund quotes
- **HistoryQuery** — Entity history data queries
- **SmartDiscovery** — Intelligent entity discovery (by person name / area / state / device type)

**AI Self-Governance**
- **InstallSkill / DeleteSkill** — Skill installation and removal
- **ProposeSelfEdit** — AI proposes modifications to its own skills/guides (requires human approval)
- **MemoryGraph** — Long-term graph memory network based on SQLite + FTS5
- **ConversationMemory** — Conversation-level memory
- **AgentHandoff / NextAgentHandoff** — Inter-AI consultation and collaboration
- **ParallelToolCall** — Parallel multi-tool execution

### Workspace Persona System

Define all AI behavior through 8 Markdown documents:

| Document | Purpose |
|----------|---------|
| `IDENTITY.md` | Identity definition (name, role) |
| `SOUL.md` | Personality and response style |
| `USER.md` | User profile (family members, habits, preferences) |
| `MEMORY.md` | Persistent memory (everything the AI learns about you) |
| `TOOLS.md` | Tool usage preferences and constraints |
| `AGENTS.md` | Multi-Agent collaboration rules |
| `BOOTSTRAP.md` | First-install bootstrap instructions |
| `HEARTBEAT.md` | Scheduled tasks and reminders |

All editable directly in the HA interface (`Configure → Workspace Editor`), no SSH or file editing required.

### Skill System

Skills are Markdown-formatted instruction files that the AI automatically matches and loads based on user intent.

- Install anytime via conversation: *"Install a weather briefing skill for me"*
- Manage in the configuration interface: `Configure → Skill Editor`
- AI can write new skills on its own — but modifications must go through human approval (ProposeSelfEdit mechanism)

### Slash Commands

| Command | Description |
|---------|-------------|
| `/new` | Start a new conversation |
| `/reset` | Clear history and state |
| `/stop` | Abort the current task |
| `/history` | Manage conversation history |
| `/skill` | View/invoke skills |
| `/model` | List/switch Agents |
| `/help` | Help |
| `/commands` | List all commands |

### Scheduled Tasks (Heartbeat)

The AI can create scheduled check tasks and proactively notify you:

- *"Tell me the weather every morning at 8 AM"*
- *"Check the server status every hour"*
- *"Alert me when the stock price drops below XX"*

---

## Compatibility

Different models vary in capability (smaller local models may not be able to complete complex multi-step tasks), but basic conversation and device control work across the board.

---

## Installation

### HACS (Recommended)

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-china&repository=ha_claw&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open in HACS" /></a>

Click the button above to install directly via HACS, then restart Home Assistant.


### Manual Installation

1. Download the code and place the `claw_assistant` directory into `config/custom_components/`
2. Restart Home Assistant
3. Add the integration

### Prerequisites

- Home Assistant 2025.12+
- At least one AI Conversation Agent integration (OpenAI / Google AI / Claude / Ollama, etc.)
- Python dependencies install automatically: `trafilatura` (web extraction), `asyncssh` (remote operations)

---

## Configuration Guide

###  Conversation Settings

Split into three sub-pages:

**Conversation Mode** (controls the level of context detail sent to the AI)
- `no_name` — Most concise, saves tokens
- `add_name` — Recommended, balances accuracy and cost
- `detailed` — Most complete, suitable for complex scenarios

**Display Options**
- Streaming output (typewriter effect)
- Continuous conversation (no need to re-trigger; seamless multi-turn dialogue that never forgets)
- Context status bar (view AI work progress)
- File upload
- Rich Markdown rendering

**Runtime Parameters**
- Tool loop limit: 3–50 times (default 15)
- Pipeline timeout: 5–360 minutes (default 15)

###  Workspace Editor

Edit the AI's identity, memory, personality, and other Markdown documents directly in the interface.

###  Skill Editor

Manage installed skills with support for online editing and deletion.

---

## Architecture

```
User Input
  |
  +-- Slash command? --> Handle directly (/new /stop /skill ...)
  |
  +-- Simple intent? --> HA native engine ("turn on light", "close door", etc.)
  |
  +-- Complex request --> Orchestrator
                           |
                           +-- Build System Prompt (Workspace + Skill + Memory + Entity context)
                           |
                           +-- Agent cascading execution
                           |   +-- Primary Agent --> Success? Return
                           |   +-- Fail -> Fallback Agent --> Success? Return
                           |   +-- Fail -> Tertiary fallback --> Return
                           |
                           +-- Multi-turn tool loop (up to 50 rounds)
                           |   +-- AI decides to call a tool
                           |   +-- Execute tool, return result
                           |   +-- AI analyzes result, decides next step
                           |   +-- Repeat until task complete or limit reached
                           |
                           +-- Response processing
                               +-- Text cleanup & formatting
                               +-- Markdown rendering
                               +-- Streaming output
```

---

## Security & Permissions

I'll be upfront: **Claw Assistant has elevated system privileges.**

It transforms HA's conversation pipeline via the Hook mechanism, enabling Shell command execution, configuration file read/write, integration management, and arbitrary service calls. This is the root cause of its power — but it also means you need to understand these risks:

- **Hook transformation**: The integration modifies HA core's conversation processing flow through runtime patches (pipeline event filtering, tool result extraction, streaming output, thinking content handling, etc.). This is not standard practice, and the official team does not recommend it
- **Shell access**: The `HAControl` tool supports executing host Shell commands. The AI will ask for confirmation before destructive operations, but you still need to trust the AI model you've configured
- **File read/write**: The `ConfigFile` tool can read/write the HA configuration directory. It includes a built-in staging + confirmation mechanism, and delete operations require secondary confirmation

**Design Philosophy**: Opening up these permissions is primarily about fully unleashing the host machine's performance and capabilities — letting AI truly become the "butler" of your home, rather than a chatbot limited to simple operations. I've seen others recreate bridge layers inside add-ons or separate containers, wasting all the super-powers that HA already natively supports.

**Security Guarantees**:
- The entire codebase is 100% open source; every line of logic can be audited on [GitHub](https://github.com/ha-china/ha_claw)
- All user data (Workspace, Skill, Memory) is stored in HA's `.storage/claw_assistant/` directory, covered by your backup strategy, and will not be lost
- No dependency on any third-party cloud services (except your AI model); no data is uploaded
- The self-edit mechanism (ProposeSelfEdit) requires human approval; the AI cannot silently modify itself

**If you don't need these capabilities, or have concerns about the permission scope — that's perfectly fine.** Simply remove Claw Assistant from `Settings → Integrations` and all Hooks will automatically revert. Your HA system will not be affected in any way.

---

## FAQ

**Q: How is it different from HA's built-in Assist?**
HA Assist can only handle predefined intents (turn on light, turn off light, check temperature — fixed patterns). Claw Assistant builds on top of this by connecting to an LLM, supporting arbitrary natural language understanding, and completing complex tasks through multi-turn tool calls — for example, "Create an automation: when I arrive home, if it's nighttime, set the living room to warm light mode."

**Q: Does it support voice?**
Yes. Set Claw Assistant as the conversation engine in Assist Pipeline and you're good to go.

**Q: What are some previously unsolvable scenarios?**
- Create complex automations with multiple conditions and actions in a single sentence
- Trigger actions based on camera feed content
- Have the AI automatically diagnose system error logs and suggest fixes
- Dynamically generate Lovelace dashboard cards
- Manage HACS plugins via natural language
- Batch scene control across devices and areas
- AI automatically learns and remembers your preferences

**Q: Will uninstalling affect my HA?**
No. After removing the integration, all Hooks automatically revert and HA returns to its original state.

---

## Source Code & Feedback
- **Maintainer**: [@knoop7](https://github.com/knoop7)

