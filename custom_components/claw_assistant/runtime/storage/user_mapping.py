"""External identity → HA user_id mapping store.

Stores the mapping in YAML under workspace/user_mapping.yaml.
Supports resolve(provider, ext_id) → ha_user_id | None for conversation_id parsing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...const import IM_CHANNEL_NAMES
from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_MAPPING_FILE = "workspace/user_mapping.yaml"


def _mapping_path() -> Path:
    return get_data_dir() / _MAPPING_FILE


def _im_prefix_provider_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for prefix in IM_CHANNEL_NAMES:
        provider = prefix.rstrip(":").lower()
        pairs.append((prefix.lower(), provider))
    return pairs


class MappingStore:
    """Persistent mapping from external IM identity to HA user_id."""

    @staticmethod
    def resolve(provider: str | None, ext_id: str | None) -> str | None:
        if not provider or not ext_id:
            return None
        mappings = MappingStore.load()
        for entry in mappings:
            if entry.get("provider") == provider and entry.get("ext_id") == ext_id:
                return entry.get("ha_user_id")
        return None

    @staticmethod
    def resolve_by_conversation_id(conversation_id: str | None) -> str | None:
        if not conversation_id:
            return None
        return MappingStore._resolve_by_prefix(conversation_id)

    @staticmethod
    def _resolve_by_prefix(conv_id: str) -> str | None:
        # #### @C3H3-AI ha_claw#14 — MappingStore._resolve_by_prefix()
        lowered_conv_id = conv_id.lower()
        for prefix, provider in _im_prefix_provider_pairs():
            if lowered_conv_id.startswith(prefix):
                rest = conv_id[len(prefix):]
                parts = rest.split(":", 1)
                ext_id = parts[1] if len(parts) >= 2 else parts[0]
                return MappingStore.resolve(provider, ext_id)
        return None

    @staticmethod
    def load() -> list[dict[str, str]]:
        path = _mapping_path()
        if not path.exists():
            return []

        try:
            import yaml

            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                entries = data.get("mappings")
                if isinstance(entries, list):
                    return entries
        except Exception as exc:
            LOGGER.warning("Failed to load user mapping: %s", exc)
        return []

    @staticmethod
    def save(mappings: list[dict[str, str]]) -> None:
        path = _mapping_path()
        try:
            import yaml

            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(mappings, f, default_flow_style=False, allow_unicode=True)
        except Exception as exc:
            LOGGER.error("Failed to save user mapping: %s", exc)

    @staticmethod
    def set(provider: str, ext_id: str, ha_user_id: str) -> None:
        mappings = MappingStore.load()
        mappings = [
            e
            for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        mappings.append(
            {
                "provider": provider,
                "ext_id": ext_id,
                "ha_user_id": ha_user_id,
            }
        )
        MappingStore.save(mappings)

    @staticmethod
    def remove(provider: str, ext_id: str) -> None:
        mappings = MappingStore.load()
        mappings = [
            e
            for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        MappingStore.save(mappings)
