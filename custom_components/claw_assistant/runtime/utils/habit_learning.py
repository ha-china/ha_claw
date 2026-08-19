"""G3 learning glue — bridge habit_detector → pending_insights → notification.

Kept as a separate module so ``signal_capture`` stays a thin hook: it records
the observation, runs the lightweight frequency detection, and queues any new
routine candidate into ``InsightQueue``. When a genuinely new candidate is
queued, a persistent notification with [确认/忽略/不再提示] is fired so the
user can act from the "学习" tab (or IM, via existing notification transport).

Design trade-off (documented per G3 spec): the hook runs synchronously on the
signal_capture call chain but every file operation is pushed to the executor;
detection is a cheap in-memory frequency scan over a bounded JSON file, so no
separate scheduler is needed for v1. A future version can move detection to a
cron/time-interval job without changing the queue API.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)


async def async_observe_signal_and_enqueue_insights(
    hass: HomeAssistant,
    *,
    key: str,
    hour: int,
    text: str = "",
    source: str = "",
    min_frequency: int = 3,
    window_days: int = 14,
    min_confidence: float = 0.3,
) -> dict[str, Any]:
    """Record one observation, detect candidates, queue new insights.

    Returns a summary dict with ``observed`` and ``queued`` counts for tests /
    debugging. Never raises: all failure modes are logged and swallowed so the
    passive-capture chain is never broken by the learning hook.
    """
    from .habit_detector import (
        HabitObservation,
        detect_candidates,
        record_observation,
    )
    from ..storage.pending_insights import InsightQueue

    observation = HabitObservation(
        key=str(key or "").strip()[:128] or "unknown",
        hour=int(hour) % 24,
        text=str(text or ""),
        source=str(source or ""),
    )
    await hass.async_add_executor_job(record_observation, observation)

    candidates = await hass.async_add_executor_job(
        lambda: detect_candidates(
            min_frequency=min_frequency,
            window_days=window_days,
            min_confidence=min_confidence,
        )
    )
    queued: list[dict[str, Any]] = []
    for candidate in candidates:
        result = await hass.async_add_executor_job(
            lambda c=candidate: InsightQueue.add(
                pattern=c.pattern,
                confidence=c.confidence,
                source=c.source or source,
                category="routine",
            )
        )
        if result.get("added"):
            queued.append(result.get("insight") or {})
            try:
                await _notify_new_insight(hass, result["insight"])
            except Exception:
                LOGGER.debug("Failed to notify new insight (non-blocking)", exc_info=True)

    return {"observed": True, "candidates": len(candidates), "queued": queued}


async def _notify_new_insight(hass: HomeAssistant, insight: dict[str, Any]) -> None:
    """Fire a persistent notification for a newly queued routine candidate."""
    pattern = str(insight.get("pattern") or "")
    confidence = float(insight.get("confidence") or 0.0)
    source = str(insight.get("source") or "")
    insight_id = str(insight.get("id") or "")
    if not pattern:
        return
    from homeassistant.components import persistent_notification

    message = (
        f"🧠 发现新规律：{pattern}\n"
        f"置信度 {confidence:.0%} · 来源 {source}\n\n"
        f"[确认] 在「学习」页确认并写入记忆\n"
        f"[忽略] 不再提醒这条\n"
        f"[不再提示] 永久停用这类规律"
    )
    await persistent_notification.async_create(
        hass,
        message,
        "Claw 被动学习",
        f"claw_learning_{insight_id}",
    )
