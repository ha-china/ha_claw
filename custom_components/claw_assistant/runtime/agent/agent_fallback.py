

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from ...const import (
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
)
from ...conversation_utils import detect_user_ending_intent
from ..storage.adaptive_memory import (
    async_record_agent_failure,
    async_record_agent_success,
    is_known_incompatible_agent,
    prioritize_agents,
    should_temporarily_skip_agent,
)
from ..storage.curator import record_turn_activity
from ..storage.evolution_review import async_schedule_evolution_review, consume_loaded_skills
from ..storage.goals import get_goal_manager
from ..core.events import fire_ai_response
from ..utils.i18n import t
from ..history.chat_history_api import clear_resume_history_binding, get_active_resume_history_id, _strip_internal_tags
from ..llm.internal_llm import (
    _build_budgeted_prompt,
    _fit_head_section_to_required_suffix,
    _MAX_SYSTEM_PROMPT_CHARS,
    reset_runtime_tool_mode,
    set_runtime_tool_mode,
)
from ..history.history_title import async_generate_history_title
from .loop_controller import record_response
from .loop_controller import get_configured_pipeline_timeout
from ..history.native_chatlog_bridge import async_bridge_native_chatlog_turn
from ..llm.prompting import _fit_base_prompt
from ..llm.response_format import (
    _looks_like_error,
    apply_agent_response_format,
    get_response_text,
    prettify_agent_error,
    sanitize_response_text,
)
from ..output.response_policy import is_user_done_text
from ..utils.signal_capture import async_capture_passive_signal
from ..core.state import (
    consume_next_agent_handoff,
    consume_should_end_flag,
    get_active_conversation_state,
    get_conversation_status,
    get_runtime_store,
    get_should_end_flag,
    get_task_loop_state,
    get_tool_calls_state,
    get_tool_results_state,
    set_current_thought,
)
from ..tools.tool_result_summary import NON_USER_FACING_TOOLS, extract_successful_tool_response
from ..tools.tool_result_summary import extract_failed_tool_response
from ..output.reply_formatter import format_reply_speech
from ..storage.live_turn_store import async_save_live_turn_snapshot

LOGGER = logging.getLogger(__name__)

from ..core.state import IM_CHANNEL_NAMES as _IM_CHANNEL_PREFIXES


def _detect_channel(conversation_id: str | None, conv_status: dict[str, Any]) -> str:
    if not conversation_id:
        return ""
    for prefix, name in _IM_CHANNEL_PREFIXES.items():
        if conversation_id.startswith(prefix):
            return name
    if conv_status.get("is_voice_pipeline"):
        return "Voice"
    from ..core.state import is_mobile_platform
    if is_mobile_platform(conv_status.get("frontend_platform", "")):
        return "Mobile"
    return "Desktop"


def _get_chat_log_content(hass: HomeAssistant, conversation_id: str) -> list:
    from homeassistant.util.hass_dict import HassKey
    DATA_CHAT_LOGS: HassKey = HassKey("conversation_chat_log")
    all_chat_logs = hass.data.get(DATA_CHAT_LOGS)
    if not all_chat_logs:
        return []
    chat_log = all_chat_logs.get(conversation_id)
    return chat_log.content if chat_log else []


def _get_last_assistant_content(hass: HomeAssistant, conversation_id: str) -> str:
    from homeassistant.components.conversation.chat_log import AssistantContent
    content = _get_chat_log_content(hass, conversation_id)
    if not content:
        return ""
    for item in reversed(content):
        if isinstance(item, AssistantContent) and item.content:
            return item.content.strip()
    return ""


async def _call_external_agent_with_timeout(
    hass: HomeAssistant,
    original_async_converse,
    *,
    text: str,
    conversation_id,
    context,
    language,
    agent_id: str,
    device_id,
    satellite_id,
    extra_system_prompt,
):
    timeout = get_configured_pipeline_timeout(hass)
    try:
        await async_save_live_turn_snapshot(
            hass,
            conversation_id=str(conversation_id or "default"),
            active=True,
            status="running",
            text=text,
            phase="external_agent",
        )
    except Exception:
        LOGGER.debug("Failed to persist external-agent start snapshot", exc_info=True)
    try:
        return await asyncio.wait_for(
            original_async_converse(
                hass,
                text,
                conversation_id,
                context,
                language,
                agent_id,
                device_id,
                satellite_id,
                extra_system_prompt,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            await async_save_live_turn_snapshot(
                hass,
                conversation_id=str(conversation_id or "default"),
                active=False,
                status="timeout",
                reason=f"External agent timed out after {timeout}s",
                text=text,
                current_thought=_get_last_assistant_content(hass, conversation_id),
                tool_results=_snapshot_tool_results(get_tool_results_state(hass)),
                phase="external_agent_timeout",
            )
        except Exception:
            LOGGER.debug("Failed to persist external-agent timeout snapshot", exc_info=True)
        raise


async def _trim_chat_log_for_context_overflow(hass: HomeAssistant, conversation_id: str, *, summary_agent_id: str = "", force: bool = False) -> None:
    from ..llm.context_compressor import compress_chat_log, apply_pending_compression
    if apply_pending_compression(hass, conversation_id):
        return
    await compress_chat_log(hass, conversation_id, summary_agent_id=summary_agent_id, force=force)


def _schedule_background_compression_if_needed(hass: HomeAssistant, conversation_id: str, *, summary_agent_id: str = "") -> None:
    from ..llm.context_compressor import schedule_background_compression
    schedule_background_compression(hass, conversation_id, summary_agent_id=summary_agent_id)


def _strip_image_blocks_from_chat(chat_content: list) -> int:
    stripped = 0
    for item in chat_content:
        content = getattr(item, "content", None)
        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    stripped += 1
                    continue
                new_content.append(part)
            if stripped and new_content:
                item.content = new_content
            elif stripped and not new_content:
                item.content = "[image content removed - model does not support vision]"
    return stripped


def _snapshot_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:

    return copy.deepcopy(tool_results)


def _build_tool_summary(tool_results: list[dict[str, Any]]) -> str:

    return (
        extract_successful_tool_response(tool_results)
        or extract_failed_tool_response(tool_results)
        or ""
    )


def _all_tools_succeeded(tool_results: list[dict[str, Any]]) -> bool:
    if not tool_results:
        return False
    for entry in tool_results:
        if not isinstance(entry, dict):
            return False
        if entry.get("success") is False or entry.get("error"):
            return False
    return True


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


def _resolve_history_write_id(
    hass: HomeAssistant,
    conversation_id,
    conv_history,
) -> str:
    current_id = str(conversation_id or "default")
    resume_id = get_active_resume_history_id(hass)
    if resume_id and conv_history.get_history(resume_id):
        return resume_id
    cont_id = str(get_conversation_status(hass).get("history_continuation_id") or "")
    if cont_id and cont_id != current_id and conv_history.get_history(cont_id):
        return cont_id
    return current_id


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
    title_agent_ids: list[str] | None = None,
) -> tuple[str, bool, str | None]:

    plain = response.speech.get("plain", {}) if isinstance(response.speech, dict) else {}
    raw = plain.get("original_speech", plain.get("speech", ""))
    final_text = sanitize_response_text(raw)
    if not final_text:
        return "", False, None

    converse_channel = original_async_converse or get_runtime_store(hass).get(
        "original_async_converse"
    )
    verdict_suffix, should_continue, continuation_prompt = await _gate_goal(
        hass,
        conversation_id=conversation_id,
        final_text=final_text,
        original_async_converse=converse_channel,
    )
    if verdict_suffix or should_continue:
        LOGGER.info(
            "goal gate result: suffix=%r continue=%s prompt=%r",
            verdict_suffix[:80] if verdict_suffix else "", should_continue, bool(continuation_prompt),
        )
    pending = get_runtime_store(hass).setdefault("pending_goal_continuations", {})
    completed = get_runtime_store(hass).setdefault("completed_goal_conversations", set())
    conv_key = str(conversation_id or "default")
    if should_continue and continuation_prompt:
        completed.discard(conv_key)
        pending[conv_key] = continuation_prompt
        pending["latest"] = continuation_prompt
        try:
            await async_save_live_turn_snapshot(
                hass,
                conversation_id=conv_key,
                active=True,
                status="pending_continuation",
                reason=verdict_suffix,
                text=continuation_prompt,
                phase="goal_continuation",
            )
        except Exception:
            LOGGER.debug("Failed to persist pending goal continuation", exc_info=True)
    else:
        pending.pop(conv_key, None)
        pending.pop("latest", None)
        if verdict_suffix:
            completed.add(conv_key)
    if verdict_suffix:
        final_text = f"{final_text}\n\n{verdict_suffix}"
        from ..history.native_chatlog_bridge import append_final_message, emit_live_content_delta
        await emit_live_content_delta(agent_id=agent_id, text=f"\n\n{verdict_suffix}")
        append_final_message(agent_id=agent_id, content=final_text)

    plain["speech"] = final_text
    plain["original_speech"] = final_text
    task_loop["history"].append({"role": "assistant", "content": final_text})
    record_response(
        hass,
        response_text=final_text,
        agent_id=agent_id,
    )
    tool_calls = list(get_tool_calls_state(hass))
    agent_name = get_agent_name(hass, agent_id)
    conv_status = get_conversation_status(hass)
    display_lang = conv_status.get("user_language") or language or hass.config.language or "zh"
    assistant_display = format_reply_speech(agent_name, final_text, display_lang)
    history_id = _resolve_history_write_id(hass, conversation_id, conv_history)
    channel = _detect_channel(conversation_id, conv_status)
    conv_history.add_turn(
        history_id,
        original_text,
        final_text,
        tool_calls=tool_calls,
        metadata={
            "agent_id": agent_id,
            "agent_name": agent_name,
            "assistant_display": assistant_display,
            "language": language or "",
            "channel": channel,
        },
    )
    if not conv_history.get_conversation_title(history_id):
        title_agent_ids = [agent_id for agent_id in (title_agent_ids or [agent_id]) if agent_id]
        hass.async_create_task(
            async_generate_history_title(
                hass,
                conv_history=conv_history,
                conversation_id=history_id,
                title_agent_ids=title_agent_ids,
                language=(
                    get_conversation_status(hass).get("user_language")
                    or language
                    or getattr(hass.config, "language", None)
                    or "en"
                ),
            )
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
        loaded_skills=consume_loaded_skills(hass, conversation_id),
        tool_results=tool_results,
    )
    if should_continue and not continuation_prompt:
        LOGGER.warning(
            "goal: judge said continue but no continuation_prompt — Ralph loop will stall",
        )
    get_tool_calls_state(hass).clear()
    try:
        live_parts = get_runtime_store(hass).get("live_response_parts", {})
        live_parts.pop(str(conversation_id or "default"), None)
    except Exception:
        pass
    return final_text, should_continue, continuation_prompt if should_continue else None


async def _gate_goal(
    hass: HomeAssistant,
    *,
    conversation_id,
    final_text: str,
    original_async_converse,
) -> tuple[str, bool, str | None]:
    if not conversation_id:
        return "", False, None
    mgr = get_goal_manager(hass, conversation_id)
    await mgr.async_ensure_loaded()
    if not mgr.is_active():
        return "", False, None
    try:
        decision = await mgr.async_evaluate_after_turn(final_text)
    except Exception as err:
        LOGGER.debug("goal: evaluate_after_turn failed: %s", err, exc_info=True)
        return "", False, None
    suffix = (decision.get("message") or "").strip()
    return (
        suffix,
        bool(decision.get("should_continue")),
        decision.get("continuation_prompt"),
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
    title_agent_ids: list[str] | None = None,
) -> Any:

    await async_bridge_native_chatlog_turn(
        hass,
        agent_id=agent_id,
        response_text=response_text,
    )
    tool_results = _snapshot_tool_results(get_tool_results_state(hass))
    final_text, goal_continuing, cont_prompt = await _finalize_completed_response(
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
        title_agent_ids=title_agent_ids,
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

    if _all_tools_succeeded(tool_results):
        result.continue_conversation = bool(goal_continuing)
    else:
        result.continue_conversation = goal_continuing or not is_user_done_text(
            user_text, detect_user_ending_intent
        )
    record_turn_activity(hass)
    set_current_thought(hass, None)
    _schedule_background_compression_if_needed(hass, conversation_id)
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
    title_agent_ids: list[str] | None = None,
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
    final_text, goal_continuing, cont_prompt = await _finalize_completed_response(
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
        title_agent_ids=title_agent_ids,
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

    if _all_tools_succeeded(tool_results):
        result.continue_conversation = bool(goal_continuing)
    else:
        result.continue_conversation = goal_continuing or not is_user_done_text(
            user_text, detect_user_ending_intent
        )
    record_turn_activity(hass)
    _schedule_background_compression_if_needed(hass, conversation_id)
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


_TRANSIENT_BACKOFF_BASE = 2.0
_TRANSIENT_BACKOFF_CAP = 15.0
_TRANSIENT_JITTER_MAX = 1.5


async def _schedule_transient_retry(
    agent_queue: list[str],
    *,
    current_agent_id: str,
    primary_agent_id: str | None,
    transient_retry_counts: dict[str, int],
    max_retries: int,
) -> tuple[bool, int]:

    import random
    retries = transient_retry_counts.get(current_agent_id, 0)
    if retries >= max_retries:
        return False, retries

    retries += 1
    transient_retry_counts[current_agent_id] = retries
    agent_queue.insert(0, current_agent_id)
    backoff = min(_TRANSIENT_BACKOFF_CAP, _TRANSIENT_BACKOFF_BASE * (2 ** (retries - 1)))
    jitter = random.uniform(0, _TRANSIENT_JITTER_MAX)
    wait = backoff + jitter
    LOGGER.info(
        "Transient retry backoff: agent=%s attempt=%d/%d wait=%.1fs",
        current_agent_id, retries, max_retries, wait,
    )
    await asyncio.sleep(wait)
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


def _resolve_history_context_id(
    hass: HomeAssistant,
    conversation_id,
    conv_history,
) -> str | None:
    current_id = str(conversation_id or "default")
    status = get_conversation_status(hass)

    resume_id = get_active_resume_history_id(hass)
    if resume_id and conv_history.get_history(resume_id):
        return resume_id
    if status.get("resume_history_conversation_id"):
        clear_resume_history_binding(hass)

    cont_id = str(status.get("history_continuation_id") or "")
    for candidate in (
        current_id,
        cont_id,
        status.get("last_conversation_id"),
        get_active_conversation_state(hass).get("id"),
    ):
        candidate = str(candidate or "")
        if candidate and conv_history.get_history(candidate):
            return candidate
    return None


def build_recovered_history_context_prompt(
    hass: HomeAssistant,
    *,
    conversation_id,
    conv_history,
) -> str:
    history_id = _resolve_history_context_id(
        hass,
        conversation_id,
        conv_history,
    )
    if not history_id:
        return ""

    recent_context = conv_history.get_recent_context(
        history_id,
        max_turns=6,
        include_tools=True,
    )
    if not recent_context:
        return ""

    return (
        "## Recovered Conversation Context\n"
        f"History ID: {history_id}\n"
        f"Current window ID: {conversation_id or 'default'}\n\n"
        f"{recent_context}\n"
        "Continue using this recovered context. Do not ask the user to repeat it."
    )


def _build_fallback_extra_prompt(
    *,
    hass=None,
    base_prompt: str | None,
    user_text: str,
    pending_handoff_context: str,
    previous_tool_results: list[dict[str, Any]],
) -> str | None:
    del hass
    del user_text

    required_suffix_sections = [pending_handoff_context] if pending_handoff_context else []
    optional_tail_sections: list[str] = []

    tool_results_prompt = _build_tool_results_prompt(previous_tool_results)
    if tool_results_prompt:
        optional_tail_sections.append(tool_results_prompt)

    required_prompt = _build_budgeted_prompt(
        head_sections=[],
        required_prefix_sections=[],
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


_RECOVERABLE_TOOL_ERROR_HINTS = (
    "missing required parameter",
    "missing parameter",
    "invalid parameter",
    "unknown action",
    "missing action",
    "invalid action",
    "expected type",
    "not a valid value",
    "extra keys not allowed",
    "required key not provided",
    "for dictionary value",
    "'action'",
    "keyerror",
    "key error",
    "got an unexpected keyword",
    "unexpected keyword argument",
    "missing 1 required",
    "takes 0 positional",
)


def _is_recoverable_tool_failure(item: dict[str, Any]) -> bool:
    error = str(item.get("error") or "").lower()
    result = item.get("result")
    result_error = ""
    if isinstance(result, dict):
        result_error = str(result.get("error") or "").lower()
    combined = f"{error} {result_error}"
    return any(hint in combined for hint in _RECOVERABLE_TOOL_ERROR_HINTS)


def is_error_response(hass: HomeAssistant, result: Any) -> bool:

    if not result or not result.response:
        return True
    if result.response.response_type == intent.IntentResponseType.ERROR:
        return True

    tool_results = get_tool_results_state(hass)
    if tool_results:
        failed_tools = [item for item in tool_results if not item.get("success", True)]
        if failed_tools:
            all_recoverable = all(_is_recoverable_tool_failure(item) for item in failed_tools)
            if all_recoverable:
                LOGGER.info(
                    "Tool call(s) failed with recoverable parameter errors, "
                    "allowing AI to self-correct: %s",
                    [item.get("tool_name") for item in failed_tools],
                )
                return False
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


_UNAVAILABLE_ERROR_KEYWORDS = (
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

_TRANSIENT_ERROR_KEYWORDS = (
    "disconnected",
    "connection",
    "timeout",
    "timed out",
    "reset by peer",
    "broken pipe",
    "eof occurred",
    "cannot connect",
    "server disconnected",
    "ssl",
    "clientconnector",
    "serverdisconnected",
    "ai service",
    "returned an error",
    "service error",
    "internal error",
    "bad gateway",
    "502",
    "503",
    "504",
    "429",
    "rate limit",
    "overloaded",
    "temporarily unavailable",
    "try again",
    "error talking to api",
    "error code: 4",
    "error code: 5",
    "无法连接",
    "连接失败",
    "网络错误",
    "请检查网络",
    "连接超时",
    "响应超时",
    "服务器断开",
    "服务不可用",
    "请稍后再试",
    "ai 服务",
    "服务返回错误",
    "返回了错误",
)

_TOOL_PAIR_ERROR_KEYWORDS = (
    "tool_calls",
    "tool_call_id",
    "must be followed by",
    "messages with role 'tool'",
    'messages with role "tool"',
    "orphaned tool",
)

_IMAGE_CONTENT_ERROR_KEYWORDS = (
    "image_url",
    "unknown variant",
    "expected `text`",
    "expected text",
)

_EMPTY_CONTENT_ERROR_KEYWORDS = (
    "content parts are required",
    "content is required",
    "empty content",
    "empty response",
)


def _is_agent_unavailable_error(err: Exception, err_lower: str) -> bool:
    return any(kw in err_lower for kw in _UNAVAILABLE_ERROR_KEYWORDS)


def _is_transient_agent_error(err: Exception, err_lower: str) -> bool:
    return (
        isinstance(err, (TimeoutError, asyncio.TimeoutError))
        or any(kw in err_lower for kw in _TRANSIENT_ERROR_KEYWORDS)
    )


def _format_agent_errors_for_user(
    agent_errors: list[str],
    *,
    language: str | None,
) -> str:
    friendly_errors: list[str] = []
    seen: set[str] = set()
    for raw_error in agent_errors:
        body = raw_error
        if body.startswith("conversation.") and ":" in body:
            _, body = body.split(":", 1)
        friendly = prettify_agent_error(body.strip(), language=language) or t(
            "err_service_unavailable",
            language,
        )
        if friendly in seen:
            continue
        seen.add(friendly)
        friendly_errors.append(friendly)
    if not friendly_errors:
        return t("agents_all_failed", language)
    if len(friendly_errors) == 1:
        return friendly_errors[0]
    return "\n".join(f"- {error}" for error in friendly_errors)


def _drop_empty_assistant_messages(chat_content: list) -> int:
    from homeassistant.components.conversation.chat_log import AssistantContent

    removed = 0
    for item in list(chat_content):
        if not isinstance(item, AssistantContent) and getattr(item, "role", None) != "assistant":
            continue
        content = getattr(item, "content", None)
        if content:
            continue
        try:
            chat_content.remove(item)
            removed += 1
        except ValueError:
            pass
    return removed


async def _try_self_repair_agent_error(
    hass: HomeAssistant,
    conversation_id,
    *,
    err_lower: str,
    current_agent_id: str,
    transient_retry_counts: dict[str, int],
) -> str | None:
    chat_content = _get_chat_log_content(hass, conversation_id)
    if not chat_content:
        return None

    repair_kind = ""
    if any(kw in err_lower for kw in _TOOL_PAIR_ERROR_KEYWORDS):
        repair_kind = "tool_pairs"
    elif any(kw in err_lower for kw in _IMAGE_CONTENT_ERROR_KEYWORDS):
        repair_kind = "image_content"
    elif any(kw in err_lower for kw in _EMPTY_CONTENT_ERROR_KEYWORDS):
        repair_kind = "empty_assistant"
    else:
        return None

    repair_key = f"__self_repair:{current_agent_id}:{repair_kind}"
    if transient_retry_counts.get(repair_key, 0) >= 1:
        return None
    transient_retry_counts[repair_key] = 1

    if repair_kind == "tool_pairs":
        try:
            from ..llm.context_compressor import sanitize_tool_pairs

            repaired = sanitize_tool_pairs(chat_content)
            if repaired is not chat_content:
                chat_content.clear()
                chat_content.extend(repaired)
            return "repaired tool-call history"
        except Exception as err:
            LOGGER.debug("Self-repair of tool-call history failed: %s", err)
            return None

    if repair_kind == "image_content":
        stripped = _strip_image_blocks_from_chat(chat_content)
        if stripped:
            return f"removed {stripped} incompatible image block(s)"
        return None

    removed = _drop_empty_assistant_messages(chat_content)
    if removed:
        return f"removed {removed} empty assistant message(s)"
    return None


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
    is_first_turn: bool = False,
) -> Any:

    task_loop = get_task_loop_state(hass)
    agent_errors: list[str] = []
    previous_tool_results: list[dict[str, Any]] = []
    handoff_replies: list[tuple[str, str]] = []
    base_extra_prompt = extra_system_prompt
    pending_handoff_context = ""
    title_agent_ids = list(
        dict.fromkeys(
            agent_id
            for agent_id in (
                fallback_agents[2] if len(fallback_agents) > 2 else "",
                fallback_agents[0] if fallback_agents else "",
            )
            if agent_id
        )
    )

    ha_internal_agent = "conversation.home_assistant" if len(text) <= 200 else ""
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
                    title_agent_ids=title_agent_ids,
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
                        from ..llm.response_format import language_of, reply_labels

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
                            title_agent_ids=title_agent_ids,
                        )
                        from ..output.reply_formatter import stamp_plain
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

    _MAX_TRANSIENT_RETRIES = 3
    transient_retry_counts: dict[str, int] = {}
    primary_external_agent = ordered_agents[0] if ordered_agents else None

    agent_queue = list(ordered_agents)
    while agent_queue:
        if get_should_end_flag(hass).get("value"):
            LOGGER.info("Stop signal detected, breaking agent fallback chain")
            break
        current_agent_id = agent_queue.pop(0)
        get_conversation_status(hass)["current_agent_id"] = current_agent_id
        tool_results_state = get_tool_results_state(hass)
        tool_results_state.clear()
        tool_calls_state = get_tool_calls_state(hass)
        tool_calls_state.clear()

        if is_first_turn:
            current_extra_prompt = _build_fallback_extra_prompt(
                hass=hass,
                base_prompt=base_extra_prompt,
                user_text=text,
                pending_handoff_context=pending_handoff_context,
                previous_tool_results=previous_tool_results,
            )
        else:
            current_extra_prompt = None

        _ctx_continue_hint = transient_retry_counts.pop("__ctx_continue_hint", None)
        if _ctx_continue_hint:
            if current_extra_prompt:
                current_extra_prompt = f"{current_extra_prompt}\n{_ctx_continue_hint}"
            else:
                current_extra_prompt = _ctx_continue_hint

        try:
            from ..llm.context_compressor import get_compressor, run_deferred_compression
            run_deferred_compression(hass, conversation_id)
            _cc = get_compressor()
            if _cc.preflight_check(_get_chat_log_content(hass, conversation_id)):
                LOGGER.info("Preflight compression: context exceeds threshold, compressing before API call")
                await _trim_chat_log_for_context_overflow(hass, conversation_id)
        except Exception as _pf_err:
            LOGGER.debug("Preflight compression check failed: %s", _pf_err)

        try:
            from ..llm.context_compressor import sanitize_tool_pairs
            _chat_content = _get_chat_log_content(hass, conversation_id)
            if _chat_content:
                _repaired = sanitize_tool_pairs(_chat_content)
                if _repaired is not _chat_content:
                    _chat_content.clear()
                    _chat_content.extend(_repaired)
        except Exception as _san_err:
            LOGGER.debug("Pre-API tool pair sanitize failed: %s", _san_err)

        try:
            tool_mode_token = set_runtime_tool_mode("native")
            try:
                result = await _call_external_agent_with_timeout(
                    hass,
                    original_async_converse,
                    text=text,
                    conversation_id=conversation_id,
                    context=context,
                    language=language,
                    agent_id=current_agent_id,
                    device_id=device_id,
                    satellite_id=satellite_id,
                    extra_system_prompt=current_extra_prompt,
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

                _CTX_TOO_LONG_HINTS = ("context_length_exceeded", "context length", "token_limit", "input too long", "context too long", "message too long", "max_tokens", "token limit", "too large")
                _is_ctx_too_long = any(h in failure_reason.lower() for h in _CTX_TOO_LONG_HINTS) or any(
                    h in error_probe.lower() for h in _CTX_TOO_LONG_HINTS
                )

                _IMAGE_URL_HINTS = ("image_url", "unknown variant", "expected `text`")
                _is_image_url_error = any(h in failure_reason.lower() for h in _IMAGE_URL_HINTS) or any(
                    h in error_probe.lower() for h in _IMAGE_URL_HINTS
                )
                _img_attempts = transient_retry_counts.get("__image_strip_attempts", 0)
                if _is_image_url_error and _img_attempts < 1:
                    transient_retry_counts["__image_strip_attempts"] = _img_attempts + 1
                    _chat_content = _get_chat_log_content(hass, conversation_id)
                    if _chat_content:
                        _stripped = _strip_image_blocks_from_chat(_chat_content)
                        if _stripped:
                            LOGGER.info(
                                "Agent %s hit image_url error; stripped %d image blocks and retrying",
                                current_agent_id, _stripped,
                            )
                            agent_queue.insert(0, current_agent_id)
                            continue

                if _is_ctx_too_long:
                    from ..llm.context_compressor import _estimate_total_tokens
                    _chat_chk = _get_chat_log_content(hass, conversation_id)
                    _est_chk = _estimate_total_tokens(_chat_chk or [])
                    if _est_chk < 20000:
                        _is_ctx_too_long = False
                _ctx_attempts = transient_retry_counts.get("__ctx_compress_attempts", 0)
                if _is_ctx_too_long and _ctx_attempts < 3:
                    transient_retry_counts["__ctx_compress_attempts"] = _ctx_attempts + 1
                    from ..llm.context_compressor import get_compressor
                    _cc = get_compressor()
                    _error_text = failure_reason or error_probe
                    _cc.step_down_context(_error_text)
                    await _trim_chat_log_for_context_overflow(hass, conversation_id, force=True)
                    try:
                        from ..llm.context_compressor import sanitize_tool_pairs
                        _rc = _get_chat_log_content(hass, conversation_id)
                        if _rc:
                            _rr = sanitize_tool_pairs(_rc)
                            if _rr is not _rc:
                                _rc.clear()
                                _rc.extend(_rr)
                    except Exception:
                        pass
                    _partial = agent_response_text if (agent_response_text and len(agent_response_text) > 50 and not _is_error_text) else ""
                    if _partial:
                        _continue_hint = (
                            f"\n\n[SYSTEM: Context was compressed due to length. "
                            f"Your previous partial response ({len(_partial)} chars) is preserved above. "
                            f"If the response was complete or nearly complete, just say 'OK' or provide a brief closing. "
                            f"If more content is needed, continue naturally from where you left off. "
                            f"Do NOT repeat what was already said.]"
                        )
                        transient_retry_counts["__ctx_continue_hint"] = _continue_hint
                    LOGGER.info(
                        "Agent %s hit context_length_exceeded (attempt %d/3); "
                        "context stepped to %d, compressed and retrying%s",
                        current_agent_id, _ctx_attempts + 1, _cc.context_length,
                        " with continuation hint" if _partial else "",
                    )
                    agent_queue.insert(0, current_agent_id)
                    continue

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
                        title_agent_ids=title_agent_ids,
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
                    if agent_queue:
                        pending_handoff_context = _build_agent_recovery_prompt(
                            failed_agent_name=get_agent_name(hass, current_agent_id),
                            original_text=original_text,
                            error=failure_reason,
                            tool_results=previous_tool_results,
                            task_loop=task_loop,
                        )
                    retried_now, retries = await _schedule_transient_retry(
                        agent_queue,
                        current_agent_id=current_agent_id,
                        primary_agent_id=primary_external_agent,
                        transient_retry_counts=transient_retry_counts,
                        max_retries=_MAX_TRANSIENT_RETRIES,
                    )
                    if retried_now:
                        LOGGER.info(
                            "Agent %s tool failure; retry %d/%d before fallback",
                            current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
                        )
                    else:
                        agent_errors.append(f"{current_agent_id}: {failure_reason[:160]}")
                    continue
                await async_record_agent_failure(
                    hass,
                    current_agent_id,
                    error=failure_reason,
                    conversation_id=conversation_id,
                    stage="response_error",
                )
                pending_handoff_context = _build_agent_recovery_prompt(
                    failed_agent_name=get_agent_name(hass, current_agent_id),
                    original_text=original_text,
                    error=failure_reason,
                    tool_results=previous_tool_results,
                    task_loop=task_loop,
                )
                retried_now, retries = await _schedule_transient_retry(
                    agent_queue,
                    current_agent_id=current_agent_id,
                    primary_agent_id=primary_external_agent,
                    transient_retry_counts=transient_retry_counts,
                    max_retries=_MAX_TRANSIENT_RETRIES,
                )
                if retried_now:
                    LOGGER.info(
                        "Agent %s error response; retry %d/%d before fallback",
                        current_agent_id, retries, _MAX_TRANSIENT_RETRIES,
                    )
                else:
                    agent_errors.append(f"{current_agent_id}: {failure_reason[:160]}")
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
                    title_agent_ids=title_agent_ids,
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
            is_unavailable = _is_agent_unavailable_error(err, err_lower)
            if is_unavailable:
                failure_reason = f"agent unavailable: {err_msg[:160]}"
                LOGGER.info(
                    "Agent %s unavailable (%s: %s), skipping",
                    current_agent_id, type(err).__name__, err_msg[:120],
                )
                await async_record_agent_failure(
                    hass,
                    current_agent_id,
                    error=failure_reason,
                    conversation_id=conversation_id,
                    stage="unavailable",
                )
                agent_errors.append(f"{current_agent_id}: {failure_reason}")
                continue

            _CTX_EXC_HINTS = ("context_length_exceeded", "context length", "token_limit", "input too long", "context too long", "message too long", "max_tokens", "token limit", "too large")
            _is_ctx_exc = any(h in err_lower for h in _CTX_EXC_HINTS)
            _is_timeout = isinstance(err, (TimeoutError, asyncio.TimeoutError)) or "timeout" in err_lower
            _is_transient_error = _is_transient_agent_error(err, err_lower)
            from ..llm.context_compressor import _estimate_total_tokens
            _chat_exc = _get_chat_log_content(hass, conversation_id)
            _est_exc = _estimate_total_tokens(_chat_exc or [])
            if _is_timeout and not _is_ctx_exc and _est_exc > 50000:
                _is_ctx_exc = True
            if _is_ctx_exc and _est_exc < 20000:
                _is_ctx_exc = False
            _ctx_exc_attempts = transient_retry_counts.get("__ctx_compress_exc_attempts", 0)
            if _is_ctx_exc and _ctx_exc_attempts < 3:
                _partial_output = _get_last_assistant_content(hass, conversation_id)
                if _partial_output and len(_partial_output) > 100:
                    LOGGER.info(
                        "Agent %s raised context exception but already has partial output (%d chars); preserving",
                        current_agent_id, len(_partial_output),
                    )
                    result = _build_synthesized_result(
                        language=language or hass.config.language,
                        conversation_id=conversation_id,
                        response_text=_partial_output,
                    )
                    agent_name = get_agent_name(hass, current_agent_id)
                    result = await _finalize_synthesized_success(
                        hass,
                        result=result,
                        agent_id=current_agent_id,
                        agent_name=agent_name,
                        response_text=_partial_output,
                        conversation_mode=conversation_mode,
                        conversation_id=conversation_id,
                        original_text=original_text,
                        user_text=text,
                        conv_history=conv_history,
                        task_loop=task_loop,
                        title_agent_ids=title_agent_ids,
                    )
                    await async_record_agent_success(
                        hass,
                        current_agent_id,
                        conversation_id=conversation_id,
                    )
                    return result
                else:
                    transient_retry_counts["__ctx_compress_exc_attempts"] = _ctx_exc_attempts + 1
                    try:
                        from ..llm.context_compressor import get_compressor
                        _cc = get_compressor()
                        _cc.step_down_context(err_msg)
                        await _trim_chat_log_for_context_overflow(hass, conversation_id, force=True)
                        try:
                            from ..llm.context_compressor import sanitize_tool_pairs
                            _rc2 = _get_chat_log_content(hass, conversation_id)
                            if _rc2:
                                _rr2 = sanitize_tool_pairs(_rc2)
                                if _rr2 is not _rc2:
                                    _rc2.clear()
                                    _rc2.extend(_rr2)
                        except Exception:
                            pass
                        LOGGER.info(
                            "Agent %s raised context-too-long exception (attempt %d/2); "
                            "context stepped to %d, compressed and retrying same agent",
                            current_agent_id, _ctx_exc_attempts + 1, _cc.context_length,
                        )
                    except Exception as _comp_err:
                        LOGGER.debug("Exception-path compression failed: %s", _comp_err)
                    agent_queue.insert(0, current_agent_id)
                    continue

            repair_detail = await _try_self_repair_agent_error(
                hass,
                conversation_id,
                err_lower=err_lower,
                current_agent_id=current_agent_id,
                transient_retry_counts=transient_retry_counts,
            )
            if repair_detail:
                LOGGER.info(
                    "Agent %s error self-repaired (%s); retrying same agent once",
                    current_agent_id,
                    repair_detail,
                )
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
                    title_agent_ids=title_agent_ids,
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
            partial_output = _get_last_assistant_content(hass, conversation_id)
            if _is_transient_error and partial_output and len(partial_output) > 100:
                result = _build_synthesized_result(
                    language=language or hass.config.language,
                    conversation_id=conversation_id,
                    response_text=partial_output,
                )
                agent_name = get_agent_name(hass, current_agent_id)
                result = await _finalize_synthesized_success(
                    hass,
                    result=result,
                    agent_id=current_agent_id,
                    agent_name=agent_name,
                    response_text=partial_output,
                    conversation_mode=conversation_mode,
                    conversation_id=conversation_id,
                    original_text=original_text,
                    user_text=text,
                    conv_history=conv_history,
                    task_loop=task_loop,
                    title_agent_ids=title_agent_ids,
                )
                LOGGER.info(
                    "Agent %s raised transient error after partial output; preserved %d chars",
                    current_agent_id,
                    len(partial_output),
                )
                await async_record_agent_success(
                    hass,
                    current_agent_id,
                    conversation_id=conversation_id,
                )
                return result
            await async_record_agent_failure(
                hass,
                current_agent_id,
                error=err_msg,
                conversation_id=conversation_id,
                stage="exception",
            )
            retried_now, retries = await _schedule_transient_retry(
                agent_queue,
                current_agent_id=current_agent_id,
                primary_agent_id=primary_external_agent,
                transient_retry_counts=transient_retry_counts,
                max_retries=_MAX_TRANSIENT_RETRIES,
            )
            if retried_now:
                LOGGER.info(
                    "Agent %s exception; retry %d/%d before moving on: %s",
                    current_agent_id, retries, _MAX_TRANSIENT_RETRIES, err_msg[:120],
                )
            else:
                if "content parts are required" in err_msg:
                    agent_errors.append(f"{current_agent_id}: empty response")
                elif _is_transient_error:
                    agent_errors.append(f"{current_agent_id}: transient service error: {err_msg[:120]}")
                else:
                    agent_errors.append(f"{current_agent_id}: {err_msg[:100]}")
            continue

    resolved_lang = language or hass.config.language or "en"

    if consume_should_end_flag(hass):
        LOGGER.info("Agent fallback chain stopped by user command")
        from homeassistant.components.conversation import ConversationResult
        intent_response = intent.IntentResponse(language=resolved_lang)
        intent_response.async_set_speech("")
        stop_result = ConversationResult(
            response=intent_response, conversation_id=conversation_id
        )
        stop_result.continue_conversation = False
        return stop_result

    if agent_errors:
        raw_error_detail = "; ".join(agent_errors)
        error_detail = _format_agent_errors_for_user(
            agent_errors,
            language=resolved_lang,
        )
    elif not fallback_agents:
        raw_error_detail = ""
        error_detail = t("agents_none_configured", resolved_lang)
    elif getattr(hass.state, "value", str(hass.state)) != "RUNNING":
        raw_error_detail = ""
        error_detail = t("agents_starting", resolved_lang)
    elif not ordered_agents:
        raw_error_detail = ""
        error_detail = t("agents_all_failed", resolved_lang)
    else:
        raw_error_detail = ""
        error_detail = t("agents_unavailable", resolved_lang)
    LOGGER.warning(
        "Agent fallback chain exhausted: %s%s",
        error_detail,
        f" (raw: {raw_error_detail})" if raw_error_detail else "",
    )

    intent_response = intent.IntentResponse(language=resolved_lang)
    intent_response.async_set_error(
        intent.IntentResponseErrorCode.UNKNOWN,
        error_detail,
    )
    from homeassistant.components.conversation import ConversationResult

    return ConversationResult(response=intent_response, conversation_id=conversation_id)
