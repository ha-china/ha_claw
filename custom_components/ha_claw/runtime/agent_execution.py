

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.conversation import intent, trace
from homeassistant.util import ulid

from .i18n import t
from .response_format import apply_agent_response_format, get_response_text

LOGGER = logging.getLogger(__name__)


def build_error_result(
    user_input: conversation.ConversationInput, message: str
) -> conversation.ConversationResult:

    intent_response = intent.IntentResponse(language=user_input.language)
    intent_response.async_set_speech(message)
    return conversation.ConversationResult(
        conversation_id=user_input.conversation_id or ulid.ulid(),
        response=intent_response,
    )


async def process_agent_turn(
    hass,
    *,
    agent_id: str,
    agent_name: str,
    user_input: conversation.ConversationInput,
    conversation_mode: str,
    previous_result: Any = None,
    search_results: str | None = None,
) -> conversation.ConversationResult:

    if not user_input.text or not user_input.text.strip():
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_error(
            intent.IntentResponseErrorCode.NO_INTENT_MATCH,
            t("no_valid_input", user_input.language),
        )
        return conversation.ConversationResult(
            conversation_id=user_input.conversation_id or ulid.ulid(),
            response=intent_response,
        )

    trace.async_conversation_trace_append(
        trace.ConversationTraceEventType.AGENT_DETAIL,
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "text": user_input.text[:100] + "..." if len(user_input.text) > 100 else user_input.text,
        },
    )

    try:
        agent = conversation.agent_manager.async_get_agent(hass, agent_id)
        result = await agent.async_process(user_input)

        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "response": (
                    result.response.speech["plain"]["speech"]
                    if result.response.speech and "plain" in result.response.speech
                    else "No response"
                ),
            },
        )
    except Exception as err:
        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "error": str(err),
            },
        )
        raise

    if result.response.speech and "plain" in result.response.speech:
        response_text = get_response_text(result)
        apply_agent_response_format(
            result,
            hass=hass,
            agent_name=agent_name,
            agent_id=agent_id,
            conversation_mode=conversation_mode,
            response_text=response_text,
            previous_result=previous_result,
            search_results=search_results,
        )

    return result
