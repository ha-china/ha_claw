from __future__ import annotations

from homeassistant.core import HomeAssistant

from ..const import CONF_CONTINUOUS_CONVERSATION, DOMAIN

_STATE_KEY = "continuous_conversation"
_ID_PREFIX = "claw-continuous-"


def _enabled(hass: HomeAssistant) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.options.get(CONF_CONTINUOUS_CONVERSATION, False):
            return True
    return False


def _state(hass: HomeAssistant) -> dict[str, str]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(_STATE_KEY, {})


def get_effective_conversation_id(
    hass: HomeAssistant,
    requested_conversation_id: str | None,
) -> str | None:
    if not _enabled(hass):
        return requested_conversation_id

    state = _state(hass)
    current = state.get("conversation_id")
    if current:
        return current

    current = requested_conversation_id
    if not current or not current.startswith(_ID_PREFIX):
        current = f"{_ID_PREFIX}1"
    state["conversation_id"] = current
    return current


def start_new_conversation(hass: HomeAssistant, fallback_conversation_id: str | None) -> str:
    state = _state(hass)
    generation = int(state.get("generation", "1")) + 1
    conversation_id = f"{_ID_PREFIX}{generation}"
    state["generation"] = str(generation)
    state["conversation_id"] = conversation_id
    if fallback_conversation_id:
        state["previous_window_id"] = fallback_conversation_id
    return conversation_id


def continuous_conversation_enabled(hass: HomeAssistant) -> bool:
    return _enabled(hass)
