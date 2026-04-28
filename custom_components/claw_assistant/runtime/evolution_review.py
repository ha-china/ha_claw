from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .state import get_runtime_store

LOGGER = logging.getLogger(__name__)

_TASKS_KEY = "evolution_review_tasks"
_RECENT_REVIEWS_KEY = "evolution_review_recent"
_REVIEW_TTL = timedelta(hours=2)
_MAX_TRACKED_REVIEWS = 100
_REVIEW_MARKER = "[EVOLUTION-REVIEW]"
_REVIEW_SYSTEM_PROMPT = (
    "Internal background evolution review.\n"
    "This is not a user-facing turn.\n"
    "Review the completed task and decide whether any reusable skill or runtime "
    "guide should be improved.\n"
    "You must not edit anything directly.\n"
    "If review is warranted, call ReviewSelfSkills first, then stage one or more "
    "ProposeSelfEdit proposals.\n"
    "Prefer updating an existing skill over creating a new one.\n"
    "Work at the task-class level, not the single-instance level.\n"
    "Never call ApplyProposal.\n"
    "If nothing is worth saving, reply exactly: Nothing to save."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _task_bucket(hass: HomeAssistant) -> set[asyncio.Task]:
    runtime_store = get_runtime_store(hass)
    bucket = runtime_store.get(_TASKS_KEY)
    if not isinstance(bucket, set):
        bucket = set()
        runtime_store[_TASKS_KEY] = bucket
    return bucket


def _recent_bucket(hass: HomeAssistant) -> dict[str, str]:
    runtime_store = get_runtime_store(hass)
    bucket = runtime_store.get(_RECENT_REVIEWS_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        runtime_store[_RECENT_REVIEWS_KEY] = bucket
    return bucket


def _prune_recent(bucket: dict[str, str]) -> None:
    now = _utcnow()
    expired: list[str] = []
    for key, raw_timestamp in bucket.items():
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            expired.append(key)
            continue
        if timestamp + _REVIEW_TTL < now:
            expired.append(key)
    for key in expired:
        bucket.pop(key, None)
    if len(bucket) > _MAX_TRACKED_REVIEWS:
        ordered = sorted(bucket.items(), key=lambda item: item[1], reverse=True)
        bucket.clear()
        bucket.update(ordered[:_MAX_TRACKED_REVIEWS])


def _review_fingerprint(
    *,
    original_text: str,
    assistant_text: str,
    tool_calls: list[Any],
    tool_summary: str,
    agent_id: str,
) -> str:
    payload = "|".join(
        [
            original_text.strip(),
            assistant_text.strip(),
            ",".join(str(item) for item in tool_calls),
            tool_summary.strip(),
            agent_id.strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _should_review(
    *,
    original_text: str,
    assistant_text: str,
    tool_calls: list[Any],
    conversation_id: str | None,
) -> bool:
    if not assistant_text.strip():
        return False
    if not tool_calls:
        return False
    if _REVIEW_MARKER in original_text:
        return False
    if original_text.strip().startswith("/"):
        return False
    if conversation_id and conversation_id.startswith("evolution:"):
        return False
    return True


def _build_review_prompt(
    *,
    original_text: str,
    assistant_text: str,
    tool_calls: list[Any],
    tool_summary: str,
) -> str:
    tool_line = ", ".join(str(item) for item in tool_calls) if tool_calls else "none"
    tool_summary_block = tool_summary.strip() or "No structured tool result summary available."
    return (
        f"{_REVIEW_MARKER}\n"
        "Review this completed task for reusable learning.\n\n"
        "User request:\n"
        f"{original_text.strip()}\n\n"
        "Assistant outcome:\n"
        f"{assistant_text.strip()}\n\n"
        "Tools used:\n"
        f"{tool_line}\n\n"
        "Tool result summary:\n"
        f"{tool_summary_block}\n\n"
        "Decide whether any reusable skill or guide should be updated or created. "
        "Use ReviewSelfSkills first, then ProposeSelfEdit only if worthwhile."
    )


async def _run_evolution_review(
    hass: HomeAssistant,
    *,
    original_async_converse,
    review_text: str,
    review_conversation_id: str,
    language: str | None,
    agent_id: str | None,
) -> None:
    try:
        await original_async_converse(
            hass,
            review_text,
            review_conversation_id,
            None,
            language,
            agent_id,
            None,
            None,
            _REVIEW_SYSTEM_PROMPT,
        )
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("Background evolution review failed: %s", err)


def async_schedule_evolution_review(
    hass: HomeAssistant,
    *,
    original_text: str,
    assistant_text: str,
    tool_calls: list[Any],
    tool_summary: str,
    conversation_id: str | None,
    language: str | None,
    agent_id: str | None,
    original_async_converse=None,
) -> None:
    if not _should_review(
        original_text=original_text,
        assistant_text=assistant_text,
        tool_calls=tool_calls,
        conversation_id=conversation_id,
    ):
        return

    if not callable(original_async_converse):
        original_async_converse = get_runtime_store(hass).get("original_async_converse")
    if not callable(original_async_converse):
        return

    fingerprint = _review_fingerprint(
        original_text=original_text,
        assistant_text=assistant_text,
        tool_calls=tool_calls,
        tool_summary=tool_summary,
        agent_id=agent_id or "",
    )
    recent = _recent_bucket(hass)
    _prune_recent(recent)
    if fingerprint in recent:
        return
    recent[fingerprint] = _utcnow().isoformat()

    review_text = _build_review_prompt(
        original_text=original_text,
        assistant_text=assistant_text,
        tool_calls=tool_calls,
        tool_summary=tool_summary,
    )
    review_conversation_id = f"evolution:{conversation_id or 'default'}:{fingerprint}"
    task = asyncio.create_task(
        _run_evolution_review(
            hass,
            original_async_converse=original_async_converse,
            review_text=review_text,
            review_conversation_id=review_conversation_id,
            language=language,
            agent_id=agent_id,
        )
    )
    bucket = _task_bucket(hass)
    bucket.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        bucket.discard(done_task)

    task.add_done_callback(_cleanup)
