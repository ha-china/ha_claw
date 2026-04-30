

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir

LOGGER = logging.getLogger(__name__)


def _workspace_dir() -> Path:
    return get_data_dir() / "workspace"


def _workspace_state_path() -> Path:
    return _workspace_dir() / ".workspace_state.json"


def _workspace_memory_dir() -> Path:
    return _workspace_dir() / "memory"
WORKSPACE_DOC_NAMES = (
    "AGENTS",
    "BOOTSTRAP",
    "HEARTBEAT",
    "IDENTITY",
    "MEMORY",
    "SOUL",
    "TOOLS",
    "USER",
)
_MEMORY_TOKEN_RE = re.compile(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", flags=re.IGNORECASE)
_MEMORY_ALWAYS_INCLUDE_MARKERS = (
    "preferred_address",
    "user_preference",
    "concise",
    "少废话",
    "简短",
    "直接",
    "timezone",
    "称呼",
)
_HEARTBEAT_KEYWORDS = (
    "heartbeat",
    "follow-up",
    "follow up",
    "reminder",
    "remind",
    "提醒",
    "回头",
    "稍后",
    "之后",
    "待会",
)
_MAX_MEMORY_LINES = 20
_MAX_MEMORY_CHARS = 2000
_DOC_PURPOSES = {
    "SOUL": "communication style and tone",
    "IDENTITY": "confirmed assistant identity facts (name, persona, vibe, emoji)",
    "USER": "confirmed objective user facts (name, timezone, pronouns, household context)",
    "MEMORY": "long-term user preferences and durable constraints (address preference, reply style, operational habits)",
    "TOOLS": "environment notes, entity/service identifiers, credentials when explicitly provided by user",
    "HEARTBEAT": "follow-up tasks, managed through HeartbeatManager",
    "BOOTSTRAP": "first-run collection flow",
    "AGENTS": "workspace governance rules",
}


@dataclass(slots=True, frozen=True)
class WorkspaceSnapshot:


    agents: str = ""
    bootstrap: str = ""
    bootstrap_active: bool = True
    heartbeat: str = ""
    identity: str = ""
    memory: str = ""
    soul: str = ""
    tools: str = ""
    user: str = ""
    daily_memory: str = ""
    startup_read_order: tuple[str, ...] = ()
    signature: tuple[str, ...] = ()


_WORKSPACE_STORE: dict[str, WorkspaceSnapshot] = {"snapshot": WorkspaceSnapshot()}
def _read_doc(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _read_memory_doc() -> str:
    ws = _workspace_dir()
    content = _read_doc(ws / "MEMORY.md")
    if content:
        return content
    return _read_doc(ws / "memory.md")


def _today_memory_name() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d") + ".md"


def _today_memory_path() -> Path:
    return _workspace_memory_dir() / _today_memory_name()


def _read_daily_memory_doc() -> str:
    return _read_doc(_today_memory_path())


def _parse_startup_read_order(agents_markdown: str) -> tuple[str, ...]:
    matches = re.findall(r"^\d+\.\s+`([^`]+\.md)`", agents_markdown, flags=re.MULTILINE)
    order = [Path(match).stem.upper() for match in matches]
    return tuple(item for item in order if item in WORKSPACE_DOC_NAMES)


def _normalize_doc_name(name: str) -> str:
    normalized = Path(name.strip()).name.upper()
    normalized = normalized.removesuffix(".MD")
    if normalized not in WORKSPACE_DOC_NAMES:
        raise ValueError(f"Unknown workspace document: {name}")
    return normalized


def _doc_path(name: str) -> Path:
    normalized = _normalize_doc_name(name)
    return _workspace_dir() / f"{normalized}.md"


def _workspace_governance_block() -> str:
    lines = [
        "### Workspace Governance",
        "Each workspace markdown is a strict typed store with a single purpose.",
        "Before writing, always read the target document first and apply the smallest confirmed change.",
        "Never invent values to fill empty templates. Never move facts between files unless explicitly asked.",
        "Same-category facts must use a single canonical key; do not create duplicate keys for one concept.",
        "All workspace markdown is automatically indexed into a graph memory "
        "(SQLite + FTS5, BM25 ranked, time decay, typed edges). "
        "Use the **MemoryGraph** tool to recall durable facts, link causes/effects, "
        "or remember decisions and bug fixes that do not belong in any single markdown.",
        "Allowed scope per document:",
    ]
    lines.extend(f"- **{name}.md** — {purpose}." for name, purpose in _DOC_PURPOSES.items())
    return "\n".join(lines)


def _read_workspace_state() -> dict[str, bool]:
    wsp = _workspace_state_path()
    if not wsp.exists():
        return {"bootstrap_active": True}
    try:
        data = json.loads(wsp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"bootstrap_active": True}
    return {
        "bootstrap_active": bool(data.get("bootstrap_active", True)),
    }


def _write_workspace_state(state: dict[str, bool]) -> None:
    _workspace_dir().mkdir(parents=True, exist_ok=True)
    _workspace_state_path().write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_doc(path: Path, markdown: str) -> Path:
    _workspace_dir().mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(path), markdown.strip() + "\n")
    return path


def _write_daily_memory_doc(markdown: str) -> Path:
    _workspace_memory_dir().mkdir(parents=True, exist_ok=True)
    path = _today_memory_path()
    write_utf8_file(str(path), markdown.strip() + "\n")
    return path


def _load_workspace_snapshot() -> WorkspaceSnapshot:
    workspace_state = _read_workspace_state()
    ws = _workspace_dir()
    agents = _read_doc(ws / "AGENTS.md")
    return WorkspaceSnapshot(
        agents=agents,
        bootstrap=_read_doc(ws / "BOOTSTRAP.md"),
        bootstrap_active=workspace_state.get("bootstrap_active", True),
        heartbeat=_read_doc(ws / "HEARTBEAT.md"),
        identity=_read_doc(ws / "IDENTITY.md"),
        memory=_read_memory_doc(),
        soul=_read_doc(ws / "SOUL.md"),
        tools=_read_doc(ws / "TOOLS.md"),
        user=_read_doc(ws / "USER.md"),
        daily_memory=_read_daily_memory_doc(),
        startup_read_order=_parse_startup_read_order(agents),
        signature=_workspace_store_signature(),
    )


def _set_snapshot(snapshot: WorkspaceSnapshot) -> None:
    _WORKSPACE_STORE["snapshot"] = snapshot


def _workspace_store_signature() -> tuple[str, ...]:

    ws = _workspace_dir()
    paths = [
        _workspace_state_path(),
        *sorted(ws.glob("*.md")),
        ws / "memory.md",
        *sorted(_workspace_memory_dir().glob("*.md")),
    ]
    signature: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        stat = path.stat()
        signature.append(
            f"{path.relative_to(ws).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}"
        )
    return tuple(signature)


def _ensure_workspace_store_fresh() -> WorkspaceSnapshot:

    return _WORKSPACE_STORE["snapshot"]


async def async_setup_workspace_store(hass: HomeAssistant) -> None:

    await async_refresh_workspace_store(hass)


async def async_finalize_bootstrap_if_ready(hass: HomeAssistant) -> bool:

    snapshot = _WORKSPACE_STORE["snapshot"]
    if not snapshot.bootstrap_active:
        return False
    if _is_identity_incomplete(snapshot.identity) or _is_user_incomplete(snapshot.user):
        return False
    await hass.async_add_executor_job(
        _write_workspace_state, {"bootstrap_active": False}
    )
    return True


async def async_refresh_workspace_store(hass: HomeAssistant) -> None:

    snapshot = await hass.async_add_executor_job(_load_workspace_snapshot)
    _set_snapshot(snapshot)
    if await async_finalize_bootstrap_if_ready(hass):
        snapshot = await hass.async_add_executor_job(_load_workspace_snapshot)
        _set_snapshot(snapshot)


def list_workspace_docs() -> list[dict[str, str | bool]]:

    snapshot = _ensure_workspace_store_fresh()
    docs = {
        "AGENTS": snapshot.agents,
        "BOOTSTRAP": snapshot.bootstrap,
        "HEARTBEAT": snapshot.heartbeat,
        "IDENTITY": snapshot.identity,
        "MEMORY": snapshot.memory,
        "SOUL": snapshot.soul,
        "TOOLS": snapshot.tools,
        "USER": snapshot.user,
    }
    listed = [
        {
            "name": name,
            "file": f"{name}.md",
            "chars": str(len(content)),
            "active": bool(snapshot.bootstrap_active) if name == "BOOTSTRAP" else True,
        }
        for name, content in docs.items()
    ]
    listed.append(
        {
            "name": "TODAY_MEMORY",
            "file": f"memory/{_today_memory_name()}",
            "chars": str(len(snapshot.daily_memory)),
            "active": True,
        }
    )
    return listed


def get_workspace_doc(name: str) -> dict[str, str | bool]:

    normalized = _normalize_doc_name(name)
    snapshot = _ensure_workspace_store_fresh()
    docs = {
        "AGENTS": snapshot.agents,
        "BOOTSTRAP": snapshot.bootstrap,
        "HEARTBEAT": snapshot.heartbeat,
        "IDENTITY": snapshot.identity,
        "MEMORY": snapshot.memory,
        "SOUL": snapshot.soul,
        "TOOLS": snapshot.tools,
        "USER": snapshot.user,
    }
    return {
        "name": normalized,
        "markdown": docs[normalized],
        "active": bool(snapshot.bootstrap_active) if normalized == "BOOTSTRAP" else True,
    }


def get_today_memory_doc() -> dict[str, str | bool]:

    snapshot = _ensure_workspace_store_fresh()
    return {
        "name": "TODAY_MEMORY",
        "markdown": snapshot.daily_memory,
        "file": f"memory/{_today_memory_name()}",
        "active": True,
    }


async def async_save_workspace_doc(
    hass: HomeAssistant,
    name: str,
    markdown: str,
) -> Path:

    path = _doc_path(name)
    saved_path = await hass.async_add_executor_job(_write_doc, path, markdown)
    await async_refresh_workspace_store(hass)
    try:
        from .graph_service import async_reindex_doc  # noqa: PLC0415

        await async_reindex_doc(hass, _normalize_doc_name(name), markdown)
    except Exception:  # noqa: BLE001 - never block save on indexer
        LOGGER.exception("Graph reindex after save of %s failed", name)
    return saved_path


async def async_save_today_memory_doc(hass: HomeAssistant, markdown: str) -> Path:

    path = await hass.async_add_executor_job(_write_daily_memory_doc, markdown)
    await async_refresh_workspace_store(hass)
    return path


def _is_identity_incomplete(content: str) -> bool:
    if not content:
        return True
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in {
            "- **Name:**",
            "- **Creature:**",
            "- **Vibe:**",
            "- **Emoji:**",
        }:
            return True
    return any(
        marker in content
        for marker in (
            "_(pick something you like)_",
            "_(AI? robot? familiar? ghost in the machine? something weirder?)_",
        )
    )


def _is_user_incomplete(content: str) -> bool:
    if not content:
        return True
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in {
            "- **Name:**",
            "- **What to call them:**",
            "- **Timezone:**",
        }:
            return True
    return "_(optional)_" in content and "- **Pronouns:**" in content


def _is_tools_template_only(content: str) -> bool:
    if not content:
        return True
    template_markers = [
        "# TOOLS.md - Local Notes",
        "## What Goes Here",
        "## Examples",
        "## Why Separate?",
    ]
    if all(marker in content for marker in template_markers):
        return True
    if "<camera-slug>" in content or "<speaker-slug>" in content or "<notify-target>" in content:
        return True
    return False


def _tokenize_memory_query(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _MEMORY_TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("_+-")
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _build_memory_prompt_block(memory_markdown: str, user_text: str) -> str:
    if not memory_markdown.strip():
        return ""

    graph_lines: list[str] = []
    try:
        from .graph_service import recall_memory_lines_sync  # noqa: PLC0415

        graph_lines = recall_memory_lines_sync(user_text)
    except Exception:  # noqa: BLE001
        LOGGER.debug("graph recall unavailable, using keyword fallback", exc_info=True)

    if graph_lines:
        joined = "\n".join(graph_lines[:_MAX_MEMORY_LINES])
        if len(joined) > _MAX_MEMORY_CHARS:
            joined = joined[:_MAX_MEMORY_CHARS].rstrip()
        return joined

    lines = memory_markdown.splitlines()
    bullet_lines = [line.strip() for line in lines if line.strip().startswith("- ")]
    if not bullet_lines:
        return memory_markdown[:_MAX_MEMORY_CHARS].rstrip()

    query_tokens = _tokenize_memory_query(user_text)
    selected: list[str] = []
    total_chars = 0
    for line in bullet_lines:
        lowered = line.lower()
        is_preference_line = any(marker in lowered for marker in _MEMORY_ALWAYS_INCLUDE_MARKERS)
        is_query_relevant = bool(query_tokens) and any(token in lowered for token in query_tokens)
        if not is_preference_line and not is_query_relevant:
            continue
        if line in selected:
            continue
        if len(selected) >= _MAX_MEMORY_LINES:
            break
        if total_chars + len(line) > _MAX_MEMORY_CHARS:
            break
        selected.append(line)
        total_chars += len(line)

    if not selected:
        for line in bullet_lines[: min(len(bullet_lines), _MAX_MEMORY_LINES)]:
            if total_chars + len(line) > _MAX_MEMORY_CHARS:
                break
            selected.append(line)
            total_chars += len(line)

    return "\n".join(selected)


def _build_profile_prompt_block(content: str, *, title: str) -> str:

    if not content.strip():
        return ""

    selected: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("- **") or line.endswith((":**", ":")):
            continue
        if re.fullmatch(r"- \*\*.+\*\*:\s*", line):
            continue
        if "_(" in line:
            continue
        selected.append(line)

    if not selected:
        return ""

    return "\n".join(selected[:6])


def _should_include_heartbeat(snapshot: WorkspaceSnapshot, user_text: str) -> bool:
    if not snapshot.heartbeat.strip():
        return False
    lowered = user_text.lower().strip()
    return any(keyword in lowered for keyword in _HEARTBEAT_KEYWORDS)


def _workspace_doc_map(snapshot: WorkspaceSnapshot) -> dict[str, str]:
    return {
        "AGENTS": snapshot.agents,
        "BOOTSTRAP": snapshot.bootstrap,
        "HEARTBEAT": snapshot.heartbeat,
        "IDENTITY": snapshot.identity,
        "MEMORY": snapshot.memory,
        "SOUL": snapshot.soul,
        "TOOLS": snapshot.tools,
        "USER": snapshot.user,
    }


def _build_workspace_startup_docs(
    *, user_text: str = ""
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    snapshot = _ensure_workspace_store_fresh()
    doc_map = _workspace_doc_map(snapshot)
    ordered_names = snapshot.startup_read_order or (
        "SOUL",
        "IDENTITY",
        "USER",
        "MEMORY",
        "HEARTBEAT",
        "TOOLS",
    )

    loaded_docs: list[tuple[str, str]] = []
    skipped_docs: list[tuple[str, str]] = []
    emitted_names: set[str] = set()

    agents_content = snapshot.agents.strip()
    if agents_content:
        loaded_docs.append(("AGENTS", agents_content))
        emitted_names.add("AGENTS")
    else:
        skipped_docs.append(("AGENTS", "empty"))

    bootstrap_content = snapshot.bootstrap.strip()
    if snapshot.bootstrap_active and bootstrap_content:
        loaded_docs.append(("BOOTSTRAP", bootstrap_content))
        emitted_names.add("BOOTSTRAP")
    elif bootstrap_content:
        skipped_docs.append(("BOOTSTRAP", "bootstrap_complete"))
    else:
        skipped_docs.append(("BOOTSTRAP", "empty"))

    for name in ordered_names:
        if name in emitted_names:
            skipped_docs.append((name, "duplicate_in_order"))
            continue

        content = doc_map.get(name, "").strip()
        if not content:
            skipped_docs.append((name, "empty"))
            continue

        if name == "IDENTITY" and _is_identity_incomplete(content):
            skipped_docs.append((name, "incomplete"))
            continue
        if name == "USER" and _is_user_incomplete(content):
            skipped_docs.append((name, "incomplete"))
            continue
        if name == "TOOLS" and _is_tools_template_only(content):
            skipped_docs.append((name, "template_only"))
            continue
        if name == "MEMORY":
            content = _build_memory_prompt_block(content, user_text)
            if not content:
                skipped_docs.append((name, "empty_after_filter"))
                continue
        if name == "HEARTBEAT" and not _should_include_heartbeat(snapshot, user_text):
            skipped_docs.append((name, "not_relevant_this_turn"))
            continue

        loaded_docs.append((name, content))
        emitted_names.add(name)

    daily_memory = snapshot.daily_memory.strip()
    if daily_memory:
        loaded_docs.append((f"memory/{_today_memory_name()}", daily_memory))

    return loaded_docs, skipped_docs


def get_workspace_startup_doc_names(*, user_text: str = "") -> tuple[str, ...]:
    loaded_docs, _ = _build_workspace_startup_docs(user_text=user_text)
    return tuple(name for name, _ in loaded_docs)


_SKIP_REASON_GUIDANCE = {
    "empty": "File is empty. When the user mentions a relevant fact, call SetWorkspaceDoc to record it.",
    "incomplete": "Template fields are still placeholders. When you confidently learn a value during the conversation, propose it to the user and persist via SetWorkspaceDoc.",
    "template_only": "Only the boilerplate template is present. Replace it with real environment notes the user shares (entity ids, notify targets, credentials when explicitly provided).",
    "not_relevant_this_turn": "Not relevant to this turn; full content is retrievable via GetWorkspaceDoc when the user asks about follow-up tasks.",
    "bootstrap_complete": "Bootstrap already finished; reuse only if the user asks to restart onboarding.",
    "empty_after_filter": "No memory bullet matched current request; full memory is still retrievable via GetWorkspaceDoc.",
    "duplicate_in_order": "Already emitted above; safe to ignore.",
}


def build_workspace_startup_bundle(*, user_text: str = "") -> str:

    loaded_docs, skipped_docs = _build_workspace_startup_docs(user_text=user_text)
    sections: list[str] = []

    sections.append("## Workspace")
    sections.append(_workspace_governance_block())
    for name, content in loaded_docs:
        header = f"### {name}.md" if not name.startswith("memory/") else f"### {name}"
        sections.append(f"{header}\n{content}")
    if skipped_docs:
        status_lines = [
            "### Workspace Document Status",
            "Not inlined this turn. Use GetWorkspaceDoc to read, SetWorkspaceDoc to write.",
        ]
        for name, reason in skipped_docs:
            guidance = _SKIP_REASON_GUIDANCE.get(reason, "")
            line = f"- **{name}.md** [{reason}]"
            if guidance:
                line += f" {guidance}"
            status_lines.append(line)
        sections.append("\n".join(status_lines))

    LOGGER.debug(
        "Workspace startup loaded docs=%s skipped=%s",
        [name for name, _ in loaded_docs],
        skipped_docs,
    )

    return "\n\n".join(section for section in sections if section.strip())


def build_workspace_prompt_sections(
    *,
    mode: str = "conversation",
    user_text: str = "",
    exclude_doc_names: set[str] | None = None,
) -> tuple[str, ...]:

    snapshot = _ensure_workspace_store_fresh()
    sections: list[str] = []
    excluded = exclude_doc_names or set()

    if mode == "heartbeat" and snapshot.heartbeat:
        sections.append(f"## Heartbeat Rules\n{snapshot.heartbeat}")
        return tuple(sections)

    identity_incomplete = _is_identity_incomplete(snapshot.identity)
    user_incomplete = _is_user_incomplete(snapshot.user)

    if not identity_incomplete and "IDENTITY" not in excluded:
        identity_block = _build_profile_prompt_block(snapshot.identity, title="IDENTITY.md")
        if identity_block:
            sections.append(f"## Identity File\n{identity_block}")

    if not user_incomplete and "USER" not in excluded:
        user_block = _build_profile_prompt_block(snapshot.user, title="USER.md")
        if user_block:
            sections.append(f"## User File\n{user_block}")

    memory_block = _build_memory_prompt_block(snapshot.memory, user_text)
    if memory_block and "MEMORY" not in excluded:
        sections.append(f"## Memory File\n{memory_block}")

    if _should_include_heartbeat(snapshot, user_text) and "HEARTBEAT" not in excluded:
        sections.append(f"## Heartbeat Rules\n{snapshot.heartbeat}")

    return tuple(section for section in sections if section.strip())


def build_workspace_prompt_block(*, mode: str = "conversation", user_text: str = "") -> str:

    return "\n\n".join(
        build_workspace_prompt_sections(mode=mode, user_text=user_text)
    )
