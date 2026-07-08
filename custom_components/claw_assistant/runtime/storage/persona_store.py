"""Persona store — per-user personality profiles for Claw.

Stores persona data as markdown files in the workspace/personas/ directory.
Provides dual-injection: extra_system_prompt (system-level, turn 1+) and
user_context_prefix (text-level, turn 2+). Also manages a shadow user index
with LRU eviction (max 20) persisted as JSON.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from homeassistant.core import HomeAssistant

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_SHADOW_MAX = 20
_PERSONA_DIR = "workspace/personas"
_SHADOW_INDEX_FILE = "workspace/shadow_index.json"

# Fields that go into extra_system_prompt (persona + tone)
_SYSTEM_PROMPT_KEYS = ("Name", "Role", "Tone", "Call me", "Language")
# Fields that go into user_context_prefix (compact identity)
_CONTEXT_PREFIX_KEYS = ("Name", "Call me")


def _personas_dir() -> Path:
    p = get_data_dir() / _PERSONA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persona_path(user_key: str) -> Path:
    safe_key = _safe_filename(user_key)
    return _personas_dir() / f"{safe_key}.md"


def _shadow_index_path() -> Path:
    return get_data_dir() / _SHADOW_INDEX_FILE


def _safe_filename(key: str) -> str:
    """Sanitize user_key for filesystem use."""
    safe = []
    for ch in key:
        if ch.isalnum() or ch in ("-", "_", "."):
            safe.append(ch)
        else:
            safe.append(f"_{ord(ch):02x}")
    return "".join(safe)


def _load_shadow_index() -> dict[str, float]:
    """Load shadow index {user_key: last_active_timestamp}."""
    path = _shadow_index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Failed to load shadow index: %s", exc)
    return {}


def _save_shadow_index(index: dict[str, float]) -> None:
    """Persist shadow index."""
    path = _shadow_index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.error("Failed to save shadow index: %s", exc)


def _evict_shadow(index: dict[str, float]) -> dict[str, float]:
    """Enforce SHADOW_MAX, evicting least-recently-active entries."""
    if len(index) <= _SHADOW_MAX:
        return index
    sorted_items = sorted(index.items(), key=lambda x: x[1])  # oldest first
    evicted = len(sorted_items) - _SHADOW_MAX
    pruned = dict(sorted_items[evicted:])
    LOGGER.info("Evicted %d shadow entries (max %d)", evicted, _SHADOW_MAX)
    return pruned


def _parse_md_block(content: str) -> dict[str, str]:
    """Parse '- **Key**: value' markdown block into a dict."""
    result: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- **"):
            continue
        # Extract key
        key_end = stripped.find("**:")
        if key_end == -1:
            continue
        key = stripped[4:key_end].strip()
        # Extract value (after "**: ")
        val = stripped[key_end + 3:].strip()
        if val:
            result[key] = val
    return result


def _build_md_block(data: dict[str, str]) -> str:
    """Build '- **Key**: value' markdown block from a dict."""
    lines = []
    for key in ("Name", "Role", "Tone", "Call me", "Preferences", "Time zone", "Language"):
        val = data.get(key)
        if val:
            lines.append(f"- **{key}**: {val}")
    return "\n".join(lines)


class PersonaStore:
    """Read/write persona profiles keyed by user_key."""

    @staticmethod
    def get(user_key: str | None) -> dict[str, str]:
        """Return persona dict. None or missing returns empty dict (global fallback)."""
        if user_key is None:
            return {}

        path = _persona_path(user_key)
        if not path.exists():
            return {}

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            LOGGER.warning("Cannot read persona %s", path)
            return {}

        return _parse_md_block(content)

    @staticmethod
    def ensure(user_key: str | None, hass: HomeAssistant) -> dict[str, str]:
        """Auto-create a default persona if one doesn't exist.

        For HA user keys (UUID format), resolves the person entity name.
        For shadow keys, uses the external ID as display name.
        Returns the persona dict (existing or newly created).
        """
        if user_key is None:
            return {}

        # Already exists?
        existing = PersonaStore.get(user_key)
        if existing:
            return existing

        # Try to resolve from HA person entity
        name = None
        if not user_key.startswith("shadow:"):
            # HA user_id → look up person entity name
            try:
                person_data = json.loads(
                    (Path(hass.config.config_dir) / ".storage" / "person").read_text(
                        encoding="utf-8"
                    )
                )
                for item in person_data.get("data", {}).get("items", []):
                    if item.get("user_id") == user_key:
                        name = item.get("name")
                        break
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("Cannot read person storage for auto-creation: %s", exc)

        if not name:
            # Shadow users: derive name from key
            if user_key.startswith("shadow:"):
                parts = user_key.split(":", 2)
                if len(parts) >= 3:
                    name = f"{parts[1]}:{parts[2][:8]}"
            if not name:
                name = "User"

        # Build default persona with just the name
        persona = {
            "Name": name,
            "Role": "家人",
            "Language": "中文",
        }
        PersonaStore.set(user_key, persona)
        LOGGER.info("Auto-created default persona for %s (%s)", user_key, name)
        return persona

        return _parse_md_block(content)

    @staticmethod
    def set(user_key: str, data: dict[str, str]) -> None:
        """Write persona dict to file."""
        content = _build_md_block(data)
        path = _persona_path(user_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            LOGGER.error("Cannot write persona %s: %s", path, exc)

    @staticmethod
    def delete(user_key: str) -> None:
        """Remove a persona file."""
        path = _persona_path(user_key)
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            LOGGER.error("Cannot delete persona %s: %s", path, exc)

    @staticmethod
    def build_system_prompt(user_key: str | None) -> str:
        """Build the persona section for extra_system_prompt (turn 1+).

        Includes Name, Role, Tone, Call me, Language and any custom preferences.
        Returns empty string for global fallback.
        """
        persona = PersonaStore.get(user_key)
        if not persona:
            return ""

        lines = ["## User Profile"]
        for key in _SYSTEM_PROMPT_KEYS:
            val = persona.get(key)
            if val:
                lines.append(f"- **{key}**: {val}")

        # Add custom preferences
        for key, val in persona.items():
            if key not in _SYSTEM_PROMPT_KEYS and key not in _CONTEXT_PREFIX_KEYS:
                lines.append(f"- **{key}**: {val}")
            elif key in ("Preferences",) and val:
                lines.append(f"- **{key}**: {val}")

        return "\n".join(lines)

    @staticmethod
    def build_user_context_prefix(user_key: str | None) -> str:
        """Build the [Name: ...] identity prefix for text prepend (turn 2+).

        Returns empty string for global fallback.
        """
        persona = PersonaStore.get(user_key)
        if not persona:
            return ""

        parts = []
        for key in _CONTEXT_PREFIX_KEYS:
            val = persona.get(key)
            if val:
                parts.append(f"{key}: {val}")

        if not parts:
            return ""

        return "[" + " | ".join(parts) + "]"

    # -- shadow user management --

    @staticmethod
    def touch_shadow(user_key: str) -> None:
        """Record activity timestamp for a shadow user. Evict if over limit."""
        if not user_key.startswith("shadow:"):
            return

        index = _load_shadow_index()
        index[user_key] = time.time()
        index = _evict_shadow(index)
        _save_shadow_index(index)

    @staticmethod
    def is_shadow_evicted(user_key: str) -> bool:
        """Check if a shadow key has been evicted from the index."""
        if not user_key.startswith("shadow:"):
            return False
        index = _load_shadow_index()
        return user_key not in index

    @staticmethod
    def active_shadow_count() -> int:
        """Return number of active shadow entries."""
        return len(_load_shadow_index())
