

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .state import get_config_approval_state, get_task_loop_state

_PREVIEW_LIMIT = 1200
_READ_LIMIT = 20000
# Actions that are reversible enough to skip user-consent gating entirely.
# Only `delete` is destructive and requires explicit AI-asserted consent.
_ACTIONS_REQUIRING_CONFIRMATION: frozenset[str] = frozenset({"delete"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _config_root(hass: HomeAssistant) -> Path:
    config_dir = getattr(hass.config, "config_dir", None)
    if not config_dir:
        raise ValueError("Home Assistant config directory is unavailable")
    return Path(config_dir).resolve()


_SENSITIVE_KEYS = frozenset({"state_template", "press_action"})


def _redact_templates_in_json(raw: str) -> str:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for k in _SENSITIVE_KEYS:
                    if k in item:
                        item[k] = "(redacted)"
                opts = item.get("options")
                if isinstance(opts, dict):
                    for k in _SENSITIVE_KEYS:
                        if k in opts:
                            opts[k] = "(redacted)"
    return json.dumps(data, ensure_ascii=False, indent=2)


def _resolve_config_path(hass: HomeAssistant, relative_path: str = "") -> Path:
    root = _config_root(hass)
    normalized = (relative_path or "").strip().lstrip("/")
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Path escapes the Home Assistant config directory")
    return target


def _preview_text(content: str, limit: int = _PREVIEW_LIMIT) -> str:
    stripped = content.strip()
    return stripped if len(stripped) <= limit else stripped[:limit] + "\n...[truncated]"


def _serialize_entry(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix() if path != root else "."
    return {
        "path": relative,
        "name": path.name or ".",
        "is_dir": path.is_dir(),
        "size": path.stat().st_size if path.exists() and path.is_file() else 0,
    }


def list_config_entries_sync(
    hass: HomeAssistant,
    relative_path: str = "",
    include_hidden: bool = False,
) -> dict[str, Any]:
    root = _config_root(hass)
    target = _resolve_config_path(hass, relative_path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {relative_path or '.'}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {relative_path}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not include_hidden and child.name.startswith("."):
            continue
        entries.append(_serialize_entry(child, root))

    return {
        "path": target.relative_to(root).as_posix() if target != root else ".",
        "entries": entries,
        "count": len(entries),
    }


def read_config_file_sync(hass: HomeAssistant, relative_path: str) -> dict[str, Any]:
    target = _resolve_config_path(hass, relative_path)
    root = _config_root(hass)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {relative_path}")
    if not target.is_file():
        raise IsADirectoryError(f"Expected a file, got directory: {relative_path}")

    content = target.read_text(encoding="utf-8", errors="ignore")
    if target.name == "custom_entities.json":
        content = _redact_templates_in_json(content)
    truncated = len(content) > _READ_LIMIT
    if truncated:
        content = content[:_READ_LIMIT] + "\n...[truncated]"
    return {
        "path": target.relative_to(root).as_posix(),
        "content": content,
        "truncated": truncated,
        "size": target.stat().st_size,
    }


def _apply_operation(target: Path, action: str, content: str = "", *, create_dirs: bool = False) -> None:
    if action == "write":
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return
    if action == "append":
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return
    if action == "mkdir":
        target.mkdir(parents=create_dirs, exist_ok=True)
        return
    if action == "delete":
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        return
    raise ValueError(f"Unsupported config action: {action}")


def stage_config_operation(
    hass: HomeAssistant,
    *,
    action: str,
    relative_path: str,
    content: str = "",
    create_dirs: bool = False,
) -> dict[str, Any]:
    root = _config_root(hass)
    target = _resolve_config_path(hass, relative_path)
    approval_id = uuid4().hex[:12]
    operation = {
        "approval_id": approval_id,
        "action": action,
        "path": target.relative_to(root).as_posix() if target != root else ".",
        "content": content,
        "create_dirs": create_dirs,
        "created_at": _now_iso(),
        "preview": _preview_text(content) if content else "",
        "exists": target.exists(),
        "is_dir": target.is_dir() if target.exists() else action == "mkdir",
    }
    state = get_config_approval_state(hass)
    state.setdefault("pending", {})[approval_id] = operation

    task_loop = get_task_loop_state(hass)
    task_loop["waiting_choice"] = True
    task_loop["last_choice"] = None

    return operation


def list_pending_operations(hass: HomeAssistant) -> list[dict[str, Any]]:
    state = get_config_approval_state(hass)
    return list(state.get("pending", {}).values())


def apply_staged_operation_sync(
    hass: HomeAssistant,
    approval_id: str,
    *,
    user_consent: bool = False,
    consent_quote: str = "",
) -> dict[str, Any]:
    """Apply a staged operation.

    Consent model (no token dictionaries, no history scanning):
      * write / append / mkdir are reversible and apply unconditionally.
      * delete requires ``user_consent=True``. The AI is responsible for
        having actually asked the user in chat and judging the reply.
        ``consent_quote`` is optional audit metadata (the user's own
        words); it is only stored, never matched against a dictionary.
    """

    state = get_config_approval_state(hass)
    operation = state.get("pending", {}).get(approval_id)
    if not operation:
        raise ValueError(f"Pending approval not found: {approval_id}")
    if (
        operation["action"] in _ACTIONS_REQUIRING_CONFIRMATION
        and not user_consent
    ):
        raise PermissionError(
            "This is a destructive delete. Ask the user in chat what should be "
            "deleted and why, decide whether their reply is an agreement, then "
            "retry apply with user_consent=true (and consent_quote=\"<their words>\" "
            "for audit)."
        )

    target = _resolve_config_path(hass, operation["path"])
    _apply_operation(
        target,
        operation["action"],
        operation.get("content", ""),
        create_dirs=bool(operation.get("create_dirs", False)),
    )
    state["pending"].pop(approval_id, None)
    resolution: dict[str, Any] = {
        "approval_id": approval_id,
        "status": "applied",
        "path": operation["path"],
        "action": operation["action"],
    }
    if operation["action"] in _ACTIONS_REQUIRING_CONFIRMATION:
        resolution["consent_quote"] = consent_quote.strip()
    state["last_resolution"] = resolution
    return dict(resolution)


def cancel_staged_operation(hass: HomeAssistant, approval_id: str) -> dict[str, Any]:
    state = get_config_approval_state(hass)
    if approval_id not in state.get("pending", {}):
        raise ValueError(f"Pending approval not found: {approval_id}")
    operation = state["pending"].pop(approval_id)
    state["last_resolution"] = {
        "approval_id": approval_id,
        "status": "cancelled",
        "path": operation["path"],
        "action": operation["action"],
    }
    return {
        "approval_id": approval_id,
        "status": "cancelled",
        "path": operation["path"],
        "action": operation["action"],
    }


def build_config_approval_prompt_block(hass: HomeAssistant) -> str:

    state = get_config_approval_state(hass)
    pending = list(state.get("pending", {}).values())
    if not pending and not state.get("last_resolution"):
        return ""

    lines = ["## Config Directory Approval State"]
    if pending:
        lines.append("Pending operations:")
        for item in pending[:3]:
            lines.append(
                f"- approval_id={item['approval_id']} action={item['action']} path=config/{item['path']}"
            )
            if item.get("preview"):
                lines.append(f"  preview: {item['preview'][:200]}")
    if state.get("last_resolution"):
        last = state["last_resolution"]
        lines.append(
            f"Last resolution: approval_id={last.get('approval_id')} status={last.get('status')}"
        )
    lines.append(
        "Rules: write/append/mkdir auto-apply on `apply` (reversible). "
        "delete is destructive — first describe in chat what will be deleted "
        "and why, judge the user's reply yourself, and only then call `apply` "
        "with user_consent=true (and consent_quote=<their words> for audit). "
        "If they decline, call `cancel`."
    )
    return "\n".join(lines)
