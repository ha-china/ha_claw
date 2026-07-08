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
        """Look up ha_user_id by IM provider + external user ID.

        Returns:
            ha_user_id if mapping found, None otherwise.
        """
        if not provider or not ext_id:
            return None
        mappings = MappingStore._load()
        for entry in mappings:
            if entry.get("provider") == provider and entry.get("ext_id") == ext_id:
                return entry.get("ha_user_id")
        return None

    @staticmethod
    def resolve_by_conversation_id(conversation_id: str | None) -> str | None:
        """Parse conversation_id and look up mapping.

        Expected format: '{provider}:{account}:{user_id}'
        Example: 'feishu:oc_xxx:user_123'
        """
        if not conversation_id:
            return None
        return MappingStore._resolve_by_prefix(conversation_id)

    @staticmethod
    def _resolve_by_prefix(conv_id: str) -> str | None:
        """Try each known IM prefix to extract provider and ext_id."""
        # Known IM prefixes from const.IM_CHANNEL_NAMES
        prefixes = [
            ("wechat:", "wechat"),
            ("feishu:", "feishu"),
            ("dingtalk:", "dingtalk"),
            ("qq:", "qq"),
            ("wecom:", "wecom"),
            ("xiaoyi:", "xiaoyi"),
        ]
        for prefix, provider in prefixes:
            if conv_id.lower().startswith(prefix):
                # Format: {provider}:{identifier}
                # identifier could be 'account:user_id' or just 'open_id'
                rest = conv_id[len(prefix):]
                parts = rest.split(":", 1)
                ext_id = parts[1] if len(parts) >= 2 else parts[0]
                return MappingStore.resolve(provider, ext_id)
        return None

    @staticmethod
    def _load() -> list[dict[str, str]]:
        """Load mappings from YAML file."""
        path = _mapping_path()
        if not path.exists():
            return []

        try:
            import yaml
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if isinstance(data, list):
                return data
            # Also handle 'mappings:' key format
            if isinstance(data, dict):
                entries = data.get("mappings")
                if isinstance(entries, list):
                    return entries
        except Exception as exc:
            LOGGER.warning("Failed to load user mapping: %s", exc)
        return []

    @staticmethod
    def save(mappings: list[dict[str, str]]) -> None:
        """Save mappings to YAML file."""
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
        """Add or update a single mapping entry."""
        mappings = MappingStore._load()
        # Remove existing entry for same provider+ext_id
        mappings = [
            e for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        mappings.append({
            "provider": provider,
            "ext_id": ext_id,
            "ha_user_id": ha_user_id,
        })
        MappingStore.save(mappings)

    @staticmethod
    def remove(provider: str, ext_id: str) -> None:
        """Remove a mapping entry."""
        mappings = MappingStore._load()
        mappings = [
            e for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        MappingStore.save(mappings)
