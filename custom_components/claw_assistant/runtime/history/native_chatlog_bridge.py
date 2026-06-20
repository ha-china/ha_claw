

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncGenerator
from typing import Any

from homeassistant.components.conversation.chat_log import (
    AssistantContent,
    ToolResultContent,
    current_chat_log,
)
from homeassistant.helpers import llm

from ..core.state import get_conversation_status, get_tool_results_state

_KM_STEP_AGENT_MARKER = "__km_step__"
_STEP_BARRIER_DELAY_SECONDS = 0.05


def _latest_turn_contents(chat_log) -> list[Any]:
    last_user_index = -1
    for index, content in enumerate(chat_log.content):
        if getattr(content, "role", None) == "user":
            last_user_index = index
    return chat_log.content[last_user_index + 1 :]


def _turn_has_native_steps(turn_contents: list[Any]) -> bool:
    for content in turn_contents:
        if isinstance(content, ToolResultContent):
            return True
        if isinstance(content, AssistantContent) and (
            content.thinking_content or content.tool_calls
        ):
            return True
    return False


def _turn_only_has_plain_assistant(turn_contents: list[Any]) -> bool:
    return bool(turn_contents) and all(
        isinstance(content, AssistantContent)
        and bool(content.content)
        and not content.thinking_content
        and not content.tool_calls
        for content in turn_contents
    )


def _turn_has_final_assistant_content(turn_contents: list[Any]) -> bool:

    if not turn_contents:
        return False

    last_content = turn_contents[-1]
    return (
        isinstance(last_content, AssistantContent)
        and bool(last_content.content)
        and not last_content.thinking_content
        and not last_content.tool_calls
    )


def _turn_has_assistant_text(turn_contents: list[Any], text: str) -> bool:
    target = text.strip()
    if not target:
        return False
    for content in turn_contents:
        if (
            isinstance(content, AssistantContent)
            and (content.content or "").strip() == target
        ):
            return True
    return False


def _build_external_tool_inputs(tool_results: list[dict[str, Any]]) -> list[llm.ToolInput]:
    tool_inputs: list[llm.ToolInput] = []
    for index, item in enumerate(tool_results):
        tool_inputs.append(
            llm.ToolInput(
                id=f"km_tool_{index}",
                tool_name=str(item.get("tool_name", "tool")),
                tool_args=dict(item.get("tool_args") or {}),
                external=True,
            )
        )
    return tool_inputs


async def _delta_stream(
    *,
    thought: str,
    tool_results: list[dict[str, Any]],
) -> AsyncGenerator[dict[str, Any]]:
    if thought or tool_results:
        yield {"role": "assistant"}
        if thought:
            yield {"thinking_content": thought}
        if tool_results:
            yield {"tool_calls": _build_external_tool_inputs(tool_results)}
            for index, item in enumerate(tool_results):
                yield {
                    "role": "tool_result",
                    "tool_call_id": f"km_tool_{index}",
                    "tool_name": str(item.get("tool_name", "tool")),
                    "tool_result": {
                        "success": bool(item.get("success", False)),
                        **({"error": item["error"]} if item.get("error") else {}),
                        **({"result": item["result"]} if item.get("result") is not None else {}),
                    },
                }


async def _final_content_stream(
    response_text: str,
) -> AsyncGenerator[dict[str, Any]]:
    yield {"role": "assistant"}
    if response_text:
        yield {"content": response_text}


async def async_bridge_native_chatlog_turn(
    hass,
    *,
    agent_id: str,
    response_text: str,
) -> bool:

    chat_log = current_chat_log.get()
    if chat_log is None:
        return False

    turn_contents = _latest_turn_contents(chat_log)
    if _turn_has_assistant_text(turn_contents, response_text):
        return False
    if _turn_has_native_steps(turn_contents):
        return False

    tool_results = copy.deepcopy(get_tool_results_state(hass))
    thought = str(get_conversation_status(hass).get("current_thought") or "").strip()
    if not thought and not tool_results and turn_contents:
        return False

    if _turn_only_has_plain_assistant(turn_contents):
        del chat_log.content[-len(turn_contents) :]

    async for _ in chat_log.async_add_delta_content_stream(
        agent_id,
        _delta_stream(
            thought=thought,
            tool_results=tool_results,
        ),
    ):
        pass

    if response_text:
        async for _ in chat_log.async_add_delta_content_stream(
            agent_id,
            _final_content_stream(response_text),
        ):
            pass
    return True


def reset_live_delta_state(hass) -> None:

    status = get_conversation_status(hass)
    status["live_tool_call_ids_emitted"] = []


def build_step_agent_id(agent_id: str, *, step_index: int, phase: str) -> str:

    return f"{agent_id}{_KM_STEP_AGENT_MARKER}{step_index}_{phase}"


def is_step_agent_id(agent_id: str | None) -> bool:

    return bool(agent_id and _KM_STEP_AGENT_MARKER in agent_id)

async def _emit_delta_stream(agent_id: str, deltas: list[dict[str, Any]]) -> bool:
    chat_log = current_chat_log.get()
    if chat_log is None:
        return False

    async def _stream():
        for delta in deltas:
            yield delta

    async for _ in chat_log.async_add_delta_content_stream(agent_id, _stream()):
        pass
    return True


async def emit_live_thinking_delta(hass, *, agent_id: str, thought: str) -> bool:

    if not thought:
        return False
    return await _emit_delta_stream(
        agent_id,
        [{"role": "assistant"}, {"thinking_content": thought}],
    )


async def emit_live_content_delta(*, agent_id: str, text: str) -> bool:

    if not text:
        return False
    chat_log = current_chat_log.get()
    if chat_log is None:
        return False
    if not chat_log.delta_listener:
        return False
    chat_log.delta_listener(chat_log, {"role": "assistant"})
    chunk = ""
    for char in text:
        chunk += char
        if len(chunk) < 6 and char not in " \n，。！？,.!?;；:：":
            continue
        chat_log.delta_listener(chat_log, {"content": chunk})
        chunk = ""
        await asyncio.sleep(0.01)
    if chunk:
        chat_log.delta_listener(chat_log, {"content": chunk})
    return True


async def emit_live_step_delta(
    *,
    agent_id: str,
    step_index: int,
    phase: str,
    title: str,
    detail: str = "",
) -> bool:

    if not title:
        return False
    return await _emit_delta_stream(
        agent_id,
        [
            {
                "role": "assistant",
                "km_step_event": {
                    "index": step_index,
                    "phase": phase,
                    "title": title,
                    "detail": detail,
                },
            }
        ],
    )


def append_step_message(
    *,
    agent_id: str,
    step_index: int,
    phase: str,
    content: str,
) -> bool:

    if not content:
        return False
    chat_log = current_chat_log.get()
    if chat_log is None:
        return False

    chat_log.async_add_assistant_content_without_tools(
        AssistantContent(
            agent_id=build_step_agent_id(agent_id, step_index=step_index, phase=phase),
            content=content,
        )
    )
    return True


async def append_step_message_and_pause(
    *,
    agent_id: str,
    step_index: int,
    phase: str,
    content: str,
) -> bool:

    appended = append_step_message(
        agent_id=agent_id,
        step_index=step_index,
        phase=phase,
        content=content,
    )
    if appended:
        await asyncio.sleep(_STEP_BARRIER_DELAY_SECONDS)
    return appended


def append_final_message(*, agent_id: str, content: str) -> bool:

    if not content:
        return False
    chat_log = current_chat_log.get()
    if chat_log is None:
        return False
    turn_contents = _latest_turn_contents(chat_log)
    if _turn_has_assistant_text(turn_contents, content):
        return False
    if chat_log.content:
        last = chat_log.content[-1]
        if (
            isinstance(last, AssistantContent)
            and last.agent_id == agent_id
            and (last.content or "").strip() == content.strip()
        ):
            return False

    chat_log.async_add_assistant_content_without_tools(
        AssistantContent(
            agent_id=agent_id,
            content=content,
        )
    )
    return True


async def append_final_message_and_pause(*, agent_id: str, content: str) -> bool:

    appended = append_final_message(agent_id=agent_id, content=content)
    if appended:
        await asyncio.sleep(0)
    return appended


def discard_last_planner_message(*, agent_id: str, content: str) -> bool:

    chat_log = current_chat_log.get()
    if chat_log is None or not chat_log.content:
        return False

    last = chat_log.content[-1]
    if (
        isinstance(last, AssistantContent)
        and last.agent_id == agent_id
        and (last.content or "").strip() == content.strip()
    ):
        chat_log.content.pop()
        return True
    return False


async def emit_live_tool_call_delta(
    hass,
    *,
    agent_id: str,
    tool_input: llm.ToolInput,
) -> bool:

    status = get_conversation_status(hass)
    emitted_ids = set(status.get("live_tool_call_ids_emitted", []))
    if tool_input.id in emitted_ids:
        return False

    emitted = await _emit_delta_stream(
        agent_id,
        [
            {"role": "assistant"},
            {
                "tool_calls": [
                    llm.ToolInput(
                        id=tool_input.id,
                        tool_name=tool_input.tool_name,
                        tool_args=dict(tool_input.tool_args),
                        external=True,
                    )
                ]
            },
        ],
    )
    if emitted:
        status.setdefault("live_tool_call_ids_emitted", []).append(tool_input.id)
    return emitted


async def emit_live_tool_result_delta(
    *,
    agent_id: str,
    tool_input: llm.ToolInput,
    tool_result: dict[str, Any],
) -> bool:

    return await _emit_delta_stream(
        agent_id,
        [
            {
                "role": "tool_result",
                "tool_call_id": tool_input.id,
                "tool_name": tool_input.tool_name,
                "tool_result": tool_result,
            }
        ],
    )
