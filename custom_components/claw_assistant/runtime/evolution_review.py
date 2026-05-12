from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .goals import is_continuation_prompt
from .state import get_runtime_store

LOGGER = logging.getLogger(__name__)

_TASKS_KEY = "evolution_review_tasks"
_RECENT_REVIEWS_KEY = "evolution_review_recent"
_LOADED_SKILLS_KEY = "evolution_review_loaded_skills"
_LOADED_SKILLS_PER_CONV_LIMIT = 32
_REVIEW_TTL = timedelta(hours=2)
_MAX_TRACKED_REVIEWS = 100
_REVIEW_MARKER = "[EVOLUTION-REVIEW]"
_REVIEW_SYSTEM_PROMPT = (
    "Internal background evolution review.\n"
    "This is not a user-facing turn.\n\n"
    "RUBRIC — any ONE signal below warrants a skill/guide update:\n"
    "  • User corrected your style, tone, format, verbosity, or approach. "
    "Frustration ('stop doing X', 'don't format like this') is a FIRST-CLASS "
    "skill signal — embed the lesson in the skill that governs that task class.\n"
    "  • User corrected your workflow or sequence of steps. Encode the "
    "correction as a pitfall or explicit step in the governing skill.\n"
    "  • Non-trivial technique, fix, workaround, debugging path, or tool-usage "
    "pattern emerged that a future session would benefit from.\n"
    "  • A skill that got loaded this session turned out wrong, missing a "
    "step, or outdated — patch it NOW.\n\n"
    "Preference order — pick the earliest that fits:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. If any loaded skill covers the "
    "territory of the new learning, PATCH that one first (active-update bias).\n"
    "  2. UPDATE AN EXISTING UMBRELLA. If no loaded skill fits but an existing "
    "class-level skill does, patch it.\n"
    "  3. CREATE A NEW CLASS-LEVEL SKILL when nothing exists. Name at the "
    "class level — NOT a specific error string or session artifact.\n\n"
    "Stage every change via ProposeSelfEdit (never edit directly). "
    "Call ReviewSelfSkills first to see current skills/guides/memory.\n\n"
    "Memory hygiene (target_type=memory):\n"
    "- self-purification: stage delete for stale, redundant, or duplicate keys.\n"
    "- self-evolution: stage update to consolidate fragmented entries under a "
    "canonical key.\n"
    "- self-boundary: only stable, user-level, durable facts belong in memory. "
    "Transient session context belongs in conversation history. Structured "
    "relations belong in MemoryGraph. Reject anything else.\n\n"
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


def _loaded_skills_bucket(hass: HomeAssistant) -> dict[str, list[str]]:
    runtime_store = get_runtime_store(hass)
    bucket = runtime_store.get(_LOADED_SKILLS_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        runtime_store[_LOADED_SKILLS_KEY] = bucket
    return bucket


def record_loaded_skill(
    hass: HomeAssistant, slug: str, *, conversation_id: str | None = None
) -> None:
    """Record a skill slug as loaded by the active conversation.

    Used to feed the active-update bias section of the evolution review prompt.
    """
    if not slug:
        return
    if conversation_id is None:
        try:
            from .state import _active_conversation_id

            conversation_id = _active_conversation_id.get()
        except Exception:
            conversation_id = "default"
    key = conversation_id or "default"
    bucket = _loaded_skills_bucket(hass)
    skills = bucket.setdefault(key, [])
    if slug in skills:
        skills.remove(slug)
    skills.append(slug)
    if len(skills) > _LOADED_SKILLS_PER_CONV_LIMIT:
        del skills[: len(skills) - _LOADED_SKILLS_PER_CONV_LIMIT]


def consume_loaded_skills(
    hass: HomeAssistant, conversation_id: str | None
) -> list[str]:
    """Return and clear the loaded skill slugs for the given conversation."""
    bucket = _loaded_skills_bucket(hass)
    key = conversation_id or "default"
    skills = bucket.pop(key, [])
    return list(skills)


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
    tool_results: list[Any] | None = None,
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
    if is_continuation_prompt(original_text):
        return False
    # Skip review when every tool succeeded — there is no failure or anomaly
    # worth learning from, and the post-response LLM call wastes a slot.
    if tool_results:
        all_ok = True
        for entry in tool_results:
            if isinstance(entry, dict):
                if entry.get("success") is False or entry.get("error"):
                    all_ok = False
                    break
            else:
                if getattr(entry, "success", True) is False or getattr(entry, "error", None):
                    all_ok = False
                    break
        if all_ok:
            return False
    return True


def _build_review_prompt(
    *,
    original_text: str,
    assistant_text: str,
    tool_calls: list[Any],
    tool_summary: str,
    loaded_skills: list[str] | None = None,
) -> str:
    tool_line = ", ".join(str(item) for item in tool_calls) if tool_calls else "none"
    tool_summary_block = tool_summary.strip() or "No structured tool result summary available."
    loaded_block = ""
    if loaded_skills:
        loaded_block = (
            "\nSkills loaded this session (active-update bias — prefer patching these):\n"
            + "\n".join(f"  - {s}" for s in loaded_skills)
            + "\n"
        )
    return (
        f"{_REVIEW_MARKER}\n"
        "Review this completed task for reusable learning.\n\n"
        "User request:\n"
        f"{original_text.strip()}\n\n"
        "Assistant outcome (tool messages excluded for clarity):\n"
        f"{assistant_text.strip()}\n\n"
        "Tools used:\n"
        f"{tool_line}\n\n"
        "Tool result summary:\n"
        f"{tool_summary_block}\n"
        f"{loaded_block}\n"
        "Apply the rubric from your system prompt. Use ReviewSelfSkills "
        "first, then ProposeSelfEdit only when worthwhile. "
        "For memory entries apply the purification/evolution/boundary policy."
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
    except Exception as err:
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
    loaded_skills: list[str] | None = None,
    tool_results: list[Any] | None = None,
) -> None:
    if not _should_review(
        original_text=original_text,
        assistant_text=assistant_text,
        tool_calls=tool_calls,
        conversation_id=conversation_id,
        tool_results=tool_results,
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
        loaded_skills=loaded_skills,
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
