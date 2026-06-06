

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from ..utils.data_path import get_data_dir
from ..utils.route_hints import build_route_envelope, build_route_hint


MEMORY_TARGETS = ("memory", "user")


def _memory_path(target: str = "memory") -> Path:
    ws = get_data_dir() / "workspace"
    if target == "user":
        return ws / "USER.md"
    return ws / "MEMORY.md"


def _target_subtitle(target: str) -> str:
    if target == "user":
        return "_User preferences, style, habits for claw_assistant._"
    return "_Curated long-term memory for claw_assistant._"


def _target_limit(target: str) -> int:
    if target == "user":
        return USER_NOTES_CHAR_LIMIT
    return MEMORY_CHAR_LIMIT


def _normalize_target(target: str) -> str:
    t = (target or "").strip().lower()
    if t not in MEMORY_TARGETS:
        return "memory"
    return t

MEMORY_SUBTITLE = "_Curated long-term memory for claw_assistant._"
_TIMESTAMP_RE = re.compile(r"\s*\[(\d{4}-\d{2}-\d{2}T[^\]]+)\]\s*$")
MEMORY_CHAR_LIMIT = 7000
USER_NOTES_CHAR_LIMIT = 4000

_MEMORY_THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ignore\s+(?:previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(?:your|all|any)\s+(?:instructions|rules|guidelines)", "disregard_rules"),
    (r"curl\s+[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget"),
    (r"cat\s+[^\n]*(?:\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", "read_secrets"),
    (r"authorized_keys", "ssh_backdoor"),
)
_INVISIBLE_CHARS = frozenset(
    "\u200b\u200c\u200d\u2060\ufeff\u202a\u202b\u202c\u202d\u202e"
)


def _scan_memory_content(content: str) -> str:
    for char in content:
        if char in _INVISIBLE_CHARS:
            return f"invisible_unicode_U+{ord(char):04X}"
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, flags=re.IGNORECASE):
            return pid
    return ""


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


def _read_memory_markdown(target: str = "memory") -> str:
    mp = _memory_path(target)
    if not mp.exists():
        return ""
    return mp.read_text(encoding="utf-8").strip()


def _write_memory_markdown(markdown: str, target: str = "memory") -> Path:
    mp = _memory_path(target)
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


def _serialize_entries(entries: list[MemoryEntry], target: str = "memory") -> str:
    lines = [_target_subtitle(target), ""]
    for entry in entries:
        ts_suffix = f" [{entry.timestamp}]" if entry.timestamp else ""
        lines.append(f"- {entry.key}: {entry.value}{ts_suffix}")
    return "\n".join(lines).strip()


def _normalize_key(key: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", key.strip()).strip("_").lower()
    return normalized or f"memory_{int(datetime.now(UTC).timestamp())}"


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


_HARD_TODO_PREFIX_RE = re.compile(
    r"^(?:todo|wip|next[\s_-]?step|next[\s_-]?action|progress|session[\s_-]?note|task[\s_-]?status|just[\s_-]?did|completed|result)\s*[:：]",
    flags=re.IGNORECASE,
)
_HARD_TODO_PREFIX_CN_RE = re.compile(r"^(?:待办|下一步|本轮|进度|刚才|本次|会话|任务状态|执行结果|图片描述|分析结果)\s*[:：]")


def _transient_reason(key: str, value: str) -> tuple[str, str, str, str, dict[str, Any] | None]:
    normalized_key = key.lower().strip()
    normalized_value = value.strip()
    if not normalized_value:
        return "empty_value", "Provide a stable fact before saving long-term memory.", "", "", None
    if normalized_key in {"heartbeat", "reminder", "follow_up", "follow_up_task"}:
        return "follow_up_task", "Use HeartbeatManager / HeartbeatSkill for reminders and follow-up tasks.", "HeartbeatManager", "upsert", {"title": value[:48], "schedule": "", "objective": value, "steps": value}
    if _HARD_TODO_PREFIX_RE.match(normalized_value) or _HARD_TODO_PREFIX_CN_RE.match(normalized_value):
        return "transient_progress_note", "Keep task progress in conversation history, not long-term memory.", "GetConversationHistory", "history", {"max_turns": 5}
    if normalized_key in {
        "todo", "wip", "next_step", "next_action", "progress",
        "session_note", "task_status", "just_did", "completed_task",
        "tool_result", "image_description", "media_description",
        "current_task", "this_session", "analysis_result",
    }:
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
    target: str = "memory",
) -> tuple[list[MemoryEntry], MemorySaveOutcome]:
    char_limit = _target_limit(target)
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

    projected_chars = sum(len(e.key) + len(e.value) + 6 for e in next_entries)
    if projected_chars > char_limit:
        return _dedupe_entries(entries), MemorySaveOutcome(
            status="rejected_full",
            key=normalized_key,
            value=normalized_value,
            reason="memory_at_capacity",
            recommendation=(
                f"{target.capitalize()} memory at {projected_chars}/{char_limit} chars. "
                "Consolidate or remove existing entries before adding new ones."
            ),
            suggested_tool="ConversationMemory",
            suggested_action="list",
            **build_route_envelope("memory_full", "ConversationMemory", "list", args={"target": target}),
        )

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


async def async_list_memory_entries(hass: HomeAssistant, *, target: str = "memory") -> list[dict[str, str]]:

    target = _normalize_target(target)
    markdown = await hass.async_add_executor_job(_read_memory_markdown, target)
    return [{"key": entry.key, "value": entry.value} for entry in _parse_entries(markdown)]


async def async_get_memory_entry(hass: HomeAssistant, key: str, *, target: str = "memory") -> str:

    target = _normalize_target(target)
    normalized_key = _normalize_key(key)
    markdown = await hass.async_add_executor_job(_read_memory_markdown, target)
    for entry in _parse_entries(markdown):
        if entry.key == normalized_key:
            return entry.value
    return ""


async def async_save_memory_entry_result(
    hass: HomeAssistant,
    key: str,
    value: str,
    *,
    target: str = "memory",
) -> dict[str, Any]:

    target = _normalize_target(target)
    normalized_key = _normalize_key(key)
    normalized_value = _normalize_value(value)
    threat_id = _scan_memory_content(f"{normalized_key} {normalized_value}")
    if threat_id:
        return {
            "path": "",
            "status": "rejected_unsafe",
            "key": normalized_key,
            "value": normalized_value,
            "reason": threat_id,
            "existing_key": "",
            "recommendation": (
                f"Blocked: content matched threat pattern '{threat_id}'. "
                "Memory entries inject into the system prompt and must not carry "
                "prompt-injection or exfiltration payloads."
            ),
            "suggested_tool": "",
            "suggested_action": "",
            "suggested_args": {},
            "route_kind": "memory_unsafe",
            "next_action": {},
            "route_hint": {},
            "target": target,
        }
    markdown = await hass.async_add_executor_job(_read_memory_markdown, target)
    entries = _parse_entries(markdown)
    next_entries, outcome = _prepare_memory_save(
        entries,
        normalized_key=normalized_key,
        normalized_value=normalized_value,
        target=target,
    )
    path = await hass.async_add_executor_job(
        _write_memory_markdown, _serialize_entries(next_entries, target), target
    )
    if outcome.status in ("stored", "updated"):
        try:
            from .graph_service import async_link, async_recall, async_remember
            kind = "preference" if target == "user" else "fact"
            result = await async_remember(
                hass,
                kind=kind,
                title=outcome.key,
                body=outcome.value,
                source_doc=f"ConversationMemory/{target}",
            )
            if result is not None:
                new_id, was_new = result
                if was_new:
                    hits = await async_recall(
                        hass, f"{outcome.key} {outcome.value}",
                        limit=3, expand=False,
                    )
                    for h in hits:
                        if h.node.id != new_id:
                            await async_link(hass, new_id, h.node.id, "related_to")
        except Exception:
            pass
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
        "target": target,
    }


async def async_set_memory_entry(hass: HomeAssistant, key: str, value: str, *, target: str = "memory") -> Path:

    result = await async_save_memory_entry_result(hass, key, value, target=target)
    path_str = result.get("path") or ""
    return Path(path_str) if path_str else _memory_path(_normalize_target(target))


async def async_clear_memory_entries(hass: HomeAssistant, *, target: str = "memory") -> Path:

    target = _normalize_target(target)
    return await hass.async_add_executor_job(
        _write_memory_markdown, _serialize_entries([], target), target
    )


async def async_delete_memory_entry(
    hass: HomeAssistant, key: str, *, target: str = "memory"
) -> tuple[Path, bool]:
    target = _normalize_target(target)
    normalized_key = _normalize_key(key)
    markdown = await hass.async_add_executor_job(_read_memory_markdown, target)
    entries = _parse_entries(markdown)
    next_entries = [entry for entry in entries if entry.key != normalized_key]
    deleted = len(next_entries) != len(entries)
    if not deleted:
        return _memory_path(target), False
    path = await hass.async_add_executor_job(
        _write_memory_markdown, _serialize_entries(next_entries, target), target
    )
    return path, True


def suggest_memory_key(text: str) -> str:

    stripped = text.strip()
    if not stripped:
        return _normalize_key("")
    return _normalize_key(stripped[:32])


async def async_append_memory_note(hass: HomeAssistant, note: str) -> Path:

    return await async_set_memory_entry(hass, suggest_memory_key(note), note)
