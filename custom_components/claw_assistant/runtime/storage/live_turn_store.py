from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
import json
import logging
from pathlib import Path
import threading
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_MAX_EVENTS = 200
_MAX_TOOL_RESULTS = 20
_MAX_RESPONSE_PARTS = 20

_STORE_LOCK = threading.Lock()


def _store_path() -> Path:
    return get_data_dir() / "runtime" / "live-turns.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _backup_corrupt_store(path: Path) -> None:
    try:
        backup = path.with_name(
            f"{path.stem}.corrupt-{int(datetime.now(UTC).timestamp())}{path.suffix}"
        )
        path.replace(backup)
        LOGGER.warning("live-turns store was corrupt; backed up to %s", backup)
    except OSError:
        LOGGER.warning("live-turns store corrupt and backup failed", exc_info=True)


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {"turns": {}}
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _backup_corrupt_store(path)
        return {"turns": {}}
    turns = data.get("turns")
    return {"turns": turns if isinstance(turns, dict) else {}}


def _read_store_safe() -> dict[str, Any]:
    try:
        return _read_store()
    except OSError:
        LOGGER.debug("live-turns read failed", exc_info=True)
        return {"turns": {}}


def _write_store(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(path), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    safe = _json_safe(event)
    if not isinstance(safe, dict):
        return {"data": _clip_text(safe, 1000)}
    data = safe.get("data")
    if isinstance(data, dict):
        delta = data.get("delta") or data.get("chat_log_delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            delta["content"] = _clip_text(delta["content"], 2000)
    return safe


def save_live_turn_snapshot(
    *,
    conversation_id: str,
    active: bool,
    status: str = "",
    reason: str = "",
    text: str = "",
    current_thought: str = "",
    tool_results: list[Any] | None = None,
    response_parts: list[Any] | None = None,
    event: dict[str, Any] | None = None,
    phase: str = "",
    update_status: bool = True,
) -> None:
    if not conversation_id:
        return
    with _STORE_LOCK:
        try:
            data = _read_store()
        except OSError:
            LOGGER.warning(
                "Skipping live-turn write for %s: store read failed",
                conversation_id,
                exc_info=True,
            )
            return
        _apply_live_turn_update(
            data,
            conversation_id=conversation_id,
            active=active,
            status=status,
            reason=reason,
            text=text,
            current_thought=current_thought,
            tool_results=tool_results,
            response_parts=response_parts,
            event=event,
            phase=phase,
            update_status=update_status,
        )
        _write_store(data)


def _apply_live_turn_update(
    data: dict[str, Any],
    *,
    conversation_id: str,
    active: bool,
    status: str,
    reason: str,
    text: str,
    current_thought: str,
    tool_results: list[Any] | None,
    response_parts: list[Any] | None,
    event: dict[str, Any] | None,
    phase: str,
    update_status: bool,
) -> None:
    turns = data.setdefault("turns", {})
    turn = turns.setdefault(conversation_id, {})
    now = _now_iso()
    turn["conversation_id"] = conversation_id
    turn["updated_at"] = now
    if update_status:
        turn.update(
            {
                "active": active,
                "status": status or ("active" if active else "finished"),
                "reason": reason,
            }
        )
    if active and not turn.get("started_at"):
        turn["started_at"] = now
    if text:
        turn["text"] = _clip_text(text, 4000)
    if current_thought:
        turn["current_thought"] = _clip_text(current_thought, 1000)
    if phase:
        turn["phase"] = phase
    if tool_results is not None:
        turn["tool_results"] = _json_safe(tool_results[-_MAX_TOOL_RESULTS:])
    if response_parts is not None:
        turn["response_parts"] = _json_safe(response_parts[-_MAX_RESPONSE_PARTS:])
    if event is not None:
        events = turn.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            turn["events"] = events
        events.append(_normalize_event(event))
        del events[:-_MAX_EVENTS]


async def async_save_live_turn_snapshot(
    hass: HomeAssistant,
    *,
    conversation_id: str,
    active: bool,
    status: str = "",
    reason: str = "",
    text: str = "",
    current_thought: str = "",
    tool_results: list[Any] | None = None,
    response_parts: list[Any] | None = None,
    event: dict[str, Any] | None = None,
    phase: str = "",
    update_status: bool = True,
) -> None:
    await hass.async_add_executor_job(
        partial(
            save_live_turn_snapshot,
            conversation_id=conversation_id,
            active=active,
            status=status,
            reason=reason,
            text=text,
            current_thought=current_thought,
            tool_results=tool_results,
            response_parts=response_parts,
            event=event,
            phase=phase,
            update_status=update_status,
        )
    )


async def async_finalize_live_turn(
    hass: HomeAssistant,
    conversation_id: str,
    final_text: str = "",
) -> None:
    if not conversation_id:
        return
    text = str(final_text or "").strip()
    await async_save_live_turn_snapshot(
        hass,
        conversation_id=str(conversation_id),
        active=False,
        status="finished",
        reason="finalized",
        text=text,
        response_parts=[text] if text else [],
        phase="finished",
    )


async def async_get_live_turn_snapshot(
    hass: HomeAssistant,
    conversation_id: str = "",
) -> dict[str, Any] | None:
    data = await hass.async_add_executor_job(_read_store_safe)
    turns = data.get("turns", {})
    if not isinstance(turns, dict) or not turns:
        return None
    if conversation_id and isinstance(turns.get(conversation_id), dict):
        return dict(turns[conversation_id])
    active = [
        item for item in turns.values()
        if isinstance(item, dict) and item.get("active")
    ]
    if active:
        return dict(sorted(active, key=lambda item: str(item.get("updated_at", "")))[-1])
    snapshots = [item for item in turns.values() if isinstance(item, dict)]
    if not snapshots:
        return None
    return dict(sorted(snapshots, key=lambda item: str(item.get("updated_at", "")))[-1])


async def async_get_live_turn_events(
    hass: HomeAssistant,
    conversation_id: str,
) -> list[dict[str, Any]]:
    snapshot = await async_get_live_turn_snapshot(hass, conversation_id)
    if not snapshot:
        return []
    events = snapshot.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]
