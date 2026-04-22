

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file

from .data_path import get_data_dir
from .route_hints import build_route_envelope, build_route_hint

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


def ensure_skill_store() -> None:

    _data_dir().mkdir(parents=True, exist_ok=True)
    _skills_dir().mkdir(parents=True, exist_ok=True)
    _prompts_dir().mkdir(parents=True, exist_ok=True)


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
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
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


def _skill_document_from_path(path: Path, content: str) -> SkillDocument:
    frontmatter = _parse_frontmatter(content)
    title = _title_from_content(path.stem, content)
    description = _extract_description(title, content, frontmatter)
    return SkillDocument(
        slug=path.stem,
        file_name=path.name,
        title=title,
        content=content,
        description=description,
        keywords=_extract_keywords(
            slug=path.stem,
            file_name=path.name,
            title=title,
            description=description,
            content=content,
            frontmatter=frontmatter,
        ),
    )


def _prompt_document_from_path(path: Path, content: str) -> PromptDocument:

    return PromptDocument(
        slug=path.stem.lower(),
        file_name=path.name,
        content=content,
    )


def _prompt_store_signature() -> tuple[str, ...]:

    ensure_skill_store()
    data_dir = _data_dir()
    paths = [
        _master_prompt_path(),
        *sorted(_skills_dir().glob("*.md")),
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


def _read_prompt_store_from_disk() -> PromptStoreSnapshot:

    ensure_skill_store()

    master_prompt = ""
    mpp = _master_prompt_path()
    if mpp.exists():
        master_prompt = mpp.read_text(encoding="utf-8").strip()

    skills: list[SkillDocument] = []
    for path in sorted(_skills_dir().glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        skills.append(_skill_document_from_path(path, content))

    runtime_prompt_docs: list[PromptDocument] = []
    for path in sorted(_prompts_dir().glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
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

    snapshot = _PROMPT_STORE["snapshot"]
    if (
        snapshot.signature
        or snapshot.master_prompt
        or snapshot.skills
        or snapshot.runtime_prompt_docs
        or snapshot.installed_skill_metadata
    ):
        return snapshot

    if not (_master_prompt_path().exists() or _skills_dir().exists() or _prompts_dir().exists()):
        return snapshot

    snapshot = _read_prompt_store_from_disk()
    _set_prompt_store(snapshot)
    return snapshot


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

    skill_path = _skills_dir() / f"{_slugify(name)}.md"
    if skill_path.exists() and not overwrite:
        raise FileExistsError(f"Skill already exists: {skill_path.name}")

    write_utf8_file(str(skill_path), markdown.strip() + "\n")
    return skill_path


async def async_setup_prompt_store(hass: HomeAssistant) -> None:

    await async_refresh_prompt_store(hass)


async def async_refresh_prompt_store(hass: HomeAssistant) -> None:

    snapshot = await hass.async_add_executor_job(_read_prompt_store_from_disk)
    _set_prompt_store(snapshot)
    await async_reload_concept_aliases(hass)


async def async_save_master_prompt(hass: HomeAssistant, markdown: str) -> Path:

    path = await hass.async_add_executor_job(_write_master_prompt, markdown)
    await async_refresh_prompt_store(hass)
    return path


def _read_skill_raw(name: str) -> str | None:

    skill_path = _skills_dir() / f"{_slugify(name)}.md"
    if not skill_path.exists():
        return None
    return skill_path.read_text(encoding="utf-8")


def _delete_skill_sync(name: str) -> tuple[Path, str | None]:

    if not name.strip():
        raise ValueError("Skill name is required")
    skill_path = _skills_dir() / f"{_slugify(name)}.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill not found: {skill_path.name}")
    previous = skill_path.read_text(encoding="utf-8")
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
    await async_record_change(
        hass,
        target_type="skill",
        target_id=path.stem,
        action="update" if previous else "create",
        before=previous,
        after=markdown,
        actor=actor,
        reason=reason,
    )
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
        "keywords": keywords,
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
        return skill.content.strip()
    return ""


def _score_skill_match(skill: SkillDocument, query: str, query_tokens: tuple[str, ...]) -> int:
    if not query:
        return 0

    score = 0
    normalized_query = query.lower().strip()
    title = skill.title.lower()
    slug = skill.slug.lower()
    description = skill.description.lower()
    keywords = " ".join(skill.keywords)
    content = skill.content.lower()

    if normalized_query in title:
        score += 16
    if normalized_query in description:
        score += 12
    if normalized_query in keywords:
        score += 10
    if normalized_query in content:
        score += 6

    for token in query_tokens:
        if token in title or token in slug:
            score += 8
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
        for token in query_tokens:
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
    return [
        {
            "name": skill.title,
            "slug": skill.slug,
            "file": skill.file_name,
            "description": skill.description,
            "score": score,
        }
        for score, skill in scored[: max(limit, 0)]
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

    for skill in _ensure_prompt_store_fresh().skills:
        candidates = {
            skill.slug.lower(),
            skill.file_name.lower(),
            skill.title.lower(),
        }
        if lookup in candidates:
            return {
                "name": skill.title,
                "slug": skill.slug,
                "file": skill.file_name,
                "markdown": skill.content,
                "description": skill.description,
            }

    for skill in _ensure_prompt_store_fresh().skills:
        haystacks = (skill.slug.lower(), skill.file_name.lower(), skill.title.lower())
        if any(lookup in hay for hay in haystacks):
            return {
                "name": skill.title,
                "slug": skill.slug,
                "file": skill.file_name,
                "markdown": skill.content,
                "description": skill.description,
            }

    raise ValueError(f"Skill not found: {identifier}")
