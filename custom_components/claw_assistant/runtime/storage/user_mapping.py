"""External identity → HA user_id mapping store (G5 member store).

Stores the mapping in YAML under workspace/user_mapping.yaml.
Supports resolve(provider, ext_id) → ha_user_id | None for conversation_id parsing.

G5 extension: each mapping entry may carry member metadata — role
(owner/member), allowed_areas (list of area tokens), label and created_at.
Existing entries without these fields keep working and default to
role=member / allowed_areas=[].
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_MAPPING_FILE = "workspace/user_mapping.yaml"

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
    def load() -> list[dict[str, Any]]:
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
    def save(mappings: list[dict[str, Any]]) -> bool:
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
    def set(
        provider: str,
        ext_id: str,
        ha_user_id: str,
        *,
        role: str = ROLE_MEMBER,
        allowed_areas: list[str] | None = None,
        label: str = "",
    ) -> bool:
        mappings = MappingStore.load()
        mappings = [
            e
            for e in mappings
            if not (e.get("provider") == provider and e.get("ext_id") == ext_id)
        ]
        role = str(role or ROLE_MEMBER).strip()
        if role not in (ROLE_OWNER, ROLE_MEMBER):
            role = ROLE_MEMBER
        entry: dict[str, Any] = {
            "provider": provider,
            "ext_id": ext_id,
            "ha_user_id": ha_user_id,
            "role": role,
            "allowed_areas": [str(a) for a in (allowed_areas or []) if str(a).strip()],
            "label": str(label or "").strip(),
        }
        entry.setdefault("created_at", _now_iso())
        mappings.append(entry)
        return MappingStore.save(mappings)

    @staticmethod
    def resolve_by_user_key(user_key: str | None) -> dict[str, Any] | None:
        """Resolve a full mapping entry by user_key.

        A user_key is either an HA user id (``ha_user_id``) or an IM
        ``provider:ext_id`` compound. Shadow keys (``shadow:...``) never match.
        """
        if not user_key:
            return None
        key = str(user_key)
        if key.startswith("shadow:"):
            return None
        for entry in MappingStore.load():
            if entry.get("ha_user_id") == key:
                return entry
            compound = f"{entry.get('provider', '')}:{entry.get('ext_id', '')}"
            if compound == key:
                return entry
        return None

    @staticmethod
    def update_member(
        *,
        provider: str | None = None,
        ext_id: str | None = None,
        ha_user_id: str | None = None,
        role: str | None = None,
        allowed_areas: list[str] | None = None,
        label: str | None = None,
    ) -> bool:
        """Update member metadata. Locates the entry by any of provider/ext_id/ha_user_id.

        At least one locator must be provided. Returns False when no entry matches.
        """
        mappings = MappingStore.load()
        target = None
        for entry in mappings:
            if provider and entry.get("provider") == provider and (
                not ext_id or entry.get("ext_id") == ext_id
            ):
                target = entry
                break
            if ha_user_id and entry.get("ha_user_id") == ha_user_id:
                target = entry
                break
        if target is None:
            return False

        if role is not None:
            role = str(role).strip()
            target["role"] = role if role in (ROLE_OWNER, ROLE_MEMBER) else ROLE_MEMBER
        if allowed_areas is not None:
            target["allowed_areas"] = [str(a) for a in allowed_areas if str(a).strip()]
        if label is not None:
            target["label"] = str(label).strip()
        return MappingStore.save(mappings)

    @staticmethod
    def remove_by_user_key(user_key: str | None) -> bool:
        """Remove a member entry by user_key (ha_user_id or provider:ext_id)."""
        if not user_key:
            return False
        key = str(user_key)
        if key.startswith("shadow:"):
            return False
        mappings = MappingStore.load()
        before = len(mappings)
        mappings = [
            e
            for e in mappings
            if e.get("ha_user_id") != key
            and f"{e.get('provider', '')}:{e.get('ext_id', '')}" != key
        ]
        if len(mappings) == before:
            return False
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
