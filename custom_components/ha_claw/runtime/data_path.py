from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

BUNDLED_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_STORAGE_SUBDIR = "claw_assistant"

_root: Path = BUNDLED_DATA_DIR


def get_data_dir() -> Path:
    return _root


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


_SYSTEM_UPDATE_VERSION = "6.2.0"

_FORCE_UPDATE_ENTRIES = [
    "prompts/runtime_context.md",
    "prompts/memory_routing.md",
    "prompts/native_mode.md",
    "prompts/skill_mode.md",
    "skills/homeassistant_runtime_guide.md",
    "workspace/AGENTS.md",
    "workspace/BOOTSTRAP.md",
    "homeassistant_guide",
]


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

    _root = root
    LOGGER.info("Data storage initialized at %s", root)
    return root
