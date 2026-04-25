

from __future__ import annotations

from collections.abc import Callable
import copy
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ..const import CONVERSATION_MODE_ADD_NAME, CONVERSATION_MODE_DETAILED
from ..conversation_utils import detect_user_ending_intent
from .adaptive_memory import (
    async_record_agent_failure,
    async_record_agent_success,
    is_known_incompatible_agent,
    prioritize_agents,
    should_temporarily_skip_agent,
)
from .events import fire_ai_response
from .i18n import t
from .internal_llm import reset_runtime_tool_mode, set_runtime_tool_mode
from .loop_controller import record_response
from .native_chatlog_bridge import async_bridge_native_chatlog_turn
from .prompting import _fit_base_prompt
from .response_format import (
    apply_agent_response_format,
    get_response_text,
    sanitize_response_text,
)
from .response_policy import is_user_done_text
from .signal_capture import async_capture_passive_signal
from .state import (
    consume_next_agent_handoff,
    get_conversation_status,
    get_task_loop_state,
    get_tool_calls_state,
    get_tool_results_state,
    set_current_thought,
)
from .tool_result_summary import NON_USER_FACING_TOOLS, extract_successful_tool_response

LOGGER = logging.getLogger(__name__)


def _snapshot_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:

    return copy.deepcopy(tool_results)


def _summarize_tool_failures(tool_results: list[dict[str, Any]]) -> str:
    failed_tools = [item for item in tool_results if not item.get("success", True)]
    if not failed_tools:
        return "error_response"

    details: list[str] = []
    for item in failed_tools[:3]:
        tool_name = item.get("tool_name", "unknown")
        error = item.get("error") or "tool_failure"
        result = item.get("result")
        if isinstance(result, dict) and result.get("missing_target"):
            error = f"{error}; recoverable=resolve_target_and_retry"
        details.append(f"{tool_name}:{error}")
    return "tool_failure:" + "; ".join(details)


def _build_synthesized_result(
    *,
    language: str | None,
    conversation_id,
    response_text: str,
):

    from homeassistant.components.conversation import ConversationResult

    intent_response = intent.IntentResponse(language=language)
    intent_response.async_set_speech(response_text)
    intent_response.response_type = intent.IntentResponseType.ACTION_DONE
    intent_response.error_code = None
    return ConversationResult(
        response=intent_response,
        conversation_id=conversation_id,
    )


async def _finalize_synthesized_success(
    hass: HomeAssistant,
    *,
    result: Any,
    agent_id: str,
    agent_name: str,
    response_text: str,
    conversation_mode: str,
    conversation_id,
    original_text: str,
    user_text: str,
    conv_history,
    task_loop: dict[str, Any],
) -> Any:

    await async_bridge_native_chatlog_turn(
        hass,
        agent_id=agent_id,
        response_text=response_text,
    )
    task_loop["history"].append({"role": "assistant", "content": response_text})
    record_response(
        hass,
        response_text=response_text,
        agent_id=agent_id,
    )
    tool_calls = list(get_tool_calls_state(hass))
    conv_history.add_turn(
        conversation_id or "default",
        original_text,
        response_text,
        tool_calls=tool_calls,
    )
    await async_capture_passive_signal(
        hass,
        user_text=original_text,
        assistant_text=response_text,
        tool_calls=tool_calls,
        conversation_id=conversation_id,
    )
    tool_calls_state = get_tool_calls_state(hass)
    tool_calls_state.clear()
    fire_ai_response(
        hass,
        response=response_text,
        user_request=user_text,
        conversation_id=conversation_id,
        iteration=1,
        agent_id=agent_id,
    )
    apply_agent_response_format(
        result,
        hass=hass,
        agent_name=agent_name,
        agent_id=agent_id,
        conversation_mode=conversation_mode,
        response_text=response_text,
    )
    if getattr(result, "response", None) and hasattr(result.response, "response_type"):
        result.response.response_type = intent.IntentResponseType.ACTION_DONE
    if getattr(result, "response", None) and hasattr(result.response, "error_code"):
        result.response.error_code = None
    result.continue_conversation = not is_user_done_text(
        user_text, detect_user_ending_intent
    )
    set_current_thought(hass, None)
    return result


def _extract_response_error_reason(result: Any) -> str:

    if not result or not getattr(result, "response", None):
        return "missing_response"

    response = result.response
    speech = getattr(response, "speech", None) or {}
    plain = speech.get("plain", {}) if isinstance(speech, dict) else {}
    message = plain.get("speech") if isinstance(plain, dict) else None
    error_code = getattr(response, "error_code", None)

    if message:
        return str(message)
    if error_code:
        return str(error_code)

    data = None
    if hasattr(response, "as_dict"):
        try:
            data = response.as_dict()
        except Exception:
            data = None
    if isinstance(data, dict):
        speech = data.get("speech", {})
        plain = speech.get("plain", {}) if isinstance(speech, dict) else {}
        if isinstance(plain, dict) and plain.get("speech"):
            return str(plain["speech"])
        response_data = data.get("data", {})
        if isinstance(response_data, dict) and response_data.get("code"):
            return str(response_data["code"])

    return "error_response"


def _append_prompt(base_prompt: str | None, extra_block: str) -> str:
    return _fit_base_prompt(base_prompt or "", [extra_block])


def _find_previous_agent_id(
    task_loop: dict[str, Any],
    *,
    exclude_agent_id: str,
) -> str:
    for item in reversed(task_loop.get("trace", [])):
        if item.get("kind") != "assistant_response":
            continue
        agent_id = str(item.get("agent_id", "") or "")
        if agent_id and agent_id != exclude_agent_id:
            return agent_id
    return ""


def _resolve_handoff_target(
    *,
    direction: str,
    current_agent_id: str,
    remaining_agents: list[str],
    task_loop: dict[str, Any],
) -> str:
    if direction == "previous":
        return _find_previous_agent_id(task_loop, exclude_agent_id=current_agent_id)
    return remaining_agents[0] if remaining_agents else ""


def _build_next_agent_handoff_prompt(
    *,
    previous_agent_name: str,
    previous_response_text: str,
    reason: str = "",
    handoff_intent: str = "request",
    expected_action: str = "reply",
    task_summary: str = "",
) -> str:
    _INTENT_LABELS = {
        "request": "needs you to take action",
        "consult": "wants your opinion or analysis",
        "notify": "is informing you (FYI, no action required unless you see a problem)",
        "handback": "is returning results from a task you delegated",
    }
    _ACTION_LABELS = {
        "reply": "Answer the user directly based on the context below.",
        "execute": "Execute the described task using your tools, then report the result.",
        "review": "Review the previous AI's work, correct if needed, then present to the user.",
        "continue": "Continue where the previous AI left off.",
    }
    intent_desc = _INTENT_LABELS.get(handoff_intent, _INTENT_LABELS["request"])
    action_desc = _ACTION_LABELS.get(expected_action, _ACTION_LABELS["reply"])

    lines = [
        "## Agent Handoff",
        f"From: {previous_agent_name}",
        f"Intent: {handoff_intent} — {intent_desc}",
        f"Expected action: {expected_action} — {action_desc}",
    ]
    if task_summary:
        lines.append(f"Task: {task_summary}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.extend([
        "",
        "### Context from previous AI:",
        previous_response_text,
        "",
        "### Instructions:",
        action_desc,
        "Do not ask the user to repeat themselves — all context is above.",
        "Do not call AgentHandoff again unless the user explicitly asks for another handoff after you answer.",
    ])
    return "\n".join(lines)


def make_agent_name_getter(hass: HomeAssistant) -> Callable[[str], str]:

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)

    def get_agent_name(agent_id: str) -> str:
        ent = ent_reg.async_get(agent_id)
        if ent and ent.name:
            return ent.name
        if ent and ent.original_name:
            return ent.original_name
        state = hass.states.get(agent_id)
        if state:
            return state.attributes.get("friendly_name", agent_id.split(".")[-1])
        return agent_id.split(".")[-1].replace("_", " ").title()

    return get_agent_name


def make_error_response_checker(hass: HomeAssistant) -> Callable[[Any], bool]:


    def is_error_response(result) -> bool:
        if not result or not result.response:
            return True
        if result.response.response_type == intent.IntentResponseType.ERROR:
            return True

        tool_results = get_tool_results_state(hass)
        if tool_results:
            failed_tools = [item for item in tool_results if not item.get("success", True)]
            if failed_tools:
                LOGGER.debug(
                    "Detected failed tool calls: %s",
                    [item["tool_name"] for item in failed_tools],
                )
                return True
        return False

    return is_error_response


async def run_agent_fallback_chain(
    hass: HomeAssistant,
    *,
    text: str,
    original_text: str,
    conversation_id,
    context,
    language,
    fallback_agents: list[str],
    conversation_mode: str,
    original_async_converse,
    extra_system_prompt,
    device_id,
    satellite_id,
    conv_history,
    is_error_response: Callable[[Any], bool],
    get_agent_name: Callable[[str], str],
) -> Any:

    task_loop = get_task_loop_state(hass)
    agent_errors: list[str] = []
    previous_tool_results: list[dict[str, Any]] = []
    base_extra_prompt = extra_system_prompt
    pending_handoff_context = ""

    ha_internal_agent = "conversation.home_assistant"
    try:
        tool_results_state = get_tool_results_state(hass)
        tool_results_state.clear()
        tool_calls_state = get_tool_calls_state(hass)
        tool_calls_state.clear()
        get_conversation_status(hass)["is_internal_llm"] = True
        tool_mode_token = set_runtime_tool_mode("native")

        try:
            internal_result = await original_async_converse(
                hass,
                text,
                conversation_id,
                context,
                language,
                ha_internal_agent,
                device_id,
                satellite_id,
                None,
            )
        finally:
            reset_runtime_tool_mode(tool_mode_token)

        tool_results = get_tool_results_state(hass)
        failed_tools = [item for item in tool_results if not item.get("success", True)]
        synthesized_response = extract_successful_tool_response(tool_results)

        only_think_tools = all(
            item.get("tool_name") in NON_USER_FACING_TOOLS
            for item in tool_results
        ) if tool_results else False

        if synthesized_response and not failed_tools:
            handoff_request = consume_next_agent_handoff(hass)
            if handoff_request["requested"] and fallback_agents:
                pending_handoff_context = _build_next_agent_handoff_prompt(
                    previous_agent_name="Home Assistant",
                    previous_response_text=(
                        handoff_request["reply_content"] or synthesized_response
                    ),
                    reason=handoff_request["reason"] or "explicit_tool_request",
                    handoff_intent=str(handoff_request.get("intent", "request")),
                    expected_action=str(handoff_request.get("expected_action", "reply")),
                    task_summary=str(handoff_request.get("task_summary", "")),
                )
                previous_tool_results = _snapshot_tool_results(tool_results)
                LOGGER.info("HA internal LLM requested handoff to next AI")
            else:
                internal_result.response.async_set_speech(synthesized_response)
                internal_result = await _finalize_synthesized_success(
                    hass,
                    result=internal_result,
                    agent_id=ha_internal_agent,
                    agent_name="Home Assistant",
                    response_text=synthesized_response,
                    conversation_mode=conversation_mode,
                    conversation_id=conversation_id,
                    original_text=original_text,
                    user_text=text,
                    conv_history=conv_history,
                    task_loop=task_loop,
                )
                LOGGER.info(
                    "HA internal LLM closed the turn using successful tool results: %s...",
                    synthesized_response[:50],
                )
                await async_record_agent_success(
                    hass,
                    ha_internal_agent,
                    conversation_id=conversation_id,
                )
                return internal_result

        if (not is_error_response(internal_result) and not failed_tools) or (only_think_tools and not failed_tools):
            if internal_result.response.speech and "plain" in internal_result.response.speech:
                response_text = sanitize_response_text(
                    internal_result.response.speech["plain"].get("speech", "").strip()
                )
                if response_text:
                    handoff_request = consume_next_agent_handoff(hass)
                    if handoff_request["requested"] and fallback_agents:
                        pending_handoff_context = _build_next_agent_handoff_prompt(
                            previous_agent_name="Home Assistant",
                            previous_response_text=(
                                handoff_request["reply_content"] or response_text
                            ),
                            reason=handoff_request["reason"] or "explicit_tool_request",
                            handoff_intent=str(handoff_request.get("intent", "request")),
                            expected_action=str(handoff_request.get("expected_action", "reply")),
                            task_summary=str(handoff_request.get("task_summary", "")),
                        )
                        previous_tool_results = _snapshot_tool_results(tool_results)
                        LOGGER.info("HA internal LLM handed off to next AI")
                    else:
                        from .response_format import language_of, reply_labels

                        agent_name = "Home Assistant"
                        await async_bridge_native_chatlog_turn(
                            hass,
                            agent_id=ha_internal_agent,
                            response_text=response_text,
                        )
                        task_loop["history"].append(
                            {"role": "assistant", "content": response_text}
                        )
                        record_response(
                            hass,
                            response_text=response_text,
                            agent_id=ha_internal_agent,
                        )
                        tool_calls = list(get_tool_calls_state(hass))
                        conv_history.add_turn(
                            conversation_id or "default",
                            original_text,
                            response_text,
                            tool_calls=tool_calls,
                        )
                        if (
                            internal_result.response.response_type
                            == intent.IntentResponseType.ACTION_DONE
                        ):
                            await async_capture_passive_signal(
                                hass,
                                user_text=original_text,
                                assistant_text=response_text,
                                tool_calls=tool_calls,
                                conversation_id=conversation_id,
                            )
                        tool_calls_state = get_tool_calls_state(hass)
                        tool_calls_state.clear()
                        fire_ai_response(
                            hass,
                            response=response_text,
                            user_request=text,
                            conversation_id=conversation_id,
                            iteration=1,
                            agent_id=ha_internal_agent,
                        )
                        reply = reply_labels(language_of(internal_result))["reply"]
                        if conversation_mode in (
                            CONVERSATION_MODE_ADD_NAME,
                            CONVERSATION_MODE_DETAILED,
                        ):
                            internal_result.response.speech["plain"]["speech"] = (
                                f"({agent_name}) {reply}: {response_text}"
                            )
                        internal_result.response.speech["plain"]["original_speech"] = response_text
                        internal_result.response.speech["plain"]["agent_name"] = agent_name
                        internal_result.response.speech["plain"]["agent_id"] = ha_internal_agent
                        internal_result.continue_conversation = not is_user_done_text(
                            text, detect_user_ending_intent
                        )
                        set_current_thought(hass, None)
                        LOGGER.info(
                            "HA internal LLM handled the request successfully: %s...",
                            response_text[:50],
                        )
                        await async_record_agent_success(
                            hass,
                            ha_internal_agent,
                            conversation_id=conversation_id,
                        )
                        return internal_result

        LOGGER.debug(
            "HA internal LLM could not handle the request; switching to external AI. Failed tools: %s",
            [item.get("tool_name") for item in failed_tools],
        )
        await async_record_agent_failure(
            hass,
            ha_internal_agent,
            error=_summarize_tool_failures(tool_results) if failed_tools else "error_response",
            conversation_id=conversation_id,
            stage="internal_llm",
        )
        previous_tool_results = _snapshot_tool_results(tool_results)
    except Exception as err:
        LOGGER.debug(
            "HA internal LLM call failed: %s; switching to external AI",
            err,
        )
        await async_record_agent_failure(
            hass,
            ha_internal_agent,
            error=str(err),
            conversation_id=conversation_id,
            stage="internal_llm",
        )
    finally:
        get_conversation_status(hass)["is_internal_llm"] = False

    ordered_agents = prioritize_agents(hass, fallback_agents)
    if ordered_agents != fallback_agents:
        LOGGER.debug("Adaptive memory reordered agents: %s -> %s", fallback_agents, ordered_agents)

    compatible_agents = [
        agent_id
        for index, agent_id in enumerate(ordered_agents)
        if index == 0 or not is_known_incompatible_agent(hass, agent_id)
    ]
    if compatible_agents:
        skipped_incompatible = [
            agent_id for agent_id in ordered_agents if agent_id not in compatible_agents
        ]
        if skipped_incompatible:
            LOGGER.debug(
                "Skipping known incompatible agents for this turn: %s",
                skipped_incompatible,
            )
        ordered_agents = compatible_agents

    active_agents = [
        agent_id
        for index, agent_id in enumerate(ordered_agents)
        if index == 0 or not should_temporarily_skip_agent(hass, agent_id)
    ]
    if active_agents:
        skipped_agents = [agent_id for agent_id in ordered_agents if agent_id not in active_agents]
        if skipped_agents:
            LOGGER.debug("Skipping cooled-down agents for this turn: %s", skipped_agents)
        ordered_agents = active_agents

    _TRANSIENT_ERROR_KEYWORDS = ("disconnected", "connection", "timeout", "reset by peer", "broken pipe", "eof occurred",
                                 "cannot connect", "server disconnected", "ssl", "clientconnector", "serverdisconnected")
    _MAX_TRANSIENT_RETRIES = 3
    transient_retry_counts: dict[str, int] = {}

    agent_queue = list(ordered_agents)
    while agent_queue:
        current_agent_id = agent_queue.pop(0)
        get_conversation_status(hass)["current_agent_id"] = current_agent_id
        tool_results_state = get_tool_results_state(hass)
        tool_results_state.clear()
        tool_calls_state = get_tool_calls_state(hass)
        tool_calls_state.clear()

        current_extra_prompt = base_extra_prompt
        if pending_handoff_context:
            current_extra_prompt = _append_prompt(
                current_extra_prompt,
                pending_handoff_context,
            )

        if previous_tool_results:
            tool_context = "## Previous Agent Tool Results (DO NOT repeat these calls!):\n"
            for tool_result in previous_tool_results:
                status = "SUCCESS" if tool_result.get("success", False) else "FAILED"
                tool_context += f"- {tool_result.get('tool_name', 'unknown')}: {status}"
                if tool_result.get("error"):
                    tool_context += f" - Error: {tool_result['error']}"
                if tool_result.get("result"):
                    summarized = extract_successful_tool_response([tool_result]).strip()
                    if summarized:
                        tool_context += f" - Result: {summarized[:200]}"
                tool_context += "\n"
            tool_context += (
                "\nBased on these results, provide a response to the user. "
                "Do NOT call the same tools again!\n"
            )
            current_extra_prompt = _append_prompt(current_extra_prompt, tool_context)

        try:
            tool_mode_token = set_runtime_tool_mode("minimal")
            try:
                result = await original_async_converse(
                    hass,
                    text,
                    conversation_id,
                    context,
                    language,
                    current_agent_id,
                    device_id,
                    satellite_id,
                    current_extra_prompt,
                )
            finally:
                reset_runtime_tool_mode(tool_mode_token)

            if is_error_response(result):
                all_tools = get_tool_results_state(hass)
                failed_tools = [item for item in all_tools if not item.get("success", True)]
                previous_tool_results.extend(_snapshot_tool_results(all_tools))
                synthesized_response = extract_successful_tool_response(all_tools)
                agent_response_text = get_response_text(result).strip()
                _is_error_text = agent_response_text and any(
                    kw in agent_response_text.lower()
                    for kw in ("error getting response", "server disconnected", "timed out", "connection reset")
                )
                if agent_response_text and not _is_error_text:
                    agent_name = get_agent_name(current_agent_id)
                    result = await _finalize_synthesized_success(
                        hass,
                        result=result,
                        agent_id=current_agent_id,
                        agent_name=agent_name,
                        response_text=agent_response_text,
                        conversation_mode=conversation_mode,
                        conversation_id=conversation_id,
                        original_text=original_text,
                        user_text=text,
                        conv_history=conv_history,
                        task_loop=task_loop,
                    )
                    LOGGER.info(
                        "Agent %s had mixed tool outcomes but already produced a final reply; preserving agent reply",
                        current_agent_id,
                    )
                    await async_record_agent_success(
                        hass,
                        current_agent_id,
                        conversation_id=conversation_id,
                    )
                    return result
                if synthesized_response:
                    agent_name = get_agent_name(current_agent_id)
                    result = await _finalize_synthesized_success(
                        hass,
                        result=result,
                        agent_id=current_agent_id,
                        agent_name=agent_name,
                        response_text=synthesized_response,
                        conversation_mode=conversation_mode,
                        conversation_id=conversation_id,
                        original_text=original_text,
                        user_text=text,
                        conv_history=conv_history,
                        task_loop=task_loop,
                    )
                    LOGGER.info(
                        "Agent %s had mixed tool outcomes; synthesized final response from successful results",
                        current_agent_id,
                    )
                    await async_record_agent_success(
                        hass,
                        current_agent_id,
                        conversation_id=conversation_id,
                    )
                    return result

                if failed_tools:
                    LOGGER.info(
                        "Agent %s had a tool-call failure; trying the next agent: %s",
                        current_agent_id,
                        [item["tool_name"] for item in failed_tools],
                    )
                    failure_reason = _summarize_tool_failures(all_tools)
                    await async_record_agent_failure(
                        hass,
                        current_agent_id,
                        error=failure_reason,
                        conversation_id=conversation_id,
                        stage="tool_failure",
                    )
                    agent_errors.append(f"{current_agent_id}: {failure_reason[:160]}")
                    if any(kw in failure_reason.lower() for kw in _TRANSIENT_ERROR_KEYWORDS):
                        retries = transient_retry_counts.get(current_agent_id, 0)
                        if retries < _MAX_TRANSIENT_RETRIES:
                            transient_retry_counts[current_agent_id] = retries + 1
                            agent_queue.append(current_agent_id)
                            LOGGER.info(
                                "Agent %s tool failure looks transient; re-queuing retry %d/%d",
                                current_agent_id, retries + 1, _MAX_TRANSIENT_RETRIES,
                            )
                else:
                    synthesized_response = extract_successful_tool_response(all_tools)
                    if synthesized_response:
                        agent_name = get_agent_name(current_agent_id)
                        result = await _finalize_synthesized_success(
                            hass,
                            result=result,
                            agent_id=current_agent_id,
                            agent_name=agent_name,
                            response_text=synthesized_response,
                            conversation_mode=conversation_mode,
                            conversation_id=conversation_id,
                            original_text=original_text,
                            user_text=text,
                            conv_history=conv_history,
                            task_loop=task_loop,
                        )
                        LOGGER.info(
                            "Agent %s tool execution succeeded; synthesized final response",
                            current_agent_id,
                        )
                        await async_record_agent_success(
                            hass,
                            current_agent_id,
                            conversation_id=conversation_id,
                        )
                        return result

                    failure_reason = _extract_response_error_reason(result)
                    LOGGER.info(
                        "Agent %s returned an error response; trying the next agent: %s",
                        current_agent_id,
                        failure_reason[:160],
                    )
                    await async_record_agent_failure(
                        hass,
                        current_agent_id,
                        error=failure_reason,
                        conversation_id=conversation_id,
                        stage="response_error",
                    )
                    agent_errors.append(f"{current_agent_id}: {failure_reason[:160]}")
                    if any(kw in failure_reason.lower() for kw in _TRANSIENT_ERROR_KEYWORDS):
                        retries = transient_retry_counts.get(current_agent_id, 0)
                        if retries < _MAX_TRANSIENT_RETRIES:
                            transient_retry_counts[current_agent_id] = retries + 1
                            agent_queue.append(current_agent_id)
                            LOGGER.info(
                                "Agent %s response error looks transient; re-queuing retry %d/%d",
                                current_agent_id, retries + 1, _MAX_TRANSIENT_RETRIES,
                            )
                continue

            if result.response.speech and "plain" in result.response.speech:
                response_text = sanitize_response_text(
                    result.response.speech["plain"]
                    .get(
                        "original_speech",
                        result.response.speech["plain"].get("speech", ""),
                    )
                    .strip()
                )
                if not response_text:
                    continue

                agent_name = get_agent_name(current_agent_id)
                handoff_request = consume_next_agent_handoff(hass)
                target_agent_id = ""
                if handoff_request["requested"]:
                    target_agent_id = _resolve_handoff_target(
                        direction=str(handoff_request.get("direction", "next")),
                        current_agent_id=current_agent_id,
                        remaining_agents=agent_queue,
                        task_loop=task_loop,
                    )
                if target_agent_id:
                    pending_handoff_context = _build_next_agent_handoff_prompt(
                        previous_agent_name=agent_name,
                        previous_response_text=(
                            handoff_request["reply_content"] or response_text
                        ),
                        reason=handoff_request["reason"] or "explicit_tool_request",
                        handoff_intent=str(handoff_request.get("intent", "request")),
                        expected_action=str(handoff_request.get("expected_action", "reply")),
                        task_summary=str(handoff_request.get("task_summary", "")),
                    )
                    previous_tool_results.extend(
                        _snapshot_tool_results(get_tool_results_state(hass))
                    )
                    agent_queue = [target_agent_id] + [
                        agent_id for agent_id in agent_queue if agent_id != target_agent_id
                    ]
                    LOGGER.info("Agent %s handed off to next AI", current_agent_id)
                    await async_record_agent_success(
                        hass,
                        current_agent_id,
                        conversation_id=conversation_id,
                    )
                    continue

                result.response.speech["plain"]["speech"] = response_text
                result.response.speech["plain"]["original_speech"] = response_text
                await async_bridge_native_chatlog_turn(
                    hass,
                    agent_id=current_agent_id,
                    response_text=response_text,
                )
                task_loop["history"].append({"role": "assistant", "content": response_text})
                record_response(
                    hass,
                    response_text=response_text,
                    agent_id=current_agent_id,
                )
                tool_calls = list(get_tool_calls_state(hass))
                conv_history.add_turn(
                    conversation_id or "default",
                    original_text,
                    response_text,
                    tool_calls=tool_calls,
                )
                if result.response.response_type == intent.IntentResponseType.ACTION_DONE:
                    await async_capture_passive_signal(
                        hass,
                        user_text=original_text,
                        assistant_text=response_text,
                        tool_calls=tool_calls,
                        conversation_id=conversation_id,
                    )
                tool_calls_state = get_tool_calls_state(hass)
                tool_calls_state.clear()

                fire_ai_response(
                    hass,
                    response=response_text,
                    user_request=text,
                    conversation_id=conversation_id,
                    iteration=1,
                    agent_id=current_agent_id,
                )

                LOGGER.info("AI response: %s...", response_text[:100])

                set_current_thought(hass, None)
                apply_agent_response_format(
                    result,
                    hass=hass,
                    agent_name=agent_name,
                    agent_id=current_agent_id,
                    conversation_mode=conversation_mode,
                    response_text=response_text,
                )
                set_current_thought(hass, None)

                result.continue_conversation = not is_user_done_text(
                    text, detect_user_ending_intent
                )
                LOGGER.info("Agent %s succeeded", current_agent_id)
                await async_record_agent_success(
                    hass,
                    current_agent_id,
                    conversation_id=conversation_id,
                )
                return result
        except Exception as err:
            err_msg = str(err)
            err_lower = err_msg.lower()

            _UNAVAILABLE_KEYWORDS = (
                "not found",
                "not_found",
                "does not exist",
                "no longer available",
                "invalid agent",
                "not loaded",
                "not registered",
                "not initialized",
                "not configured",
                "entity not available",
                "entry not ready",
                "entry not loaded",
                "api_url_not_configured",
                "api_key_not_configured",
                "unsupported_provider",
            )
            is_unavailable = (
                isinstance(err, ValueError)
                or any(kw in err_lower for kw in _UNAVAILABLE_KEYWORDS)
            )
            if is_unavailable:
                LOGGER.info(
                    "Agent %s unavailable (%s: %s), skipping",
                    current_agent_id, type(err).__name__, err_msg[:120],
                )
                continue
            successful_tool_response = extract_successful_tool_response(
                get_tool_results_state(hass)
            )
            if successful_tool_response:
                result = _build_synthesized_result(
                    language=language or hass.config.language,
                    conversation_id=conversation_id,
                    response_text=successful_tool_response,
                )
                agent_name = get_agent_name(current_agent_id)
                result = await _finalize_synthesized_success(
                    hass,
                    result=result,
                    agent_id=current_agent_id,
                    agent_name=agent_name,
                    response_text=successful_tool_response,
                    conversation_mode=conversation_mode,
                    conversation_id=conversation_id,
                    original_text=original_text,
                    user_text=text,
                    conv_history=conv_history,
                    task_loop=task_loop,
                )
                LOGGER.info(
                    "Agent %s raised after successful tools; synthesized final response from tool results",
                    current_agent_id,
                )
                await async_record_agent_success(
                    hass,
                    current_agent_id,
                    conversation_id=conversation_id,
                )
                return result
            if "content parts are required" in err_msg:
                LOGGER.debug(
                    "Agent %s: Google AI SDK returned an empty response after tool calls; trying the next agent",
                    current_agent_id,
                )
                agent_errors.append(f"{current_agent_id}: empty response")
            else:
                agent_errors.append(f"{current_agent_id}: {err_msg[:100]}")
            await async_record_agent_failure(
                hass,
                current_agent_id,
                error=err_msg,
                conversation_id=conversation_id,
                stage="exception",
            )
            if any(kw in err_lower for kw in _TRANSIENT_ERROR_KEYWORDS):
                retries = transient_retry_counts.get(current_agent_id, 0)
                if retries < _MAX_TRANSIENT_RETRIES:
                    transient_retry_counts[current_agent_id] = retries + 1
                    agent_queue.append(current_agent_id)
                    LOGGER.info(
                        "Agent %s hit transient error; re-queuing for retry %d/%d",
                        current_agent_id, retries + 1, _MAX_TRANSIENT_RETRIES,
                    )
            continue

    resolved_lang = language or hass.config.language or "en"
    if agent_errors:
        error_detail = "; ".join(agent_errors)
    else:
        error_detail = t("agents_unavailable", resolved_lang)
    LOGGER.warning("Agent fallback chain exhausted: %s", error_detail)
    intent_response = intent.IntentResponse(language=resolved_lang)
    intent_response.async_set_error(
        intent.IntentResponseErrorCode.UNKNOWN,
        error_detail,
    )
    from homeassistant.components.conversation import ConversationResult

    return ConversationResult(response=intent_response, conversation_id=conversation_id)
