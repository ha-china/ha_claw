

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import partial
import logging
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir, sync_legacy_skill_sources
from .route_hints import build_route_envelope, build_route_hint

LOGGER = logging.getLogger(__name__)

_INTERNAL_SKILL_SLUGS: frozenset[str] = frozenset({
    "homeassistant_runtime_guide",
})

try:
    import yaml
except ImportError:
    yaml = None


def _data_dir() -> Path:
    return get_data_dir()


def _master_prompt_path() -> Path:
    return _data_dir() / "master_prompt.md"


def _skills_dir() -> Path:
    return _data_dir() / "skills"


def _prompts_dir() -> Path:
    return _data_dir() / "prompts"
MAX_SKILL_PROMPT_CHARS = 6000
MAX_RELEVANT_SKILL_MATCHES = 3
MAX_SKILL_CATALOG_ITEMS = 8
_HOMEASSISTANT_SKILL_MARKERS = ("homeassistant", "home_assistant")
_TOKEN_RE = re.compile(r"[a-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,}", flags=re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", flags=re.DOTALL)
_DEFAULT_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "扫地机器人": ("vacuum", "robot", "cleaning"),
    "扫地": ("vacuum", "cleaning"),
    "打扫": ("cleaning", "clean"),
    "清洁": ("cleaning", "clean"),
    "客厅": ("living", "room"),
    "卧室": ("bedroom",),
    "灯": ("light",),
    "开灯": ("turn_on", "light"),
    "关灯": ("turn_off", "light"),
    "股票": ("stock", "market"),
    "行情": ("market",),
}
def _concept_aliases_path() -> Path:
    return _data_dir() / "concept_aliases.yaml"


def _looks_like_html_document(content: str) -> bool:

    lowered = content.lstrip().lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")


def _is_prompt_eligible_skill(skill: SkillDocument) -> bool:

    return not _looks_like_html_document(skill.content)


def _load_concept_aliases() -> dict[str, tuple[str, ...]]:

    merged = dict(_DEFAULT_CONCEPT_ALIASES)
    aliases_path = _concept_aliases_path()
    if yaml is None or not aliases_path.exists():
        return merged
    try:
        raw = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return merged
        for key, values in raw.items():
            if isinstance(values, (list, tuple)):
                merged[str(key)] = tuple(str(v) for v in values)
        return merged
    except Exception:
        return merged


def reload_concept_aliases() -> None:

    global _CONCEPT_ALIASES
    _CONCEPT_ALIASES = _load_concept_aliases()


async def async_reload_concept_aliases(hass: HomeAssistant) -> None:

    global _CONCEPT_ALIASES
    _CONCEPT_ALIASES = await hass.async_add_executor_job(_load_concept_aliases)


_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = dict(_DEFAULT_CONCEPT_ALIASES)


@dataclass(slots=True, frozen=True)
class SkillDocument:


    slug: str
    file_name: str
    title: str
    content: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    category: str = ""
    tags: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    requires_toolsets: tuple[str, ...] = ()
    fallback_for_toolsets: tuple[str, ...] = ()
    requires_tools: tuple[str, ...] = ()
    fallback_for_tools: tuple[str, ...] = ()
    required_environment_variables: tuple[str, ...] = ()
    config_keys: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class PromptDocument:


    slug: str
    file_name: str
    content: str


@dataclass(slots=True, frozen=True)
class PromptStoreSnapshot:


    master_prompt: str = ""
    skills: tuple[SkillDocument, ...] = ()
    runtime_prompt_docs: tuple[PromptDocument, ...] = ()
    installed_skill_metadata: tuple[dict[str, Any], ...] = ()
    signature: tuple[str, ...] = ()


_PROMPT_STORE: dict[str, PromptStoreSnapshot] = {"snapshot": PromptStoreSnapshot()}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_").lower()
    return slug or "skill"


def _tokenize_for_matching(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0).strip("_+-")
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _expand_query_tokens(query: str, tokens: tuple[str, ...]) -> tuple[str, ...]:
    seen = set(tokens)
    expanded = list(tokens)
    for phrase, aliases in _CONCEPT_ALIASES.items():
        if phrase not in query:
            continue
        for alias in aliases:
            if alias in seen:
                continue
            seen.add(alias)
            expanded.append(alias)
    return tuple(expanded)


def _parse_frontmatter(content: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(content)
    if not match or yaml is None:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_frontmatter_block(content: str) -> str:
    """Remove the leading YAML frontmatter from a skill/prompt document body.

    The frontmatter is consumed once via _parse_frontmatter to populate
    structured SkillDocument fields. Leaving it inside the body would ship
    the same metadata to the LLM as raw text on every turn, wasting tokens.
    """
    return _FRONTMATTER_RE.sub("", content, count=1).lstrip()


def ensure_skill_store() -> None:

    _data_dir().mkdir(parents=True, exist_ok=True)
    _skills_dir().mkdir(parents=True, exist_ok=True)
    _prompts_dir().mkdir(parents=True, exist_ok=True)
    _migrate_flat_skills_to_folders()


def _migrate_flat_skills_to_folders() -> None:
    """Normalize installed skills to the ``<slug>/SKILL.md`` pack layout.

    Rule:
    - Flat ``<slug>.md`` files without a sibling ``<slug>/`` folder are moved
      to ``<slug>/SKILL.md`` so every skill lives in its own folder and can
      carry auxiliary files (memory.md, heartbeat.md, ...).
    - If a ``<slug>/`` folder already exists, the flat file is left alone
      (no merge, no overwrite). Resolving that conflict is up to the user.
    """

    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return

    for internal_slug in _INTERNAL_SKILL_SLUGS:
        folder = skills_dir / internal_slug
        flat = skills_dir / f"{internal_slug}.md"
        if folder.is_dir() and not flat.exists():
            pack_md = _folder_skill_file(folder)
            if pack_md is not None:
                try:
                    pack_md.rename(flat)
                    if not any(folder.iterdir()):
                        folder.rmdir()
                    LOGGER.info(
                        "Restored internal skill %s to root (non-migratable)",
                        internal_slug,
                    )
                except OSError as err:
                    LOGGER.warning(
                        "Internal skill restore failed for %s: %s",
                        internal_slug,
                        err,
                    )

    for path in list(skills_dir.iterdir()):
        if not (path.is_file() and path.suffix.lower() == ".md"):
            continue
        slug = path.stem
        if slug in _INTERNAL_SKILL_SLUGS:
            continue
        folder = skills_dir / slug
        if folder.exists():
            continue
        try:
            folder.mkdir(parents=True, exist_ok=False)
            path.rename(folder / "SKILL.md")
            LOGGER.info("Migrated flat skill %s.md -> %s/SKILL.md", slug, slug)
        except OSError as err:
            LOGGER.warning("Skill migration failed for %s: %s", slug, err)


def _folder_skill_file(folder: Path) -> Path | None:
    """Return the canonical Markdown file inside a skill folder, if any.

    Supports Anthropic-style skill packs laid out as ``<slug>/SKILL.md``
    (or ``README.md`` as a fallback).
    """

    for candidate in ("SKILL.md", "skill.md", "README.md", "readme.md"):
        candidate_path = folder / candidate
        if candidate_path.is_file():
            return candidate_path
    return None


def _iter_skill_entries() -> list[tuple[str, Path]]:
    """Enumerate installed skills across both layouts.

    Yields ``(slug, path)`` for:
    - Legacy single-file skills: ``skills/<slug>.md``
    - Pack-style skills: ``skills/<slug>/SKILL.md``
    """

    entries: list[tuple[str, Path]] = []
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        return entries

    for path in sorted(skills_dir.iterdir()):
        if path.is_file() and path.suffix.lower() == ".md":
            entries.append((path.stem, path))
            continue
        if path.is_dir():
            md_path = _folder_skill_file(path)
            if md_path is not None:
                entries.append((path.name, md_path))
    return entries


def _resolve_skill_path(name: str, *, for_write: bool = False) -> Path:
    """Return the on-disk path representing skill ``name``.

    Preference order:
    1. Existing pack layout ``<slug>/SKILL.md``
    2. Existing flat file ``<slug>.md``
    3. For writes: default to the flat file path (legacy behavior).
    """

    slug = _slugify(name)
    folder = _skills_dir() / slug
    if folder.is_dir():
        pack_md = _folder_skill_file(folder)
        if pack_md is not None:
            return pack_md
        if for_write:
            return folder / "SKILL.md"
    flat = _skills_dir() / f"{slug}.md"
    return flat


def _title_from_content(default_title: str, content: str) -> str:

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or default_title
    return default_title


def _body_from_markdown(content: str) -> str:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    return content[match.end() :]


def _extract_description(default_title: str, content: str, frontmatter: dict[str, Any]) -> str:
    description = str(frontmatter.get("description", "")).strip()
    if description:
        return description

    body = _body_from_markdown(content)
    if _looks_like_html_document(body):
        return default_title

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("<") or stripped.startswith("```"):
            continue
        return stripped[:240]

    return default_title


def _iter_frontmatter_values(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw if str(item).strip()]
    return [str(raw)]


def _extract_keywords(
    *,
    slug: str,
    file_name: str,
    title: str,
    description: str,
    content: str,
    frontmatter: dict[str, Any],
) -> tuple[str, ...]:
    values: list[str] = [slug, file_name, title, description]
    for key in ("keywords", "tags", "triggers"):
        values.extend(_iter_frontmatter_values(frontmatter.get(key)))

    lines = [line.strip() for line in _body_from_markdown(content).splitlines() if line.strip()]
    values.extend(lines[:4])

    seen: set[str] = set()
    keywords: list[str] = []
    for value in values:
        for token in _tokenize_for_matching(value):
            if token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return tuple(keywords)


def _normalize_string_tuple(raw: Any) -> tuple[str, ...]:
    values = _iter_frontmatter_values(raw)
    return tuple(
        dict.fromkeys(value.strip() for value in values if value and value.strip())
    )


def _frontmatter_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    claw = metadata.get("claw")
    if isinstance(claw, dict):
        return claw
    hermes = metadata.get("hermes")
    if isinstance(hermes, dict):
        return hermes
    return {}


def _extract_category(frontmatter: dict[str, Any]) -> str:
    metadata = _frontmatter_metadata(frontmatter)
    category = str(metadata.get("category", "")).strip()
    if category:
        return category
    return str(frontmatter.get("category", "")).strip()


def _extract_tags(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    metadata = _frontmatter_metadata(frontmatter)
    tags = _normalize_string_tuple(metadata.get("tags"))
    if tags:
        return tags
    return _normalize_string_tuple(frontmatter.get("tags"))


def _extract_platforms(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    return _normalize_string_tuple(frontmatter.get("platforms"))


def _extract_tool_visibility(
    frontmatter: dict[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    metadata = _frontmatter_metadata(frontmatter)
    requires_toolsets = _normalize_string_tuple(metadata.get("requires_toolsets"))
    fallback_for_toolsets = _normalize_string_tuple(metadata.get("fallback_for_toolsets"))
    requires_tools = _normalize_string_tuple(metadata.get("requires_tools"))
    fallback_for_tools = _normalize_string_tuple(metadata.get("fallback_for_tools"))
    return (
        requires_toolsets,
        fallback_for_toolsets,
        requires_tools,
        fallback_for_tools,
    )


def _extract_required_environment_variables(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    raw = frontmatter.get("required_environment_variables")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    envs: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            envs.append(entry.strip())
            continue
        if isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("env_var") or "").strip()
            if name:
                envs.append(name)
    return tuple(dict.fromkeys(env for env in envs if env))


def _extract_config_keys(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    metadata = _frontmatter_metadata(frontmatter)
    raw = metadata.get("config")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    keys: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        if key:
            keys.append(key)
    return tuple(dict.fromkeys(keys))


def infer_skill_name(default_name: str, content: str) -> str:

    frontmatter = _parse_frontmatter(content)
    value = str(frontmatter.get("name", "")).strip()
    if value:
        return value

    title = _title_from_content(default_name, content).strip()
    if title:
        return title

    return default_name


def infer_skill_name_from_url(url: str) -> str:

    path_parts = [part for part in urlparse(url).path.rstrip("/").split("/") if part]
    last_segment = path_parts[-1] if path_parts else ""
    if last_segment.lower() in {"readme.md", "skill.md"}:
        if "blob" in path_parts:
            blob_index = path_parts.index("blob")
            if blob_index >= 1:
                return path_parts[blob_index - 1]
        if len(path_parts) >= 2:
            return path_parts[-2]
    return last_segment or "skill"


def _skill_document_from_path(
    path: Path, content: str, *, slug_override: str | None = None
) -> SkillDocument:
    frontmatter = _parse_frontmatter(content)
    body = _strip_frontmatter_block(content)
    slug = slug_override or path.stem
    title = _title_from_content(slug, body)
    description = _extract_description(title, body, frontmatter)
    (
        requires_toolsets,
        fallback_for_toolsets,
        requires_tools,
        fallback_for_tools,
    ) = _extract_tool_visibility(frontmatter)
    return SkillDocument(
        slug=slug,
        file_name=path.name,
        title=title,
        content=body,
        description=description,
        keywords=_extract_keywords(
            slug=slug,
            file_name=path.name,
            title=title,
            description=description,
            content=body,
            frontmatter=frontmatter,
        ),
        category=_extract_category(frontmatter),
        tags=_extract_tags(frontmatter),
        platforms=_extract_platforms(frontmatter),
        requires_toolsets=requires_toolsets,
        fallback_for_toolsets=fallback_for_toolsets,
        requires_tools=requires_tools,
        fallback_for_tools=fallback_for_tools,
        required_environment_variables=_extract_required_environment_variables(frontmatter),
        config_keys=_extract_config_keys(frontmatter),
    )


def _prompt_document_from_path(path: Path, content: str) -> PromptDocument:

    return PromptDocument(
        slug=path.stem.lower(),
        file_name=path.name,
        content=_strip_frontmatter_block(content),
    )


def _prompt_store_signature() -> tuple[str, ...]:

    ensure_skill_store()
    data_dir = _data_dir()
    paths = [
        _master_prompt_path(),
        *(path for _, path in _iter_skill_entries()),
        *sorted(_prompts_dir().glob("*.md")),
    ]
    signature: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        stat = path.stat()
        signature.append(
            f"{path.relative_to(data_dir).as_posix()}:{stat.st_mtime_ns}:{stat.st_size}"
        )
    return tuple(signature)


_META_MARKER_RE = re.compile(
    r"\A(?:\s*<!--\s*[A-Za-z][\w-]*\s*:[^>]*-->\s*)+",
)


def _strip_meta_markers(content: str) -> str:
    return _META_MARKER_RE.sub("", content, count=1).lstrip()


def _read_prompt_store_from_disk() -> PromptStoreSnapshot:

    ensure_skill_store()

    master_prompt = ""
    mpp = _master_prompt_path()
    if mpp.exists():
        master_prompt = _strip_meta_markers(
            mpp.read_text(encoding="utf-8")
        ).strip()

    skills: list[SkillDocument] = []
    for slug, path in _iter_skill_entries():
        content = _strip_meta_markers(
            path.read_text(encoding="utf-8")
        ).strip()
        if not content:
            continue
        skills.append(
            _skill_document_from_path(path, content, slug_override=slug)
        )

    runtime_prompt_docs: list[PromptDocument] = []
    for path in sorted(_prompts_dir().glob("*.md")):
        content = _strip_meta_markers(
            path.read_text(encoding="utf-8")
        ).strip()
        if not content:
            continue
        runtime_prompt_docs.append(_prompt_document_from_path(path, content))

    skill_tuple = tuple(skills)
    return PromptStoreSnapshot(
        master_prompt=master_prompt,
        skills=skill_tuple,
        runtime_prompt_docs=tuple(runtime_prompt_docs),
        installed_skill_metadata=_build_installed_skill_catalog(skill_tuple),
        signature=_prompt_store_signature(),
    )




def _build_installed_skill_catalog(
    skills: tuple[SkillDocument, ...],
) -> tuple[dict[str, str | list[str] | dict[str, object]], ...]:

    return tuple(_build_installed_skill_metadata(skill) for skill in skills)

def _set_prompt_store(snapshot: PromptStoreSnapshot) -> None:

    _PROMPT_STORE["snapshot"] = snapshot


def _ensure_prompt_store_fresh() -> PromptStoreSnapshot:

    return _PROMPT_STORE["snapshot"]


def _write_master_prompt(markdown: str) -> Path:

    ensure_skill_store()
    mpp = _master_prompt_path()
    write_utf8_file(str(mpp), markdown.strip() + "\n")
    return mpp


def _write_skill(name: str, markdown: str, *, overwrite: bool) -> Path:

    ensure_skill_store()
    if not name.strip():
        raise ValueError("Skill name is required")
    if not markdown.strip():
        raise ValueError("Skill markdown is empty")
    if _looks_like_html_document(markdown):
        raise ValueError(
            "Skill content looks like a full HTML document. Installing raw "
            "HTML bloats prompts and pollutes retrieval. Convert to Markdown "
            "first (strip <!DOCTYPE>, <html>, <head>, <script>, <style>)."
        )

    skill_path = _resolve_skill_path(name, for_write=True)
    if skill_path.exists() and not overwrite:
        raise FileExistsError(f"Skill already exists: {skill_path.name}")

    skill_path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_file(str(skill_path), markdown.strip() + "\n")
    return skill_path


async def async_setup_prompt_store(hass: HomeAssistant) -> None:

    await async_refresh_prompt_store(hass)


async def async_refresh_prompt_store(hass: HomeAssistant) -> None:

    await hass.async_add_executor_job(
        sync_legacy_skill_sources,
        Path(hass.config.config_dir),
        get_data_dir(),
    )
    new_signature = await hass.async_add_executor_job(_prompt_store_signature)
    current = _PROMPT_STORE.get("snapshot")
    if (
        current is not None
        and current.signature
        and current.signature == new_signature
    ):
        return
    snapshot = await hass.async_add_executor_job(_read_prompt_store_from_disk)
    _set_prompt_store(snapshot)
    await async_reload_concept_aliases(hass)


async def async_save_master_prompt(hass: HomeAssistant, markdown: str) -> Path:

    path = await hass.async_add_executor_job(_write_master_prompt, markdown)
    await async_refresh_prompt_store(hass)
    return path


def _read_skill_raw(name: str) -> str | None:

    skill_path = _resolve_skill_path(name)
    if not skill_path.exists():
        return None
    return skill_path.read_text(encoding="utf-8")


async def async_read_skill_markdown(hass: HomeAssistant, name: str) -> str:
    """Return the raw on-disk markdown of an installed skill (or '')."""

    raw = await hass.async_add_executor_job(partial(_read_skill_raw, name))
    return raw or ""


def _delete_skill_sync(name: str) -> tuple[Path, str | None]:

    if not name.strip():
        raise ValueError("Skill name is required")
    skill_path = _resolve_skill_path(name)
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_path.name}")
    previous = skill_path.read_text(encoding="utf-8")

    slug = _slugify(name)
    folder = _skills_dir() / slug
    if skill_path.parent == folder and folder.is_dir():
        import shutil

        shutil.rmtree(folder)
        return skill_path, previous

    skill_path.unlink()
    return skill_path, previous


async def async_install_skill(
    hass: HomeAssistant,
    name: str,
    markdown: str,
    *,
    overwrite: bool = False,
    actor: str = "ai",
    reason: str = "",
) -> Path:

    from .self_edit import async_record_change

    previous = await hass.async_add_executor_job(partial(_read_skill_raw, name))
    path = await hass.async_add_executor_job(
        partial(_write_skill, name, markdown, overwrite=overwrite)
    )
    await async_refresh_prompt_store(hass)
    is_update = previous is not None
    await async_record_change(
        hass,
        target_type="skill",
        target_id=path.stem,
        action="update" if is_update else "create",
        before=previous,
        after=markdown,
        actor=actor,
        reason=reason,
    )
    try:
        from . import skill_usage
        slug = _slugify(name)
        if is_update:
            await hass.async_add_executor_job(skill_usage.bump_patch, slug)
        else:
            await hass.async_add_executor_job(skill_usage.bump_use, slug)
    except Exception:
        pass
    return path


async def async_delete_skill(
    hass: HomeAssistant,
    name: str,
    *,
    actor: str = "ai",
    reason: str = "",
) -> Path:

    from .self_edit import async_record_change

    path, previous = await hass.async_add_executor_job(
        partial(_delete_skill_sync, name)
    )
    await async_refresh_prompt_store(hass)
    await async_record_change(
        hass,
        target_type="skill",
        target_id=path.stem,
        action="delete",
        before=previous,
        after=None,
        actor=actor,
        reason=reason,
    )
    return path


def load_master_prompt() -> str:

    return _ensure_prompt_store_fresh().master_prompt


def load_runtime_prompt_doc(name: str) -> str:

    lookup = Path(name.strip()).stem.lower()
    if not lookup:
        return ""

    snapshot = _ensure_prompt_store_fresh()
    for document in snapshot.runtime_prompt_docs:
        if document.slug == lookup:
            return document.content
    return ""


class _SafePromptFormatDict(dict[str, str]):


    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_runtime_prompt_doc(name: str, **kwargs: str) -> str:

    template = load_runtime_prompt_doc(name)
    if not template:
        return ""
    return template.format_map(_SafePromptFormatDict(kwargs)).strip()


def _build_installed_skill_use_for(skill: SkillDocument) -> list[str]:

    return list(
        dict.fromkeys((list(skill.keywords[:8]) + ([skill.description] if skill.description else []))[:4])
    )


def _build_installed_skill_avoid_for() -> list[str]:

    return ["blind execution without reading the installed markdown first"]


def _build_installed_skill_route_to(skill: SkillDocument) -> str:

    return f"Read {skill.slug} with GetInstalledSkill before execution"


def _build_installed_skill_match_fields(
    skill: SkillDocument,
    keywords: list[str],
) -> list[str]:

    fields = [skill.slug.lower(), skill.file_name.lower(), skill.title.lower()]
    if skill.description:
        fields.append(skill.description.lower())
    if skill.category:
        fields.append(skill.category.lower())
    fields.extend(tag.lower() for tag in skill.tags)
    fields.extend(platform.lower() for platform in skill.platforms)
    fields.extend(toolset.lower() for toolset in skill.requires_toolsets)
    fields.extend(toolset.lower() for toolset in skill.fallback_for_toolsets)
    fields.extend(tool.lower() for tool in skill.requires_tools)
    fields.extend(tool.lower() for tool in skill.fallback_for_tools)
    fields.extend(env.lower() for env in skill.required_environment_variables)
    fields.extend(key.lower() for key in skill.config_keys)
    fields.extend(keyword.lower() for keyword in keywords)
    return list(dict.fromkeys(field for field in fields if field))


def _build_installed_skill_metadata(skill: SkillDocument) -> dict[str, str | list[str] | dict[str, object]]:

    keywords = list(skill.keywords[:8])
    deduped_match_fields = _build_installed_skill_match_fields(skill, keywords)
    use_for = _build_installed_skill_use_for(skill)
    avoid_for = _build_installed_skill_avoid_for()
    route_to = _build_installed_skill_route_to(skill)
    return {
        "name": skill.title,
        "slug": skill.slug,
        "file": skill.file_name,
        "chars": str(len(skill.content)),
        "description": skill.description,
        "category": skill.category,
        "tags": list(skill.tags),
        "platforms": list(skill.platforms),
        "requires_toolsets": list(skill.requires_toolsets),
        "fallback_for_toolsets": list(skill.fallback_for_toolsets),
        "keywords": keywords,
        "requires_tools": list(skill.requires_tools),
        "fallback_for_tools": list(skill.fallback_for_tools),
        "required_environment_variables": list(skill.required_environment_variables),
        "config_keys": list(skill.config_keys),
        "use_for": use_for,
        "avoid_for": avoid_for,
        "route_to": route_to,
        "match_fields": deduped_match_fields,
        **build_route_envelope("installed_skill", "GetInstalledSkill", "get", args={"name": skill.slug}),
        "route_hint": build_route_hint(
            "installed_skill",
            "GetInstalledSkill",
            "get",
            args={"name": skill.slug},
            recommendation=f"Read {skill.slug} before using it.",
        ),
    }


def list_installed_skills() -> list[dict[str, str | list[str] | dict[str, object]]]:

    snapshot = _ensure_prompt_store_fresh()
    cached_metadata = snapshot.installed_skill_metadata or _build_installed_skill_catalog(
        snapshot.skills
    )
    return deepcopy(list(cached_metadata))




def _installed_skill_matches_keyword(
    item: dict[str, str | list[str] | dict[str, object]],
    keyword: str,
) -> bool:

    normalized_keyword = keyword.strip().lower()
    if not normalized_keyword:
        return True
    return (
        any(normalized_keyword in field for field in item.get("match_fields", []))
        or any(normalized_keyword in value.lower() for value in item.get("use_for", []))
        or any(normalized_keyword in value.lower() for value in item.get("avoid_for", []))
        or normalized_keyword in str(item.get("route_to", "")).lower()
        or normalized_keyword in str(item.get("route_kind", "")).lower()
        or normalized_keyword in str(item.get("route_hint", {})).lower()
        or normalized_keyword in str(item.get("next_action", {})).lower()
    )

def filter_installed_skills(keyword: str) -> list[dict[str, str | list[str] | dict[str, object]]]:

    normalized_keyword = keyword.strip().lower()
    skills = list_installed_skills()
    if not normalized_keyword:
        return skills
    return [item for item in skills if _installed_skill_matches_keyword(item, normalized_keyword)]


def skill_matches_visibility(
    skill: dict[str, Any],
    *,
    channel_type: str = "ha",
    tool_names: set[str] | None = None,
    toolsets: set[str] | None = None,
) -> bool:
    visible_platforms = {
        str(item).strip().lower()
        for item in skill.get("platforms", [])
        if str(item).strip()
    }
    if visible_platforms:
        normalized_channel = channel_type.strip().lower() or "ha"
        accepted = {"all", normalized_channel}
        if normalized_channel == "ha":
            accepted.add("homeassistant")
        else:
            accepted.add("im")
        if visible_platforms.isdisjoint(accepted):
            return False

    available_tools = {name.strip() for name in (tool_names or set()) if name.strip()}
    available_toolsets = {
        name.strip().lower() for name in (toolsets or set()) if name and name.strip()
    }
    required_toolsets = {
        str(item).strip().lower()
        for item in skill.get("requires_toolsets", [])
        if str(item).strip()
    }
    if required_toolsets and not required_toolsets.issubset(available_toolsets):
        return False

    fallback_for_toolsets = {
        str(item).strip().lower()
        for item in skill.get("fallback_for_toolsets", [])
        if str(item).strip()
    }
    if fallback_for_toolsets and fallback_for_toolsets.intersection(available_toolsets):
        return False

    required_tools = {
        str(item).strip()
        for item in skill.get("requires_tools", [])
        if str(item).strip()
    }
    if required_tools and not required_tools.issubset(available_tools):
        return False

    fallback_for_tools = {
        str(item).strip()
        for item in skill.get("fallback_for_tools", [])
        if str(item).strip()
    }
    if fallback_for_tools and fallback_for_tools.intersection(available_tools):
        return False

    return True


def filter_visible_installed_skills(
    *,
    keyword: str = "",
    channel_type: str = "ha",
    tool_names: set[str] | None = None,
    toolsets: set[str] | None = None,
) -> list[dict[str, str | list[str] | dict[str, object]]]:
    skills = filter_installed_skills(keyword)
    return [
        skill
        for skill in skills
        if skill_matches_visibility(
            skill,
            channel_type=channel_type,
            tool_names=tool_names,
            toolsets=toolsets,
        )
    ]


def get_missing_required_environment_variables(skill: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for env_name in skill.get("required_environment_variables", []):
        normalized = str(env_name).strip()
        if normalized and not os.environ.get(normalized):
            missing.append(normalized)
    return missing


def _is_homeassistant_priority_skill(skill: SkillDocument) -> bool:
    lookup_values = (
        skill.slug.lower(),
        skill.file_name.lower(),
        skill.title.lower(),
    )
    return any(
        marker in value for marker in _HOMEASSISTANT_SKILL_MARKERS for value in lookup_values
    )


def load_homeassistant_priority_skill_block() -> str:

    for skill in _ensure_prompt_store_fresh().skills:
        if not _is_prompt_eligible_skill(skill):
            continue
        if not _is_homeassistant_priority_skill(skill):
            continue
        title = skill.title or skill.slug
        description = skill.description or "Primary Home Assistant runtime guide."
        return (
            f"## Home Assistant Runtime Guide\n"
            f"Index only: {title} — {description}. "
            "Details: GetInstalledSkill(homeassistant_runtime_guide) or HomeAssistantGuide."
        )
    return ""


def _score_skill_match(skill: SkillDocument, query: str, query_tokens: tuple[str, ...]) -> int:
    if not query:
        return 0

    score = 0
    normalized_query = query.lower().strip()
    title = skill.title.lower()
    slug = skill.slug.lower()
    description = skill.description.lower()
    category = skill.category.lower()
    tags = " ".join(tag.lower() for tag in skill.tags)
    keywords = " ".join(skill.keywords)
    content = skill.content.lower()

    if normalized_query in title:
        score += 16
    if normalized_query in slug:
        score += 14
    if normalized_query in description:
        score += 12
    if category and normalized_query in category:
        score += 12
    if tags and normalized_query in tags:
        score += 11
    if normalized_query in keywords:
        score += 10
    if normalized_query in content:
        score += 6

    for token in query_tokens:
        if token in title or token in slug:
            score += 8
        if category and token in category:
            score += 6
        if tags and token in tags:
            score += 6
        if token in description:
            score += 5
        if token in keywords:
            score += 4
        if token in content:
            score += 2


    if score < 4:
        title_ratio = SequenceMatcher(None, normalized_query, title).ratio()
        if title_ratio >= 0.6:
            score += int(title_ratio * 12)
        desc_ratio = SequenceMatcher(None, normalized_query, description).ratio()
        if desc_ratio >= 0.5:
            score += int(desc_ratio * 8)
        if category:
            category_ratio = SequenceMatcher(None, normalized_query, category).ratio()
            if category_ratio >= 0.6:
                score += int(category_ratio * 8)
        for token in query_tokens:
            for tag in skill.tags:
                tag_ratio = SequenceMatcher(None, token, tag.lower()).ratio()
                if tag_ratio >= 0.75:
                    score += int(tag_ratio * 5)
            for kw in skill.keywords:
                kw_ratio = SequenceMatcher(None, token, kw.lower()).ratio()
                if kw_ratio >= 0.7:
                    score += int(kw_ratio * 4)

    if any(token in keywords for token in ("homeassistant", "automation", "dashboard")):
        score += 1

    return score


def match_installed_skills(
    query: str,
    *,
    limit: int = MAX_RELEVANT_SKILL_MATCHES,
    exclude_homeassistant_priority: bool = False,
) -> list[dict[str, str | int]]:

    normalized_query = query.strip().lower()
    if not normalized_query:
        return []

    query_tokens = _expand_query_tokens(
        normalized_query, _tokenize_for_matching(normalized_query)
    )
    scored: list[tuple[int, SkillDocument]] = []
    for skill in _ensure_prompt_store_fresh().skills:
        if not _is_prompt_eligible_skill(skill):
            continue
        if exclude_homeassistant_priority and _is_homeassistant_priority_skill(skill):
            continue
        score = _score_skill_match(skill, normalized_query, query_tokens)
        if score <= 0:
            continue
        scored.append((score, skill))

    scored.sort(key=lambda item: (-item[0], item[1].title.lower()))
    results = scored[: max(limit, 0)]
    if results:
        try:
            from . import skill_usage
            for _score, _skill in results:
                skill_usage.bump_use(_skill.slug)
        except Exception:
            pass
    return [
        {
            "name": skill.title,
            "slug": skill.slug,
            "file": skill.file_name,
            "description": skill.description,
            "score": score,
        }
        for score, skill in results
    ]


def load_relevant_skill_prompt_blocks(
    query: str,
    *,
    max_chars: int = MAX_SKILL_PROMPT_CHARS,
    limit: int = MAX_RELEVANT_SKILL_MATCHES,
    exclude_homeassistant_priority: bool = True,
) -> str:

    remaining = max_chars
    blocks: list[str] = []
    matches = match_installed_skills(
        query,
        limit=limit,
        exclude_homeassistant_priority=exclude_homeassistant_priority,
    )
    if not matches:
        return ""

    skills_by_slug = {skill.slug: skill for skill in _ensure_prompt_store_fresh().skills}
    for match in matches:
        skill = skills_by_slug.get(str(match["slug"]))
        if skill is None:
            continue
        block = f"### Skill: {skill.slug}\n{skill.content}"
        if len(block) > remaining:
            if not blocks and remaining > 200:
                blocks.append(block[:remaining].rstrip())
            break
        blocks.append(block)
        remaining -= len(block)

    return "\n\n".join(blocks)


def load_skill_catalog_prompt(
    *,
    max_items: int = MAX_SKILL_CATALOG_ITEMS,
    exclude_homeassistant_priority: bool = False,
) -> str:

    items: list[str] = []
    for skill in _ensure_prompt_store_fresh().skills:
        if not _is_prompt_eligible_skill(skill):
            continue
        if exclude_homeassistant_priority and _is_homeassistant_priority_skill(skill):
            continue
        description = skill.description or skill.title
        items.append(f"- {skill.slug}: {description}")
        if len(items) >= max_items:
            break
    return "\n".join(items)


def load_skill_prompt_blocks(
    *,
    max_chars: int = MAX_SKILL_PROMPT_CHARS,
    exclude_homeassistant_priority: bool = False,
) -> str:

    remaining = max_chars
    blocks: list[str] = []

    for skill in _ensure_prompt_store_fresh().skills:
        if not _is_prompt_eligible_skill(skill):
            continue
        if exclude_homeassistant_priority and _is_homeassistant_priority_skill(skill):
            continue
        block = f"### Skill: {skill.slug}\n{skill.content}"
        if len(block) > remaining:
            if not blocks and remaining > 200:
                blocks.append(block[:remaining].rstrip())
            break
        blocks.append(block)
        remaining -= len(block)

    return "\n\n".join(blocks)


def get_installed_skill(identifier: str) -> dict[str, str]:

    lookup = identifier.strip().lower()
    if not lookup:
        raise ValueError("Skill identifier is required")

    def _track_view(slug: str) -> None:
        try:
            from . import skill_usage
            skill_usage.bump_view(slug)
        except Exception:
            pass

    for skill in _ensure_prompt_store_fresh().skills:
        candidates = {
            skill.slug.lower(),
            skill.file_name.lower(),
            skill.title.lower(),
        }
        if lookup in candidates:
            _track_view(skill.slug)
            return {
                "name": skill.title,
                "slug": skill.slug,
                "file": skill.file_name,
                "markdown": skill.content,
                "description": skill.description,
                "category": skill.category,
                "tags": list(skill.tags),
                "platforms": list(skill.platforms),
                "requires_tools": list(skill.requires_tools),
                "fallback_for_tools": list(skill.fallback_for_tools),
                "required_environment_variables": list(skill.required_environment_variables),
                "config_keys": list(skill.config_keys),
            }

    for skill in _ensure_prompt_store_fresh().skills:
        haystacks = (skill.slug.lower(), skill.file_name.lower(), skill.title.lower())
        if any(lookup in hay for hay in haystacks):
            _track_view(skill.slug)
            return {
                "name": skill.title,
                "slug": skill.slug,
                "file": skill.file_name,
                "markdown": skill.content,
                "description": skill.description,
                "category": skill.category,
                "tags": list(skill.tags),
                "platforms": list(skill.platforms),
                "requires_tools": list(skill.requires_tools),
                "fallback_for_tools": list(skill.fallback_for_tools),
                "required_environment_variables": list(skill.required_environment_variables),
                "config_keys": list(skill.config_keys),
            }

    raise ValueError(f"Skill not found: {identifier}")
