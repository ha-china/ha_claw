

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir
from .route_hints import build_route_envelope, build_route_hint


def _memory_path() -> Path:
    return get_data_dir() / "workspace" / "MEMORY.md"
MEMORY_HEADER = "# MEMORY.md"
MEMORY_SUBTITLE = "_Curated long-term memory for kadermanager._"
_FOLLOW_UP_MEMORY_PATTERNS = (
    r"\b(remind|reminder|follow[- ]?up|check later|check back)\b",
    r"(提醒我|记得|稍后|之后|待会|回头|晚点|明天|下周|稍后检查|回头检查)",
)
_PROGRESS_MEMORY_PATTERNS = (
    r"\b(todo|next step|next action|wip|work in progress|temporary|scratch)\b",
    r"\b(iteration|wave|phase|progress|blocked|blocking)\b",
    r"\b(heartbeat|follow-up task)\b",
    r"(本轮|这一轮|下一步|稍后再修|继续修|测试通过|已修复)",
    r"(待办|进度|阻塞|临时|暂存|临时记录)",
)
_TIMESTAMP_RE = re.compile(r"\s*\[(\d{4}-\d{2}-\d{2}T[^\]]+)\]\s*$")


@dataclass(slots=True, frozen=True)
class MemoryEntry:


    key: str
    value: str
    timestamp: str = ""


@dataclass(slots=True, frozen=True)
class MemorySaveOutcome:


    status: str
    key: str
    value: str
    reason: str = ""
    existing_key: str = ""
    recommendation: str = ""
    suggested_tool: str = ""
    suggested_action: str = ""
    suggested_args: dict[str, Any] | None = None
    route_kind: str = ""
    next_action: dict[str, Any] | None = None


def _read_memory_markdown() -> str:
    mp = _memory_path()
    if not mp.exists():
        return ""
    return mp.read_text(encoding="utf-8").strip()


def _write_memory_markdown(markdown: str) -> Path:
    mp = _memory_path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(mp), markdown.strip() + "\n")
    return mp


def _parse_entries(markdown: str) -> list[MemoryEntry]:
    entries: list[MemoryEntry] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:]
        if ":" not in body:
            continue
        key, value = body.split(":", 1)
        key = key.strip()
        value = value.strip()
        timestamp = ""
        ts_match = _TIMESTAMP_RE.search(value)
        if ts_match:
            timestamp = ts_match.group(1)
            value = value[: ts_match.start()].strip()
        if key:
            entries.append(MemoryEntry(key=key, value=value, timestamp=timestamp))
    return entries


def _serialize_entries(entries: list[MemoryEntry]) -> str:
    lines = [MEMORY_HEADER, "", MEMORY_SUBTITLE, ""]
    for entry in entries:
        ts_suffix = f" [{entry.timestamp}]" if entry.timestamp else ""
        lines.append(f"- {entry.key}: {entry.value}{ts_suffix}")
    return "\n".join(lines).strip()


def _normalize_key(key: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", key.strip()).strip("_").lower()
    return normalized or f"memory_{int(datetime.now(UTC).timestamp())}"


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _transient_reason(key: str, value: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    normalized_key = key.lower().strip()
    normalized_value = value.lower().strip()
    if not normalized_value:
        return "empty_value", "Provide a stable fact before saving long-term memory.", "", "", None
    if normalized_key in {"heartbeat", "reminder", "follow_up", "follow_up_task"}:
        return "follow_up_task", "Use HeartbeatManager / HeartbeatSkill for reminders and follow-up tasks.", "HeartbeatManager", "upsert", {"title": value[:48], "schedule": "", "objective": value, "steps": value}
    if any(
        re.search(pattern, normalized_value, flags=re.IGNORECASE)
        for pattern in _FOLLOW_UP_MEMORY_PATTERNS
    ):
        return "follow_up_task", "Use HeartbeatManager / HeartbeatSkill for reminders and follow-up tasks.", "HeartbeatManager", "upsert", {"title": value[:48], "schedule": "", "objective": value, "steps": value}
    if normalized_key in {"todo", "next_step", "next_action", "progress"}:
        return "transient_progress_note", "Keep task progress in conversation history, not long-term memory.", "GetConversationHistory", "history", {"max_turns": 5}
    if any(
        re.search(pattern, normalized_value, flags=re.IGNORECASE)
        for pattern in _PROGRESS_MEMORY_PATTERNS
    ):
        return "transient_progress_note", "Keep task progress in conversation history, not long-term memory.", "GetConversationHistory", "history", {"max_turns": 5}
    return "", "", "", "", None


def _dedupe_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    seen_keys: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    deduped: list[MemoryEntry] = []
    for entry in entries:
        normalized_value = _normalize_value(entry.value)
        pair = (entry.key, normalized_value.casefold())
        if entry.key in seen_keys or pair in seen_pairs:
            continue
        seen_keys.add(entry.key)
        seen_pairs.add(pair)
        deduped.append(MemoryEntry(key=entry.key, value=normalized_value))
    return deduped


def _prepare_memory_save(
    entries: list[MemoryEntry],
    *,
    normalized_key: str,
    normalized_value: str,
) -> tuple[list[MemoryEntry], MemorySaveOutcome]:
    now_iso = datetime.now(UTC).isoformat()
    transient_reason, recommendation, suggested_tool, suggested_action, suggested_args = _transient_reason(normalized_key, normalized_value)

    next_entries: list[MemoryEntry] = []
    updated = False
    for entry in entries:
        if entry.key == normalized_key:
            next_entries.append(MemoryEntry(key=normalized_key, value=normalized_value, timestamp=now_iso))
            updated = True
        else:
            next_entries.append(entry)

    if not updated:
        for entry in entries:
            if _normalize_value(entry.value).casefold() == normalized_value.casefold():
                return _dedupe_entries(entries), MemorySaveOutcome(
                    status="skipped_duplicate",
                    key=normalized_key,
                    value=normalized_value,
                    reason="same_value_already_saved",
                    existing_key=entry.key,
                    **build_route_envelope("memory_duplicate", "ConversationMemory", "get", args={"key": entry.key}),
                )
        next_entries.append(MemoryEntry(key=normalized_key, value=normalized_value, timestamp=now_iso))

    if transient_reason:
        return _dedupe_entries(next_entries), MemorySaveOutcome(
            status="stored_with_hint",
            key=normalized_key,
            value=normalized_value,
            reason=transient_reason,
            recommendation=recommendation,
            suggested_tool=suggested_tool,
            suggested_action=suggested_action,
            suggested_args=suggested_args,
            **build_route_envelope(
                transient_reason,
                suggested_tool,
                suggested_action,
                args=suggested_args,
            ),
        )

    if updated:
        return _dedupe_entries(next_entries), MemorySaveOutcome(
            status="updated",
            key=normalized_key,
            value=normalized_value,
            **build_route_envelope("memory_updated", "ConversationMemory", "get", args={"key": normalized_key}),
        )

    return _dedupe_entries(next_entries), MemorySaveOutcome(
        status="stored",
        key=normalized_key,
        value=normalized_value,
        **build_route_envelope("memory_saved", "ConversationMemory", "get", args={"key": normalized_key}),
    )


async def async_list_memory_entries(hass: HomeAssistant) -> list[dict[str, str]]:

    markdown = await hass.async_add_executor_job(_read_memory_markdown)
    return [{"key": entry.key, "value": entry.value} for entry in _parse_entries(markdown)]


async def async_get_memory_entry(hass: HomeAssistant, key: str) -> str:

    normalized_key = _normalize_key(key)
    markdown = await hass.async_add_executor_job(_read_memory_markdown)
    for entry in _parse_entries(markdown):
        if entry.key == normalized_key:
            return entry.value
    return ""


async def async_save_memory_entry_result(
    hass: HomeAssistant,
    key: str,
    value: str,
) -> dict[str, Any]:

    normalized_key = _normalize_key(key)
    normalized_value = _normalize_value(value)
    markdown = await hass.async_add_executor_job(_read_memory_markdown)
    entries = _parse_entries(markdown)
    next_entries, outcome = _prepare_memory_save(
        entries,
        normalized_key=normalized_key,
        normalized_value=normalized_value,
    )
    path = await hass.async_add_executor_job(
        _write_memory_markdown, _serialize_entries(next_entries)
    )
    return {
        "path": str(path),
        "status": outcome.status,
        "key": outcome.key,
        "value": outcome.value,
        "reason": outcome.reason,
        "existing_key": outcome.existing_key,
        "recommendation": outcome.recommendation,
        "suggested_tool": outcome.suggested_tool,
        "suggested_action": outcome.suggested_action,
        "suggested_args": outcome.suggested_args or {},
        "route_kind": outcome.route_kind,
        "next_action": outcome.next_action or {},
        "route_hint": build_route_hint(
            outcome.route_kind,
            outcome.suggested_tool or (outcome.next_action or {}).get("tool", ""),
            outcome.suggested_action or (outcome.next_action or {}).get("action", ""),
            args=outcome.suggested_args or (outcome.next_action or {}).get("args", {}),
            recommendation=outcome.recommendation,
        ) if outcome.route_kind else {},
    }


async def async_set_memory_entry(hass: HomeAssistant, key: str, value: str) -> Path:

    result = await async_save_memory_entry_result(hass, key, value)
    return Path(result["path"])


async def async_clear_memory_entries(hass: HomeAssistant) -> Path:

    return await hass.async_add_executor_job(_write_memory_markdown, _serialize_entries([]))


def suggest_memory_key(text: str) -> str:

    stripped = text.strip()
    if not stripped:
        return _normalize_key("")
    return _normalize_key(stripped[:32])


async def async_append_memory_note(hass: HomeAssistant, note: str) -> Path:

    return await async_set_memory_entry(hass, suggest_memory_key(note), note)
