<!-- version: 9 -->
---
name: homeassistant_runtime_guide
description: Primary runtime guide for Home Assistant workflows inside claw_assistant.
category: homeassistant
tags:
  - homeassistant
  - runtime
  - automation
  - dashboard
  - integration
platforms:
  - homeassistant
metadata:
  claw:
    category: homeassistant
    tags:
      - homeassistant
      - runtime
      - troubleshooting
      - device-control
    requires_tools:
      - GetSystemIndex
      - GetLiveContext
      - HomeAssistantGuide
      - ServiceCall
    config:
      - key: skill.homeassistant_runtime_guide.preferred_flow
        description: Preferred execution flow for Home Assistant tasks.
        default: Understand -> Resolve -> Act -> Troubleshoot
required_environment_variables: []
---

# Home Assistant Runtime Guide

## Order
Understand (`GetSystemIndex`/`GetLiveContext`) → Resolve (`SmartDiscovery`/`EntityQuery`) → Act (`ServiceCall`/`BatchControl`/intent tools) → Troubleshoot (`HomeAssistantGuide`)

## Rules
- Verify live state before claiming results.
- Use native HA tools first.
- Dashboards/automations/integrations questions → `HomeAssistantGuide` first.

## Power Tools
- `HAControl(shell)` — shell in HA process. Refuse destructive commands.
- `HAControl(ssh)` — remote SSH (asyncssh).
- `ExecutePython` inline (default) — runs in HA process, has `hass`, supports `requirements`. See § ExecutePython.
- `ExecutePython` sandbox (`sandbox=true`) — isolated child venv subprocess, no `hass`.

Priority: native tool → inline Python → shell → ssh → sandbox.

## ExecutePython

Inline globals and output URL rules live in the `ExecutePython` tool description; do not shadow injected names.

### Tmp steering (inline only)

Writes via the injected `open(...)` pass through unchanged except when write modes target system scratch dirs, which are transparently redirected into `TMP_DIR`. Each redirect is reported in `artefacts.redirects[]`. Reads and fd writes are untouched. Inline is not a sandbox — use `sandbox=true` for isolation.

### Return contract (inline)

- `phase` — `"install"` if pip failed before exec, else `"exec"`.
- `install` — `{requested, already_present, installed, failed, ok}` when `requirements` was non-empty.
- `result` — explicit `result = ...` wins over the trailing-expression value.
- `stdout`, `stderr`, `duration_ms`.
- `artefacts.output[]` — `{name, path, url}` for new files written under `OUTPUT_DIR`.
- `artefacts.tmp[]` — `{name, path}` for new files written under `TMP_DIR`.
- `artefacts.redirects[]` — `{from, to, reason}` for each rerouted system-tmp write.

### House rules

- Shareable output → `OUTPUT_DIR`; reply with `output_url(name)`.
- `OUTPUT_DIR` filenames MUST be ASCII (letters, digits, `_`, `-`, `.`). Non-ASCII filenames break Markdown URL rendering.
- `TMP_DIR` is auto-pruned; do not delete manually.
- Destructive operations require user consent first.
- Do not shadow injected globals.
- Do not list `requirements` you do not import.

## Skill Path
All skills go to `.storage/claw_assistant/skills/`. Refuse other locations. `~/.openclaw/workspace/skills/` and `config/skills/` are legacy-only and are auto-imported into the claw_assistant store when possible, but must never be used as active install destinations.
