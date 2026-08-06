"""ApprovalStore — generic confirmation queue (G5).

Generalizes the pending/approval_id mechanism previously embedded in
``config_file_store`` (uuid4().hex[:12], ``state["pending"][id] = operation``,
resolve with validation). Every pending entry carries a ``kind`` marker so the
same state bucket can host both config-file operations (``kind=config_operation``)
and G5 tool approvals (``kind=tool_approval``) without ambiguity.

The queue lives in the runtime config-approval state bucket
(``get_config_approval_state(hass)``) so the existing IM approval bridge and
task-loop choice surfacing keep working unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

from ..core.state import get_config_approval_state, get_task_loop_state

LOGGER = logging.getLogger(__name__)

KIND_CONFIG_OPERATION = "config_operation"
KIND_TOOL_APPROVAL = "tool_approval"

_MAX_HISTORY = 50


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _history(hass: HomeAssistant) -> list[dict[str, Any]]:
    state = get_config_approval_state(hass)
    history = state.setdefault("approval_history", [])
    if not isinstance(history, list):
        history = []
        state["approval_history"] = history
    return history


def create(
    hass: HomeAssistant,
    tool: str,
    params: dict[str, Any],
    user_key: str | None,
    risk: int,
    *,
    kind: str = KIND_TOOL_APPROVAL,
    **extra: Any,
) -> dict[str, Any]:
    """Create a pending approval and return the stored entry dict.

    ``extra`` carries kind-specific fields (e.g. config-file operation payload)
    and is stored verbatim on the entry.
    """
    approval_id = uuid4().hex[:12]
    entry: dict[str, Any] = {
        "approval_id": approval_id,
        "kind": kind,
        "tool": str(tool or ""),
        "params": params if isinstance(params, dict) else {},
        "user_key": user_key,
        "risk": int(risk),
        "status": "pending",
        "created_at": _now_iso(),
    }
    entry.update(extra)

    state = get_config_approval_state(hass)
    state.setdefault("pending", {})[approval_id] = entry

    task_loop = get_task_loop_state(hass)
    task_loop["waiting_choice"] = True
    task_loop["last_choice"] = None
    return dict(entry)


def list_pending(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return all pending approval entries (newest first)."""
    state = get_config_approval_state(hass)
    pending = state.get("pending", {})
    entries = [dict(e) for e in pending.values()]
    entries.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
    return entries


def get(hass: HomeAssistant, approval_id: str) -> dict[str, Any] | None:
    """Fetch a pending entry by id without resolving it."""
    state = get_config_approval_state(hass)
    entry = state.get("pending", {}).get(approval_id)
    return dict(entry) if entry else None


def resolve(
    hass: HomeAssistant,
    approval_id: str,
    approved: bool,
    approver: str | None = None,
) -> bool:
    """Resolve a pending approval. Returns True on success (entry existed)."""
    state = get_config_approval_state(hass)
    entry = state.get("pending", {}).pop(approval_id, None)
    if entry is None:
        return False

    entry["status"] = "approved" if approved else "denied"
    entry["approver"] = approver or ""
    entry["resolved_at"] = _now_iso()

    history = _history(hass)
    history.insert(0, dict(entry))
    del history[_MAX_HISTORY:]

    state["last_resolution"] = {
        "approval_id": approval_id,
        "status": entry["status"],
        "tool": entry.get("tool", ""),
        "kind": entry.get("kind", ""),
    }
    return True


def history(hass: HomeAssistant, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent resolved approval records (newest first)."""
    items = _history(hass)
    return [dict(e) for e in items[: max(0, int(limit))]]


def cancel(hass: HomeAssistant, approval_id: str, approver: str | None = None) -> bool:
    """Remove a pending approval without a decision (e.g. timeout)."""
    return resolve(hass, approval_id, False, approver=approver or "timeout")
