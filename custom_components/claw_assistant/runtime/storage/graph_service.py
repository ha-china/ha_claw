from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from homeassistant.core import HomeAssistant

from ..utils.data_path import get_data_dir
from .graph_store import GraphStore, Node, RecallHit
from .md_to_graph import reindex_markdown

LOGGER = logging.getLogger(__name__)

_HASS_DATA_KEY = "claw_assistant_graph_store"
_DB_FILENAME = "graph.db"

_singleton_store: GraphStore | None = None


def _db_path() -> Path:
    return get_data_dir() / _DB_FILENAME


def get_graph_store(hass: HomeAssistant) -> GraphStore | None:

    return hass.data.get(_HASS_DATA_KEY)


def get_graph_store_sync() -> GraphStore | None:

    return _singleton_store


def _open_store() -> GraphStore:
    return GraphStore(_db_path())


async def async_setup_graph_store(hass: HomeAssistant) -> GraphStore:


    global _singleton_store

    existing = get_graph_store(hass)
    if existing is not None:
        return existing

    store = await hass.async_add_executor_job(_open_store)
    hass.data[_HASS_DATA_KEY] = store
    _singleton_store = store

    try:
        totals = await async_bootstrap_reindex(hass, store)
        LOGGER.info(
            "Graph store ready at %s (bootstrap inserted=%d, refreshed=%d, stats=%s)",
            _db_path(),
            totals["inserted"],
            totals["updated"],
            await hass.async_add_executor_job(store.stats),
        )
    except Exception:
        LOGGER.exception("Graph bootstrap reindex failed; store is still usable")

    return store


async def async_unload_graph_store(hass: HomeAssistant) -> None:
    global _singleton_store

    store = hass.data.pop(_HASS_DATA_KEY, None)
    _singleton_store = None
    if store is None:
        return
    await hass.async_add_executor_job(store.close)


def recall_memory_lines_sync(
    user_text: str, *, limit: int = 12, kinds: tuple[str, ...] | None = None,
    user: str | None = None,
) -> list[str]:


    store = _singleton_store
    if store is None or not user_text.strip():
        return []
    try:
        hits = store.recall(
            user_text, kinds=list(kinds) if kinds else None, limit=limit,
            user=user,
        )
    except Exception:
        LOGGER.exception("Sync graph recall failed")
        return []

    lines: list[str] = []
    for hit in hits:
        title = hit.node.title.strip()
        body = hit.node.body.strip()
        if title and body and title.lower() != body.lower():
            line = f"- {title}: {body}"
        else:
            line = f"- {body or title}"
        if line not in lines:
            lines.append(line)
    return lines


async def async_bootstrap_reindex(
    hass: HomeAssistant, store: GraphStore
) -> dict[str, int]:

    from .workspace_store import WORKSPACE_DOC_NAMES, _doc_path 

    def _scan_and_index() -> dict[str, int]:
        totals = {"inserted": 0, "updated": 0}
        for name in WORKSPACE_DOC_NAMES:
            try:
                path = _doc_path(name)
            except ValueError:
                continue
            if not path.exists():
                continue
            try:
                markdown = path.read_text(encoding="utf-8")
            except OSError as exc:
                LOGGER.warning("Cannot read %s for reindex: %s", path, exc)
                continue
            result = reindex_markdown(store, name, markdown)
            totals["inserted"] += result["inserted"]
            totals["updated"] += result["updated"]
        return totals

    return await hass.async_add_executor_job(_scan_and_index)


async def async_reindex_doc(
    hass: HomeAssistant, doc_name: str, markdown: str
) -> dict[str, int]:


    store = get_graph_store(hass)
    if store is None:
        return {"inserted": 0, "updated": 0}

    def _do() -> dict[str, int]:
        return reindex_markdown(store, doc_name, markdown)

    try:
        return await hass.async_add_executor_job(_do)
    except Exception:
        LOGGER.exception("Graph reindex of %s failed", doc_name)
        return {"inserted": 0, "updated": 0}


async def async_recall(
    hass: HomeAssistant,
    query: str,
    *,
    kinds: Iterable[str] | None = None,
    limit: int = 8,
    expand: bool = True,
    user: str | None = None,
) -> list[RecallHit]:

    store = get_graph_store(hass)
    if store is None:
        return []
    kinds_list = list(kinds) if kinds else None

    def _do() -> list[RecallHit]:
        return store.recall(
            query, kinds=kinds_list, limit=limit, expand=expand,
            user=user,
        )

    try:
        return await hass.async_add_executor_job(_do)
    except Exception:
        LOGGER.exception("Graph recall for %r failed", query)
        return []


async def async_remember(
    hass: HomeAssistant,
    *,
    kind: str,
    title: str,
    body: str,
    source_doc: str | None = None,
    confidence: float = 1.0,
    pinned: bool = False,
    user: str | None = None,
) -> tuple[int, bool] | None:
    store = get_graph_store(hass)
    if store is None:
        return None

    def _do() -> tuple[int, bool]:
        return store.upsert_node(
            kind=kind,
            title=title,
            body=body,
            source_doc=source_doc,
            confidence=confidence,
            pinned=pinned,
            user=user,
        )

    try:
        return await hass.async_add_executor_job(_do)
    except Exception:
        LOGGER.exception("Graph upsert failed for %r", title)
        return None


async def async_link(
    hass: HomeAssistant,
    src_id: int,
    dst_id: int,
    relation: str,
    *,
    weight: float = 1.0,
) -> bool:
    store = get_graph_store(hass)
    if store is None:
        return False

    def _do() -> None:
        store.link(int(src_id), int(dst_id), str(relation), weight=float(weight))

    try:
        await hass.async_add_executor_job(_do)
        return True
    except Exception:
        LOGGER.exception("Graph link failed (%s -[%s]-> %s)", src_id, relation, dst_id)
        return False


async def async_get_node(hass: HomeAssistant, node_id: int) -> Node | None:
    store = get_graph_store(hass)
    if store is None:
        return None

    def _do() -> Node | None:
        return store.get(int(node_id))

    return await hass.async_add_executor_job(_do)
