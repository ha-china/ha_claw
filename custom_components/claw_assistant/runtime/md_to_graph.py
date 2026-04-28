"""Markdown → graph node extraction.

The workspace markdown files remain the human-editable source of truth.
This module converts their current content into graph nodes so the graph
store can be used as a derived retrieval index.

Intentionally stdlib-only; no Home Assistant imports so it stays unit
testable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .graph_store import GraphStore

__all__ = [
    "ExtractedNode",
    "extract_nodes_from_markdown",
    "reindex_markdown",
]


# kind is the *default* for lines that do not carry stronger signal; a
# doc may still emit multiple kinds in the future (e.g. MEMORY has both
# preferences and constraints) — for now we keep it coarse.
_DOC_DEFAULT_KIND: dict[str, str] = {
    "MEMORY": "preference",
    "USER": "user_fact",
    "IDENTITY": "assistant_fact",
    "TOOLS": "tool_usage",
    "HEARTBEAT": "follow_up",
    "SOUL": "style",
    "AGENTS": "rule",
    "BOOTSTRAP": "rule",
}

_KV_RE = re.compile(
    r"^\s*[-*]?\s*(?P<key>[A-Za-z_][\w.-]*)\s*[:=]\s*(?P<val>.+?)\s*$"
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<val>.+?)\s*$")

_PLACEHOLDER_RE = re.compile(
    r"^(<[^>]+>|none|n/?a|pending|tbd|todo"
    r"|no confirmed facts.*|configured externally|optional.*)\s*$",
    re.IGNORECASE,
)


def _is_placeholder(value: str) -> bool:
    stripped = value.strip().strip("`")
    if not stripped:
        return True
    return bool(_PLACEHOLDER_RE.match(stripped))


def _skip_line(line: str) -> bool:
    """Headings, italics/subtitles and code fences are not node-worthy."""

    if not line.strip():
        return True
    if line.lstrip().startswith("#"):
        return True
    stripped = line.strip()
    if stripped.startswith("_") and stripped.endswith("_"):
        return True
    if stripped.startswith("```"):
        return True
    return False


@dataclass(slots=True, frozen=True)
class ExtractedNode:
    kind: str
    title: str
    body: str


def extract_nodes_from_markdown(
    doc_name: str, markdown: str
) -> list[ExtractedNode]:
    """Parse ``markdown`` and yield one node per meaningful line.

    Pure function: no I/O, no DB. Safe to unit test.
    """

    default_kind = _DOC_DEFAULT_KIND.get(doc_name.upper(), "note")
    out: list[ExtractedNode] = []
    seen: set[tuple[str, str]] = set()

    in_code_block = False
    for raw in markdown.splitlines():
        if raw.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if _skip_line(raw):
            continue

        kv = _KV_RE.match(raw)
        if kv:
            key = kv.group("key").strip()
            val = kv.group("val").strip()
            if _is_placeholder(val):
                continue
            title = key
            body = val
        else:
            bullet = _BULLET_RE.match(raw)
            if not bullet:
                continue
            val = bullet.group("val").strip()
            if _is_placeholder(val):
                continue
            title = val[:80]
            body = val

        dedup_key = (title.lower(), body.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        out.append(ExtractedNode(kind=default_kind, title=title, body=body))

    return out


def reindex_markdown(
    store: GraphStore,
    doc_name: str,
    markdown: str,
    *,
    confidence: float = 1.0,
) -> dict[str, int]:
    """Upsert every extractable node from ``markdown`` into ``store``.

    Idempotent via checksum: rerunning on unchanged markdown only bumps
    ``access_count`` / ``last_accessed_at``. Nodes removed from the
    markdown are *not* purged here — that is a separate reconciliation
    concern handled by a future GC pass.
    """

    inserted = 0
    updated = 0
    for node in extract_nodes_from_markdown(doc_name, markdown):
        _, is_new = store.upsert_node(
            kind=node.kind,
            title=node.title,
            body=node.body,
            source_doc=doc_name,
            confidence=confidence,
        )
        if is_new:
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated}


def reindex_many(
    store: GraphStore, documents: Iterable[tuple[str, str]]
) -> dict[str, int]:
    """Batch convenience wrapper over :func:`reindex_markdown`."""

    totals = {"inserted": 0, "updated": 0}
    for doc_name, markdown in documents:
        result = reindex_markdown(store, doc_name, markdown)
        totals["inserted"] += result["inserted"]
        totals["updated"] += result["updated"]
    return totals
