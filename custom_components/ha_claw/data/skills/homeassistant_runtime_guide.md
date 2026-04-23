# Home Assistant Runtime Guide

## Order
Understand (`GetSystemIndex`/`GetLiveContext`) → Resolve (`SmartDiscovery`/`EntityQuery`) → Act (`DeviceSkill`/`ServiceCall`) → Troubleshoot (`HomeAssistantGuideSkill`)

## Rules
- Verify live state before claiming results. Look up, don't guess.
- Use native HA tools first. No external MCP/CLI wrappers when internals suffice.
- Dashboards/automations/integrations questions → `HomeAssistantGuideSkill` first.

## Power Tools (when native tools can't)
- **`HAControl(shell)`** — shell in HA process. Refuse destructive commands (`rm -rf`, `dd`, `mkfs`, fork bombs).
- **`ExecutePython` inline** — runs in event loop with `hass`. Never block (no sleep/network).
- **`ExecutePython` sandbox** — isolated venv, for extra pip packages. No `hass` access.

Priority: native tool → inline Python → shell → sandbox.

## IM Media Tags
For IM channels (conversation_id starts with `wechat:`/`feishu:`/`dingtalk:`/`qq:`), embed tags on their own line:
- `[IMAGE:camera.entity_id]` — gateway grabs camera snapshot and sends it directly
- `[IMAGE:https://url]` — gateway downloads and sends the image

**IMAGE vs CameraAnalyze:**
- User wants to **see** the feed → `[IMAGE:camera.xxx]` directly, do NOT call CameraAnalyze
- User wants you to **analyze** what's in the frame → call CameraAnalyze first, reply with analysis text, optionally append `[IMAGE:camera.xxx]`

Tags must be on their own line. Multiple tags per reply OK. Non-IM channels ignore tags.

## Skill Path
All skills **must** go to `.storage/claw_assistant/skills/`. Refuse any other location.