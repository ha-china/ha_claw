

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps

from .tool_result_summary import (
    extract_failed_tool_response,
    extract_successful_tool_response,
)
from ..hooks.events import HookPayload, fire_hook_event

def _sanitize_tool_payload(value: Any) -> Any:

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(k): _sanitize_tool_payload(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_tool_payload(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _sanitize_tool_payload(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _sanitize_tool_payload(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            data = vars(value)
            if data:
                return _sanitize_tool_payload(data)
        except Exception:
            pass
    try:
        return json.loads(json_dumps(value))
    except Exception:
        return str(value)


_KERNEL_BLOCKED_TOOLS = frozenset(
    {
        "AgentHandoff",
        "NextAgentHandoff",
        "ParallelToolCall",
        "SetConversationState",
        "ThinkContinue",
        
    }
)


def list_kernel_tool_specs() -> list[dict[str, str]]:

    from ...tools.registry import get_full_tool_registry

    specs: list[dict[str, str]] = []
    for name, meta in get_full_tool_registry().items():
        if name in _KERNEL_BLOCKED_TOOLS:
            continue
        specs.append(
            {
                "name": name,
                "category": str(meta.get("category", "")),
                "description": str(meta.get("desc", "")),
            }
        )
    return specs


def _gated_tool_result(
    tool_name: str,
    tool_args: dict[str, Any],
    error: str,
    summary: str,
) -> dict[str, Any]:
    """Build a tool result shaped exactly like a failed real call."""
    return {
        "tool_name": tool_name,
        "tool_args": _sanitize_tool_payload(tool_args),
        "success": False,
        "error": error,
        "result": None,
        "summary": summary,
    }


def _extract_entity_from_args(tool_args: dict[str, Any]) -> str | None:
    """Best-effort entity id extraction from flat tool args + nested params."""
    from ..storage.authorization import extract_entity_id

    if not isinstance(tool_args, dict):
        return None
    entity_id = extract_entity_id(tool_args)
    if entity_id:
        return entity_id
    nested = tool_args.get("params")
    if isinstance(nested, dict):
        return extract_entity_id(nested)
    return None


def _record_audit(
    hass,
    *,
    user_key: str | None,
    tool_name: str,
    risk: int,
    decision: str,
    entity_id: str | None,
    result: str,
    approver: str | None = None,
    approval_id: str | None = None,
) -> None:
    """Fire-and-forget audit write (never blocks the executor)."""
    from ..storage.audit_store import record

    try:
        record(
            hass,
            {
                "user_key": user_key,
                "tool": tool_name,
                "risk": risk,
                "decision": decision,
                "entity_id": entity_id,
                "result": result,
                "approver": approver,
                "approval_id": approval_id,
            },
        )
    except Exception:
        pass


async def _wait_for_approval(
    hass,
    approval_id: str,
    timeout: float = 180.0,
) -> bool:
    """Poll the approval queue until resolved or timeout (default 180s)."""
    from ..storage import approval_store

    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(timeout)
    while loop.time() < deadline:
        if approval_store.get(hass, approval_id) is None:
            for item in approval_store.history(hass, limit=10):
                if item.get("approval_id") == approval_id:
                    return item.get("status") == "approved"
            return False
        await asyncio.sleep(0.5)
    approval_store.cancel(hass, approval_id, approver="timeout")
    return False


async def _enforce_policy_gate(
    hass,
    tool_name: str,
    tool_args: dict[str, Any],
) -> dict[str, Any] | None:
    """Run the G5 PolicyGate three-state gate.

    Returns a blocked/cancelled tool result when the call must not proceed,
    otherwise None (ALLOW / approved CONFIRM). Any internal failure degrades to
    ALLOW so the executor never breaks for unrelated errors.
    """
    try:
        from ..storage.user_activity import get_active_user_key
        from ..storage.authorization import PolicyGate, risk_of
        from ..storage import approval_store
    except Exception:
        return None

    user_key = get_active_user_key(hass)
    entity_id = _extract_entity_from_args(tool_args)
    risk = risk_of(tool_name)
    decision = PolicyGate.evaluate(user_key, tool_name, entity_id)

    if decision == "ALLOW":
        _record_audit(
            hass,
            user_key=user_key,
            tool_name=tool_name,
            risk=risk,
            decision="ALLOW",
            entity_id=entity_id,
            result="allow",
        )
        return None

    if decision == "DENY":
        _record_audit(
            hass,
            user_key=user_key,
            tool_name=tool_name,
            risk=risk,
            decision="DENY",
            entity_id=entity_id,
            result="deny",
        )
        return _gated_tool_result(
            tool_name,
            tool_args,
            "无权限执行该操作",
            f"{tool_name} is not permitted for this user.",
        )

    # CONFIRM — push to the approval queue and wait for a human decision.
    entry = approval_store.create(
        hass,
        tool=tool_name,
        params=tool_args if isinstance(tool_args, dict) else {},
        user_key=user_key,
        risk=risk,
    )
    approval_id = entry.get("approval_id", "")
    _record_audit(
        hass,
        user_key=user_key,
        tool_name=tool_name,
        risk=risk,
        decision="CONFIRM",
        entity_id=entity_id,
        result="pending",
        approval_id=approval_id,
    )
    approved = await _wait_for_approval(
        hass,
        approval_id,
        timeout=float(
            hass.data.get("claw_assistant", {}).get("approval_timeout", 180.0)
        ),
    )
    resolved = next(
        (
            item
            for item in approval_store.history(hass, limit=10)
            if item.get("approval_id") == approval_id
        ),
        None,
    )
    _record_audit(
        hass,
        user_key=user_key,
        tool_name=tool_name,
        risk=risk,
        decision="CONFIRM",
        entity_id=entity_id,
        result="approved" if approved else "cancelled",
        approver=(resolved or {}).get("approver"),
        approval_id=approval_id,
    )
    if approved:
        return None
    return _gated_tool_result(
        tool_name,
        tool_args,
        "操作未获确认，已取消",
        f"{tool_name} was not confirmed and has been cancelled.",
    )


async def execute_kernel_tool(
    hass,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    agent_id: str,
    context,
    language: str | None,
    device_id: str | None,
) -> dict[str, Any]:

    from ...tools.registry import build_tool_list
    from ...tools.skill_tools import build_skill_tool_list

    if tool_name in _KERNEL_BLOCKED_TOOLS:
        return {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "success": False,
            "error": f"Tool is blocked in kernel mode: {tool_name}",
            "result": None,
            "summary": f"{tool_name} is blocked in kernel mode.",
        }

    tool = next((item for item in build_tool_list(include_names={tool_name}) if item.name == tool_name), None)
    if tool is None:
        tool = next((item for item in build_skill_tool_list() if item.name == tool_name), None)
    if tool is None:
        return {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "success": False,
            "error": f"Unknown tool: {tool_name}",
            "result": None,
            "summary": f"Unknown tool: {tool_name}",
        }

    pre_report = await fire_hook_event(
        hass,
        HookPayload(
            event="PreToolUse",
            agent_id=agent_id,
            tool_name=tool_name,
            tool_args=tool_args,
            metadata={
                "language": language,
                "device_id": device_id,
            },
        ),
    )
    if pre_report.blocked:
        message = next(
            (
                outcome.message
                for outcome in pre_report.outcomes
                if outcome.decision == "block" and outcome.message
            ),
            f"Tool blocked by claw_assistant hook: {tool_name}",
        )
        return {
            "tool_name": tool_name,
            "tool_args": _sanitize_tool_payload(tool_args),
            "success": False,
            "error": message,
            "result": None,
            "summary": message,
        }

    llm_context = llm.LLMContext(
        platform=agent_id,
        context=context,
        language=language,
        assistant=agent_id,
        device_id=device_id,
    )
    tool_input = llm.ToolInput(
        id=f"kernel_{tool_name}",
        tool_name=tool_name,
        tool_args=tool_args,
    )

    try:
        # ── G5 PolicyGate three-state gate (ALLOW / CONFIRM / DENY) ──
        gate_block = await _enforce_policy_gate(hass, tool_name, tool_args)
        if gate_block is not None:
            return gate_block
        result = await tool.async_call(hass, tool_input, llm_context)
    except Exception as err:
        tool_result = {
            "tool_name": tool_name,
            "tool_args": _sanitize_tool_payload(tool_args),
            "success": False,
            "error": str(err),
            "result": None,
        }
        tool_result["summary"] = extract_failed_tool_response([tool_result]) or str(err)
        await fire_hook_event(
            hass,
            HookPayload(
                event="PostToolUse",
                agent_id=agent_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
            ),
        )
        return tool_result

    result = _sanitize_tool_payload(result)
    success = True
    error = None
    if isinstance(result, dict):
        if "success" in result:
            success = bool(result.get("success", True))
            error = str(result.get("error", "")) or None
        elif "response_type" in result:
            success = result.get("response_type") != "error"

    tool_result = {
        "tool_name": tool_name,
        "tool_args": _sanitize_tool_payload(tool_args),
        "success": success,
        "error": error,
        "result": result,
    }
    if success:
        summary = extract_successful_tool_response([tool_result])
    else:
        summary = extract_failed_tool_response([tool_result])
    tool_result["summary"] = summary[:1200] if summary else ""
    await fire_hook_event(
        hass,
        HookPayload(
            event="PostToolUse",
            agent_id=agent_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
        ),
    )
    return tool_result
