from __future__ import annotations

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

EXPOSE_PATH = "Settings > Voice assistants > Expose > Add entity"


def assistant_id(llm_context: llm.LLMContext | None) -> str:
    assistant = getattr(llm_context, "assistant", None) if llm_context else None
    return assistant or "conversation"


def entity_is_exposed(hass: HomeAssistant, entity_id: str, llm_context: llm.LLMContext | None = None) -> bool:
    return async_should_expose(hass, assistant_id(llm_context), entity_id)


def privacy_blocked_response(entity_id: str, *, hint: str | None = None) -> dict:
    resp: dict = {
        "success": False,
        "error": f"Entity not exposed to assistant: {entity_id}",
        "privacy_blocked": True,
        "expose_path": EXPOSE_PATH,
    }
    if hint:
        resp["hint"] = hint
    return resp


def domain_unexposed_response(hass: HomeAssistant, entity_id: str, domain: str, llm_context: llm.LLMContext | None = None) -> dict | None:
    unexposed = [
        s.entity_id for s in hass.states.async_all()
        if s.entity_id.startswith(f"{domain}.")
        and not entity_is_exposed(hass, s.entity_id, llm_context)
    ]
    if unexposed:
        return {
            "success": False,
            "error": f"Entity not found: {entity_id}",
            "privacy_blocked": True,
            "expose_path": EXPOSE_PATH,
            "hint": f"{len(unexposed)} unexposed {domain} entities exist",
        }
    return None
