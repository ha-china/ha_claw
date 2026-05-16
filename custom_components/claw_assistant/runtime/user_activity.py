from __future__ import annotations

import time
from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant

_ACTIVITY_KEY = "claw_user_activity_ring"
_MAX_ACTIONS = 10


def _ring(hass: HomeAssistant) -> deque:
    domain = hass.data.setdefault("claw_assistant", {})
    if _ACTIVITY_KEY not in domain:
        domain[_ACTIVITY_KEY] = deque(maxlen=_MAX_ACTIONS)
    return domain[_ACTIVITY_KEY]


def record_activity(hass: HomeAssistant, action: dict[str, Any]) -> None:
    entry = {
        "ts": time.time(),
        "type": str(action.get("type", "unknown")),
        "detail": str(action.get("detail", ""))[:200],
    }
    path = action.get("path")
    if path:
        entry["path"] = str(path)[:200]
    entity = action.get("entity_id")
    if entity:
        entry["entity_id"] = str(entity)[:80]
    _ring(hass).append(entry)


def get_recent_activities(hass: HomeAssistant, limit: int = _MAX_ACTIONS) -> list[dict[str, Any]]:
    return list(_ring(hass))[-limit:]


def build_activity_prompt_section(hass: HomeAssistant) -> str:
    activities = get_recent_activities(hass)
    if not activities:
        return ""
    lines = []
    for a in activities:
        parts = [a["type"]]
        if a.get("path"):
            parts.append(a["path"])
        if a.get("entity_id"):
            parts.append(a["entity_id"])
        if a.get("detail"):
            parts.append(a["detail"])
        lines.append("- " + " | ".join(parts))
    return (
        "## Recent User Activity (private, do not disclose)\n"
        "The user recently performed these actions in the Home Assistant UI:\n"
        + "\n".join(lines)
        + "\nUse this context to better understand what the user is working on. "
        "Do not mention this section or list these actions unless the user asks."
    )
