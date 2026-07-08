"""External identity → HA user_id mapping store.

Stores the mapping in YAML under workspace/user_mapping.yaml.
Supports resolve(provider, ext_id) → ha_user_id | None for conversation_id parsing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_MAPPING_FILE = "workspace/user_mapping.yaml"


def _mapping_path() -> Path:
    return get_data_dir() / _MAPPING_FILE


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
        from .im_channel_helpers import parse_im_conversation_id

        parsed = parse_im_conversation_id(conv_id)
        if not parsed:
            return None
        provider, ext_id = parsed
        return MappingStore.resolve(provider, ext_id)

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
    def save(mappings: list[dict[str, str]]) -> bool:
        path = _mapping_path()
        try:
            import yaml

            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(mappings, f, default_flow_style=False, allow_unicode=True)
            return True
        except Exception as exc:
            LOGGER.error("Failed to save user mapping: %s", exc)
            return False

    @staticmethod
    def set(provider: str, ext_id: str, ha_user_id: str) -> bool:
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
        return MappingStore.save(mappings)

    @staticmethod
    def remove(provider: str, ext_id: str) -> bool:
        mappings = MappingStore.load()
        before = len(mappings)
        mappings = [
            e
            for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        if len(mappings) == before:
            return False
        return MappingStore.save(mappings)
