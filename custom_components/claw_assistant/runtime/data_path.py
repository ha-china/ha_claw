from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

BUNDLED_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_STORAGE_SUBDIR = "claw_assistant"

_root: Path = BUNDLED_DATA_DIR


def get_data_dir() -> Path:
    return _root



_OUTPUT_SUBDIR = "claw_assistant"
TMP_RETENTION_HOURS = 24
OUTPUT_MEDIA_RETENTION_HOURS = 2
_OUTPUT_MEDIA_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
    }
)


def get_output_dir(hass: HomeAssistant) -> Path:
    """Return ``<config>/www/claw_assistant/`` (created if missing)."""

    out = output_dir_path(hass)
    out.mkdir(parents=True, exist_ok=True)
    return out


def output_dir_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / "www" / _OUTPUT_SUBDIR


def is_output_media_file(path: Path) -> bool:
    return path.suffix.lower() in _OUTPUT_MEDIA_EXTENSIONS


def get_tmp_dir(hass: HomeAssistant) -> Path:
    """Return ``<config>/.storage/claw_assistant/tmp/`` (created if missing)."""

    tmp = tmp_dir_path(hass)
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def tmp_dir_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / ".storage" / _STORAGE_SUBDIR / "tmp"


def output_url_for(filename: str) -> str:
    """Return the relative ``/local/...`` URL for a file under ``OUTPUT_DIR``.

    This is a pure string helper with no HA dependency; use
    :func:`absolute_output_url` when a link that can be shared outside the
    HA frontend (chat apps, emails, etc.) is required.
    """

    name = str(filename).lstrip("/")
    return f"/local/{_OUTPUT_SUBDIR}/{name}"


def get_ha_base_url(hass: HomeAssistant) -> str | None:
    try:
        from homeassistant.helpers.network import get_url

        base = get_url(
            hass,
            allow_internal=True,
            allow_external=True,
            allow_cloud=True,
            prefer_external=True,
        )
    except Exception:
        return None
    return base.rstrip("/") if base else None


def absolute_output_url(hass: HomeAssistant, filename: str) -> str:
    relative = output_url_for(filename)
    base = get_ha_base_url(hass)
    if not base:
        return relative
    return base + relative


def _copy_if_missing(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.rglob("*"):
            rel = child.relative_to(src)
            target = dst / rel
            if child.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, target)
    else:
        if not dst.exists():
            shutil.copy2(src, dst)


def _migrate_from_kadermanager(config_dir: Path) -> None:
    storage = config_dir / ".storage"
    old_dir = storage / "kadermanager"
    new_dir = storage / _STORAGE_SUBDIR
    if old_dir.is_dir() and not new_dir.exists():
        try:
            old_dir.rename(new_dir)
            LOGGER.info("Migrated .storage/kadermanager -> %s", _STORAGE_SUBDIR)
        except Exception:
            LOGGER.warning("Failed to migrate .storage/kadermanager, will retry next restart")
    old_mem = storage / "kadermanager.adaptive_memory"
    new_mem = storage / f"{_STORAGE_SUBDIR}.adaptive_memory"
    if old_mem.exists() and not new_mem.exists():
        try:
            old_mem.rename(new_mem)
            LOGGER.info("Migrated kadermanager.adaptive_memory -> %s.adaptive_memory", _STORAGE_SUBDIR)
        except Exception:
            LOGGER.warning("Failed to migrate adaptive_memory, will retry next restart")


def _migrate_from_openclaw_skills(target_root: Path) -> None:
    legacy_skills_dir = Path.home() / ".openclaw" / "workspace" / "skills"
    _import_legacy_skill_dir(legacy_skills_dir, target_root, label="OpenClaw")


def _migrate_from_config_skills(config_dir: Path, target_root: Path) -> None:
    legacy_skills_dir = config_dir / "skills"
    _import_legacy_skill_dir(legacy_skills_dir, target_root, label="config/skills")


def _import_legacy_skill_dir(source_dir: Path, target_root: Path, *, label: str) -> None:
    if not source_dir.is_dir():
        return

    target_skills_dir = target_root / "skills"
    target_skills_dir.mkdir(parents=True, exist_ok=True)

    migrated = 0
    skipped = 0
    for source in sorted(source_dir.glob("*.md")):
        destination = target_skills_dir / source.name
        try:
            if destination.exists():
                skipped += 1
                continue
            shutil.copy2(source, destination)
            migrated += 1
        except Exception:
            LOGGER.warning("Failed to import legacy %s skill: %s", label, source)

    for source_dir_entry in sorted(path for path in source_dir.iterdir() if path.is_dir()):
        source_skill = source_dir_entry / "SKILL.md"
        if not source_skill.is_file():
            continue
        destination_dir = target_skills_dir / source_dir_entry.name
        try:
            if destination_dir.exists():
                skipped += 1
                continue
            shutil.copytree(source_dir_entry, destination_dir)
            migrated += 1
        except Exception:
            LOGGER.warning("Failed to import legacy %s skill directory: %s", label, source_dir_entry)

    if migrated or skipped:
        LOGGER.info(
            "Legacy skill import complete (%s): migrated=%d skipped=%d source=%s target=%s",
            label,
            migrated,
            skipped,
            source_dir,
            target_skills_dir,
        )


_SYSTEM_UPDATE_VERSION = "8.5.0"

_FORCE_UPDATE_ENTRIES = [
    "prompts/runtime_context.md",
    "prompts/memory_routing.md",
    "prompts/native_mode.md",
    "prompts/skill_mode.md",
    "workspace/AGENTS.md",
    "workspace/BOOTSTRAP.md",
    "homeassistant_guide",
]

_VERSIONED_BUNDLED_DOCS: tuple[str, ...] = (
    "prompts/runtime_context.md",
    "skills/homeassistant_runtime_guide.md",
)

_VERSION_MARKER_RE = re.compile(
    r"<!--\s*version\s*:\s*([0-9]+(?:\.[0-9]+)*)\s*-->", re.IGNORECASE
)


def _read_md_version(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:512]
    except OSError:
        return ""
    match = _VERSION_MARKER_RE.search(head)
    return match.group(1) if match else ""


def _version_tuple(value: str) -> tuple[int, ...]:
    if not value:
        return (0,)
    parts: list[int] = []
    for piece in value.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            return (0,)
    return tuple(parts) or (0,)


def _sync_versioned_docs(root: Path) -> None:
    """Force-copy bundled markdown whose version marker is newer than user's."""

    for entry in _VERSIONED_BUNDLED_DOCS:
        src = BUNDLED_DATA_DIR / entry
        if not src.exists() or not src.is_file():
            continue
        dst = root / entry
        bundle_version = _read_md_version(src)
        if not bundle_version:
            LOGGER.debug("Skipping versioned sync for %s: no marker in bundle", entry)
            continue
        local_version = _read_md_version(dst)
        if _version_tuple(bundle_version) > _version_tuple(local_version):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            LOGGER.info(
                "Versioned doc updated: %s (%s -> %s)",
                entry,
                local_version or "none",
                bundle_version,
            )


def _apply_system_update(root: Path) -> None:
    version_file = root / ".update_version"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if current == _SYSTEM_UPDATE_VERSION:
        return
    for entry in _FORCE_UPDATE_ENTRIES:
        src = BUNDLED_DATA_DIR / entry
        dst = root / entry
        if not src.exists():
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    version_file.write_text(_SYSTEM_UPDATE_VERSION, encoding="utf-8")
    LOGGER.info("System files updated to %s", _SYSTEM_UPDATE_VERSION)


def init_storage(hass: HomeAssistant) -> Path:
    global _root
    config_dir = Path(hass.config.config_dir)
    _migrate_from_kadermanager(config_dir)
    root = config_dir / ".storage" / _STORAGE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)

    entries = [
        "master_prompt.md",
        "concept_aliases.yaml",
        "custom_entities.json",
        "skills",
        "prompts",
        "workspace",
        "homeassistant_guide",
    ]
    for name in entries:
        src = BUNDLED_DATA_DIR / name
        dst = root / name
        if src.exists():
            _copy_if_missing(src, dst)

    for subdir in ("skills", "prompts", "workspace", "workspace/memory", "pending"):
        (root / subdir).mkdir(parents=True, exist_ok=True)

    _migrate_from_openclaw_skills(root)
    _migrate_from_config_skills(config_dir, root)
    _apply_system_update(root)
    _sync_versioned_docs(root)

    _root = root
    LOGGER.info("Data storage initialized at %s", root)
    return root


def sync_legacy_skill_sources(config_dir: Path, target_root: Path) -> None:
    _migrate_from_openclaw_skills(target_root)
    _migrate_from_config_skills(config_dir, target_root)
