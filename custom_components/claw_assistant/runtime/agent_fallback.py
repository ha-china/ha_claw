

from __future__ import annotations

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
from .evolution_review import async_schedule_evolution_review
from .events import fire_ai_response
from .i18n import t
from .internal_llm import (
    _build_budgeted_prompt,
    _fit_head_section_to_required_suffix,
    _MAX_SYSTEM_PROMPT_CHARS,
    reset_runtime_tool_mode,
    set_runtime_tool_mode,
)
from .loop_controller import record_response
from .native_chatlog_bridge import async_bridge_native_chatlog_turn
from .prompting import _fit_base_prompt
from .response_format import (
    _looks_like_error,
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
from .tool_result_summary import extract_failed_tool_response

LOGGER = logging.getLogger(__name__)


def _get_chat_log_content(hass: HomeAssistant, conversation_id: str) -> list:
    from homeassistant.util.hass_dict import HassKey
    DATA_CHAT_LOGS: HassKey = HassKey("conversation_chat_log")
    all_chat_logs = hass.data.get(DATA_CHAT_LOGS)
    if not all_chat_logs:
        return []
    chat_log = all_chat_logs.get(conversation_id)
    return chat_log.content if chat_log else []


async def _trim_chat_log_for_context_overflow(hass: HomeAssistant, conversation_id: str, *, summary_agent_id: str = "", force: bool = False) -> None:
    from .context_compressor import compress_chat_log
    await compress_chat_log(hass, conversation_id, summary_agent_id=summary_agent_id, force=force)


def _snapshot_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:

    return copy.deepcopy(tool_results)


def _build_tool_summary(tool_results: list[dict[str, Any]]) -> str:

    return (
        extract_successful_tool_response(tool_results)
        or extract_failed_tool_response(tool_results)
        or ""
    )


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


async def _finalize_completed_response(
    hass: HomeAssistant,
    *,
    response: Any,
    task_loop: dict[str, Any],
    original_text: str,
    conversation_id,
    agent_id: str,
    conv_history,
    tool_results: list[dict[str, Any]],
    language: str | None,
    original_async_converse,
) -> str:

    plain = response.speech.get("plain", {}) if isinstance(response.speech, dict) else {}
    raw = plain.get("original_speech", plain.get("speech", ""))
    final_text = sanitize_response_text(raw)
    if not final_text:
        return ""

    plain["speech"] = final_text
    plain["original_speech"] = final_text
    task_loop["history"].append({"role": "assistant", "content": final_text})
    record_response(
        hass,
        response_text=final_text,
        agent_id=agent_id,
    )
    tool_calls = list(get_tool_calls_state(hass))
    conv_history.add_turn(
        conversation_id or "default",
        original_text,
        final_text,
        tool_calls=tool_calls,
    )
    if response.response_type == intent.IntentResponseType.ACTION_DONE:
        await async_capture_passive_signal(
            hass,
            user_text=original_text,
            assistant_text=final_text,
            tool_calls=tool_calls,
            conversation_id=conversation_id,
        )
    async_schedule_evolution_review(
        hass,
        original_text=original_text,
        assistant_text=final_text,
        tool_calls=tool_calls,
        tool_summary=_build_tool_summary(tool_results),
        conversation_id=conversation_id,
        language=language,
        agent_id=agent_id,
        original_async_converse=original_async_converse,
    )
    get_tool_calls_state(hass).clear()
    return final_text


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
    tool_results = _snapshot_tool_results(get_tool_results_state(hass))
    final_text = await _finalize_completed_response(
        hass,
        response=result.response,
        task_loop=task_loop,
        original_text=original_text,
        conversation_id=conversation_id,
        agent_id=agent_id,
        conv_history=conv_history,
        tool_results=tool_results,
        language=None,
        original_async_converse=None,
    )
    fire_ai_response(
        hass,
        response=final_text or response_text,
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
        response_text=final_text or response_text,
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


async def _finalize_agent_success(
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
    language: str | None,
    original_async_converse,
    tool_results: list[dict[str, Any]],
    handoff_replies: list[tuple[str, str]] | None = None,
) -> Any:

    if getattr(result, "response", None) and getattr(result.response, "speech", None):
        plain = result.response.speech.get("plain", {}) if isinstance(result.response.speech, dict) else {}
        plain["speech"] = response_text
        plain["original_speech"] = response_text

    await async_bridge_native_chatlog_turn(
        hass,
        agent_id=agent_id,
        response_text=response_text,
    )
    final_text = await _finalize_completed_response(
        hass,
        response=result.response,
        task_loop=task_loop,
        original_text=original_text,
        conversation_id=conversation_id,
        agent_id=agent_id,
        conv_history=conv_history,
        tool_results=tool_results,
        language=language,
        original_async_converse=original_async_converse,
    )
    fire_ai_response(
        hass,
        response=final_text or response_text,
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
        response_text=final_text or response_text,
        handoff_replies=handoff_replies,
    )
    LOGGER.info("AI response: %s...", (final_text or response_text)[:100])
    set_current_thought(hass, None)
    result.continue_conversation = not is_user_done_text(
        user_text, detect_user_ending_intent
    )
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


def _schedule_transient_retry(
    agent_queue: list[str],
    *,
    current_agent_id: str,
    primary_agent_id: str | None,
    transient_retry_counts: dict[str, int],
    max_retries: int,
) -> tuple[bool, int]:

    retries = transient_retry_counts.get(current_agent_id, 0)
    if retries >= max_retries:
        return False, retries

    retries += 1
    transient_retry_counts[current_agent_id] = retries
    agent_queue.insert(0, current_agent_id)
    return True, retries


def _append_prompt(base_prompt: str | None, extra_block: str) -> str:
    return _fit_base_prompt(base_prompt or "", [extra_block])


def _head_tail_compact_text(value: str, *, head: int, tail: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= head + tail + 3:
        return text
    return f"{text[:head].rstrip()} ... {text[-tail:].lstrip()}"


def _compact_text(value: str, limit: int = 1200) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    if limit <= 80:
        return f"{value[:limit].rstrip()}..."
    tail = min(max(limit // 4, 40), 240)
    head = max(limit - tail - 5, 32)
    return _head_tail_compact_text(value, head=head, tail=tail)


def _normalize_for_dedupe(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _append_unique_line(lines: list[str], seen: set[str], value: str) -> None:
    normalized = _normalize_for_dedupe(value)
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    lines.append(value)


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
    compact_task_summary = _compact_text(task_summary, 400)
    compact_reason = _compact_text(reason, 320)
    compact_previous_response = _compact_text(previous_response_text, 2400)

    lines = [
        "## Agent Handoff",
        f"From: {previous_agent_name}",
        f"Intent: {handoff_intent} — {intent_desc}",
        f"Expected action: {expected_action} — {action_desc}",
    ]
    if compact_task_summary:
        lines.append(f"Task: {compact_task_summary}")
    if compact_reason:
        lines.append(f"Reason: {compact_reason}")
    lines.extend([
        "",
        "### Context from previous AI:",
        compact_previous_response,
        "",
        "### Instructions:",
        action_desc,
        "Do not ask the user to repeat themselves — all context is above.",
        "Do not call AgentHandoff again unless the user explicitly asks for another handoff after you answer.",
        "If details appear condensed, rely on the preserved task, intent, recent context, and verified tool results rather than inventing missing facts.",
    ])
    return "\n".join(lines)


def _build_agent_recovery_prompt(
    *,
    failed_agent_name: str,
    original_text: str,
    error: str,
    tool_results: list[dict[str, Any]],
    task_loop: dict[str, Any],
) -> str:
    seen: set[str] = set()
    lines = [
        "## Seamless Agent Recovery",
        f"Previous AI: {failed_agent_name}",
        f"Failure: {_compact_text(error, 500)}",
        "",
        "### Original user task",
        _compact_text(original_text, 1200),
    ]
    history = task_loop.get("history", [])
    if history:
        lines.extend(["", "### Recent progress"])
        for item in history[-4:]:
            role = str(item.get("role", "assistant"))
            content = _compact_text(str(item.get("content", "")), 500)
            if content:
                _append_unique_line(lines, seen, f"- {role}: {content}")
    if tool_results:
        lines.extend(["", "### Tool results already produced"])
        for item in tool_results[-8:]:
            status = "SUCCESS" if item.get("success", False) else "FAILED"
            tool_name = str(item.get("tool_name", "unknown"))
            line = f"- {tool_name}: {status}"
            if item.get("error"):
                line += f" - {_compact_text(str(item['error']), 300)}"
            if item.get("result"):
                summarized = extract_successful_tool_response([item]).strip()
                if summarized:
                    line += f" - Result: {_compact_text(summarized, 500)}"
            _append_unique_line(lines, seen, line)
    lines.extend([
        "",
        "### Instructions",
        "Continue the same task from here without asking the user to repeat anything.",
        "Do not repeat successful tool calls unless a fresh check is necessary.",
        "If the previous AI failed while streaming, ignore the broken partial answer and produce a clean final answer.",
        "If prompt budget removes detail, prioritize the preserved task summary, latest progress, and successful tool results.",
    ])
    return "\n".join(lines)


def _summarize_tool_result_entry(tool_result: dict[str, Any]) -> str:
    status = "SUCCESS" if tool_result.get("success", False) else "FAILED"
    tool_name = str(tool_result.get("tool_name", "unknown"))
    line = f"- {tool_name}: {status}"
    if tool_result.get("error"):
        line += f" - Error: {_compact_text(str(tool_result['error']), 320)}"
    if tool_result.get("result"):
        summarized = extract_successful_tool_response([tool_result]).strip()
        if summarized:
            line += f" - Result: {_compact_text(summarized, 700)}"
    return line


def _dedupe_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tool_results:
        signature = _normalize_for_dedupe(_summarize_tool_result_entry(item))
        if not signature or signature in seen:
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped


def _build_tool_results_prompt(tool_results: list[dict[str, Any]]) -> str:
    deduped_results = _dedupe_tool_results(tool_results)
    if not deduped_results:
        return ""

    lines = ["## Previous Agent Tool Results"]
    seen: set[str] = set()
    for tool_result in deduped_results[-8:]:
        _append_unique_line(lines, seen, _summarize_tool_result_entry(tool_result))
    lines.extend([
        "",
        "Based on these results, continue from the current state.",
        "Do not repeat successful tool calls unless a fresh check is truly necessary.",
    ])
    return "\n".join(lines)


def _build_fallback_extra_prompt(
    *,
    base_prompt: str | None,
    user_text: str,
    pending_handoff_context: str,
    previous_tool_results: list[dict[str, Any]],
) -> str | None:
    required_prefix_sections = [
        "## Active User Task\n" + _compact_text(user_text, 2400),
    ]
    required_suffix_sections = [pending_handoff_context] if pending_handoff_context else []
    optional_tail_sections: list[str] = []

    tool_results_prompt = _build_tool_results_prompt(previous_tool_results)
    if tool_results_prompt:
        optional_tail_sections.append(tool_results_prompt)

    required_prompt = _build_budgeted_prompt(
        head_sections=[],
        required_prefix_sections=required_prefix_sections,
        required_suffix_sections=required_suffix_sections,
        optional_tail_sections=optional_tail_sections,
        max_chars=_MAX_SYSTEM_PROMPT_CHARS,
    )
    if not base_prompt:
        return required_prompt
    if not required_prompt:
        return base_prompt

    return _fit_head_section_to_required_suffix(
        base_prompt,
        [required_prompt],
        max_chars=_MAX_SYSTEM_PROMPT_CHARS,
    )


def get_agent_name(hass: HomeAssistant, agent_id: str) -> str:

    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)

    ent = ent_reg.async_get(agent_id)
    if ent and ent.name:
        return ent.name
    if ent and ent.original_name:
        return ent.original_name
    state = hass.states.get(agent_id)
    if state:
        return state.attributes.get("friendly_name", agent_id.split(".")[-1])
    if "." in agent_id:
        return agent_id.split(".")[-1].replace("_", " ").title()
    for ent in ent_reg.entities.get_entries_for_config_entry_id(agent_id):
        if ent.domain == "conversation":
            name = ent.name or ent.original_name
            if name:
                return name
            s = hass.states.get(ent.entity_id)
            if s:
                return s.attributes.get("friendly_name", ent.entity_id.split(".")[-1])
    entry = hass.config_entries.async_get_entry(agent_id)
    if entry and entry.title:
        return entry.title
    return "AI Assistant"


def is_error_response(hass: HomeAssistant, result: Any) -> bool:

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


def _raw_response_text(result: Any) -> str:
    response = getattr(result, "response", None)
    speech = getattr(response, "speech", None) if response else None
    if not isinstance(speech, dict):
        return ""
    plain = speech.get("plain", {})
    if not isinstance(plain, dict):
        return ""
    return str(plain.get("original_speech", plain.get("speech", "")) or "")


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
) -> Any:

    task_loop = get_task_loop_state(hass)
    agent_errors: list[str] = []
    previous_tool_results: list[dict[str, Any]] = []
    handoff_replies: list[tuple[str, str]] = []
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

        if (not is_error_response(hass, internal_result) and not failed_tools) or (only_think_tools and not failed_tools):
            if internal_result.response.speech and "plain" in internal_result.response.speech:
                response_text = sanitize_response_text(
                    internal_result.response.speech["plain"].get("speech", "").strip()
                )
                if response_text:
                    handoff_request = consume_next_agent_handoff(hass)
                    if handoff_request["requested"] and fallback_agents:
                        handoff_replies.append(("Home Assistant", response_text))
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
                        internal_result = await _finalize_agent_success(
                            hass,
                            result=internal_result,
                            agent_id=ha_internal_agent,
                            agent_name=agent_name,
                            response_text=response_text,
                            conversation_mode=conversation_mode,
                            conversation_id=conversation_id,
                            original_text=original_text,
                            user_text=text,
                            conv_history=conv_history,
                            task_loop=task_loop,
                            language=language,
                            original_async_converse=original_async_converse,
                            tool_results=_snapshot_tool_results(tool_results),
                        )
                        from .reply_formatter import stamp_plain
                        stamp_plain(
                            internal_result.response.speech.setdefault("plain", {}),
                            agent_name=agent_name,
                            agent_id=ha_internal_agent,
                            text=response_text,
                            language=language,
                            add_prefix=conversation_mode != CONVERSATION_MODE_NO_NAME,
                        )
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
                                 "cannot connect", "server disconnected", "ssl", "clientconnector", "serverdisconnected",
                                 "无法连接", "连接失败", "网络错误", "请检查网络", "连接超时", "服务器断开",
                                 "服务不可用", "请稍后再试", "ai 服务")
    _MAX_TRANSIENT_RETRIES = 2
    transient_retry_counts: dict[str, int] = {}
    primary_external_agent = ordered_agents[0] if ordered_agents else None

    agent_queue = list(ordered_agents)
    while agent_queue:
        current_agent_id = agent_queue.pop(0)
        get_conversation_status(hass)["current_agent_id"] = current_agent_id
        tool_results_state = get_tool_results_state(hass)
        tool_results_state.clear()
        tool_calls_state = get_tool_calls_state(hass)
        tool_calls_state.clear()

        current_extra_prompt = _build_fallback_extra_prompt(
            base_prompt=base_extra_prompt,
            user_text=text,
            pending_handoff_context=pending_handoff_context,
            previous_tool_results=previous_tool_results,
        )

        try:
            from .context_compressor import get_compressor
            _cc = get_compressor()
            if _cc.preflight_check(_get_chat_log_content(hass, conversation_id)):
                LOGGER.info("Preflight compression: context exceeds threshold, compressing before API call")
                await _trim_chat_log_for_context_overflow(hass, conversation_id)
        except Exception as _pf_err:
            LOGGER.debug("Preflight compression check failed: %s", _pf_err)

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

            if is_error_response(hass, result):
                all_tools = get_tool_results_state(hass)
                failed_tools = [item for item in all_tools if not item.get("success", True)]
                previous_tool_results.extend(_snapshot_tool_results(all_tools))
                synthesized_response = extract_successful_tool_response(all_tools)
                raw_agent_response_text = _raw_response_text(result).strip()
                agent_response_text = get_response_text(result).strip()
                error_probe = raw_agent_response_text or agent_response_text
                _is_error_text = bool(error_probe) and _looks_like_error(error_probe)
                failure_reason = _extract_response_error_reason(result)

                _CTX_TOO_LONG_HINTS = ("context_length_exceeded", "context length", "token_limit", "input too long", "context too long", "message too long", "max_tokens", "token limit")
                _is_ctx_too_long = any(h in failure_reason.lower() for h in _CTX_TOO_LONG_HINTS) or any(
                    h in error_probe.lower() for h in _CTX_TOO_LONG_HINTS
                )
                _ctx_attempts = transient_retry_counts.get("__ctx_compress_attempts", 0)
                if _is_ctx_too_long and _ctx_attempts < 3:
                    transient_retry_counts["__ctx_compress_attempts"] = _ctx_attempts + 1
                    from .context_compressor import get_compressor
                    _cc = get_compressor()
                    _error_text = failure_reason or error_probe
                    _cc.step_down_context(_error_text)
                    await _trim_chat_log_for_context_overflow(hass, conversation_id, force=True)
                    LOGGER.info(
                        "Agent %s hit context_length_exceeded (attempt %d/3); "
                        "context stepped to %d, compressed and retrying",
                        current_agent_id, _ctx_attempts + 1, _cc.context_length,
                    )
                    agent_queue.insert(0, current_agent_id)
                    continue

                transient_response_error = any(
                    kw in failure_reason.lower() for kw in _TRANSIENT_ERROR_KEYWORDS
                ) or bool(_is_error_text)
                if agent_response_text and not _is_error_text:
                    agent_name = get_agent_name(hass, current_agent_id)
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
                    if agent_queue:
                        pending_handoff_context = _build_agent_recovery_prompt(
                            failed_agent_name=get_agent_name(hass, current_agent_id),
                            original_text=original_text,
                            error=failure_reason,
                            tool_results=previous_tool_results,
                            task_loop=task_loop,
                        )
                    if any(kw in failure_reason.lower() for kw in _TRANSIENT_ERROR_KEYWORDS):
                        retried_now, retries = _schedule_transient_retry(
                            agent_queue,
                            current_agent_id=current_agent_id,
                            primary_agent_id=primary_external_agent,
                            transient_retry_counts=transient_retry_counts,
                            max_retries=_MAX_TRANSIENT_RETRIES,
                        )
                        if retried_now:
                            LOGGER.info(
                                "Agent %s tool failure looks transient; re-queuing retry %d/%d",
                                current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
                            )
                    continue
                if transient_response_error:
                    retried_now, retries = _schedule_transient_retry(
                        agent_queue,
                        current_agent_id=current_agent_id,
                        primary_agent_id=primary_external_agent,
                        transient_retry_counts=transient_retry_counts,
                        max_retries=_MAX_TRANSIENT_RETRIES,
                    )
                    if retried_now:
                        LOGGER.info(
                            "Agent %s hit transient error; retry %d/%d",
                            current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
                        )
                    else:
                        LOGGER.info(
                            "Agent %s exhausted %d retries; moving to next agent",
                            current_agent_id, _MAX_TRANSIENT_RETRIES,
                        )
                    await async_record_agent_failure(
                        hass,
                        current_agent_id,
                        error=failure_reason,
                        conversation_id=conversation_id,
                        stage="response_error",
                    )
                    agent_errors.append(f"{current_agent_id}: {failure_reason[:160]}")
                    pending_handoff_context = _build_agent_recovery_prompt(
                        failed_agent_name=get_agent_name(hass, current_agent_id),
                        original_text=original_text,
                        error=failure_reason,
                        tool_results=previous_tool_results,
                        task_loop=task_loop,
                    )
                    continue
                if synthesized_response:
                    agent_name = get_agent_name(hass, current_agent_id)
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
                synthesized_response = extract_successful_tool_response(all_tools)
                if synthesized_response:
                    agent_name = get_agent_name(hass, current_agent_id)
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
                if agent_queue:
                    pending_handoff_context = _build_agent_recovery_prompt(
                        failed_agent_name=get_agent_name(hass, current_agent_id),
                        original_text=original_text,
                        error=failure_reason,
                        tool_results=previous_tool_results,
                        task_loop=task_loop,
                    )
                if any(kw in failure_reason.lower() for kw in _TRANSIENT_ERROR_KEYWORDS):
                    retried_now, retries = _schedule_transient_retry(
                        agent_queue,
                        current_agent_id=current_agent_id,
                        primary_agent_id=primary_external_agent,
                        transient_retry_counts=transient_retry_counts,
                        max_retries=_MAX_TRANSIENT_RETRIES,
                    )
                    if retried_now:
                        LOGGER.info(
                            "Agent %s response error looks transient; re-queuing retry %d/%d",
                            current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
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

                agent_name = get_agent_name(hass, current_agent_id)
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
                    handoff_replies.append((agent_name, response_text))
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

                result = await _finalize_agent_success(
                    hass,
                    result=result,
                    agent_id=current_agent_id,
                    agent_name=agent_name,
                    response_text=response_text,
                    conversation_mode=conversation_mode,
                    conversation_id=conversation_id,
                    original_text=original_text,
                    user_text=text,
                    conv_history=conv_history,
                    task_loop=task_loop,
                    language=language,
                    original_async_converse=original_async_converse,
                    tool_results=_snapshot_tool_results(get_tool_results_state(hass)),
                    handoff_replies=handoff_replies or None,
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

            _CTX_EXC_HINTS = ("context_length_exceeded", "context length", "token_limit", "input too long", "context too long", "message too long", "max_tokens", "token limit", "too large")
            _is_ctx_exc = any(h in err_lower for h in _CTX_EXC_HINTS)
            _ctx_exc_attempts = transient_retry_counts.get("__ctx_compress_exc_attempts", 0)
            if _is_ctx_exc and _ctx_exc_attempts < 2:
                transient_retry_counts["__ctx_compress_exc_attempts"] = _ctx_exc_attempts + 1
                try:
                    from .context_compressor import get_compressor
                    _cc = get_compressor()
                    _cc.step_down_context(err_msg)
                    await _trim_chat_log_for_context_overflow(hass, conversation_id, force=True)
                    LOGGER.info(
                        "Agent %s raised context-too-long exception (attempt %d/2); "
                        "context stepped to %d, compressed and retrying same agent",
                        current_agent_id, _ctx_exc_attempts + 1, _cc.context_length,
                    )
                except Exception as _comp_err:
                    LOGGER.debug("Exception-path compression failed: %s", _comp_err)
                agent_queue.insert(0, current_agent_id)
                continue

            successful_tool_response = extract_successful_tool_response(
                get_tool_results_state(hass)
            )
            current_tool_results = _snapshot_tool_results(get_tool_results_state(hass))
            if current_tool_results:
                previous_tool_results.extend(current_tool_results)
            if agent_queue:
                pending_handoff_context = _build_agent_recovery_prompt(
                    failed_agent_name=get_agent_name(hass, current_agent_id),
                    original_text=original_text,
                    error=err_msg,
                    tool_results=previous_tool_results,
                    task_loop=task_loop,
                )
            if successful_tool_response and not agent_queue:
                result = _build_synthesized_result(
                    language=language or hass.config.language,
                    conversation_id=conversation_id,
                    response_text=successful_tool_response,
                )
                agent_name = get_agent_name(hass, current_agent_id)
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
                retried_now, retries = _schedule_transient_retry(
                    agent_queue,
                    current_agent_id=current_agent_id,
                    primary_agent_id=primary_external_agent,
                    transient_retry_counts=transient_retry_counts,
                    max_retries=_MAX_TRANSIENT_RETRIES,
                )
                if retried_now:
                    LOGGER.info(
                        "Agent %s hit transient error; re-queuing for retry %d/%d",
                        current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
                    )
            continue

    resolved_lang = language or hass.config.language or "en"
    if agent_errors:
        error_detail = "; ".join(agent_errors)
    elif not fallback_agents:
        error_detail = t("agents_none_configured", resolved_lang)
    elif getattr(hass.state, "value", str(hass.state)) != "RUNNING":
        error_detail = t("agents_starting", resolved_lang)
    elif not ordered_agents:
        error_detail = t("agents_all_failed", resolved_lang)
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
