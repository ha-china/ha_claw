

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import logging
from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir

LOGGER = logging.getLogger(__name__)


def _guide_dir() -> Path:
    return get_data_dir() / "homeassistant_guide"


def _runtime_guide_dir() -> Path:
    return _guide_dir() / "runtime"


def _source_guide_dir() -> Path:
    return _guide_dir() / "source"
MAX_SEARCH_SNIPPET_CHARS = 1600
RUNTIME_COLLECTION = "runtime"
SOURCE_COLLECTION = "source"
COLLECTION_PURPOSE = {
    RUNTIME_COLLECTION: "Primary runtime playbooks adapted for kadermanager",
    SOURCE_COLLECTION: "Original migrated source material and deep references",
}


@dataclass(slots=True, frozen=True)
class GuideDocument:


    doc_id: str
    collection: str
    relative_path: str
    title: str
    content: str
    keywords: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class GuideStoreSnapshot:


    documents: tuple[GuideDocument, ...] = ()
    signature: tuple[str, ...] = ()


_GUIDE_STORE: dict[str, GuideStoreSnapshot] = {"snapshot": GuideStoreSnapshot()}
_GUIDE_TOPIC_HINTS: dict[str, tuple[str, ...]] = {
    "automation": ("automation", "automations", "自动化", "脚本", "script", "trigger", "trace"),
    "dashboard": ("dashboard", "lovelace", "仪表盘", "卡片", "card", "视图", "view"),
    "integration": ("integration", "integrations", "集成", "reload", "repair", "repairs", "修复"),
    "calendar": ("calendar", "calendars", "日历"),
    "todo": ("todo", "待办", "任务", "清单", "shopping list", "购物"),
    "backup": ("backup", "restore", "备份", "恢复", "rollback", "回滚"),
    "diagnostics": ("diagnostics", "logs", "trace", "日志", "诊断"),
    "esphome": ("esphome", "esp", "固件", "ota", "节点"),
    "safety": ("safety", "safe", "风险", "安全", "确认", "危险"),
    "naming context": ("alias", "aliases", "命名", "名称", "别名", "friendly name"),
    "template": ("template", "jinja", "模板", "jinja2", "state_attr", "trigger."),
    "blueprint": ("blueprint", "蓝图", "blueprints"),
    "scene": ("scene", "scenes", "场景", "快照"),
    "zigbee": ("zigbee", "zha", "z2m", "zigbee2mqtt", "coordinator", "配对", "pairing"),
    "zwave": ("zwave", "z-wave", "zwavejs", "s2", "dsk"),
    "addon": ("add-on", "addon", "supervisor", "插件", "附加组件"),
    "yaml config": ("yaml", "configuration.yaml", "customize", "package", "packages"),
    "network": ("ssl", "certificate", "dns", "duckdns", "nginx", "proxy", "远程访问", "external_url"),
    "energy": ("energy", "电量", "用电", "solar", "太阳能", "utility_meter"),
    "recorder": ("recorder", "数据库", "database", "purge", "statistics"),
    "voice": ("tts", "piper", "whisper", "语音", "voice", "assist", "stt"),
    "presence": ("device_tracker", "zone", "person", "位置", "在家", "离家", "geofence"),
    "custom component": ("custom_component", "hacs", "自定义组件", "第三方"),
    "area floor": ("area", "floor", "区域", "楼层", "房间", "label"),
}


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _title_from_content(default_title: str, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or default_title
    return default_title


def _build_keywords(collection: str, relative_path: str, title: str, content: str) -> tuple[str, ...]:
    seeds = {
        collection,
        title,
        relative_path,
        Path(relative_path).stem.replace("_", " ").replace("-", " "),
    }
    first_lines = " ".join(content.splitlines()[:8])
    tokens = re.findall(r"[a-zA-Z0-9_./-]+", " ".join((*seeds, first_lines)))
    keywords = {_slugify(token) for token in tokens if len(token) >= 3}
    keywords.discard("")
    return tuple(sorted(keywords))


def _iter_markdown_documents(base_dir: Path, collection: str) -> list[GuideDocument]:
    if not base_dir.exists():
        return []

    documents: list[GuideDocument] = []
    for path in sorted(base_dir.rglob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue

        relative_path = path.relative_to(base_dir).as_posix()
        stem = relative_path.removesuffix(".md")
        title = _title_from_content(Path(relative_path).stem, content)
        doc_id = f"{collection}/{stem}"
        documents.append(
            GuideDocument(
                doc_id=doc_id,
                collection=collection,
                relative_path=relative_path,
                title=title,
                content=content,
                keywords=_build_keywords(collection, relative_path, title, content),
            )
        )
    return documents


def _read_guide_store_from_disk() -> GuideStoreSnapshot:
    documents = [
        *_iter_markdown_documents(_runtime_guide_dir(), RUNTIME_COLLECTION),
        *_iter_markdown_documents(_source_guide_dir(), SOURCE_COLLECTION),
    ]
    return GuideStoreSnapshot(
        documents=tuple(documents),
        signature=_guide_store_signature(),
    )


def _guide_store_signature() -> tuple[str, ...]:

    paths = [
        *sorted(_runtime_guide_dir().rglob("*.md")),
        *sorted(_source_guide_dir().rglob("*.md")),
    ]
    signature: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        stat = path.stat()
        signature.append(
            f"{path.relative_to(_guide_dir()).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}"
        )
    return tuple(signature)


def _set_guide_store(snapshot: GuideStoreSnapshot) -> None:
    _GUIDE_STORE["snapshot"] = snapshot


def _ensure_guide_store_fresh() -> GuideStoreSnapshot:

    return _GUIDE_STORE["snapshot"]


async def async_setup_homeassistant_guide_store(hass: HomeAssistant) -> None:

    await async_refresh_homeassistant_guide_store(hass)


async def async_refresh_homeassistant_guide_store(hass: HomeAssistant) -> None:

    snapshot = await hass.async_add_executor_job(_read_guide_store_from_disk)
    _set_guide_store(snapshot)


def _snapshot() -> GuideStoreSnapshot:
    return _ensure_guide_store_fresh()


def list_homeassistant_guide_docs() -> list[dict[str, str]]:

    return [
        {
            "id": document.doc_id,
            "collection": document.collection,
            "purpose": COLLECTION_PURPOSE.get(document.collection, ""),
            "path": document.relative_path,
            "title": document.title,
            "chars": str(len(document.content)),
        }
        for document in _snapshot().documents
    ]


def get_homeassistant_guide_overview() -> dict[str, object]:

    documents = _snapshot().documents
    runtime_titles = [
        document.title
        for document in documents
        if document.collection == RUNTIME_COLLECTION
    ]
    collections = {
        RUNTIME_COLLECTION: sum(1 for document in documents if document.collection == RUNTIME_COLLECTION),
        SOURCE_COLLECTION: sum(1 for document in documents if document.collection == SOURCE_COLLECTION),
    }
    usage_rules = [
        "Read runtime docs first for kadermanager behavior.",
        "Only consult source docs when runtime docs are insufficient or when you explicitly need the original teaching material.",
        "Inside kadermanager never require HA_TOKEN, mcporter, hab, hub, or terminal setup.",
        "Use Home Assistant internal permissions, native intents, entity state APIs, and services instead.",
    ]
    return {
        "success": True,
        "count": len(documents),
        "collections": collections,
        "collection_purpose": COLLECTION_PURPOSE,
        "read_order": [
            "runtime/00_overview",
            "runtime/10_quickstart",
            "runtime/20_capability_mapping",
            "runtime/30_safety_and_workflows",
            "runtime/40_workflow_playbooks",
            "runtime/50_checklists_and_naming",
        ],
        "runtime_topics": runtime_titles,
        "usage_rules": usage_rules,
    }


def build_homeassistant_guide_prompt_block() -> str:

    runtime_docs = {
        document.relative_path: document
        for document in _snapshot().documents
        if document.collection == "runtime"
    }

    overview = runtime_docs.get("00_overview.md")
    quickstart = runtime_docs.get("10_quickstart.md")

    lines = [
        "Home Assistant requests inside kadermanager follow an internal operating model.",
        "Start by understanding the system with GetSystemIndex or GetLiveContext.",
        "Resolve concrete entities with SmartDiscovery or EntityQuery before acting.",
        "Execute actions with DeviceSkill first, then ServiceCall when precise service data is required.",
        "For workflows, troubleshooting, dashboards, integrations, repairs, backups, calendars, diagnostics, and ESPHome, use HomeAssistantGuideSkill first.",
        "Never route the user toward external MCP setup, shell wrappers, ha.sh, hab, or external credentials from inside this integration.",
    ]

    if overview:
        lines.append(f"Guide overview: {overview.title}.")
    if quickstart:
        lines.append(f"Quick start doc: {quickstart.title}.")

    return "\n".join(lines)


def build_homeassistant_topic_hint(query: str) -> str:

    lowered = query.lower()
    matched_topics = [
        topic
        for topic, keywords in _GUIDE_TOPIC_HINTS.items()
        if any(keyword in lowered for keyword in keywords)
    ]
    if not matched_topics:
        return ""

    topics = ", ".join(dict.fromkeys(matched_topics))
    return (
        "Current request maps to Home Assistant guide topics: "
        f"{topics}. "
        "If the user is asking HOW to do something, HOW to fix something, or HOW to design something, consult HomeAssistantGuideSkill first. "
        "If the user is simply requesting an action (e.g. 'add a todo', 'turn on the light'), execute it directly without consulting the guide."
    )


def _match_document(identifier: str) -> GuideDocument | None:
    normalized = identifier.strip().lower()
    if not normalized:
        return None

    matches: list[GuideDocument] = []
    for document in _snapshot().documents:
        if normalized in {
            document.doc_id.lower(),
            document.relative_path.lower(),
            Path(document.relative_path).stem.lower(),
            document.title.lower(),
        }:
            matches.append(document)
    if not matches:
        return None
    matches.sort(
        key=lambda document: (
            document.collection != RUNTIME_COLLECTION,
            document.relative_path,
        )
    )
    return matches[0]


def get_homeassistant_guide_doc(identifier: str) -> dict[str, object]:

    document = _match_document(identifier)
    if document is None:
        return {
            "success": False,
            "error": f"Guide document not found: {identifier}",
        }
    return {
        "success": True,
        "id": document.doc_id,
        "collection": document.collection,
        "purpose": COLLECTION_PURPOSE.get(document.collection, ""),
        "path": document.relative_path,
        "title": document.title,
        "markdown": document.content,
    }


def _score_document(document: GuideDocument, query_tokens: list[str]) -> tuple[int, int]:
    lower_title = document.title.lower()
    lower_path = document.relative_path.lower()
    lower_content = document.content.lower()
    score = 0
    first_match = len(lower_content)

    for token in query_tokens:
        if token in lower_title:
            score += 8
        if token in lower_path:
            score += 6
        if token in document.keywords:
            score += 5
        index = lower_content.find(token)
        if index >= 0:
            score += 2
            first_match = min(first_match, index)

    return score, first_match


def _build_snippet(content: str, query_tokens: list[str]) -> str:
    lower_content = content.lower()
    first_match = min(
        (lower_content.find(token) for token in query_tokens if lower_content.find(token) >= 0),
        default=0,
    )
    start = max(0, first_match - 220)
    end = min(len(content), start + MAX_SEARCH_SNIPPET_CHARS)
    return content[start:end].strip()


def _resolve_search_collections(query: str) -> tuple[str, ...]:
    lowered = query.lower().strip()
    if lowered.startswith("source:") or lowered.startswith("reference:"):
        return (SOURCE_COLLECTION,)
    return (RUNTIME_COLLECTION,)







def _sanitize_runtime_relative_path(relative_path: str) -> Path:

    raw = (relative_path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("relative_path is required")
    if raw.startswith("/"):
        raw = raw.lstrip("/")
    candidate = (_runtime_guide_dir() / raw).resolve()
    base = _runtime_guide_dir().resolve()
    try:
        candidate.relative_to(base)
    except ValueError as err:
        raise ValueError("relative_path must stay inside runtime/") from err
    if candidate.suffix.lower() != ".md":
        raise ValueError("relative_path must end with .md")
    return candidate


def _read_runtime_doc_raw(relative_path: str) -> str | None:
    path = _sanitize_runtime_relative_path(relative_path)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _write_runtime_doc_sync(relative_path: str, markdown: str) -> Path:
    if not markdown.strip():
        raise ValueError("Guide markdown is empty")
    path = _sanitize_runtime_relative_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(path), markdown.strip() + "\n")
    return path


def _delete_runtime_doc_sync(relative_path: str) -> tuple[Path, str | None]:
    path = _sanitize_runtime_relative_path(relative_path)
    if not path.exists():
        raise FileNotFoundError(f"Guide document not found: {relative_path}")
    previous = path.read_text(encoding="utf-8")
    path.unlink()
    return path, previous


async def async_upsert_runtime_guide_doc(
    hass: HomeAssistant,
    relative_path: str,
    markdown: str,
    *,
    actor: str = "ai",
    reason: str = "",
) -> Path:

    from .self_edit import async_record_change

    previous = await hass.async_add_executor_job(
        partial(_read_runtime_doc_raw, relative_path)
    )
    path = await hass.async_add_executor_job(
        partial(_write_runtime_doc_sync, relative_path, markdown)
    )
    await async_refresh_homeassistant_guide_store(hass)
    await async_record_change(
        hass,
        target_type="guide",
        target_id=f"runtime/{path.relative_to(_runtime_guide_dir()).as_posix()}",
        action="update" if previous else "create",
        before=previous,
        after=markdown,
        actor=actor,
        reason=reason,
    )
    return path


async def async_delete_runtime_guide_doc(
    hass: HomeAssistant,
    relative_path: str,
    *,
    actor: str = "ai",
    reason: str = "",
) -> Path:

    from .self_edit import async_record_change

    path, previous = await hass.async_add_executor_job(
        partial(_delete_runtime_doc_sync, relative_path)
    )
    await async_refresh_homeassistant_guide_store(hass)
    await async_record_change(
        hass,
        target_type="guide",
        target_id=f"runtime/{path.relative_to(_runtime_guide_dir()).as_posix()}",
        action="delete",
        before=previous,
        after=None,
        actor=actor,
        reason=reason,
    )
    return path


def search_homeassistant_guide(query: str, *, limit: int = 5) -> dict[str, object]:

    query_tokens = [_slugify(token) for token in re.findall(r"[a-zA-Z0-9_./-]+", query.lower())]
    query_tokens = [token for token in query_tokens if token]
    if not query_tokens:
        return {
            "success": False,
            "error": "Search query is empty",
        }

    allowed_collections = _resolve_search_collections(query)
    ranked: list[tuple[int, int, GuideDocument]] = []
    for document in _snapshot().documents:
        if document.collection not in allowed_collections:
            continue
        score, first_match = _score_document(document, query_tokens)
        if score <= 0:
            continue
        ranked.append((score, first_match, document))

    ranked.sort(key=lambda item: (-item[0], item[1], item[2].doc_id))
    results = []
    for score, _, document in ranked[: max(1, limit)]:
        results.append(
            {
                "id": document.doc_id,
                "collection": document.collection,
                "purpose": COLLECTION_PURPOSE.get(document.collection, ""),
                "path": document.relative_path,
                "title": document.title,
                "score": score,
                "snippet": _build_snippet(document.content, query_tokens),
            }
        )

    return {
        "success": True,
        "query": query,
        "count": len(results),
        "searched_collections": list(allowed_collections),
        "results": results,
    }
