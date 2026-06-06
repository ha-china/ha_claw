

from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file


from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)


def _changelog_path() -> Path:
    return get_data_dir() / "changelog.jsonl"


def _pending_dir() -> Path:
    return get_data_dir() / "pending"

VALID_TARGET_TYPES = frozenset({"skill", "guide", "memory"})
VALID_ACTIONS = frozenset({"create", "update", "delete"})

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", flags=re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

try:
    import yaml
except ImportError:
    yaml = None







@dataclass(slots=True, frozen=True)
class ChangelogEntry:


    timestamp: str
    target_type: str
    target_id: str
    action: str
    actor: str
    reason: str
    before_hash: str
    after_hash: str
    before_chars: int
    after_chars: int
    diff: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "action": self.action,
            "actor": self.actor,
            "reason": self.reason,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "before_chars": self.before_chars,
            "after_chars": self.after_chars,
            "diff": self.diff,
        }







def _ensure_dirs() -> None:
    get_data_dir().mkdir(parents=True, exist_ok=True)
    _pending_dir().mkdir(parents=True, exist_ok=True)


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "proposal"


def _digest(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _unified_diff(before: str | None, after: str | None, target_id: str) -> str:
    before_lines = (before or "").splitlines(keepends=True)
    after_lines = (after or "").splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{target_id}",
        tofile=f"b/{target_id}",
        n=2,
    )
    diff = "".join(diff_iter)

    if len(diff) > 4000:
        diff = diff[:4000] + "\n... [diff truncated]\n"
    return diff


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _dump_frontmatter(payload: dict[str, Any]) -> str:
    if yaml is None:
        lines = [f"{key}: {value!r}" for key, value in payload.items()]
        return "\n".join(lines)
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()


def _parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}, markdown
    frontmatter_raw = match.group(1)
    body = markdown[match.end():]
    if yaml is None:
        return {}, body
    parsed = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, body







def _append_changelog_sync(entry: ChangelogEntry) -> None:
    _ensure_dirs()
    line = json.dumps(entry.to_dict(), ensure_ascii=False)
    with _changelog_path().open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


async def async_record_change(
    hass: HomeAssistant,
    *,
    target_type: str,
    target_id: str,
    action: str,
    before: str | None,
    after: str | None,
    actor: str = "ai",
    reason: str = "",
) -> ChangelogEntry:

    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"Unknown target_type: {target_type!r}")
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")

    entry = ChangelogEntry(
        timestamp=_now_iso(),
        target_type=target_type,
        target_id=target_id,
        action=action,
        actor=actor,
        reason=reason.strip(),
        before_hash=_digest(before),
        after_hash=_digest(after),
        before_chars=len(before or ""),
        after_chars=len(after or ""),
        diff=_unified_diff(before, after, target_id),
    )
    await hass.async_add_executor_job(partial(_append_changelog_sync, entry))
    LOGGER.info(
        "[self_edit] %s %s %s actor=%s chars %d->%d",
        action,
        target_type,
        target_id,
        actor,
        entry.before_chars,
        entry.after_chars,
    )
    return entry


def _read_changelog_sync(limit: int, target_type: str | None) -> list[dict[str, Any]]:
    cl = _changelog_path()
    if not cl.exists():
        return []
    records: list[dict[str, Any]] = []
    with cl.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if target_type and record.get("target_type") != target_type:
                continue
            records.append(record)
    return records[-limit:] if limit > 0 else records


async def async_read_changelog(
    hass: HomeAssistant, *, limit: int = 20, target_type: str | None = None
) -> list[dict[str, Any]]:

    return await hass.async_add_executor_job(
        partial(_read_changelog_sync, limit, target_type)
    )







def _proposal_path(slug: str) -> Path:
    return _pending_dir() / f"{slug}.md"


def _write_proposal_sync(
    slug: str,
    frontmatter: dict[str, Any],
    body: str,
) -> Path:
    _ensure_dirs()
    path = _proposal_path(slug)
    document = "---\n" + _dump_frontmatter(frontmatter) + "\n---\n" + body.strip() + "\n"
    write_utf8_file(str(path), document)
    return path


async def async_stage_proposal(
    hass: HomeAssistant,
    *,
    target_type: str,
    target_id: str,
    action: str,
    proposed_markdown: str | None,
    reason: str,
    actor: str = "ai",
    slug_hint: str | None = None,
) -> dict[str, Any]:

    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"Unknown target_type: {target_type!r}")
    if action not in VALID_ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")
    if action in {"create", "update"} and not (proposed_markdown or "").strip():
        raise ValueError("proposed_markdown is required for create/update actions")

    raw_slug = slug_hint or f"{target_type}-{target_id}-{action}-{_now_iso()}"
    slug = _slugify(raw_slug)[:80] or "proposal"
    existing = _proposal_path(slug)
    if await hass.async_add_executor_job(existing.exists):
        slug = _slugify(f"{slug}-{_now_iso()}")

    frontmatter = {
        "target_type": target_type,
        "target_id": target_id,
        "action": action,
        "actor": actor,
        "reason": reason.strip(),
        "proposed_at": _now_iso(),
    }
    body = proposed_markdown or ""
    path = await hass.async_add_executor_job(
        partial(_write_proposal_sync, slug, frontmatter, body)
    )
    LOGGER.info(
        "[self_edit] staged proposal slug=%s target=%s/%s action=%s",
        slug,
        target_type,
        target_id,
        action,
    )
    return {
        "slug": slug,
        "path": str(path),
        "target_type": target_type,
        "target_id": target_id,
        "action": action,
        "reason": frontmatter["reason"],
        "proposed_at": frontmatter["proposed_at"],
    }


def _list_proposals_sync() -> list[dict[str, Any]]:
    pd = _pending_dir()
    if not pd.exists():
        return []
    proposals: list[dict[str, Any]] = []
    for path in sorted(pd.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        frontmatter, body = _parse_frontmatter(text)
        proposals.append(
            {
                "slug": path.stem,
                "path": str(path),
                "target_type": frontmatter.get("target_type"),
                "target_id": frontmatter.get("target_id"),
                "action": frontmatter.get("action"),
                "actor": frontmatter.get("actor"),
                "reason": frontmatter.get("reason", ""),
                "proposed_at": frontmatter.get("proposed_at"),
                "chars": len(body.strip()),
            }
        )
    return proposals


async def async_list_proposals(hass: HomeAssistant) -> list[dict[str, Any]]:
    return await hass.async_add_executor_job(_list_proposals_sync)


_MAX_SURFACED_PROPOSALS = 6


def build_self_edit_proposal_prompt_block() -> str:
    """Surface staged self-edit proposals into the live turn context.

    Proposals are written to ``pending/`` and otherwise never re-enter the
    conversation, so they silently pile up (memory cleanups, skill/guide edits)
    until something happens to call ApplyProposal/DiscardProposal. This block
    pulls them back into the user-facing message flow so the assistant raises
    them with the user and resolves them promptly instead of hoarding them.

    Reads disk synchronously; callers must run it off the event loop (it is
    invoked from ``build_turn_context_prompt`` inside an executor job).
    """
    proposals = _list_proposals_sync()
    if not proposals:
        return ""

    from collections import Counter

    total = len(proposals)
    breakdown = Counter(
        f"{p.get('target_type') or '?'}/{p.get('action') or '?'}" for p in proposals
    )
    summary = ", ".join(f"{kind}×{count}" for kind, count in breakdown.most_common())

    lines = [
        f"## Pending Self-Edit Proposals ({total})",
        (
            "You have self-edit proposals staged earlier and still awaiting the "
            "user's decision. Raise them with the user NOW, briefly, and resolve "
            "them this turn — do not let them keep accumulating. After the user "
            "agrees, apply with ApplyProposal(slug); if they decline or the "
            "proposal is stale/no longer relevant, clear it with "
            "DiscardProposal(slug). Use GetProposal(slug) to show details."
        ),
        f"Breakdown: {summary}",
    ]
    for item in proposals[:_MAX_SURFACED_PROPOSALS]:
        reason = (item.get("reason") or "").strip().replace("\n", " ")
        if len(reason) > 90:
            reason = reason[:90] + "…"
        lines.append(
            f"- slug={item.get('slug')} "
            f"{item.get('target_type') or '?'}/{item.get('action') or '?'} "
            f"target={item.get('target_id') or '?'}"
            + (f" — {reason}" if reason else "")
        )
    if total > _MAX_SURFACED_PROPOSALS:
        lines.append(
            f"- …and {total - _MAX_SURFACED_PROPOSALS} more "
            "(call ListProposals to see them all)."
        )
    return "\n".join(lines)


def _read_proposal_sync(slug: str) -> dict[str, Any]:
    path = _proposal_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"Proposal not found: {slug}")
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)
    return {
        "slug": slug,
        "path": str(path),
        "frontmatter": frontmatter,
        "body": body.strip(),
    }


async def async_read_proposal(hass: HomeAssistant, slug: str) -> dict[str, Any]:
    return await hass.async_add_executor_job(partial(_read_proposal_sync, slug))


def _discard_proposal_sync(slug: str) -> bool:
    path = _proposal_path(slug)
    if not path.exists():
        return False
    path.unlink()
    return True


async def async_discard_proposal(hass: HomeAssistant, slug: str) -> bool:
    return await hass.async_add_executor_job(partial(_discard_proposal_sync, slug))






ProposalExecutor = Callable[[HomeAssistant, dict[str, Any], str], Awaitable[dict[str, Any]]]



async def async_apply_proposal(
    hass: HomeAssistant,
    slug: str,
    executors: dict[str, ProposalExecutor],
    *,
    approved_by: str = "human",
) -> dict[str, Any]:

    proposal = await async_read_proposal(hass, slug)
    frontmatter = dict(proposal["frontmatter"])
    target_type = frontmatter.get("target_type")
    if target_type not in executors:
        raise ValueError(f"No executor registered for target_type={target_type!r}")

    frontmatter["approved_by"] = approved_by
    frontmatter["approved_at"] = _now_iso()

    executor = executors[target_type]
    result = await executor(hass, frontmatter, proposal["body"])

    await hass.async_add_executor_job(partial(_discard_proposal_sync, slug))
    LOGGER.info(
        "[self_edit] applied proposal slug=%s target=%s/%s action=%s",
        slug,
        target_type,
        frontmatter.get("target_id"),
        frontmatter.get("action"),
    )

    return {
        "slug": slug,
        "applied": True,
        "approved_by": approved_by,
        "result": result,
    }
