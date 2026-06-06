

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant

from ...const import (
    CONF_IDENTICAL_CALL_STOP,
    CONF_IDENTICAL_CALL_WARN,
    CONF_MAX_TOOL_REPEAT,
    CONF_PIPELINE_TIMEOUT,
    DEFAULT_IDENTICAL_CALL_STOP,
    DEFAULT_IDENTICAL_CALL_WARN,
    DEFAULT_MAX_TOOL_REPEAT,
    DEFAULT_PIPELINE_TIMEOUT,
    DOMAIN,
)
from ..core.config import DEFAULT_THRESHOLDS
from ..core.state import get_task_loop_state

_MAX_TRACE_ENTRIES = 200
_MAX_HISTORY_ENTRIES = 50
_MAX_TOOL_USAGE_ENTRIES = 100


def _trim_list(lst: list, max_size: int) -> None:

    if len(lst) > max_size:
        del lst[: -max_size]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def reset_loop_for_conversation(
    hass: HomeAssistant,
    *,
    conversation_id: str | None,
    max_iterations: int = 50,
) -> dict[str, Any]:
    from ..storage.ha_guide_store import reset_tool_guide_seen

    reset_tool_guide_seen()
    task_loop = get_task_loop_state(hass)
    task_loop.clear()
    task_loop.update(
        {
            "active": True,
            "conversation_id": conversation_id,
            "turn_count": 0,
            "is_first_turn": True,
            "thought_count": 0,
            "step_count": 0,
            "max_iterations": max_iterations,
            "pending_feedback": None,
            "history": [],
            "trace": [],
            "steps": [],
            "waiting_choice": False,
            "last_choice": None,
            "last_thought": "",
            "last_response": "",
            "phase": "active",
            "started_at": _now_iso(),
            "last_progress_at": _now_iso(),
            "stop_reason": "",
            "budget_exhausted": False,
            "continuation_count": 0,
            "tool_usage": [],
        }
    )
    return task_loop


def record_user_turn(hass: HomeAssistant, *, text: str) -> dict[str, Any]:

    task_loop = reset_execution_control_for_turn(hass)
    task_loop["is_first_turn"] = int(task_loop.get("turn_count", 0)) == 0
    task_loop["turn_count"] = int(task_loop.get("turn_count", 0)) + 1
    task_loop["last_progress_at"] = _now_iso()
    task_loop["phase"] = "active"
    task_loop["history"].append({"role": "user", "content": text})
    task_loop.setdefault("trace", []).append(
        {
            "kind": "user_turn",
            "turn": task_loop["turn_count"],
            "timestamp": task_loop["last_progress_at"],
            "text": text[:300],
        }
    )
    _trim_list(task_loop["history"], _MAX_HISTORY_ENTRIES)
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
    return task_loop


def reset_execution_control_for_turn(hass: HomeAssistant) -> dict[str, Any]:
    task_loop = get_task_loop_state(hass)
    task_loop["thought_count"] = 0
    task_loop["step_count"] = 0
    task_loop["steps"] = []
    task_loop["tool_usage"] = []
    task_loop["budget_exhausted"] = False
    task_loop["continuation_count"] = 0
    task_loop["stop_reason"] = ""
    return task_loop


def record_kernel_step(
    hass: HomeAssistant,
    *,
    kind: str,
    title: str,
    explanation: str,
    tool_name: str = "",
    tool_args: dict[str, Any] | None = None,
) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
    task_loop["step_count"] = int(task_loop.get("step_count", 0)) + 1
    task_loop["last_progress_at"] = _now_iso()
    task_loop["phase"] = "kernel_execute"
    step = {
        "index": task_loop["step_count"],
        "kind": kind,
        "title": title[:160],
        "explanation": explanation[:400],
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "status": "pending",
        "observation": "",
        "timestamp": task_loop["last_progress_at"],
    }
    task_loop.setdefault("steps", []).append(step)
    task_loop.setdefault("trace", []).append(
        {
            "kind": "kernel_step",
            "index": step["index"],
            "timestamp": step["timestamp"],
            "title": step["title"],
            "tool_name": tool_name,
        }
    )
    _trim_list(task_loop["steps"], _MAX_TRACE_ENTRIES)
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
    return step


def finalize_kernel_step(
    hass: HomeAssistant,
    *,
    success: bool,
    observation: str,
) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
    steps = task_loop.setdefault("steps", [])
    if not steps:
        return task_loop

    step = steps[-1]
    step["status"] = "done" if success else "failed"
    step["observation"] = observation[:800]
    task_loop["last_progress_at"] = _now_iso()
    task_loop.setdefault("trace", []).append(
        {
            "kind": "kernel_observation",
            "index": step.get("index", 0),
            "timestamp": task_loop["last_progress_at"],
            "success": success,
            "observation": step["observation"],
        }
    )
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
    return task_loop


def record_thought(
    hass: HomeAssistant,
    *,
    thought: str,
    next_action: str = "",
    stop: bool = False,
) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
    if stop:
        task_loop["active"] = False
        task_loop["phase"] = "stopped"
        task_loop["stop_reason"] = thought
        task_loop["last_thought"] = thought
        task_loop["last_progress_at"] = _now_iso()
        task_loop.setdefault("trace", []).append(
            {
                "kind": "stop",
                "timestamp": task_loop["last_progress_at"],
                "thought": thought[:300],
            }
        )
        _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
        return task_loop

    task_loop["thought_count"] = int(task_loop.get("thought_count", 0)) + 1
    task_loop["last_thought"] = thought
    task_loop["last_progress_at"] = _now_iso()
    task_loop["phase"] = "active"
    task_loop.setdefault("trace", []).append(
        {
            "kind": "thought",
            "index": task_loop["thought_count"],
            "timestamp": task_loop["last_progress_at"],
            "thought": thought[:300],
            "next_action": next_action[:200],
        }
    )
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
    task_loop["budget_exhausted"] = (
        int(task_loop["thought_count"]) >= int(task_loop.get("max_iterations", 50))
    )
    return task_loop


def record_response(hass: HomeAssistant, *, response_text: str, agent_id: str) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
    task_loop["last_response"] = response_text
    task_loop["last_progress_at"] = _now_iso()
    task_loop.setdefault("trace", []).append(
        {
            "kind": "assistant_response",
            "timestamp": task_loop["last_progress_at"],
            "agent_id": agent_id,
            "response": response_text[:400],
        }
    )
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)
    return task_loop


def reset_continuation_count(hass: HomeAssistant) -> None:

    task_loop = get_task_loop_state(hass)
    task_loop["continuation_count"] = 0


def record_continuation(
    hass: HomeAssistant,
    *,
    thought: str,
    continuation_index: int,
) -> bool:

    task_loop = get_task_loop_state(hass)
    count = int(task_loop.get("continuation_count", 0)) + 1
    task_loop["continuation_count"] = count


    task_loop["thought_count"] = int(task_loop.get("thought_count", 0)) + 1
    task_loop["last_thought"] = thought
    task_loop["last_progress_at"] = _now_iso()

    task_loop["history"].append({"role": "thinking", "content": thought})
    _trim_list(task_loop["history"], _MAX_HISTORY_ENTRIES)

    task_loop.setdefault("trace", []).append(
        {
            "kind": "continuation",
            "index": continuation_index + 1,
            "timestamp": task_loop["last_progress_at"],
            "thought": thought[:400],
        }
    )
    _trim_list(task_loop["trace"], _MAX_TRACE_ENTRIES)


    task_loop["budget_exhausted"] = (
        int(task_loop["thought_count"])
        >= int(task_loop.get("max_iterations", 50))
    )
    if task_loop["budget_exhausted"]:
        return False


    return count < DEFAULT_THRESHOLDS.max_continuations_per_turn


TOOL_REPEAT_BAIL_PROMPT = (
    "[MANDATORY — TOOL REPEAT LIMIT REACHED]\n"
    "Tool '{tool_name}' has been called {count} times (limit: {limit}). "
    "This approach is NOT working. You MUST NOT call '{tool_name}' again for this task.\n"
    "Options:\n"
    "1. Analyze WHY the repeated calls failed and try a fundamentally DIFFERENT approach using other tools.\n"
    "2. If no viable alternative exists, respond with kind=final and honestly explain to the user "
    "what you attempted, why it did not work, and suggest what they could try manually.\n"
    "You decide. Do NOT repeat the same tool."
)

IDENTICAL_CALL_WARN_PROMPT = (
    "[WARNING — IDENTICAL TOOL CALLS DETECTED]\n"
    "You have called '{tool_name}' with IDENTICAL arguments {count} times. "
    "This is clearly not working. STOP and try a DIFFERENT approach or different parameters.\n"
    "Do NOT repeat the same call again."
)

IDENTICAL_CALL_STOP_PROMPT = (
    "[LOOP DETECTED — PLEASE STOP]\n"
    "Tool '{tool_name}' has been called {count} times with identical arguments.\n"
    "This indicates a loop that cannot be resolved automatically.\n"
    "Please respond to the user with kind=final, explain what you tried, "
    "why it didn't work, and suggest what they could do manually.\n"
    "Do NOT call any more tools. Just reply to the user politely."
)

PARALLEL_REPEAT_STOP_PROMPT = (
    "[PARALLEL TOOL LIMIT REACHED — STOP TOOLING]\n"
    "ParallelToolCall has already been invoked {count} times (limit: {limit}).\n"
    "Do NOT bypass this by splitting into one-by-one sequential tool calls.\n"
    "At this point, stop tool execution and respond with kind=final: explain "
    "what was attempted, why it still cannot be completed automatically, and "
    "what the user can do next."
)


def _args_signature(tool_args: dict | None) -> str:
    import json
    return json.dumps(tool_args or {}, sort_keys=True, ensure_ascii=False)


def get_execution_control_limits(hass: HomeAssistant) -> dict[str, int]:
    max_repeat = DEFAULT_MAX_TOOL_REPEAT
    identical_warn = DEFAULT_IDENTICAL_CALL_WARN
    identical_stop = DEFAULT_IDENTICAL_CALL_STOP
    for entry in hass.config_entries.async_entries(DOMAIN):
        max_repeat = int(entry.options.get(CONF_MAX_TOOL_REPEAT, max_repeat))
        identical_warn = int(entry.options.get(CONF_IDENTICAL_CALL_WARN, identical_warn))
        identical_stop = int(entry.options.get(CONF_IDENTICAL_CALL_STOP, identical_stop))
        break
    return {
        "max_tool_repeat": max(1, max_repeat),
        "identical_call_warn": max(1, identical_warn),
        "identical_call_stop": max(1, identical_stop),
    }


def get_configured_pipeline_timeout(hass: HomeAssistant) -> int:
    timeout = DEFAULT_PIPELINE_TIMEOUT
    for entry in hass.config_entries.async_entries(DOMAIN):
        timeout = int(entry.options.get(CONF_PIPELINE_TIMEOUT, timeout))
        break
    return max(1, timeout)


def record_tool_usage(
    hass: HomeAssistant,
    *,
    tool_name: str,
    tool_args: dict | None = None,
    success: bool | None = None,
    blocked: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    task_loop = get_task_loop_state(hass)
    usage = task_loop.setdefault("tool_usage", [])
    record = {
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "success": success,
        "blocked": blocked,
        "error": error or "",
        "timestamp": _now_iso(),
    }
    usage.append(record)
    _trim_list(usage, _MAX_TOOL_USAGE_ENTRIES)
    return record


def check_tool_repeat(
    hass: HomeAssistant,
    *,
    tool_name: str,
    tool_args: dict | None = None,
    max_repeat: int,
    identical_warn: int = 10,
    identical_stop: int = 20,
) -> tuple[str | None, bool]:
    task_loop = get_task_loop_state(hass)
    records = [
        *task_loop.get("steps", []),
        *task_loop.get("tool_usage", []),
    ]

    same_name_count = sum(1 for s in records if s.get("tool_name") == tool_name) + 1

    current_sig = _args_signature(tool_args)
    identical_count = sum(
        1 for s in records
        if s.get("tool_name") == tool_name and _args_signature(s.get("tool_args")) == current_sig
    ) + 1

    if identical_count >= identical_stop:
        return IDENTICAL_CALL_STOP_PROMPT.format(tool_name=tool_name, count=identical_count), True

    if identical_count >= identical_warn:
        return IDENTICAL_CALL_WARN_PROMPT.format(tool_name=tool_name, count=identical_count), False

    if tool_name == "ParallelToolCall" and same_name_count >= max_repeat:
        return PARALLEL_REPEAT_STOP_PROMPT.format(
            count=same_name_count, limit=max_repeat
        ), True

    if same_name_count >= max_repeat:
        return TOOL_REPEAT_BAIL_PROMPT.format(
            tool_name=tool_name, count=same_name_count, limit=max_repeat,
        ), False

    return None, False


def get_loop_status(hass: HomeAssistant) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
    max_iterations = int(task_loop.get("max_iterations", 50))
    thought_count = int(task_loop.get("thought_count", 0))
    remaining = max(max_iterations - thought_count, 0)
    return {
        "active": bool(task_loop.get("active", False)),
        "phase": task_loop.get("phase", "idle"),
        "turn_count": int(task_loop.get("turn_count", 0)),
        "thought_count": thought_count,
        "step_count": int(task_loop.get("step_count", 0)),
        "max_iterations": max_iterations,
        "remaining_budget": remaining,
        "budget_exhausted": bool(task_loop.get("budget_exhausted", False)),
        "waiting_choice": bool(task_loop.get("waiting_choice", False)),
        "continuation_count": int(task_loop.get("continuation_count", 0)),
        "stop_reason": task_loop.get("stop_reason", ""),
    }
