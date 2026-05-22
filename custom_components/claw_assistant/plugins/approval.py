from __future__ import annotations

import json
import uuid
from typing import Any, Callable

PLUGIN_APPROVAL_PREFIX = "plugin_"

_get_approval_state_fn: Callable[[Any], dict] | None = None


def set_approval_state_fn(fn: Callable[[Any], dict]) -> None:
    global _get_approval_state_fn
    _get_approval_state_fn = fn


def _get_state(hass: Any) -> dict:
    if _get_approval_state_fn:
        return _get_approval_state_fn(hass)
    raise RuntimeError("Approval state function not set")


def stage_plugin_call(
    hass: Any,
    plugin_name: str,
    tool_name: str,
    handler_name: str,
    plugin_path: str,
    args: dict,
    privileged: bool = False,
) -> dict[str, Any]:
    prefix = f"{PLUGIN_APPROVAL_PREFIX}priv_" if privileged else PLUGIN_APPROVAL_PREFIX
    approval_id = f"{prefix}{uuid.uuid4().hex[:12]}"
    state = _get_state(hass)
    state.setdefault("pending_plugin_calls", {})[approval_id] = {
        "plugin": plugin_name,
        "tool": tool_name,
        "handler": handler_name,
        "plugin_path": plugin_path,
        "args": args,
        "privileged": privileged,
    }
    warning = "PRIVILEGED: Can access HA services, events, states." if privileged else None
    return {
        "success": True,
        "status": "staged",
        "approval_id": approval_id,
        "plugin": plugin_name,
        "tool": tool_name,
        "privileged": privileged,
        "args": args,
        "warning": warning,
        "instruction": (
            f"Plugin tool '{tool_name}' from '{plugin_name}' requires user approval. "
            f"Args: {json.dumps(args, ensure_ascii=False)}. "
            f"After user confirms, call with approval_id='{approval_id}', "
            f"user_consent=true, consent_quote='<user's words>'."
        ),
    }


def execute_with_approval(
    hass: Any,
    approval_id: str,
    user_consent: bool,
) -> tuple[bool, dict | None, str | None]:
    state = _get_state(hass)
    pending = state.get("pending_plugin_calls", {}).get(approval_id)
    if not pending:
        return False, None, f"Approval not found: {approval_id}"
    if not user_consent:
        return False, None, "User consent required."
    state["pending_plugin_calls"].pop(approval_id, None)
    return True, pending, None


def cancel_approval(hass: Any, approval_id: str) -> dict[str, Any]:
    state = _get_state(hass)
    pending = state.get("pending_plugin_calls", {})
    if approval_id not in pending:
        return {"success": False, "error": f"Approval not found: {approval_id}"}
    operation = pending.pop(approval_id)
    return {
        "success": True,
        "status": "cancelled",
        "approval_id": approval_id,
        "plugin": operation.get("plugin"),
        "tool": operation.get("tool"),
    }


def list_pending(hass: Any) -> list[dict[str, Any]]:
    state = _get_state(hass)
    pending = state.get("pending_plugin_calls", {})
    return [{"approval_id": k, **v} for k, v in pending.items()]


def build_approval_prompt(hass: Any) -> str:
    pending = list_pending(hass)
    if not pending:
        return ""
    lines = ["## Pending Plugin Approvals"]
    for p in pending:
        priv = " [PRIVILEGED]" if p.get("privileged") else ""
        lines.append(
            f"- {p['approval_id']}: {p['plugin']}/{p['tool']}{priv} "
            f"args={json.dumps(p.get('args', {}), ensure_ascii=False)}"
        )
    lines.append(
        "To execute: call the tool with approval_id, user_consent=true, consent_quote. "
        "To cancel: use PluginManager action=cancel_approval."
    )
    return "\n".join(lines)
