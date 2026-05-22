

from __future__ import annotations

from typing import Any

from homeassistant.helpers import llm
from homeassistant.helpers.json import json_dumps

from .tool_result_summary import (
    extract_failed_tool_response,
    extract_successful_tool_response,
)

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

    from ..tools.registry import get_full_tool_registry

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

    from ..tools.registry import build_tool_list

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
        return {
            "tool_name": tool_name,
            "tool_args": tool_args,
            "success": False,
            "error": f"Unknown tool: {tool_name}",
            "result": None,
            "summary": f"Unknown tool: {tool_name}",
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
    return tool_result
