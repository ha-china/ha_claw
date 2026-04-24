

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant

from .config import DEFAULT_THRESHOLDS
from .state import get_task_loop_state

_MAX_TRACE_ENTRIES = 200
_MAX_HISTORY_ENTRIES = 50


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

    task_loop = get_task_loop_state(hass)
    task_loop.clear()
    task_loop.update(
        {
            "active": True,
            "conversation_id": conversation_id,
            "turn_count": 0,
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
        }
    )
    return task_loop


def record_user_turn(hass: HomeAssistant, *, text: str) -> dict[str, Any]:

    task_loop = get_task_loop_state(hass)
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


def check_tool_repeat(
    hass: HomeAssistant,
    *,
    tool_name: str,
    max_repeat: int,
) -> str | None:
    task_loop = get_task_loop_state(hass)
    steps = task_loop.get("steps", [])
    count = sum(1 for s in steps if s.get("tool_name") == tool_name)
    if count < max_repeat:
        return None
    return TOOL_REPEAT_BAIL_PROMPT.format(
        tool_name=tool_name, count=count, limit=max_repeat,
    )


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
