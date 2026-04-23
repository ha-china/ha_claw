from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .data_path import get_data_dir

_LOGGER = logging.getLogger(__name__)
_STORE_KEY = "_custom_entities"

SUPPORTED_PLATFORMS = ("sensor", "binary_sensor", "switch", "button")


def _store_path(hass: HomeAssistant) -> Path:
    return get_data_dir() / "custom_entities.json"


def _read_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return []


def _write_store(path: Path, entities: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entities, ensure_ascii=False, indent=2), "utf-8")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", text.lower().strip())
    return slug.strip("_") or "entity"


async def async_load_custom_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    path = _store_path(hass)
    entities = await hass.async_add_executor_job(_read_store, path)
    hass.data.setdefault("claw_assistant", {})[_STORE_KEY] = entities
    return entities


def get_custom_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    return hass.data.get("claw_assistant", {}).get(_STORE_KEY, [])


def get_custom_entities_by_platform(hass: HomeAssistant, platform: str) -> list[dict[str, Any]]:
    return [e for e in get_custom_entities(hass) if e.get("platform") == platform]


async def async_upsert_custom_entity(
    hass: HomeAssistant,
    *,
    entity_id: str = "",
    platform: str,
    name: str,
    state_template: str = "",
    icon: str = "",
    device_class: str = "",
    state_class: str = "",
    unit_of_measurement: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform}")

    entities = get_custom_entities(hass)
    uid = entity_id or f"km_{_slugify(name)}_{uuid.uuid4().hex[:6]}"

    existing = next((e for e in entities if e["uid"] == uid), None)
    entry: dict[str, Any] = existing or {"uid": uid, "platform": platform}
    entry["name"] = name
    entry["platform"] = platform
    if state_template:
        entry["state_template"] = state_template
    if icon:
        entry["icon"] = icon
    if device_class:
        entry["device_class"] = device_class
    if state_class:
        entry["state_class"] = state_class
    if unit_of_measurement:
        entry["unit_of_measurement"] = unit_of_measurement
    if options:
        entry["options"] = options

    if existing is None:
        entities.append(entry)

    path = _store_path(hass)
    await hass.async_add_executor_job(_write_store, path, entities)
    return entry


async def async_delete_custom_entity(hass: HomeAssistant, uid: str) -> dict[str, Any] | None:
    entities = get_custom_entities(hass)
    removed = None
    new_list = []
    for e in entities:
        if e["uid"] == uid:
            removed = e
        else:
            new_list.append(e)
    if removed is None:
        return None
    hass.data.setdefault("claw_assistant", {})[_STORE_KEY] = new_list
    path = _store_path(hass)
    await hass.async_add_executor_job(_write_store, path, new_list)
    return removed


async def async_list_custom_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    return list(get_custom_entities(hass))
