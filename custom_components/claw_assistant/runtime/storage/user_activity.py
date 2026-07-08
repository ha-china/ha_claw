from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant, callback

LOGGER = logging.getLogger(__name__)

_ACTIVITY_KEY = "claw_user_activity_ring"
_MAX_ACTIONS = 10
_ACTIVITY_USER_BUCKETS_KEY = "claw_user_activity_buckets"
_ACTIVITY_BUCKET_ORDER_KEY = "claw_user_activity_bucket_order"
_MAX_USER_BUCKETS = 20

_EVENTS_KEY = "claw_system_events_ring"
_MAX_EVENTS = 2 
_EVENT_TYPE = "claw_assistant_event"
_EVENT_DEBOUNCE_SEC = 600
_EVENT_UNSUB_KEY = "claw_event_listener_unsub"
_EVENT_SEEN_KEY = "claw_event_seen_keys"


def _ring(hass: HomeAssistant, user_key: str | None = None) -> deque:
    domain = hass.data.setdefault("claw_assistant", {})

    if user_key is None:
        # Global bucket (backward compatible)
        if _ACTIVITY_KEY not in domain:
            domain[_ACTIVITY_KEY] = deque(maxlen=_MAX_ACTIONS)
        return domain[_ACTIVITY_KEY]

    # Per-user bucket
    buckets: dict[str, deque] = domain.setdefault(_ACTIVITY_USER_BUCKETS_KEY, {})
    bucket_order: list[str] = domain.setdefault(_ACTIVITY_BUCKET_ORDER_KEY, [])

    if user_key not in buckets:
        # Enforce max user bucket limit: evict LRU
        while len(buckets) >= _MAX_USER_BUCKETS and bucket_order:
            oldest = bucket_order.pop(0)
            if oldest in buckets:
                del buckets[oldest]
                LOGGER.debug("Evicted activity bucket for user: %s", oldest)

        buckets[user_key] = deque(maxlen=_MAX_ACTIONS)
        bucket_order.append(user_key)

    return buckets[user_key]


def record_activity(
    hass: HomeAssistant,
    action: dict[str, Any],
    user_key: str | None = None,
) -> None:
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
    _ring(hass, user_key=user_key).append(entry)


def get_recent_activities(
    hass: HomeAssistant,
    limit: int = _MAX_ACTIONS,
    user_key: str | None = None,
) -> list[dict[str, Any]]:
    return list(_ring(hass, user_key=user_key))[-limit:]


def build_activity_prompt_section(
    hass: HomeAssistant,
    user_key: str | None = None,
) -> str:
    activities = get_recent_activities(hass, user_key=user_key)
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



def _events_ring(hass: HomeAssistant) -> deque:
    domain = hass.data.setdefault("claw_assistant", {})
    if _EVENTS_KEY not in domain:
        domain[_EVENTS_KEY] = deque(maxlen=_MAX_EVENTS)
    return domain[_EVENTS_KEY]


def _event_seen(hass: HomeAssistant) -> dict[str, float]:
    domain = hass.data.setdefault("claw_assistant", {})
    if _EVENT_SEEN_KEY not in domain:
        domain[_EVENT_SEEN_KEY] = {}
    return domain[_EVENT_SEEN_KEY]


def record_system_event(hass: HomeAssistant, event_data: dict[str, Any]) -> bool:
    now = time.time()
    entity_id = str(event_data.get("entity_id", "") or "")
    title = str(event_data.get("title", "") or "")
    key = f"{entity_id}:{title}"

    seen = _event_seen(hass)
    cutoff = now - _EVENT_DEBOUNCE_SEC
    for k in list(seen.keys()):
        if seen[k] < cutoff:
            del seen[k]

    if key in seen:
        LOGGER.debug("Event debounced: %s", key)
        return False
    seen[key] = now

    entry = {
        "ts": now,
        "title": title[:100],
        "message": str(event_data.get("message", "") or "")[:300],
        "severity": str(event_data.get("severity", "info") or "info"),
        "entity_id": entity_id[:80],
    }
    _events_ring(hass).append(entry)
    LOGGER.info("System event recorded: %s", title)
    return True


def get_recent_system_events(hass: HomeAssistant, limit: int = _MAX_EVENTS) -> list[dict[str, Any]]:
    return list(_events_ring(hass))[-limit:]


def build_system_events_prompt_section(hass: HomeAssistant) -> str:
    events = get_recent_system_events(hass)
    if not events:
        return ""

    lines = []
    for e in events:
        severity = e.get("severity", "info")
        title = e.get("title", "event")
        entity = e.get("entity_id", "")
        msg = e.get("message", "")
        parts = [f"[{severity}] {title}"]
        if entity:
            parts.append(f"entity={entity}")
        if msg:
            parts.append(msg[:150])
        lines.append("- " + " | ".join(parts))

    return (
        "## Recent System Events (private background context)\n"
        "The following events occurred recently in Home Assistant:\n"
        + "\n".join(lines)
        + "\n\nIMPORTANT: Do NOT proactively mention these events unless the user "
        "explicitly asks about issues, anomalies, or device status. "
        "Treat this as silent background awareness only."
    )


@callback
def async_setup_event_listener(hass: HomeAssistant) -> None:
    domain = hass.data.setdefault("claw_assistant", {})
    if _EVENT_UNSUB_KEY in domain:
        return

    unsubs = []

    @callback
    def _on_custom_event(event) -> None:
        data = dict(event.data or {})
        record_system_event(hass, data)

    unsubs.append(hass.bus.async_listen(_EVENT_TYPE, _on_custom_event))

    _last_ts = {"error": 0.0, "warning": 0.0}
    _SAMPLE_SEC = {"ERROR": 180.0, "CRITICAL": 180.0, "WARNING": 300.0}

    @callback
    def _on_log_event(event) -> None:
        data = event.data or {}
        level = str(data.get("level", "")).upper()
        if level not in ("ERROR", "CRITICAL", "WARNING"):
            return
        name = str(data.get("name", "") or "")
        if "claw_assistant" in name:
            return
        now = time.time()
        bucket = "error" if level in ("ERROR", "CRITICAL") else "warning"
        if now - _last_ts[bucket] < _SAMPLE_SEC.get(level, 300.0):
            return
        _last_ts[bucket] = now
        msg = str(data.get("message", "") or "")[:200]
        if not msg:
            return
        sev = {"ERROR": "error", "CRITICAL": "critical", "WARNING": "warning"}
        record_system_event(hass, {
            "title": f"System {level}",
            "message": msg,
            "severity": sev.get(level, "info"),
            "entity_id": name,
        })

    unsubs.append(hass.bus.async_listen("system_log_event", _on_log_event))

    domain[_EVENT_UNSUB_KEY] = unsubs
    LOGGER.info("Event listeners registered: %s, system_log_event", _EVENT_TYPE)


@callback
def async_unload_event_listener(hass: HomeAssistant) -> None:
    domain = hass.data.get("claw_assistant", {})
    unsubs = domain.pop(_EVENT_UNSUB_KEY, None)
    if unsubs:
        for unsub in unsubs:
            unsub()
        LOGGER.info("Event listeners unregistered")
