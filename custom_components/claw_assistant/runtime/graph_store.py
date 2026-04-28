"""Graph-structured memory store backed by SQLite + FTS5.

Design goals (replaces ad-hoc Markdown keyword scan):
- Deduplication via content checksum.
- Time decay via exponential half-life on `created_at`.
- Ranking via BM25 (FTS5) * decay * confidence.
- Relations via first-class edges (caused_by / supersedes / resolved_by / ...).
- No model installation: vectors live in a reserved BLOB column, populated
  later by an external embedding provider. FTS5 alone already outperforms
  the previous linear markdown scan.

This module is intentionally stdlib-only (sqlite3 + hashlib + math + re)
so it can be unit-tested without spinning up Home Assistant.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "GraphStore",
    "Node",
    "RecallHit",
    "compute_checksum",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    checksum TEXT NOT NULL UNIQUE,
    source_doc TEXT,
    created_at REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 1,
    confidence REAL NOT NULL DEFAULT 1.0,
    pinned INTEGER NOT NULL DEFAULT 0,
    embedding BLOB
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_last_accessed ON nodes(last_accessed_at);
CREATE INDEX IF NOT EXISTS idx_nodes_source ON nodes(source_doc);

CREATE TABLE IF NOT EXISTS edges (
    src_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    dst_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    PRIMARY KEY (src_id, dst_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id, relation);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    title, body, node_id UNINDEXED, tokenize='unicode61'
);
"""

_WS_RE = re.compile(r"\s+")
_FTS_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff\s]", re.UNICODE)


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip().lower()


def compute_checksum(kind: str, title: str, body: str) -> str:
    """Stable content hash used for deduplication across runs."""

    payload = (
        f"{_normalize(kind)}\x00{_normalize(title)}\x00{_normalize(body)}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Node:
    id: int
    kind: str
    title: str
    body: str
    source_doc: str | None
    created_at: float
    last_accessed_at: float
    access_count: int
    confidence: float
    pinned: bool


@dataclass(slots=True)
class RecallHit:
    node: Node
    score: float
    via: str  # "fts" | "edge:<relation>"
    related_edges: list[tuple[int, str]] = field(default_factory=list)


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=int(row["id"]),
        kind=str(row["kind"]),
        title=str(row["title"]),
        body=str(row["body"]),
        source_doc=(str(row["source_doc"]) if row["source_doc"] is not None else None),
        created_at=float(row["created_at"]),
        last_accessed_at=float(row["last_accessed_at"]),
        access_count=int(row["access_count"]),
        confidence=float(row["confidence"]),
        pinned=bool(row["pinned"]),
    )


def _build_fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Strategy: strip punctuation, split on whitespace, OR the tokens together
    with prefix matching. Keeps recall high while avoiding BM25 collapse to
    a single keyword.
    """

    cleaned = _FTS_SAFE_RE.sub(" ", text or "").strip()
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return ""
    parts: list[str] = []
    for token in tokens:
        escaped = token.replace('"', '""')
        if len(token) > 1:
            parts.append(f'"{escaped}"*')
        else:
            parts.append(f'"{escaped}"')
    return " OR ".join(parts)


def _decay_score(node: Node, now: float, half_life_days: float) -> float:
    """score = confidence * exp(-age/half_life) * (1 + ln(1 + extra_access))

    Pinned nodes get a flat 2x bonus and skip decay.
    """

    if node.pinned:
        return max(node.confidence, 0.0) * 2.0
    age_days = max(0.0, (now - node.created_at) / 86400.0)
    decay = math.exp(-age_days / max(half_life_days, 0.001))
    popularity = 1.0 + math.log1p(max(0, node.access_count - 1))
    return max(node.confidence, 0.0) * decay * popularity


class GraphStore:
    """Process-wide graph store over a SQLite file.

    Thread-safe: connection uses ``check_same_thread=False`` and every
    public method acquires ``self._lock``. Methods are synchronous; async
    callers should hop via ``hass.async_add_executor_job`` or
    ``asyncio.to_thread``.
    """

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the store can be shared across Home
        # Assistant's executor threads; we serialize ourselves with _lock.
        # isolation_level=None -> autocommit; explicit transactions only when needed.
        self._conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            # WAL not available on some platforms; safe to ignore.
            pass
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------ writes

    def upsert_node(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        source_doc: str | None = None,
        confidence: float = 1.0,
        pinned: bool = False,
    ) -> tuple[int, bool]:
        """Insert a new node or touch the existing one with the same checksum.

        Returns ``(node_id, was_newly_inserted)``.
        """

        title = title.strip()
        body = body.strip()
        if not title and not body:
            raise ValueError("node requires title or body")
        checksum = compute_checksum(kind, title, body)
        now = time.time()
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM nodes WHERE checksum = ?", (checksum,)
            ).fetchone()
            if row is not None:
                node_id = int(row["id"])
                self._conn.execute(
                    "UPDATE nodes "
                    "SET last_accessed_at = ?, access_count = access_count + 1, "
                    "    source_doc = COALESCE(?, source_doc) "
                    "WHERE id = ?",
                    (now, source_doc, node_id),
                )
                return node_id, False

            cur = self._conn.execute(
                "INSERT INTO nodes("
                "kind, title, body, checksum, source_doc, "
                "created_at, last_accessed_at, access_count, confidence, pinned"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    title,
                    body,
                    checksum,
                    source_doc,
                    now,
                    now,
                    1,
                    float(confidence),
                    1 if pinned else 0,
                ),
            )
            node_id = int(cur.lastrowid)
            self._conn.execute(
                "INSERT INTO nodes_fts(title, body, node_id) VALUES (?,?,?)",
                (title, body, node_id),
            )
            return node_id, True

    def link(
        self,
        src_id: int,
        dst_id: int,
        relation: str,
        *,
        weight: float = 1.0,
    ) -> None:
        if src_id == dst_id:
            raise ValueError("self-loop edges are not allowed")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO edges("
                "src_id, dst_id, relation, weight, created_at"
                ") VALUES (?,?,?,?,?)",
                (int(src_id), int(dst_id), str(relation), float(weight), time.time()),
            )

    def pin(self, node_id: int, pinned: bool = True) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, int(node_id)),
            )

    def forget(self, node_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM nodes_fts WHERE node_id = ?", (int(node_id),)
            )
            self._conn.execute("DELETE FROM nodes WHERE id = ?", (int(node_id),))

    def set_confidence(self, node_id: int, confidence: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET confidence = ? WHERE id = ?",
                (float(confidence), int(node_id)),
            )

    # ------------------------------------------------------------------- reads

    def get(self, node_id: int) -> Node | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (int(node_id),)
            ).fetchone()
        return _row_to_node(row) if row else None

    def neighbors(
        self, node_id: int, *, relations: Iterable[str] | None = None
    ) -> list[tuple[Node, str, float]]:
        sql = (
            "SELECT n.*, e.relation AS _rel, e.weight AS _w "
            "FROM edges e JOIN nodes n ON n.id = e.dst_id "
            "WHERE e.src_id = ? "
        )
        params: list = [int(node_id)]
        if relations:
            rels = list(relations)
            sql += f"AND e.relation IN ({','.join('?' * len(rels))}) "
            params.extend(rels)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            (_row_to_node(row), str(row["_rel"]), float(row["_w"])) for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "nodes": int(
                    self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                ),
                "edges": int(
                    self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
                ),
            }

    def recall(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 8,
        expand: bool = True,
        half_life_days: float = 30.0,
        touch: bool = True,
    ) -> list[RecallHit]:
        """Top-``limit`` hits for ``query``, ranked by BM25 * decay.

        If ``expand``, 1-hop edge neighbours of the top hits are included
        with a dampened score so that causal / supersession chains surface
        alongside direct matches.
        """

        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        sql = (
            "SELECT n.*, bm25(nodes_fts) AS rank "
            "FROM nodes_fts JOIN nodes n ON n.id = nodes_fts.node_id "
            "WHERE nodes_fts MATCH ? "
        )
        params: list = [fts_query]
        if kinds:
            kinds_list = list(kinds)
            sql += f"AND n.kind IN ({','.join('?' * len(kinds_list))}) "
            params.extend(kinds_list)
        sql += "ORDER BY rank LIMIT ?"
        params.append(max(limit * 3, limit))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        now = time.time()
        hits: dict[int, RecallHit] = {}
        for row in rows:
            node = _row_to_node(row)
            # bm25 returns a distance (lower=better, non-negative for OR'd terms).
            raw_rank = float(row["rank"])
            similarity = 1.0 / (1.0 + abs(raw_rank))
            score = _decay_score(node, now, half_life_days) * similarity
            hits[node.id] = RecallHit(
                node=node, score=score, via="fts", related_edges=[]
            )

        if expand and hits:
            ids = list(hits.keys())
            placeholders = ",".join("?" * len(ids))
            with self._lock:
                edge_rows = self._conn.execute(
                    f"SELECT src_id, dst_id, relation, weight FROM edges "
                    f"WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})",
                    ids + ids,
                ).fetchall()
            for erow in edge_rows:
                src_id = int(erow["src_id"])
                dst_id = int(erow["dst_id"])
                relation = str(erow["relation"])
                weight = float(erow["weight"])
                if src_id in hits:
                    hits[src_id].related_edges.append((dst_id, relation))
                    if dst_id not in hits:
                        neighbour = self.get(dst_id)
                        if neighbour is not None:
                            base = _decay_score(neighbour, now, half_life_days)
                            hits[dst_id] = RecallHit(
                                node=neighbour,
                                score=base * 0.3 * weight,
                                via=f"edge:{relation}",
                                related_edges=[(src_id, relation)],
                            )
                if dst_id in hits and src_id not in hits:
                    neighbour = self.get(src_id)
                    if neighbour is not None:
                        base = _decay_score(neighbour, now, half_life_days)
                        hits[src_id] = RecallHit(
                            node=neighbour,
                            score=base * 0.3 * weight,
                            via=f"edge:{relation}",
                            related_edges=[(dst_id, relation)],
                        )

        if touch and hits:
            touched = list(hits.keys())
            placeholders = ",".join("?" * len(touched))
            with self._lock:
                self._conn.execute(
                    f"UPDATE nodes "
                    f"SET last_accessed_at = ?, access_count = access_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    [now, *touched],
                )

        ranked = sorted(hits.values(), key=lambda h: h.score, reverse=True)
        return ranked[:limit]
