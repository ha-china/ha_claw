from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.conversation import agent_manager
from homeassistant.core import HomeAssistant
from homeassistant.util import ulid

from ..conversation_utils import ConversationHistory
from .reply_formatter import is_chinese

LOGGER = logging.getLogger(__name__)

_TITLE_WORD_LIMIT = 10
_TITLE_CHAR_LIMIT = 10


def _clean_title(text: str, *, language: str) -> str:
    title = re.sub(r"^[\"'“”‘’`]+|[\"'“”‘’`]+$", "", (text or "").strip())
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" -:：。.!?")
    if is_chinese(language):
        return title[:_TITLE_CHAR_LIMIT] or ""

    words = title.split()
    if len(words) > _TITLE_WORD_LIMIT:
        title = " ".join(words[:_TITLE_WORD_LIMIT])
    return title[:80] or ""


def _build_title_prompt(turns: list[Any], *, language: str) -> str:
    if is_chinese(language):
        rules = "Rules: Chinese only. Maximum 10 Chinese characters. Clear and specific. Output only the title."
    else:
        rules = "Rules: English only. Maximum 10 words. Clear and specific. Output only the title."
    lines = [
        "Create a concise conversation title.",
        rules,
        "",
        "Conversation:",
    ]
    for turn in turns[-3:]:
        user = str(getattr(turn, "user_message", "") or "").strip()
        assistant = str(getattr(turn, "assistant_response", "") or "").strip()
        if user:
            lines.append(f"User: {user[:300]}")
        if assistant:
            lines.append(f"Assistant: {assistant[:300]}")
    return "\n".join(lines)


async def _generate_title_with_agent(
    hass: HomeAssistant,
    *,
    agent_id: str,
    prompt: str,
    language: str,
) -> str:
    agent = agent_manager.async_get_agent(hass, agent_id)
    if agent is None:
        return ""

    user_input = conversation.ConversationInput(
        text=prompt,
        conversation_id=f"history-title:{ulid.ulid()}",
        language=language,
        context=None,
        device_id=None,
        agent_id=agent_id,
        satellite_id=None,
    )
    result = await asyncio.wait_for(agent.async_process(user_input), timeout=30)
    plain = result.response.speech.get("plain", {}) if result and result.response else {}
    return _clean_title(str(plain.get("speech", "") or ""), language=language)


async def async_generate_history_title(
    hass: HomeAssistant,
    *,
    conv_history: ConversationHistory,
    conversation_id: str,
    title_agent_ids: list[str],
    language: str,
) -> None:
    if conv_history.get_conversation_title(conversation_id):
        return

    turns = conv_history.get_history(conversation_id)
    if not turns:
        return

    prompt = _build_title_prompt(turns, language=language)
    for agent_id in title_agent_ids:
        if not agent_id:
            continue
        try:
            title = await _generate_title_with_agent(
                hass,
                agent_id=agent_id,
                prompt=prompt,
                language=language,
            )
        except Exception as err:
            LOGGER.debug("History title generation failed for %s: %s", agent_id, err)
            continue
        if title:
            conv_history.set_conversation_title(conversation_id, title)
            return
