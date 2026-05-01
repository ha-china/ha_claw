

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import logging
import re
from typing import Literal

from homeassistant.core import HomeAssistant

from .heartbeat_store import async_upsert_heartbeat_task

LOGGER = logging.getLogger(__name__)

_EXPLICIT_CAPTURE_TOOLS = frozenset({"HeartbeatManager"})
_RECURRING_SCHEDULE_KEYWORDS = (
    "每天",
    "每周",
    "每月",
    "工作日",
    "daily",
    "every day",
    "every week",
    "every month",
    "weekdays",
)
_SCHEDULE_HINTS = (
    "今天",
    "今晚",
    "稍后",
    "等会",
    "待会",
    "明天",
    "后天",
    "下周",
    "下个月",
    "later today",
    "later",
    "tonight",
    "tomorrow",
    "next week",
    "next month",
    "daily",
    "every day",
    "every week",
    "every month",
    "weekdays",
)
_REMINDER_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (re.compile(r"(?P<schedule>.*?)(?:提醒我)(?P<body>.+)"), False),
    (re.compile(r"(?P<schedule>.*?)(?:记得帮我)(?P<body>.+)"), False),
    (re.compile(r"(?P<schedule>.*?)(?:记得)(?P<body>.+)"), True),
    (
        re.compile(
            r"(?P<schedule>.*?)(?:remind me(?:\s+to\b)?)(?P<body>.+)",
            re.IGNORECASE,
        ),
        False,
    ),
)
@dataclass(slots=True, frozen=True)
class PassiveSignal:


    kind: Literal["heartbeat"]
    value: str
    title: str = ""
    schedule: str = ""
    objective: str = ""
    steps: str = ""
    delete_after_success: bool = True


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _trim_trailing_punctuation(text: str) -> str:
    return text.strip().strip("。！？!?.,;；：:")


def _strip_leading_schedule_hint(body: str, schedule: str) -> str:
    cleaned = body.strip()
    if not cleaned or not schedule:
        return cleaned

    prefix_patterns = (
        rf"^(?:在|于)?\s*{re.escape(schedule)}(?:\s+to\b)?[\s,，:：-]*",
        rf"^(?:on|at|by|for)?\s*{re.escape(schedule)}(?:\s+to\b)?[\s,，:：-]*",
    )
    for pattern in prefix_patterns:
        next_body = re.sub(pattern, "", cleaned, count=1, flags=re.IGNORECASE).strip()
        if next_body:
            return next_body
    return cleaned


def _detect_schedule_hint(text: str) -> str:
    lowered = text.lower()
    for hint in _SCHEDULE_HINTS:
        if hint.lower() in lowered:
            return hint
    return ""


def _looks_recurring(text: str, schedule: str) -> bool:
    lowered = f"{schedule} {text}".lower()
    return any(keyword.lower() in lowered for keyword in _RECURRING_SCHEDULE_KEYWORDS)


def _extract_heartbeat_signal(text: str) -> PassiveSignal | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    for pattern, requires_schedule_hint in _REMINDER_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        schedule = _trim_trailing_punctuation(match.group("schedule") or "")
        body = _trim_trailing_punctuation(match.group("body") or "")
        if not body:
            continue
        if not schedule:
            schedule = _detect_schedule_hint(normalized)
        body = _strip_leading_schedule_hint(body, schedule)
        if requires_schedule_hint and not schedule:
            continue
        if not body:
            continue
        return PassiveSignal(
            kind="heartbeat",
            value=normalized,
            title=body[:48],
            schedule=schedule,
            objective=body,
            steps=body,
            delete_after_success=not _looks_recurring(body, schedule),
        )

    return None


def extract_passive_signal(text: str) -> PassiveSignal | None:

    return _extract_heartbeat_signal(text)


async def async_capture_passive_signal(
    hass: HomeAssistant,
    *,
    user_text: str,
    assistant_text: str,
    tool_calls: Collection[str] | None = None,
    conversation_id: str | None = None,
) -> dict[str, str | bool] | None:

    if not _normalize_text(user_text) or not _normalize_text(assistant_text):
        return None

    used_tools = {str(tool_name) for tool_name in (tool_calls or ()) if str(tool_name).strip()}
    if used_tools & _EXPLICIT_CAPTURE_TOOLS:
        return None

    signal = extract_passive_signal(user_text)
    if signal is None:
        return None

    notify_ch = ""
    from .state import is_im_channel
    if is_im_channel(conversation_id):
        notify_ch = conversation_id
    path = await async_upsert_heartbeat_task(
        hass,
        title=signal.title or signal.objective[:48] or "follow up",
        schedule=signal.schedule,
        objective=signal.objective or signal.value,
        steps=signal.steps or signal.value,
        delete_after_success=signal.delete_after_success,
        notify_channel=notify_ch,
    )
    LOGGER.debug("Captured passive heartbeat signal from conversation turn")
    return {
        "kind": "heartbeat",
        "path": str(path),
        "title": signal.title or signal.objective[:48],
        "schedule": signal.schedule,
        "delete_after_success": signal.delete_after_success,
    }
