

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir


def _heartbeat_path() -> Path:
    return get_data_dir() / "workspace" / "HEARTBEAT.md"


def _heartbeat_state_path() -> Path:
    return get_data_dir() / "workspace" / "memory" / "heartbeat-state.json"
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", flags=re.MULTILINE)
_FIELD_RE = re.compile(r"^-\s+([a-z_]+):\s*(.*)$")
_TRUE_VALUES = {"true", "yes", "1", "on"}
_FALSE_VALUES = {"false", "no", "0", "off"}
_COMPLETION_VALUES = {"success", "completed", "done", "resolved", "ok"}


@dataclass(slots=True, frozen=True)
class HeartbeatTask:


    slug: str
    title: str
    schedule: str
    objective: str
    steps: str
    notes: str = ""
    enabled: bool = True
    delete_after_success: bool = False

    @property
    def when(self) -> str:

        return self.schedule


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", value.strip()).strip("_").lower()
    slug = slug.replace("-", "_")
    return slug or "heartbeat_task"


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(path), content.strip() + "\n")
    return path


def _read_state() -> dict[str, Any]:
    hsp = _heartbeat_state_path()
    if not hsp.exists():
        return {"tasks": {}}
    try:
        data = json.loads(hsp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tasks": {}}
    tasks = data.get("tasks")
    return {"tasks": tasks if isinstance(tasks, dict) else {}}


def _write_state(data: dict[str, Any]) -> Path:
    hsp = _heartbeat_state_path()
    hsp.parent.mkdir(parents=True, exist_ok=True)
    hsp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return hsp


def _bool_from_text(value: str, *, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_completion_status(status: str) -> bool:
    return status.strip().lower() in _COMPLETION_VALUES


def parse_heartbeat_tasks(markdown: str) -> list[HeartbeatTask]:

    content = markdown.strip()
    if not content:
        return []

    matches = list(_HEADING_RE.finditer(content))
    tasks: list[HeartbeatTask] = []
    for index, match in enumerate(matches):
        slug = _slugify(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        section = content[start:end]

        fields: dict[str, str] = {}
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            field_match = _FIELD_RE.match(stripped)
            if not field_match:
                continue
            fields[field_match.group(1)] = field_match.group(2).strip()

        title = fields.get("title", slug.replace("_", " ").title())
        schedule = fields.get("when", fields.get("schedule", ""))
        tasks.append(
            HeartbeatTask(
                slug=slug,
                title=title,
                schedule=schedule,
                objective=fields.get("objective", ""),
                steps=fields.get("steps", ""),
                notes=fields.get("notes", ""),
                enabled=_bool_from_text(fields.get("enabled", "true"), default=True),
                delete_after_success=_bool_from_text(
                    fields.get("delete_after_success", "false"),
                    default=False,
                ),
            )
        )

    return tasks


def serialize_heartbeat_tasks(tasks: list[HeartbeatTask]) -> str:

    lines = [
        "# HEARTBEAT.md",
        "",
        "Use HeartbeatManager to create, update, complete, or delete follow-up tasks.",
        "These are lightweight reminders/checks, not a background polling engine.",
        "",
    ]

    if not tasks:
        lines.append(
            '<!-- No active heartbeat tasks. Add one with HeartbeatManager(action="upsert", ...). -->'
        )
        return "\n".join(lines)

    for task in tasks:
        lines.extend(
            [
                f"## {task.slug}",
                f"- title: {task.title}",
                f"- enabled: {'true' if task.enabled else 'false'}",
                f"- when: {task.when}",
                f"- objective: {task.objective}",
                f"- steps: {task.steps}",
                f"- delete_after_success: {'true' if task.delete_after_success else 'false'}",
            ]
        )
        if task.notes:
            lines.append(f"- notes: {task.notes}")
        lines.append("")

    return "\n".join(lines).strip()


def upsert_heartbeat_task_markdown(
    markdown: str,
    *,
    slug: str = "",
    title: str,
    schedule: str,
    objective: str,
    steps: str,
    notes: str = "",
    enabled: bool = True,
    delete_after_success: bool = False,
) -> str:

    next_slug = _slugify(slug or title)
    tasks = {task.slug: task for task in parse_heartbeat_tasks(markdown)}
    tasks[next_slug] = HeartbeatTask(
        slug=next_slug,
        title=title.strip() or next_slug.replace("_", " ").title(),
        schedule=schedule.strip(),
        objective=objective.strip(),
        steps=steps.strip(),
        notes=notes.strip(),
        enabled=enabled,
        delete_after_success=delete_after_success,
    )
    return serialize_heartbeat_tasks(sorted(tasks.values(), key=lambda item: item.slug))


def delete_heartbeat_task_markdown(markdown: str, slug: str) -> str:

    target_slug = _slugify(slug)
    remaining = [task for task in parse_heartbeat_tasks(markdown) if task.slug != target_slug]
    return serialize_heartbeat_tasks(remaining)


def _build_task_payload(task: HeartbeatTask, task_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": task.slug,
        "title": task.title,
        "schedule": task.schedule,
        "when": task.when,
        "objective": task.objective,
        "steps": task.steps,
        "notes": task.notes,
        "enabled": task.enabled,
        "delete_after_success": task.delete_after_success,
        "last_checked_at": task_state.get("last_checked_at", ""),
        "last_status": task_state.get("last_status", ""),
        "last_note": task_state.get("last_note", ""),
        "last_completed_at": task_state.get("last_completed_at", ""),
        "run_count": int(task_state.get("run_count", 0) or 0),
    }


async def async_list_heartbeat_tasks(hass: HomeAssistant) -> list[dict[str, Any]]:

    markdown = await hass.async_add_executor_job(_read_text, _heartbeat_path())
    state = await hass.async_add_executor_job(_read_state)
    tasks: list[dict[str, Any]] = []
    for task in parse_heartbeat_tasks(markdown):
        task_state = state["tasks"].get(task.slug, {})
        tasks.append(_build_task_payload(task, task_state if isinstance(task_state, dict) else {}))
    return tasks


async def async_upsert_heartbeat_task(
    hass: HomeAssistant,
    *,
    slug: str = "",
    title: str,
    schedule: str,
    objective: str,
    steps: str,
    notes: str = "",
    enabled: bool = True,
    delete_after_success: bool = False,
) -> Path:

    markdown = await hass.async_add_executor_job(_read_text, _heartbeat_path())
    updated = upsert_heartbeat_task_markdown(
        markdown,
        slug=slug,
        title=title,
        schedule=schedule,
        objective=objective,
        steps=steps,
        notes=notes,
        enabled=enabled,
        delete_after_success=delete_after_success,
    )
    return await hass.async_add_executor_job(_write_text, _heartbeat_path(), updated)


async def async_delete_heartbeat_task(hass: HomeAssistant, slug: str) -> Path:

    markdown = await hass.async_add_executor_job(_read_text, _heartbeat_path())
    updated = delete_heartbeat_task_markdown(markdown, slug)
    state = await hass.async_add_executor_job(_read_state)
    state["tasks"].pop(_slugify(slug), None)
    await hass.async_add_executor_job(_write_state, state)
    return await hass.async_add_executor_job(_write_text, _heartbeat_path(), updated)


async def async_record_heartbeat_result(
    hass: HomeAssistant,
    *,
    slug: str,
    status: str,
    note: str = "",
) -> dict[str, Any]:

    normalized_slug = _slugify(slug)
    status_text = status.strip()
    note_text = note.strip()
    markdown = await hass.async_add_executor_job(_read_text, _heartbeat_path())
    tasks = {task.slug: task for task in parse_heartbeat_tasks(markdown)}
    state = await hass.async_add_executor_job(_read_state)
    task_state = state.setdefault("tasks", {}).setdefault(normalized_slug, {})
    task_state.update(
        {
            "last_checked_at": _now_iso(),
            "last_status": status_text,
            "last_note": note_text,
            "run_count": int(task_state.get("run_count", 0) or 0) + 1,
        }
    )
    if _is_completion_status(status_text):
        task_state["last_completed_at"] = _now_iso()

    state_path = await hass.async_add_executor_job(_write_state, state)
    task_deleted = False
    heartbeat_path = _heartbeat_path()
    task = tasks.get(normalized_slug)
    if task and task.delete_after_success and _is_completion_status(status_text):
        heartbeat_path = await async_delete_heartbeat_task(hass, normalized_slug)
        task_deleted = True

    return {
        "state_path": str(state_path),
        "heartbeat_path": str(heartbeat_path),
        "task_deleted": task_deleted,
        "status": status_text,
    }


async def async_clear_heartbeat_result(hass: HomeAssistant, slug: str = "") -> Path:

    state = await hass.async_add_executor_job(_read_state)
    if slug.strip():
        state.setdefault("tasks", {}).pop(_slugify(slug), None)
    else:
        state = {"tasks": {}}
    return await hass.async_add_executor_job(_write_state, state)


_INTERVAL_RE = re.compile(r"^(?:every\s+)?(\d+)\s*([smhd])(?:ec|in|our|ay)?s?$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_CRON_RE = re.compile(r"^[*/\d,\-]+(?:\s+[*/\d,\-]+){4}$")


def _parse_interval_seconds(schedule: str) -> int | None:
    m = _INTERVAL_RE.match(schedule.strip())
    if m:
        return int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()[0]]
    return None


def _cron_field_matches(field: str, value: int, max_val: int) -> bool:
    for part in field.split(","):
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            start = 0 if base == "*" else int(base)
            if step > 0 and (value - start) % step == 0 and value >= start:
                return True
        elif "-" in part and part != "*":
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        elif part == "*":
            return True
        else:
            if int(part) == value:
                return True
    return False


def _cron_matches_now(expr: str, now: datetime) -> bool:
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    return (
        _cron_field_matches(minute, now.minute, 59)
        and _cron_field_matches(hour, now.hour, 23)
        and _cron_field_matches(dom, now.day, 31)
        and _cron_field_matches(month, now.month, 12)
        and _cron_field_matches(dow, now.weekday(), 6)
    )


def _is_cron(schedule: str) -> bool:
    return bool(_CRON_RE.match(schedule.strip()))


def _next_due_seconds(schedule: str, last_checked: str, now: datetime) -> int | None:
    interval = _parse_interval_seconds(schedule)
    if interval is not None:
        if not last_checked:
            return 0
        try:
            elapsed = (now - datetime.fromisoformat(last_checked)).total_seconds()
            remaining = interval - elapsed
            return max(0, int(remaining))
        except ValueError:
            return 0

    if _is_cron(schedule):
        if not last_checked:
            if _cron_matches_now(schedule, now):
                return 0
            for future_min in range(1, 1441):
                check = now + timedelta(minutes=future_min)
                if _cron_matches_now(schedule, check):
                    return future_min
            return None
        try:
            last_dt = datetime.fromisoformat(last_checked)
            if (now - last_dt).total_seconds() < 60:
                for future_min in range(1, 1441):
                    check = now + timedelta(minutes=future_min)
                    if _cron_matches_now(schedule, check):
                        return future_min
                return None
            if _cron_matches_now(schedule, now):
                return 0
            for future_min in range(1, 1441):
                check = now + timedelta(minutes=future_min)
                if _cron_matches_now(schedule, check):
                    return future_min
        except ValueError:
            if _cron_matches_now(schedule, now):
                return 0
            for future_min in range(1, 1441):
                check = now + timedelta(minutes=future_min)
                if _cron_matches_now(schedule, check):
                    return future_min
            return None
        return None

    return None


def get_due_tasks() -> list[HeartbeatTask]:
    markdown = _read_text(_heartbeat_path())
    state = _read_state()
    now = datetime.now(UTC)
    due: list[HeartbeatTask] = []
    for task in parse_heartbeat_tasks(markdown):
        if not task.enabled:
            continue
        task_state = state.get("tasks", {}).get(task.slug, {})
        last_checked = task_state.get("last_checked_at", "")
        remaining = _next_due_seconds(task.schedule, last_checked, now)
        if remaining is not None and remaining <= 0:
            due.append(task)
    return due
