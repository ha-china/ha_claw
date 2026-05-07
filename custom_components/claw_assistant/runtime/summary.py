

from __future__ import annotations

import logging
import re

from custom_components.claw_assistant.const import (
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
)
from .agent_fallback import get_agent_name, is_error_response

from .internal_llm import (
    _MAX_SYSTEM_PROMPT_CHARS,
    _PROMPT_SECTION_SEPARATOR,
    _fit_sections_to_budget_preserving_suffix,
    _trim_prompt,
)
from .response_format import language_of, reply_labels, sanitize_response_text
from .state import get_tool_calls_state, get_tool_results_state

LOGGER = logging.getLogger(__name__)
_MAX_SUMMARY_PROMPT_CHARS = _MAX_SYSTEM_PROMPT_CHARS


def _join_summary_sections(*sections: str) -> str:

    return _PROMPT_SECTION_SEPARATOR.join(
        section for section in sections if section.strip()
    )


def _build_summary_prompt(
    text: str,
    primary_responses: list[dict[str, str]],
    language: str | None = None,
) -> str:

    intro = "Summarize and refine the following AI responses for the user request."
    request_section = f"## User Request\n{text}"
    response_sections = [
        f"## Candidate Response {index} ({response['agent_name']})\n"
        f"{response['response']}"
        for index, response in enumerate(primary_responses, start=1)
    ]
    lang_instruction = (
        "- Reply in the same language as the user request"
        if not language
        else f"- Reply in {language}"
    )
    instructions = f"""
First analyze the strengths and weaknesses of the responses, then provide a final answer.
Use this exact format:

---analysis---
Write your comparison of the responses here. Evaluate accuracy, completeness, and relevance.

---answer---
Write the final user-facing answer here in your own words.

Requirements:
- Include both sections, separated by ---analysis--- and ---answer---
{lang_instruction}
- Keep the total response under 550 words
- Do not use numbered or bullet lists; write natural paragraphs
- Only summarize and analyze. Do not execute actions, call tools, or control devices
""".strip()

    prompt = _join_summary_sections(
        intro,
        request_section,
        *response_sections,
        instructions,
    )
    if len(prompt) <= _MAX_SUMMARY_PROMPT_CHARS:
        return prompt

    required_prompt = _join_summary_sections(intro, instructions)
    core_prompt = _join_summary_sections(intro, request_section, instructions)
    if len(core_prompt) > _MAX_SUMMARY_PROMPT_CHARS:
        remaining_chars = (
            _MAX_SUMMARY_PROMPT_CHARS
            - len(required_prompt)
            - len(_PROMPT_SECTION_SEPARATOR)
        )
        if remaining_chars <= 0:
            return required_prompt
        request_section = _trim_prompt(request_section, max_chars=remaining_chars)
        core_prompt = _join_summary_sections(intro, request_section, instructions)
        if len(core_prompt) >= _MAX_SUMMARY_PROMPT_CHARS:
            return core_prompt

    remaining_chars = (
        _MAX_SUMMARY_PROMPT_CHARS
        - len(core_prompt)
        - len(_PROMPT_SECTION_SEPARATOR)
    )
    if remaining_chars <= 0:
        return core_prompt

    fitted_responses = _fit_sections_to_budget_preserving_suffix(
        response_sections,
        max_chars=remaining_chars,
    )
    return _join_summary_sections(intro, request_section, fitted_responses, instructions)


async def process_ai_summary(
    hass,
    text: str,
    conversation_id,
    context,
    language,
    fallback_agents: list[str],
    conversation_mode: str,
    original_async_converse,
    extra_system_prompt,
    device_id,
    satellite_id,
):
    has_summary_agent = len(fallback_agents) >= 3
    processing_agents = fallback_agents[:-1] if has_summary_agent else fallback_agents
    summary_agent = fallback_agents[-1] if has_summary_agent else ""
    summary_agent_name = get_agent_name(hass, summary_agent) if summary_agent else ""

    primary_responses = []
    base_result = None
    for proc_agent in processing_agents:
        try:
            get_tool_results_state(hass).clear()
            get_tool_calls_state(hass).clear()
            proc_result = await original_async_converse(
                hass,
                text,
                conversation_id,
                context,
                language,
                proc_agent,
                device_id,
                satellite_id,
                extra_system_prompt,
            )
            if (
                proc_result
                and proc_result.response
                and proc_result.response.speech
                and "plain" in proc_result.response.speech
            ):
                from .reply_formatter import strip_reply_prefix
                resp_text = sanitize_response_text(
                    strip_reply_prefix(
                        proc_result.response.speech["plain"].get("speech", "").strip()
                    )
                )
                if resp_text and not is_error_response(hass, proc_result):
                    if base_result is None:
                        base_result = proc_result
                    primary_responses.append(
                        {
                            "agent_name": get_agent_name(hass, proc_agent),
                            "response": resp_text,
                        }
                    )
        except Exception as err:
            LOGGER.debug("Agent %s failed in summary mode: %s", proc_agent, err)

    if not primary_responses:
        return None

    if not has_summary_agent:
        if len(primary_responses) < 2 or base_result is None:
            return None

        labels = reply_labels(language_of(base_result))
        reply_word = labels["reply"]
        from .reply_formatter import format_reply_speech
        combined_responses = "\n\n\u200b---\n\n".join(
            format_reply_speech(response['agent_name'], response['response'], language_of(base_result))
            for response in primary_responses
        )
        if (
            base_result.response
            and base_result.response.speech
            and "plain" in base_result.response.speech
        ):
            base_result.response.speech["plain"]["speech"] = combined_responses
            base_result.response.speech["plain"]["original_speech"] = combined_responses
            base_result.response.speech["plain"]["agent_name"] = primary_responses[-1][
                "agent_name"
            ]
            base_result.response.speech["plain"]["agent_id"] = processing_agents[-1]
        return base_result

    summary_prompt = _build_summary_prompt(text, primary_responses, language=language)

    try:
        get_tool_results_state(hass).clear()
        get_tool_calls_state(hass).clear()
        summary_result = await original_async_converse(
            hass,
            summary_prompt,
            conversation_id,
            context,
            language,
            summary_agent,
            device_id,
            satellite_id,
            extra_system_prompt,
        )
        if (
            summary_result
            and summary_result.response
            and summary_result.response.speech
            and "plain" in summary_result.response.speech
        ):
            raw_text = summary_result.response.speech["plain"].get("speech", "").strip()
            raw_text = sanitize_response_text(raw_text)
            if raw_text and not is_error_response(hass, summary_result):
                analysis_part = ""
                summary_part = raw_text
                analysis_match = re.search(
                    r"(?:---)?(?:analysis|分析)(?:---)?[：:\n](.+?)(?:(?:---)?(?:answer|summary|总结)(?:---)?|$)",
                    raw_text,
                    re.DOTALL,
                )
                summary_match = re.search(
                    r"(?:---)?(?:answer|summary|总结)(?:---)?[：:\n](.+?)$",
                    raw_text,
                    re.DOTALL,
                )
                if analysis_match:
                    analysis_part = analysis_match.group(1).strip()
                if summary_match:
                    summary_part = summary_match.group(1).strip()

                labels = reply_labels(language_of(summary_result))
                reply_word = labels["reply"]
                summary_word = labels["summary"]
                multi_agent_summary = len(primary_responses) >= 2

                combined_responses = ""
                if multi_agent_summary:
                    for response in primary_responses:
                        combined_responses += (
                            format_reply_speech(response['agent_name'], response['response'], language_of(base_result))
                            + "\n\n---\n\n"
                        )
                    if analysis_part:
                        combined_responses += f"{analysis_part}\n\n---\n\n"
                    from .reply_formatter import format_labeled_speech
                    combined_responses += format_labeled_speech(
                        summary_agent_name, summary_part, summary_word
                    )

                if conversation_mode == CONVERSATION_MODE_DETAILED:
                    detailed = combined_responses or format_labeled_speech(
                        summary_agent_name, summary_part, summary_word
                    )
                    summary_result.response.speech["plain"]["speech"] = detailed
                elif conversation_mode == CONVERSATION_MODE_ADD_NAME:
                    summary_result.response.speech["plain"]["speech"] = (
                        combined_responses
                        if multi_agent_summary
                        else format_labeled_speech(
                            summary_agent_name, summary_part, summary_word
                        )
                    )
                else:
                    summary_result.response.speech["plain"]["speech"] = summary_part

                summary_result.response.speech["plain"]["original_speech"] = (
                    summary_result.response.speech["plain"]["speech"]
                    if multi_agent_summary
                    else summary_part
                )
                summary_result.response.speech["plain"]["agent_name"] = summary_agent_name
                summary_result.response.speech["plain"]["agent_id"] = summary_agent
                return summary_result
    except Exception as err:
        LOGGER.debug("Summary agent failed: %s", err)

    return None
