from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from ..utils.data_path import get_data_dir
from ..core.state import get_runtime_store
from . import skill_usage

LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_HOURS = 24
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90

_UNSUB_KEY = "curator_unsub"
_CHECK_INTERVAL = timedelta(minutes=5)
_REVIEW_MARKER = "[CURATOR-REVIEW]"

_IDLE_MIN_TURNS = 3
_IDLE_QUIET_AFTER = timedelta(minutes=20)


def _state_path() -> Path:
    return get_data_dir() / ".curator_state"


def _reports_dir() -> Path:
    return get_data_dir() / "curator"


def _default_state() -> dict[str, Any]:
    return {
        "last_run_at": None,
        "last_run_duration_seconds": None,
        "last_run_summary": None,
        "last_report_path": None,
        "paused": False,
        "run_count": 0,
        "last_turn_at": None,
        "turns_since_last_run": 0,
    }


def load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _default_state()
            base.update({k: v for k, v in data.items() if k in base})
            return base
    except (OSError, json.JSONDecodeError) as err:
        LOGGER.debug("Failed to read curator state: %s", err)
    return _default_state()


def save_state(data: dict[str, Any]) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=".curator_state_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as err:
        LOGGER.debug("Failed to save curator state: %s", err, exc_info=True)


def is_paused() -> bool:
    return bool(load_state().get("paused"))


def set_paused(paused: bool) -> None:
    state = load_state()
    state["paused"] = bool(paused)
    save_state(state)


async def async_set_paused(hass: HomeAssistant, paused: bool) -> None:
    await hass.async_add_executor_job(set_paused, paused)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _daily_window_elapsed(state: dict[str, Any], now: datetime) -> bool:
    last = _parse_iso(state.get("last_run_at"))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last) >= timedelta(hours=DEFAULT_INTERVAL_HOURS)


def should_run_idle(now: datetime | None = None) -> bool:
    if is_paused():
        return False
    if now is None:
        now = datetime.now(UTC)
    state = load_state()

    if int(state.get("turns_since_last_run", 0) or 0) < _IDLE_MIN_TURNS:
        return False

    last_turn = _parse_iso(state.get("last_turn_at"))
    if last_turn is None:
        return False
    if last_turn.tzinfo is None:
        last_turn = last_turn.replace(tzinfo=UTC)
    if (now - last_turn) < _IDLE_QUIET_AFTER:
        return False

    return _daily_window_elapsed(state, now)


def record_turn_activity(hass: HomeAssistant) -> None:
    def _bump() -> None:
        try:
            state = load_state()
            state["last_turn_at"] = datetime.now(UTC).isoformat()
            state["turns_since_last_run"] = (
                int(state.get("turns_since_last_run", 0) or 0) + 1
            )
            save_state(state)
        except Exception as err:
            LOGGER.debug("record_turn_activity failed: %s", err)

    hass.async_add_executor_job(_bump)


def apply_automatic_transitions(now: datetime | None = None) -> dict[str, int]:
    if now is None:
        now = datetime.now(UTC)
    stale_cutoff = now - timedelta(days=DEFAULT_STALE_AFTER_DAYS)
    archive_cutoff = now - timedelta(days=DEFAULT_ARCHIVE_AFTER_DAYS)

    counts = {"marked_stale": 0, "archived": 0, "reactivated": 0, "checked": 0}

    for row in skill_usage.agent_created_report():
        counts["checked"] += 1
        name = row["name"]
        if row.get("pinned"):
            continue

        last_activity = _parse_iso(row.get("last_activity_at"))
        anchor = last_activity or _parse_iso(row.get("created_at")) or now
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)

        current = row.get("state", skill_usage.STATE_ACTIVE)

        if anchor <= archive_cutoff and current != skill_usage.STATE_ARCHIVED:
            ok, _ = skill_usage.archive_skill(name)
            if ok:
                counts["archived"] += 1
        elif anchor <= stale_cutoff and current == skill_usage.STATE_ACTIVE:
            skill_usage.set_state(name, skill_usage.STATE_STALE)
            counts["marked_stale"] += 1
        elif anchor > stale_cutoff and current == skill_usage.STATE_STALE:
            skill_usage.set_state(name, skill_usage.STATE_ACTIVE)
            counts["reactivated"] += 1

    return counts


_CURATOR_REVIEW_PROMPT = (
    "You are running as claw_assistant's background skill CURATOR. "
    "This is an UMBRELLA-BUILDING consolidation pass.\n\n"
    "Goal: maintain a LIBRARY OF CLASS-LEVEL INSTRUCTIONS. Hundreds of "
    "narrow skills where each captures one session's bug is a FAILURE. "
    "One broad umbrella skill with labeled subsections beats five narrow "
    "siblings for discoverability.\n\n"
    "Hard rules — do not violate:\n"
    "1. DO NOT touch internal or bundled skills (homeassistant_runtime_guide).\n"
    "2. DO NOT delete any skill. Archiving is the maximum destructive action.\n"
    "3. DO NOT touch skills shown as pinned=yes.\n"
    "4. DO NOT use usage counters alone to decide — judge overlap on CONTENT.\n\n"
    "How to work:\n"
    "1. Call ReviewSelfSkills to see all installed skills.\n"
    "2. Identify PREFIX CLUSTERS (skills sharing first word or domain keyword).\n"
    "3. For each cluster with 2+ members: pick/create an umbrella, absorb "
    "siblings into it via ProposeSelfEdit(action=update on the umbrella, "
    "action=delete on siblings).\n"
    "4. Flag skills with names too narrow (PR number, error string, session "
    "artifact). These belong as subsections under a class-level umbrella.\n"
    "5. Iterate — don't stop after 3 merges.\n\n"
    "'keep' is legitimate ONLY when the skill is already a class-level "
    "umbrella and no merge would improve discoverability.\n\n"
    "When done, write a human summary AND a structured block:\n\n"
    "## Structured summary (required)\n"
    "```yaml\n"
    "consolidations:\n"
    "  - from: <old-skill-name>\n"
    "    into: <umbrella-skill-name>\n"
    "    reason: <one short sentence>\n"
    "prunings:\n"
    "  - name: <skill-name>\n"
    "    reason: <one short sentence>\n"
    "```\n\n"
    "Every archived skill MUST appear in exactly one of the two lists.\n\n"
    "Stage every mutation via ProposeSelfEdit. Never call ApplyProposal."
)


def _render_candidate_list() -> str:
    rows = skill_usage.agent_created_report()
    if not rows:
        return "No agent-created skills to review."
    lines = [f"Agent-created skills ({len(rows)}):\n"]
    for r in rows:
        lines.append(
            f"- {r['name']}  "
            f"state={r['state']}  "
            f"pinned={'yes' if r.get('pinned') else 'no'}  "
            f"activity={r.get('activity_count', 0)}  "
            f"use={r.get('use_count', 0)}  "
            f"view={r.get('view_count', 0)}  "
            f"patches={r.get('patch_count', 0)}  "
            f"last_activity={r.get('last_activity_at') or 'never'}"
        )
    return "\n".join(lines)


def _write_run_report(
    *,
    started_at: datetime,
    elapsed_seconds: float,
    auto_counts: dict[str, int],
    auto_summary: str,
    before_count: int,
    after_count: int,
    llm_summary: str,
    llm_error: str | None,
) -> Path | None:
    root = _reports_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as err:
        LOGGER.debug("Curator report dir create failed: %s", err)
        return None

    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    run_dir = root / stamp
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = root / f"{stamp}-{suffix}"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except Exception as err:
        LOGGER.debug("Curator run dir create failed: %s", err)
        return None

    payload = {
        "started_at": started_at.isoformat(),
        "duration_seconds": round(elapsed_seconds, 2),
        "auto_transitions": auto_counts,
        "counts": {
            "before": before_count,
            "after": after_count,
            "delta": after_count - before_count,
        },
        "llm_summary": llm_summary,
        "llm_error": llm_error,
    }

    try:
        (run_dir / "run.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as err:
        LOGGER.debug("Curator run.json write failed: %s", err)

    try:
        md_lines = [
            f"# Curator run — {started_at.isoformat()}\n",
            f"Duration: {round(elapsed_seconds)}s  ·  "
            f"Skills: {before_count} → {after_count} ({after_count - before_count:+d})\n",
            "## Auto-transitions\n",
            f"- checked: {auto_counts.get('checked', 0)}",
            f"- marked stale: {auto_counts.get('marked_stale', 0)}",
            f"- archived: {auto_counts.get('archived', 0)}",
            f"- reactivated: {auto_counts.get('reactivated', 0)}",
            "",
        ]
        if llm_error:
            md_lines.append(f"> ⚠ LLM error: `{llm_error}`\n")
        if llm_summary:
            md_lines.append("## LLM summary\n")
            md_lines.append(llm_summary)
            md_lines.append("")
        md_lines.append("## Recovery\n")
        md_lines.append("- Restore an archived skill via the skill store archive.")
        md_lines.append("")
        (run_dir / "REPORT.md").write_text("\n".join(md_lines), encoding="utf-8")
    except Exception as err:
        LOGGER.debug("Curator REPORT.md write failed: %s", err)

    return run_dir


async def async_run_curator(
    hass: HomeAssistant,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    start = datetime.now(UTC)

    before_report = await skill_usage.async_agent_created_report(hass)
    before_count = len(before_report)

    if dry_run:
        counts = {"checked": before_count, "marked_stale": 0, "archived": 0, "reactivated": 0}
    else:
        counts = await hass.async_add_executor_job(apply_automatic_transitions, start)

    auto_parts = []
    if counts["marked_stale"]:
        auto_parts.append(f"{counts['marked_stale']} marked stale")
    if counts["archived"]:
        auto_parts.append(f"{counts['archived']} archived")
    if counts["reactivated"]:
        auto_parts.append(f"{counts['reactivated']} reactivated")
    auto_summary = ", ".join(auto_parts) if auto_parts else "no changes"

    if not dry_run:
        state = await hass.async_add_executor_job(load_state)
        state["last_run_at"] = start.isoformat()
        state["run_count"] = int(state.get("run_count", 0)) + 1
        state["last_run_summary"] = f"auto: {auto_summary}"
        state["turns_since_last_run"] = 0
        await hass.async_add_executor_job(save_state, state)

    llm_summary = ""
    llm_error = None
    auto_changed = bool(
        counts.get("marked_stale")
        or counts.get("archived")
        or counts.get("reactivated")
    )
    candidate_list = await hass.async_add_executor_job(_render_candidate_list)
    has_candidates = "No agent-created skills" not in candidate_list
    if not auto_changed and not has_candidates:
        llm_summary = "skipped (nothing to organize)"
    elif has_candidates and auto_changed:
        original_async_converse = get_runtime_store(hass).get("original_async_converse")
        if callable(original_async_converse):
            prompt = (
                f"{_REVIEW_MARKER}\n"
                f"{_CURATOR_REVIEW_PROMPT}\n\n"
                f"{candidate_list}"
            )
            review_system = (
                "You are the background curator agent. "
                "Stage proposals only via ProposeSelfEdit. "
                "Never call ApplyProposal."
            )
            review_conversation_id = f"curator:{start.isoformat()}"
            try:
                await original_async_converse(
                    hass,
                    prompt,
                    review_conversation_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    review_system,
                )
                llm_summary = "LLM review completed"
            except Exception as err:
                llm_error = str(err)
                LOGGER.debug("Curator LLM review failed: %s", err, exc_info=True)
    elif has_candidates and not auto_changed:
        llm_summary = "skipped LLM (no auto changes)"

    after_report = await skill_usage.async_agent_created_report(hass)
    after_count = len(after_report)
    elapsed = (datetime.now(UTC) - start).total_seconds()

    final_summary = f"auto: {auto_summary}; llm: {llm_summary or llm_error or 'skipped'}"
    report_path = await hass.async_add_executor_job(
        lambda: _write_run_report(
            started_at=start,
            elapsed_seconds=elapsed,
            auto_counts=counts,
            auto_summary=auto_summary,
            before_count=before_count,
            after_count=after_count,
            llm_summary=llm_summary,
            llm_error=llm_error,
        )
    )

    state2 = await hass.async_add_executor_job(load_state)
    state2["last_run_duration_seconds"] = elapsed
    state2["last_run_summary"] = final_summary
    if report_path is not None:
        state2["last_report_path"] = str(report_path)
    await hass.async_add_executor_job(save_state, state2)

    return {
        "started_at": start.isoformat(),
        "auto_transitions": counts,
        "summary": final_summary,
        "report_path": str(report_path) if report_path else None,
    }


async def _curator_tick(hass: HomeAssistant, _now: Any = None) -> None:
    try:
        if not await hass.async_add_executor_job(should_run_idle):
            return
        LOGGER.info("Curator: idle threshold met — starting review pass")
        result = await async_run_curator(hass)
        LOGGER.info("Curator: %s", result.get("summary", "done"))
    except Exception as err:
        LOGGER.debug("Curator tick failed: %s", err, exc_info=True)


async def async_setup_curator(hass: HomeAssistant) -> None:
    if _UNSUB_KEY in hass.data:
        return

    from homeassistant.core import callback as ha_callback

    @ha_callback
    def _schedule_tick(_now: Any) -> None:
        hass.async_create_task(_curator_tick(hass, _now))

    unsub = async_track_time_interval(hass, _schedule_tick, _CHECK_INTERVAL)
    hass.data[_UNSUB_KEY] = unsub
    LOGGER.debug("Curator armed: check_interval=%s", _CHECK_INTERVAL)


async def async_unload_curator(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_UNSUB_KEY, None)
    if unsub is not None:
        try:
            unsub()
        except Exception as err:
            LOGGER.debug("Curator unload error: %s", err)


def curator_status() -> dict[str, Any]:
    state = load_state()
    rows = skill_usage.agent_created_report()
    by_use = sorted(rows, key=lambda r: r.get("use_count", 0), reverse=True)
    return {
        "state": state,
        "skill_count": len(rows),
        "most_used": [
            {"name": r["name"], "use_count": r["use_count"]}
            for r in by_use[:10]
        ],
        "least_used": [
            {"name": r["name"], "use_count": r["use_count"]}
            for r in by_use[-10:]
        ] if len(by_use) > 10 else [],
    }


async def async_curator_status(hass: HomeAssistant) -> dict[str, Any]:
    return await hass.async_add_executor_job(curator_status)
