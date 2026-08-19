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
    user TEXT,
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


def compute_checksum(kind: str, title: str, body: str, user: str | None = None) -> str:
    payload = (
        f"{_normalize(kind)}\x00{_normalize(title)}\x00{_normalize(body)}"
        f"\x00{user or ''}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Node:
    id: int
    kind: str
    title: str
    body: str
    source_doc: str | None
    user: str | None
    created_at: float
    last_accessed_at: float
    access_count: int
    confidence: float
    pinned: bool


@dataclass(slots=True)
class RecallHit:
    node: Node
    score: float
    via: str
    related_edges: list[tuple[int, str]] = field(default_factory=list)


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=int(row["id"]),
        kind=str(row["kind"]),
        title=str(row["title"]),
        body=str(row["body"]),
        source_doc=(str(row["source_doc"]) if row["source_doc"] is not None else None),
        user=(str(row["user"]) if row["user"] is not None else None),
        created_at=float(row["created_at"]),
        last_accessed_at=float(row["last_accessed_at"]),
        access_count=int(row["access_count"]),
        confidence=float(row["confidence"]),
        pinned=bool(row["pinned"]),
    )


def _build_fts_query(text: str) -> str:
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
    if node.pinned:
        return max(node.confidence, 0.0) * 2.0
    age_days = max(0.0, (now - node.created_at) / 86400.0)
    decay = math.exp(-age_days / max(half_life_days, 0.001))
    popularity = 1.0 + math.log1p(max(0, node.access_count - 1))
    return max(node.confidence, 0.0) * decay * popularity


class GraphStore:

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
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
            pass
        self._conn.executescript(_SCHEMA)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        cursor = self._conn.execute("PRAGMA table_info(nodes)")
        cols = {row[1] for row in cursor.fetchall()}
        if "user" not in cols:
            self._conn.execute("ALTER TABLE nodes ADD COLUMN user TEXT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


    def upsert_node(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        source_doc: str | None = None,
        confidence: float = 1.0,
        pinned: bool = False,
        user: str | None = None,
    ) -> tuple[int, bool]:
        title = title.strip()
        body = body.strip()
        if not title and not body:
            raise ValueError("node requires title or body")
        checksum = compute_checksum(kind, title, body, user)
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
                "kind, title, body, checksum, source_doc, user, "
                "created_at, last_accessed_at, access_count, confidence, pinned"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    title,
                    body,
                    checksum,
                    source_doc,
                    user,
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

    def cleanup(self) -> dict[str, int]:
        deleted_dup = 0
        deleted_junk = 0
        new_edges = 0

        _JUNK_TITLES = frozenset({
            "title", "enabled", "when", "objective", "steps",
            "delete_after_success", "notes", "status", "created_at",
            "last_run_at", "last_result", "run_count", "state",
        })
        _JUNK_PREFIXES = (
            "last_request_before_tool", "last_tool_intent",
            "last_requested_camera", "standing_goal_",
            "goal_judge_", "judge_goal_",
        )
        _JUNK_BODIES = frozenset({
            "项目 1", "项目 2", "项目 3",
            "[x] 已完成任务", "[ ] 未完成任务",
        })

        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, title, body, source_doc FROM nodes ORDER BY id"
            ).fetchall()

        to_delete: set[int] = set()

        for row in rows:
            nid = int(row["id"])
            kind = str(row["kind"])
            title = str(row["title"]).strip()
            body = str(row["body"]).strip()
            src = str(row["source_doc"] or "")
            title_lower = title.lower()
            if any(title_lower.startswith(p) for p in _JUNK_PREFIXES):
                to_delete.add(nid)
            elif kind == "follow_up" and src == "HEARTBEAT" and title_lower in _JUNK_TITLES:
                to_delete.add(nid)
            elif kind == "style" and src == "SOUL" and body in _JUNK_BODIES:
                to_delete.add(nid)

        seen_titles: dict[str, int] = {}
        for row in rows:
            nid = int(row["id"])
            if nid in to_delete:
                continue
            key = str(row["title"]).strip().lower()
            if key in seen_titles:
                to_delete.add(nid)
            else:
                seen_titles[key] = nid

        for nid in to_delete:
            self.forget(nid)
        deleted_junk = sum(
            1 for row in rows
            if int(row["id"]) in to_delete
            and not any(str(row["title"]).strip().lower().startswith(p) for p in _JUNK_PREFIXES)
            and str(row["title"]).strip().lower() not in _JUNK_TITLES
        )
        deleted_dup = len(to_delete) - deleted_junk

        with self._lock:
            remaining = self._conn.execute(
                "SELECT id, title, body FROM nodes ORDER BY id"
            ).fetchall()
        for row in remaining:
            nid = int(row["id"])
            query_text = f"{row['title']} {str(row['body'])[:100]}"
            hits = self.recall(query_text, limit=3, expand=False, touch=False, include_all=True)
            for h in hits:
                if h.node.id != nid:
                    with self._lock:
                        existing = self._conn.execute(
                            "SELECT 1 FROM edges WHERE src_id=? AND dst_id=?",
                            (nid, h.node.id),
                        ).fetchone()
                    if not existing:
                        try:
                            self.link(nid, h.node.id, "related_to")
                            new_edges += 1
                        except ValueError:
                            pass

        return {
            "deleted_duplicates": deleted_dup,
            "deleted_junk": deleted_junk,
            "new_edges": new_edges,
            **self.stats(),
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
        user: str | None = None,
        include_all: bool = False,
    ) -> list[RecallHit]:
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
        if not include_all:
            if user is not None:
                sql += "AND (n.user = ? OR n.user IS NULL) "
                params.append(user)
            else:
                sql += "AND n.user IS NULL "
        sql += "ORDER BY rank LIMIT ?"
        params.append(max(limit * 3, limit))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        now = time.time()
        hits: dict[int, RecallHit] = {}
        for row in rows:
            node = _row_to_node(row)
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
