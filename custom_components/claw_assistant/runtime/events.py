

from __future__ import annotations

from homeassistant.core import HomeAssistant

EVENT_AI_RESPONSE = "ha_crack_ai_response"
EVENT_SHOULD_END = "ha_crack_should_end"
EVENT_THOUGHT = "ha_crack_thought"
EVENT_LIVE_PROGRESS = "ha_crack_live_progress"


def fire_ai_response(
    hass: HomeAssistant,
    *,
    response: str,
    user_request: str,
    conversation_id,
    iteration: int,
    agent_id: str,
) -> None:

    hass.bus.async_fire(
        EVENT_AI_RESPONSE,
        {
            "response": response,
            "user_request": user_request,
            "conversation_id": conversation_id,
            "iteration": iteration,
            "agent_id": agent_id,
        },
    )


def fire_should_end(
    hass: HomeAssistant,
    *,
    reason: str,
    conversation_id,
) -> None:

    hass.bus.async_fire(
        EVENT_SHOULD_END,
        {"reason": reason, "conversation_id": conversation_id},
    )


def fire_thought(hass: HomeAssistant, *, thought: str) -> None:

    hass.bus.async_fire(EVENT_THOUGHT, {"thought": thought})


def fire_live_progress(
    hass: HomeAssistant,
    *,
    conversation_id,
    phase: str,
    text: str,
    tool_name: str = "",
    display_text: str = "",
) -> None:

    hass.bus.async_fire(
        EVENT_LIVE_PROGRESS,
        {
            "conversation_id": conversation_id,
            "phase": phase,
            "text": text,
            "tool_name": tool_name,
            "display_text": display_text,
        },
    )
