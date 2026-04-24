

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.helpers import intent

from .internal_llm import reset_runtime_tool_mode, set_runtime_tool_mode
from .loop_controller import check_tool_repeat, finalize_kernel_step, record_kernel_step
from .native_chatlog_bridge import (
    append_final_message_and_pause,
    append_step_message_and_pause,
    discard_last_planner_message,
)
from .prompting import _fit_base_prompt
from .response_format import get_response_text
from .state import get_conversation_status, get_tool_calls_state, get_tool_results_state
from .step_protocol import AgentStep, StepProtocolError, parse_agent_step, render_step_contract
from .tool_executor import execute_kernel_tool, list_kernel_tool_specs

LOGGER = logging.getLogger(__name__)

_MAX_KERNEL_STEPS = 8


def _append_prompt(base: str | None, extra: str) -> str:
    return _fit_base_prompt(base or "", [extra])


def _step_fingerprint(step: AgentStep) -> str:
    if step.kind != "call_tool":
        return step.kind
    return f"{step.tool_name}:{json.dumps(step.tool_args, ensure_ascii=False, sort_keys=True)}"


def _render_tool_catalog() -> str:
    lines = ["## Allowed Runtime Tools"]
    for spec in list_kernel_tool_specs():
        lines.append(
            f'- {spec["name"]} ({spec["category"]}): {spec["description"]}'
        )
    return "\n".join(lines)


def _render_completed_steps(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "## Completed Steps\n- none yet"

    lines = ["## Completed Steps"]
    for step in steps[-8:]:
        title = step.get("title") or step.get("tool_name") or step.get("kind")
        status = step.get("status", "unknown")
        observation = str(step.get("observation", "")).strip()
        lines.append(f'- Step {step.get("index", "?")}: {title} [{status}]')
        if observation:
            lines.append(f"  Observation: {observation[:500]}")
    return "\n".join(lines)


def _render_planner_rules(
    *,
    user_text: str,
    steps: list[dict[str, Any]],
) -> str:
    tried = sorted(
        {
            step.get("fingerprint", "")
            for step in steps
            if step.get("fingerprint")
        }
    )
    tried_block = "\n".join(f"- {item}" for item in tried) if tried else "- none yet"
    return (
        "## Kernel Planner Rules\n"
        f'Current user goal: "{user_text}"\n'
        "- Decide only the next best action.\n"
        "- Use one tool per iteration at most.\n"
        "- If enough evidence already exists, return final.\n"
        "- If the user must answer a question, return ask_user.\n"
        "- If repeated attempts are failing, return stop or final.\n"
        "## Previously Attempted Actions\n"
        f"{tried_block}"
    )


def _render_final_answer(final_answer: str, steps: list[dict[str, Any]]) -> str:
    lines = ["Execution process:"]
    for step in steps:
        title = step.get("title") or step.get("tool_name") or step.get("kind")
        lines.append(f'{step.get("index", "?")}. {title}')
        explanation = str(step.get("explanation", "")).strip()
        observation = str(step.get("observation", "")).strip()
        if explanation:
            lines.append(f"Explanation: {explanation}")
        if observation:
            lines.append(f"Result: {observation}")
    lines.append("")
    lines.append("Conclusion:")
    lines.append(final_answer.strip())
    return "\n".join(line for line in lines if line is not None).strip()


def _render_step_message(step: AgentStep, index: int) -> str:
    lines = [f"Step {index}: {step.step_title}"]
    if step.step_explanation:
        lines.append(step.step_explanation)
    if step.expected_output:
        lines.append(f"Expected: {step.expected_output}")
    return "\n".join(lines)


def _render_observation_message(index: int, title: str, observation: str) -> str:
    lines = [f"Step {index} result: {title}"]
    if observation:
        lines.append(observation)
    return "\n".join(lines)


def _finalize_result(result: Any, final_text: str) -> Any:

    response = getattr(result, "response", None)
    if response is None:
        return result
    response.async_set_speech(final_text)
    response.response_type = intent.IntentResponseType.ACTION_DONE
    return result


async def execute_kernel_turn(
    hass,
    *,
    original_async_converse,
    user_text: str,
    conversation_id,
    context,
    language,
    agent_id: str | None,
    device_id,
    satellite_id,
    extra_system_prompt: str | None,
) -> Any | None:

    agent_id = agent_id or "conversation.home_assistant"
    tool_calls_state = get_tool_calls_state(hass)
    tool_results_state = get_tool_results_state(hass)
    tool_calls_state.clear()
    tool_results_state.clear()

    steps: list[dict[str, Any]] = []
    last_result = None
    get_conversation_status(hass)["kernel_mode_active"] = True

    try:
        for step_index in range(1, _MAX_KERNEL_STEPS + 1):
            kernel_prompt = _append_prompt(
                extra_system_prompt,
                "\n\n".join(
                    [
                        render_step_contract(),
                        _render_tool_catalog(),
                        _render_completed_steps(steps),
                        _render_planner_rules(user_text=user_text, steps=steps),
                    ]
                ),
            )

            tool_mode_token = set_runtime_tool_mode("kernel")
            try:
                result = await original_async_converse(
                    hass,
                    user_text,
                    conversation_id,
                    context,
                    language,
                    agent_id,
                    device_id,
                    satellite_id,
                    kernel_prompt,
                )
            finally:
                reset_runtime_tool_mode(tool_mode_token)

            last_result = result
            planner_text = get_response_text(result)
            discard_last_planner_message(agent_id=agent_id, content=planner_text)
            try:
                step = parse_agent_step(planner_text)
            except StepProtocolError as err:
                LOGGER.info("Kernel planner returned invalid step: %s", err)
                if not steps:
                    return None
                final_text = _render_final_answer(
                    f"Completed partial steps, but planner output was invalid: {err}",
                    steps,
                )
                await append_final_message_and_pause(agent_id=agent_id, content=final_text)
                return _finalize_result(result, final_text)

            if step.kind == "final":
                final_text = _render_final_answer(step.final_answer, steps)
                await append_final_message_and_pause(agent_id=agent_id, content=final_text)
                return _finalize_result(result, final_text)

            if step.kind == "ask_user":
                await append_final_message_and_pause(agent_id=agent_id, content=step.user_question)
                return _finalize_result(result, step.user_question)

            if step.kind == "stop":
                await append_final_message_and_pause(agent_id=agent_id, content=step.stop_reason)
                return _finalize_result(result, step.stop_reason)

            fingerprint = _step_fingerprint(step)
            if any(existing.get("fingerprint") == fingerprint for existing in steps):
                final_text = _render_final_answer(
                    "Planner attempted to repeat the same action; stopping loop and concluding based on existing information.",
                    steps,
                )
                await append_final_message_and_pause(agent_id=agent_id, content=final_text)
                return _finalize_result(result, final_text)

            if step.tool_name:
                from ..const import CONF_MAX_TOOL_REPEAT, DEFAULT_MAX_TOOL_REPEAT
                max_repeat = DEFAULT_MAX_TOOL_REPEAT
                for entry in hass.config_entries.async_entries("claw_assistant"):
                    max_repeat = int(entry.options.get(CONF_MAX_TOOL_REPEAT, DEFAULT_MAX_TOOL_REPEAT))
                    break
                bail_prompt = check_tool_repeat(hass, tool_name=step.tool_name, max_repeat=max_repeat)
                if bail_prompt:
                    LOGGER.warning(
                        "Tool repeat limit reached: %s called %d+ times (limit %d)",
                        step.tool_name, max_repeat, max_repeat,
                    )
                    extra_system_prompt = _append_prompt(extra_system_prompt, bail_prompt)
                    continue

            record = record_kernel_step(
                hass,
                kind=step.kind,
                title=step.step_title,
                explanation=step.step_explanation,
                tool_name=step.tool_name,
                tool_args=step.tool_args,
            )
            record["expected_output"] = step.expected_output
            record["fingerprint"] = fingerprint
            await append_step_message_and_pause(
                agent_id=agent_id,
                step_index=step_index,
                phase="start",
                content=_render_step_message(step, step_index),
            )

            tool_result = await execute_kernel_tool(
                hass,
                tool_name=step.tool_name,
                tool_args=step.tool_args,
                agent_id=agent_id,
                context=context,
                language=language,
                device_id=device_id,
            )
            tool_calls_state.append(step.tool_name)
            tool_results_state.append(
                {
                    "tool_name": tool_result["tool_name"],
                    "tool_args": tool_result["tool_args"],
                    "success": tool_result["success"],
                    "error": tool_result["error"],
                    "result": tool_result["result"],
                }
            )

            observation = str(tool_result.get("summary") or "").strip()
            if not observation:
                observation = (
                    f'{tool_result["tool_name"]} succeeded'
                    if tool_result.get("success", False)
                    else f'{tool_result["tool_name"]} failed'
                )
            finalize_kernel_step(
                hass,
                success=bool(tool_result.get("success", False)),
                observation=observation,
            )
            record["status"] = "done" if tool_result.get("success", False) else "failed"
            record["observation"] = observation
            await append_step_message_and_pause(
                agent_id=agent_id,
                step_index=step_index,
                phase="observation",
                content=_render_observation_message(step_index, step.step_title, observation),
            )
            steps.append(record)

        if last_result is None:
            return None

        final_text = _render_final_answer(
            "Step budget reached; concluding based on completed steps.",
            steps,
        )
        await append_final_message_and_pause(agent_id=agent_id, content=final_text)
        return _finalize_result(last_result, final_text)
    finally:
        get_conversation_status(hass)["kernel_mode_active"] = False
