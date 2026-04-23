

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import hashlib
import logging
import re
from typing import Literal

from homeassistant.core import HomeAssistant

from .heartbeat_store import async_upsert_heartbeat_task
from .memory_store import async_set_memory_entry, suggest_memory_key

LOGGER = logging.getLogger(__name__)

_EXPLICIT_CAPTURE_TOOLS = frozenset({"ConversationMemory", "HeartbeatManager"})
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
_PREFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^(?:请)?叫我(?P<value>.+)$"),
        "preferred_address",
    ),
    (
        re.compile(r"^(?:以后|之后)(?:回答|回复|说话|默认)?(?P<value>.+)$"),
        "reply_preference",
    ),
    (
        re.compile(r"^(?P<value>我(?:更)?喜欢.+)$"),
        "user_preference",
    ),
    (
        re.compile(r"^(?P<value>我不喜欢.+)$"),
        "user_avoidance",
    ),
)
_MEMORY_PREFIXES = (
    "记住",
    "记一下",
    "记下来",
    "记得",
    "帮我记住",
    "please remember",
    "remember that",
    "note that",
)


@dataclass(slots=True, frozen=True)
class PassiveSignal:


    kind: Literal["memory", "heartbeat"]
    value: str
    key: str = ""
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


def _strip_memory_prefix(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in _MEMORY_PREFIXES:
        if lowered.startswith(prefix.lower()):
            return stripped[len(prefix) :].strip(" ，,：:。")
    return stripped


def _build_memory_key(value: str) -> str:
    suggested = suggest_memory_key(value)
    if re.fullmatch(r"memory_\d+", suggested):
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"memory_{digest}"
    return suggested


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


def _extract_memory_signal(text: str) -> PassiveSignal | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    for pattern, key in _PREFERENCE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        value = _trim_trailing_punctuation(match.group("value") or "")
        if value:
            return PassiveSignal(kind="memory", key=key, value=value)

    stripped = _strip_memory_prefix(normalized)
    if stripped != normalized:
        value = _trim_trailing_punctuation(stripped)
        if value:
            for pattern, key in _PREFERENCE_PATTERNS:
                match = pattern.search(value)
                if match:
                    nested_value = _trim_trailing_punctuation(match.group("value") or "")
                    if nested_value:
                        return PassiveSignal(kind="memory", key=key, value=nested_value)
            return PassiveSignal(
                kind="memory",
                key=_build_memory_key(value),
                value=value,
            )

    return None


def extract_passive_signal(text: str) -> PassiveSignal | None:

    heartbeat_signal = _extract_heartbeat_signal(text)
    if heartbeat_signal is not None:
        return heartbeat_signal
    return _extract_memory_signal(text)


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

    if signal.kind == "heartbeat":
        notify_ch = ""
        if conversation_id and conversation_id.startswith("wechat:"):
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

    path = await async_set_memory_entry(hass, signal.key, signal.value)
    LOGGER.debug("Captured passive memory signal from conversation turn")
    return {
        "kind": "memory",
        "path": str(path),
        "key": signal.key,
        "value": signal.value,
    }
