"""Pending insights queue (G3) — human-confirmed passive learning.

``signal_capture`` + ``habit_detector`` produce routine candidates; they are
queued here as ``PendingInsight`` entries and surfaced to the user through the
dashboard "学习" tab and a persistent notification. The user may:

- ``confirm`` — promote the pattern to long-term memory (``USER.md``) and, when
  the insight carries an entity/action, add a G5 trust-whitelist entry so the
  same operation is no longer interrupted by confirmation;
- ``dismiss`` — drop this single candidate without learning it;
- ``block`` — permanently stop proposing this pattern class.

Storage: ``<data_dir>/workspace/memory/pending-insights.json`` with a compact
``{pending, learned, blocked}`` shape (absent file == empty queue).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from homeassistant.core import HomeAssistant

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_INSIGHTS_RELATIVE_PATH = Path("workspace") / "memory" / "pending-insights.json"

CATEGORY_ROUTINE = "routine"
CATEGORY_PREFERENCE = "preference"
CATEGORY_SCHEDULE = "schedule"
CATEGORIES = (CATEGORY_ROUTINE, CATEGORY_PREFERENCE, CATEGORY_SCHEDULE)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True, frozen=True)
class PendingInsight:
    """A routine/preference/schedule candidate awaiting human confirmation."""

    id: str
    pattern: str
    confidence: float
    source: str
    created_at: str
    category: str = CATEGORY_ROUTINE
    entity: str = ""
    action: str = ""


def _insights_path() -> Path:
    return get_data_dir() / _INSIGHTS_RELATIVE_PATH


def _empty_payload() -> dict[str, Any]:
    return {"pending": [], "learned": [], "blocked": []}


def _load_payload() -> dict[str, Any]:
    path = _insights_path()
    if not path.is_file():
        return _empty_payload()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_payload()
        payload = _empty_payload()
        pending = data.get("pending")
        if isinstance(pending, list):
            payload["pending"] = [item for item in pending if isinstance(item, dict)]
        learned = data.get("learned")
        if isinstance(learned, list):
            payload["learned"] = [item for item in learned if isinstance(item, dict)]
        blocked = data.get("blocked")
        if isinstance(blocked, list):
            payload["blocked"] = [str(item).strip() for item in blocked if str(item).strip()]
        return payload
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Failed to load pending insights: %s", exc)
        return _empty_payload()


def _save_payload(payload: dict[str, Any]) -> Path:
    path = _insights_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("Failed to write pending insights: %s", exc)
    return path


def _memory_key_from_pattern(pattern: str) -> str:
    """Normalize a pattern into a stable memory key."""
    import re

    key = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", pattern.strip()).strip("_")
    return key[:48] or "learned_insight"


class InsightQueue:
    """Persistent queue of pending insights + learned/blocked history."""

    # ── Query ──

    @staticmethod
    def list_pending() -> list[dict[str, Any]]:
        payload = _load_payload()
        pending = payload.get("pending", [])
        pending.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
        return pending

    @staticmethod
    def list_learned() -> list[dict[str, Any]]:
        payload = _load_payload()
        learned = payload.get("learned", [])
        learned.sort(key=lambda e: str(e.get("confirmed_at", "")), reverse=True)
        return learned

    @staticmethod
    def list_blocked() -> list[str]:
        return _load_payload().get("blocked", [])

    @staticmethod
    def get(insight_id: str) -> dict[str, Any] | None:
        for entry in InsightQueue.list_pending():
            if str(entry.get("id")) == str(insight_id):
                return entry
        return None

    @staticmethod
    def is_blocked(pattern: str) -> bool:
        normalized = str(pattern or "").strip()
        return any(
            str(blocked).strip() == normalized
            for blocked in InsightQueue.list_blocked()
        )

    # ── Mutations ──

    @staticmethod
    def add(
        *,
        pattern: str,
        confidence: float,
        source: str,
        category: str = CATEGORY_ROUTINE,
        entity: str = "",
        action: str = "",
        insight_id: str = "",
    ) -> dict[str, Any]:
        """Queue a new insight. Dedupes by pattern; respects blocked patterns."""
        pattern = str(pattern or "").strip()
        category = category if category in CATEGORIES else CATEGORY_ROUTINE
        if not pattern:
            return {"added": False, "reason": "empty_pattern"}
        if InsightQueue.is_blocked(pattern):
            return {"added": False, "reason": "blocked"}
        payload = _load_payload()
        for entry in payload.get("pending", []):
            if str(entry.get("pattern", "")).strip() == pattern:
                return {"added": False, "reason": "already_pending"}
        for entry in payload.get("learned", []):
            if str(entry.get("pattern", "")).strip() == pattern:
                return {"added": False, "reason": "already_learned"}
        insight = PendingInsight(
            id=insight_id or uuid4().hex[:12],
            pattern=pattern,
            confidence=float(confidence),
            source=str(source or ""),
            created_at=_now_iso(),
            category=category,
            entity=str(entity or ""),
            action=str(action or ""),
        )
        payload.setdefault("pending", []).append(asdict(insight))
        _save_payload(payload)
        return {"added": True, "insight": asdict(insight)}

    @staticmethod
    def dismiss(insight_id: str) -> bool:
        """Drop a single pending insight without learning it."""
        payload = _load_payload()
        pending = payload.get("pending", [])
        kept = [e for e in pending if str(e.get("id")) != str(insight_id)]
        if len(kept) == len(pending):
            return False
        payload["pending"] = kept
        _save_payload(payload)
        return True

    @staticmethod
    def block(insight_id: str) -> bool:
        """Permanently stop proposing the pattern of this insight."""
        payload = _load_payload()
        pending = payload.get("pending", [])
        target = next((e for e in pending if str(e.get("id")) == str(insight_id)), None)
        if target is None:
            return False
        pattern = str(target.get("pattern", "")).strip()
        if pattern:
            blocked = payload.setdefault("blocked", [])
            if pattern not in blocked:
                blocked.append(pattern)
        payload["pending"] = [e for e in pending if str(e.get("id")) != str(insight_id)]
        _save_payload(payload)
        return True

    @staticmethod
    async def confirm(
        hass: HomeAssistant,
        insight_id: str,
        *,
        user_key: str = "",
    ) -> dict[str, Any]:
        """Confirm an insight: write memory (+ G5 whitelist when applicable).

        The pattern is stored into the ``user`` memory target (``USER.md``) via
        ``memory_store``; if the insight carries an entity/action pair it is
        also added to the G5 trust-whitelist through ``PolicyGate`` so the same
        operation no longer interrupts the user.
        """
        payload = _load_payload()
        pending = payload.get("pending", [])
        target = next((e for e in pending if str(e.get("id")) == str(insight_id)), None)
        if target is None:
            return {"ok": False, "error": "not_found"}

        pattern = str(target.get("pattern", "")).strip()
        category = str(target.get("category") or CATEGORY_ROUTINE)
        entity = str(target.get("entity", "") or "")
        action = str(target.get("action", "") or "")
        source = str(target.get("source", "") or "")
        confidence = float(target.get("confidence", 0.0) or 0.0)
        confirmed_at = _now_iso()

        payload["pending"] = [
            e for e in pending if str(e.get("id")) != str(insight_id)
        ]
        learned_entry = dict(target)
        learned_entry.update(
            {
                "status": "confirmed",
                "confirmed_at": confirmed_at,
                "category": category,
            }
        )
        payload.setdefault("learned", []).append(learned_entry)

        memory_result: dict[str, Any] = {}
        whitelist_result: dict[str, Any] = {}
        if pattern:
            try:
                from .memory_store import async_save_memory_entry_result

                memory_result = await async_save_memory_entry_result(
                    hass,
                    _memory_key_from_pattern(pattern),
                    pattern,
                    target="user",
                    user_key=user_key or None,
                )
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Failed to write learned memory: %s", exc)
                memory_result = {"status": "error", "reason": str(exc)}

        if action:
            try:
                from .authorization import PolicyGate

                ok = await hass.async_add_executor_job(
                    PolicyGate.add_whitelist_entry,
                    user_key,
                    action,
                    entity or None,
                    None,
                )
                whitelist_result = {
                    "ok": bool(ok),
                    "user_key": user_key,
                    "action": action,
                    "entity": entity,
                }
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Failed to add trust whitelist: %s", exc)
                whitelist_result = {"ok": False, "error": str(exc)}

        _save_payload(payload)
        return {
            "ok": True,
            "insight": learned_entry,
            "pattern": pattern,
            "category": category,
            "confidence": confidence,
            "source": source,
            "confirmed_at": confirmed_at,
            "memory": memory_result,
            "whitelist": whitelist_result,
        }
