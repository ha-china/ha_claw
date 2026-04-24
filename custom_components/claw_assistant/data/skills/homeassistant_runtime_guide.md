# Home Assistant Runtime Guide

## Order
Understand (`GetSystemIndex`/`GetLiveContext`) → Resolve (`SmartDiscovery`/`EntityQuery`) → Act (`DeviceSkill`/`ServiceCall`) → Troubleshoot (`HomeAssistantGuideSkill`)

## Rules
- Verify live state before claiming results. Look up, don't guess.
- Use native HA tools first. No external MCP/CLI wrappers when internals suffice.
- Dashboards/automations/integrations questions → `HomeAssistantGuideSkill` first.

## Power Tools (when native tools can't)
- **`HAControl(shell)`** — shell in HA process. Refuse destructive commands (`rm -rf`, `dd`, `mkfs`, fork bombs).
- **`HAControl(ssh)`** — execute commands on remote hosts via SSH (pure Python asyncssh, no sshpass needed). Supports password and key auth.
- **`ExecutePython` inline** — runs in event loop with `hass`. Supports top-level `await`. Do not use blocking calls (use async equivalents).
- **`ExecutePython` sandbox** — isolated venv, for extra pip packages. No `hass` access.

Priority: native tool → inline Python → shell → ssh → sandbox.

## Media & Camera
- **Camera discovery:** use `CameraAnalyze(camera_entity="list")`. Do NOT use GetLiveContext — cameras may not be exposed.
- **Display format:** follow the `## Channel` section in your system prompt — it tells you exactly how to show images on the current platform.

## Skill Path
All skills **must** go to `.storage/claw_assistant/skills/`. Refuse any other location.