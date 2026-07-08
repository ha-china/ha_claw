"""Helpers for IM channel discovery via cn_im_hub and conversation history."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ...const import IM_CHANNEL_NAMES

LOGGER = logging.getLogger(__name__)

_CN_IM_DOMAIN = "cn_im_hub"
_MANUAL_KEY = "__manual__"


async def ensure_claw_storage(hass: HomeAssistant) -> None:
    from ..utils.data_path import init_storage

    await hass.async_add_executor_job(init_storage, hass)


async def get_configured_provider_keys(hass: HomeAssistant) -> list[str]:
    keys: list[str] = []
    for entry in hass.config_entries.async_entries(_CN_IM_DOMAIN):
        if entry.state != ConfigEntryState.LOADED:
            continue
        for sub in entry.subentries.values():
            provider = str(sub.subentry_type or "").strip().lower()
            if provider and provider not in keys:
                keys.append(provider)
    return sorted(keys)


def manual_ext_id_key() -> str:
    return _MANUAL_KEY


def parse_im_conversation_id(conversation_id: str) -> tuple[str, str] | None:
    """Parse provider + ext_id from an IM conversation_id (matches MappingStore)."""
    if not conversation_id:
        return None
    lowered = conversation_id.lower()
    for prefix in IM_CHANNEL_NAMES:
        if not lowered.startswith(prefix.lower()):
            continue
        provider = prefix.rstrip(":").lower()
        rest = conversation_id[len(prefix) :]
        if not rest:
            return None
        parts = rest.split(":", 1)
        ext_id = parts[1] if len(parts) >= 2 else parts[0]
        ext_id = ext_id.strip()
        if ext_id:
            return provider, ext_id
        return None
    return None


def _short_id(value: str, limit: int = 18) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _format_cn_im_label(
    hass: HomeAssistant,
    target: str,
    display_name: str,
    target_type: str,
) -> str:
    zh = (hass.config.language or "").startswith("zh")
    display = str(display_name or "").strip()
    ttype = str(target_type or "").strip().lower()

    if ttype in {"chat_id", "group"}:
        kind = "群聊" if zh else "Group"
        if display and display != target:
            return f"{kind} · {_short_id(target)} ({_short_id(display, 12)})"
        return f"{kind} · {_short_id(target)}"

    if ttype == "open_id":
        if display and display != target:
            return display
        kind = "私聊" if zh else "DM"
        return f"{kind} · {_short_id(target)}"

    if display and display != target:
        return display
    return _short_id(target)


def _pick_better_label(current: str, candidate: str, target: str) -> str:
    if not current:
        return candidate
    if current == target and candidate != target:
        return candidate
    if candidate == target and current != target:
        return current
    if len(candidate) > len(current):
        return candidate
    return current


async def _load_targets_from_store(hass: HomeAssistant, subentry_id: str) -> list[dict[str, Any]]:
    store = Store(hass, 1, f"{_CN_IM_DOMAIN}_targets_{subentry_id}")
    try:
        data = await store.async_load() or {}
    except Exception as exc:
        LOGGER.debug("Failed to load cn_im_hub targets for %s: %s", subentry_id, exc)
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return [item for item in (data.get("targets") or []) if isinstance(item, dict)]


async def _load_cn_im_hub_targets(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Return {provider: {ext_id: display_label}} from cn_im_hub known targets."""
    result: dict[str, dict[str, str]] = {}
    for entry in hass.config_entries.async_entries(_CN_IM_DOMAIN):
        if entry.state != ConfigEntryState.LOADED:
            continue
        for sub in entry.subentries.values():
            provider = str(sub.subentry_type or "").strip().lower()
            if not provider:
                continue
            bucket = result.setdefault(provider, {})
            items: list[dict[str, Any]] = []
            try:
                from cn_im_hub.core.known_targets import async_get_tracker

                tracker = await async_get_tracker(hass, sub.subentry_id)
                items = tracker.snapshot()
            except Exception:
                items = await _load_targets_from_store(hass, sub.subentry_id)
            for item in items:
                target = str(item.get("target", "")).strip()
                if not target:
                    continue
                label = _format_cn_im_label(
                    hass,
                    target,
                    str(item.get("display_name") or ""),
                    str(item.get("target_type") or ""),
                )
                if target in bucket:
                    bucket[target] = _pick_better_label(bucket[target], label, target)
                else:
                    bucket[target] = label
    return result


def _load_history_targets(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    from ...conversation_utils import get_conversation_history

    result: dict[str, dict[str, str]] = {}
    history = get_conversation_history()
    for conv_id in history.list_conversation_ids():
        parsed = parse_im_conversation_id(conv_id)
        if not parsed:
            continue
        provider, ext_id = parsed
        bucket = result.setdefault(provider, {})
        if ext_id not in bucket:
            bucket[ext_id] = _short_id(ext_id)
    return result


def _load_mapping_targets(mappings: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for mapping in mappings:
        provider = str(mapping.get("provider", "")).strip().lower()
        ext_id = str(mapping.get("ext_id", "")).strip()
        if provider and ext_id:
            bucket = result.setdefault(provider, {})
            if ext_id not in bucket:
                bucket[ext_id] = _short_id(ext_id)
    return result


async def collect_provider_targets(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    from .user_mapping import MappingStore

    merged: dict[str, dict[str, str]] = {}
    for source in (
        await _load_cn_im_hub_targets(hass),
        _load_history_targets(hass),
        _load_mapping_targets(MappingStore.load()),
    ):
        for provider, targets in source.items():
            bucket = merged.setdefault(provider, {})
            for ext_id, display in targets.items():
                if not ext_id or ext_id in bucket:
                    continue
                bucket[ext_id] = display
    return merged


def _unique_option_label(ext_id: str, display: str, used_labels: dict[str, str]) -> str:
    label = display.strip() if display.strip() else _short_id(ext_id)
    if label in used_labels and used_labels[label] != ext_id:
        suffix = _short_id(ext_id, 14)
        label = f"{label} · {suffix}" if suffix not in label else f"{label} ({suffix})"
    used_labels[label] = ext_id
    return label


def build_ext_id_options(
    hass: HomeAssistant,
    provider: str,
    provider_targets: dict[str, dict[str, str]],
    *,
    manual_label: str,
) -> dict[str, str]:
    options: dict[str, str] = {}
    used_labels: dict[str, str] = {}
    for ext_id, display in sorted(
        provider_targets.get(provider, {}).items(),
        key=lambda item: (item[1].lower(), item[0].lower()),
    ):
        options[ext_id] = _unique_option_label(ext_id, display, used_labels)
    options[_MANUAL_KEY] = manual_label
    return options
