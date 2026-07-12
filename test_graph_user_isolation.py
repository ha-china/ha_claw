"""Tests for graph_store user-based memory personalization.

Runs without HA runtime — imports graph_store directly by path.
"""

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _load_graph_store():
    mod_path = Path(__file__).parent / "custom_components" / "claw_assistant" / "runtime" / "storage" / "graph_store.py"
    spec = importlib.util.spec_from_file_location("graph_store", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["graph_store"] = mod
    spec.loader.exec_module(mod)
    return mod


gs = _load_graph_store()
GraphStore = gs.GraphStore
compute_checksum = gs.compute_checksum


def _make_store(tmpdir):
    """Create a store in tmpdir and return (store, db_path)."""
    db_path = Path(tmpdir) / "test.db"
    store = GraphStore(db_path)
    return store, db_path


def _close(store, db_path):
    """Close store and remove WAL files so cleanup works on Windows."""
    store.close()
    for suffix in ("-wal", "-shm"):
        p = db_path.parent / (db_path.name + suffix)
        if p.exists():
            try:
                os.remove(p)
            except PermissionError:
                pass


def test_compute_checksum_includes_user():
    c_without = compute_checksum("preference", "喜欢", "吃辣")
    c_alice = compute_checksum("preference", "喜欢", "吃辣", user="alice")
    c_bob = compute_checksum("preference", "喜欢", "吃辣", user="bob")
    assert c_without == compute_checksum("preference", "喜欢", "吃辣")
    assert c_alice != c_without
    assert c_bob != c_alice


def test_upsert_and_recall_by_user():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="alice")
        hits = store.recall("吃辣", user="alice")
        assert len(hits) == 1
        assert hits[0].node.title == "喜欢"
        assert hits[0].node.body == "吃辣"
        assert hits[0].node.user == "alice"
        _close(store, db)


def test_different_user_does_not_see():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="alice")
        hits = store.recall("吃辣", user="bob")
        assert len(hits) == 0
        _close(store, db)


def test_public_data_visible_to_all():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="fact", title="天气", body="深圳", user=None)
        for u in ["alice", "bob", None]:
            hits = store.recall("天气", user=u)
            assert len(hits) == 1, f"user={u}"
        _close(store, db)


def test_recall_no_user_returns_only_public():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="alice")
        store.upsert_node(kind="fact", title="天气", body="深圳", user=None)
        assert len(store.recall("吃辣")) == 0
        assert len(store.recall("天气")) == 1
        _close(store, db)


def test_include_all_bypasses_filter():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="alice")
        store.upsert_node(kind="preference", title="喜欢", body="清淡", user="bob")
        assert len(store.recall("喜欢", user="alice")) == 1
        assert len(store.recall("喜欢", user="alice", include_all=True)) == 2
        _close(store, db)


def test_two_users_same_content_ok():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        id1, new1 = store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="alice")
        id2, new2 = store.upsert_node(kind="preference", title="喜欢", body="吃辣", user="bob")
        assert new1 is True
        assert new2 is True
        assert id1 != id2
        _close(store, db)


def test_node_dataclass_has_user():
    with tempfile.TemporaryDirectory() as td:
        store, db = _make_store(td)
        store.upsert_node(kind="preference", title="测试", body="数据", user="alice")
        node = store.recall("测试", user="alice")[0].node
        assert hasattr(node, "user")
        assert node.user == "alice"
        _close(store, db)


def test_schema_migration_adds_user():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, body TEXT, checksum TEXT UNIQUE)")
        conn.close()

        store, _ = _make_store(td)
        cursor = store._conn.execute("PRAGMA table_info(nodes)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "user" in cols
        _close(store, db_path)
