from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

BUNDLED_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_STORAGE_SUBDIR = "kadermanager"

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


def init_storage(hass: HomeAssistant) -> Path:
    global _root
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

    _root = root
    LOGGER.info("Data storage initialized at %s", root)
    return root
