"""PolicyGate — three-state authorization decision maker (G5).

Core philosophy (from technical plan §4.2): keep "high privilege + human
confirmation". R0/R1 pass through transparently, R2/R3 interrupt the agent but
are *approvable executions*, not permanent denials; R3 can only be approved by
the owner. Every decision is recorded to the audit store by the caller.

Design notes
------------
- ``PolicyGate`` is a thin wrapper over ``MappingStore`` — there is no separate
  permission table. Role/areas come from the user_mapping.yaml entries.
- The trust whitelist lives at ``<data_dir>/workspace/memory/trust-whitelist.json``
  (absent file == empty list). Entries look like
  ``{"user_key": "...", "entity_id": "light.x" | "area": "living", "action": "HAControl"}``.
  A whitelist hit short-circuits to ALLOW.
- Un-mapped HA users default to ``owner`` (bootstrap backward compatibility:
  before G5 everyone had full access). Shadow IM users (``shadow:provider:ext``)
  are always ``member`` and therefore restricted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

# ── Risk levels ──
R0_READ = 0
R1_REVERSIBLE = 1
R2_SYSTEM_CHANGE = 2
R3_DESTRUCTIVE = 3

# Tool name -> risk level. Anything not listed defaults to R1 (reversible).
RISK_MAP: dict[str, int] = {
    # Destructive / privileged host access
    "HAControl": R3_DESTRUCTIVE,
    "ConfigEntries": R3_DESTRUCTIVE,
    "HACS": R3_DESTRUCTIVE,
    "ExecutePython": R3_DESTRUCTIVE,
    "FrontendInspect": R3_DESTRUCTIVE,
    # System changes that should be confirmed
    "ConfigFile": R2_SYSTEM_CHANGE,
    "Automation": R2_SYSTEM_CHANGE,
    "Script": R2_SYSTEM_CHANGE,
    "InstallSkill": R2_SYSTEM_CHANGE,
    "DeleteSkill": R2_SYSTEM_CHANGE,
    "UpsertGuideDoc": R2_SYSTEM_CHANGE,
    "DeleteGuideDoc": R2_SYSTEM_CHANGE,
    "ProposeSelfEdit": R2_SYSTEM_CHANGE,
    "ApplyProposal": R2_SYSTEM_CHANGE,
    "SetMasterPrompt": R2_SYSTEM_CHANGE,
    "SetWorkspaceDoc": R2_SYSTEM_CHANGE,
    "CustomEntityManager": R2_SYSTEM_CHANGE,
    "HelperManager": R2_SYSTEM_CHANGE,
    "Registry": R2_SYSTEM_CHANGE,
    "DashboardCard": R2_SYSTEM_CHANGE,
    "ExposeEntity": R2_SYSTEM_CHANGE,
    "PluginManager": R2_SYSTEM_CHANGE,
    "SystemControl": R2_SYSTEM_CHANGE,
    "HeartbeatManager": R2_SYSTEM_CHANGE,
    "BootstrapControl": R2_SYSTEM_CHANGE,
    "SetConversationState": R2_SYSTEM_CHANGE,
    # Reversible per-device control
    "ServiceCall": R1_REVERSIBLE,
    "BatchControl": R1_REVERSIBLE,
}

# Risk level -> label used in audit / panel
RISK_LABELS: dict[int, str] = {
    R0_READ: "R0 只读",
    R1_REVERSIBLE: "R1 可逆控制",
    R2_SYSTEM_CHANGE: "R2 系统变更",
    R3_DESTRUCTIVE: "R3 破坏性",
}

# Allowed roles
ROLE_OWNER = "owner"
ROLE_MEMBER = "member"

# Fields we try to extract an entity id from in tool params (best effort).
_ENTITY_KEYS = ("entity_id", "entity_ids", "area_id", "area", "camera_entity")


def _risk_of(tool_name: str) -> int:
    """Return the risk level for a tool (defaults to R1 reversible)."""
    return RISK_MAP.get(str(tool_name or ""), R1_REVERSIBLE)


def risk_of(tool_name: str) -> int:
    """Public alias for risk lookup used by the executor gate / audit."""
    return _risk_of(tool_name)


def _whitelist_path() -> Path:
    return get_data_dir() / "workspace" / "memory" / "trust-whitelist.json"


def _load_whitelist() -> list[dict[str, Any]]:
    path = _whitelist_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
    except Exception as exc:
        LOGGER.warning("Failed to load trust whitelist: %s", exc)
    return []


def _save_whitelist(entries: list[dict[str, Any]]) -> bool:
    path = _whitelist_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        LOGGER.error("Failed to save trust whitelist: %s", exc)
        return False


def _area_matches_entity(area: str, entity_id: str | None) -> bool:
    """Best-effort area match: domain-stripped entity contains the area token."""
    if not entity_id:
        return False
    norm_area = str(area or "").strip().lower().replace("_", "").replace("-", "")
    if not norm_area:
        return False
    local = entity_id.split(".", 1)[-1].lower().replace("_", "").replace("-", "")
    return norm_area in local


def extract_entity_id(params: dict[str, Any] | None) -> str | None:
    """Best-effort entity id extraction from tool params."""
    if not isinstance(params, dict):
        return None
    for key in _ENTITY_KEYS:
        value = params.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return None


class PolicyGate:
    """Three-state policy decision maker backed by MappingStore.

    Decisions: 'ALLOW' | 'CONFIRM' | 'DENY'.
    """

    # ── Whitelist management (static) ──

    @staticmethod
    def list_whitelist() -> list[dict[str, Any]]:
        return _load_whitelist()

    @staticmethod
    def add_whitelist_entry(
        user_key: str,
        action: str,
        entity_id: str | None = None,
        area: str | None = None,
    ) -> bool:
        user_key = str(user_key or "").strip()
        action = str(action or "").strip()
        if not user_key or not action:
            return False
        entries = _load_whitelist()
        # Deduplicate identical entries.
        entries = [
            e
            for e in entries
            if not (
                str(e.get("user_key", "")) == user_key
                and str(e.get("action", "")) == action
                and str(e.get("entity_id", "") or "") == str(entity_id or "")
                and str(e.get("area", "") or "") == str(area or "")
            )
        ]
        entry: dict[str, Any] = {"user_key": user_key, "action": action}
        if entity_id:
            entry["entity_id"] = str(entity_id)
        if area:
            entry["area"] = str(area)
        entries.append(entry)
        return _save_whitelist(entries)

    @staticmethod
    def remove_whitelist_entry(
        user_key: str,
        action: str,
        entity_id: str | None = None,
        area: str | None = None,
    ) -> bool:
        user_key = str(user_key or "").strip()
        action = str(action or "").strip()
        entries = _load_whitelist()
        before = len(entries)
        entries = [
            e
            for e in entries
            if not (
                str(e.get("user_key", "")) == user_key
                and str(e.get("action", "")) == action
                and str(e.get("entity_id", "") or "") == str(entity_id or "")
                and str(e.get("area", "") or "") == str(area or "")
            )
        ]
        if len(entries) == before:
            return False
        return _save_whitelist(entries)

    # ── Role & areas ──

    @staticmethod
    def get_user_role(user_key: str | None) -> str:
        """Return 'owner' or 'member' for a user_key.

        - None / shadow users -> member
        - MappingStore entry -> its role field (default member)
        - Un-mapped HA user -> owner (backward-compatible bootstrap default)
        """
        if not user_key:
            return ROLE_MEMBER
        if str(user_key).startswith("shadow:"):
            return ROLE_MEMBER
        from .user_mapping import MappingStore

        mapping = MappingStore.resolve_by_user_key(user_key)
        if mapping:
            role = str(mapping.get("role") or ROLE_MEMBER).strip()
            return role if role in (ROLE_OWNER, ROLE_MEMBER) else ROLE_MEMBER
        return ROLE_OWNER

    @staticmethod
    def get_allowed_areas(user_key: str | None) -> list[str]:
        """Return the allowed_areas list for a user (empty == unrestricted)."""
        if not user_key or str(user_key).startswith("shadow:"):
            return []
        from .user_mapping import MappingStore

        mapping = MappingStore.resolve_by_user_key(user_key)
        if not mapping:
            return []
        areas = mapping.get("allowed_areas") or []
        if not isinstance(areas, list):
            return []
        return [str(a) for a in areas if str(a).strip()]

    @staticmethod
    def _whitelist_hit(
        user_key: str | None,
        tool_name: str,
        entity_id: str | None,
    ) -> bool:
        user_key = str(user_key or "")
        for entry in _load_whitelist():
            if str(entry.get("user_key", "")) != user_key:
                continue
            if str(entry.get("action", "")) != tool_name:
                continue
            entry_entity = str(entry.get("entity_id", "") or "")
            entry_area = str(entry.get("area", "") or "")
            if entry_entity:
                if entry_entity == entity_id:
                    return True
                continue
            if entry_area:
                if _area_matches_entity(entry_area, entity_id):
                    return True
                continue
            # Tool-level whitelist (no entity/area): matches any call.
            return True
        return False

    @staticmethod
    def evaluate(
        user_key: str | None,
        tool_name: str,
        entity_id: str | None = None,
    ) -> str:
        """Return 'ALLOW' | 'CONFIRM' | 'DENY' for a tool invocation.

        Whitelist hits short-circuit to ALLOW. Owners are always ALLOW (the
        caller records an audit entry). Members/shadows follow risk levels:
        R0/R1 -> ALLOW (area-restricted), R2 -> CONFIRM, R3 -> DENY.
        """
        tool_name = str(tool_name or "")
        if not tool_name:
            return "ALLOW"

        # 1. Trust whitelist short-circuit.
        if PolicyGate._whitelist_hit(user_key, tool_name, entity_id):
            return "ALLOW"

        # 2. Owner: full allow (audit recorded by caller).
        if PolicyGate.get_user_role(user_key) == ROLE_OWNER:
            return "ALLOW"

        # 3. Member / shadow path.
        risk = _risk_of(tool_name)
        if risk <= R1_REVERSIBLE:
            areas = PolicyGate.get_allowed_areas(user_key)
            if entity_id and areas and not _entity_in_areas(entity_id, areas):
                return "DENY"
            return "ALLOW"
        if risk == R2_SYSTEM_CHANGE:
            return "CONFIRM"
        return "DENY"


def _entity_in_areas(entity_id: str, areas: list[str]) -> bool:
    """True if the entity matches one of the allowed areas (best effort)."""
    for area in areas:
        if _area_matches_entity(area, entity_id):
            return True
    return False
