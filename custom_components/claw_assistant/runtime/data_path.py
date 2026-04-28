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


# ---------------------------------------------------------------------------
# Artifact directories exposed to AI-generated Python.
#
# Two well-known locations are injected into ``ExecutePython`` (inline) so the
# AI can drop generated artefacts (PDFs, images, CSVs, etc.) in a predictable
# place:
#
#   * OUTPUT_DIR — persistent, browser-reachable. Maps to
#     ``<config>/www/claw_assistant/`` which Home Assistant exposes as
#     ``/local/claw_assistant/<file>`` (no auth on local network unless the
#     instance is hardened). Use for files the user is meant to download or
#     share.
#   * TMP_DIR — ephemeral, auto-cleaned. Maps to
#     ``<config>/.storage/claw_assistant/tmp/``; entries older than
#     ``TMP_RETENTION_HOURS`` are pruned hourly by ``tmp_cleanup``.
# ---------------------------------------------------------------------------

_OUTPUT_SUBDIR = "claw_assistant"
TMP_RETENTION_HOURS = 24


def get_output_dir(hass: HomeAssistant) -> Path:
    """Return ``<config>/www/claw_assistant/`` (created if missing)."""

    out = Path(hass.config.config_dir) / "www" / _OUTPUT_SUBDIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def get_tmp_dir(hass: HomeAssistant) -> Path:
    """Return ``<config>/.storage/claw_assistant/tmp/`` (created if missing)."""

    tmp = Path(hass.config.config_dir) / ".storage" / _STORAGE_SUBDIR / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


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
    except Exception:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to migrate .storage/kadermanager, will retry next restart")
    old_mem = storage / "kadermanager.adaptive_memory"
    new_mem = storage / f"{_STORAGE_SUBDIR}.adaptive_memory"
    if old_mem.exists() and not new_mem.exists():
        try:
            old_mem.rename(new_mem)
            LOGGER.info("Migrated kadermanager.adaptive_memory -> %s.adaptive_memory", _STORAGE_SUBDIR)
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to migrate adaptive_memory, will retry next restart")


_SYSTEM_UPDATE_VERSION = "6.6.0"

_FORCE_UPDATE_ENTRIES = [
    "prompts/runtime_context.md",
    "prompts/memory_routing.md",
    "prompts/native_mode.md",
    "prompts/skill_mode.md",
    # NOTE: skills/homeassistant_runtime_guide.md is intentionally excluded
    # here — it has its own per-file version marker (see
    # _VERSIONED_BUNDLED_DOCS below) so it can evolve independently of the
    # global _SYSTEM_UPDATE_VERSION.
    "workspace/AGENTS.md",
    "workspace/BOOTSTRAP.md",
    "homeassistant_guide",
]

# Markdown documents that carry their own ``<!-- version: N -->`` marker on
# the first line. On startup we compare bundle vs user copy and force-copy
# bundle whenever its version is strictly newer. Bumping the marker in
# ``data/<entry>`` is enough to push an update to every installation.
_VERSIONED_BUNDLED_DOCS: tuple[str, ...] = (
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
            # Bundle file lacks a marker — nothing to compare; skip.
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
    _migrate_from_kadermanager(Path(hass.config.config_dir))
    root = Path(hass.config.config_dir) / ".storage" / _STORAGE_SUBDIR
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

    _apply_system_update(root)
    _sync_versioned_docs(root)

    _root = root
    LOGGER.info("Data storage initialized at %s", root)
    return root
