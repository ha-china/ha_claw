"""AuditStore — append-only JSONL audit log (G5).

Every policy decision (ALLOW/CONFIRM/DENY) and every approval resolution is
appended as one JSON line to
``<data_dir>/workspace/memory/audit.log.jsonl``.

The file is append-only: entries are never rewritten or removed. ``list_recent``
reads the tail of the file for the panel / G6 dynamic page.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_AUDIT_RELATIVE = Path("workspace") / "memory" / "audit.log.jsonl"
_MAX_TAIL_BYTES = 512 * 1024  # read at most ~512KB of tail for list_recent


def _audit_path(hass: HomeAssistant) -> Path:
    return get_data_dir() / _AUDIT_RELATIVE


def record(hass: HomeAssistant, entry: dict[str, Any]) -> bool:
    """Append one audit entry as a JSON line. Fire-and-forget friendly."""
    try:
        data = dict(entry or {})
        data.setdefault("ts", time.time())
        path = _audit_path(hass)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        LOGGER.warning("Failed to write audit log: %s", exc)
        return False


def list_recent(hass: HomeAssistant, limit: int = 100) -> list[dict[str, Any]]:
    """Return the most recent audit entries (newest first)."""
    path = _audit_path(hass)
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            if size > _MAX_TAIL_BYTES:
                handle.seek(size - _MAX_TAIL_BYTES)
                # Drop a potentially truncated first line.
                handle.readline()
            lines = handle.readlines()
    except Exception as exc:
        LOGGER.warning("Failed to read audit log: %s", exc)
        return []

    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
        except (json.JSONDecodeError, ValueError):
            continue
    return entries[-max(0, int(limit)):][::-1]
