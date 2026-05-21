

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from ..conversation_utils import get_conversation_history
from .config_file_store import build_config_approval_prompt_block
from .ha_guide_store import build_homeassistant_topic_hint
from .im_approval_bridge import build_im_approval_prompt_block
from .internal_llm import (
    _MAX_SYSTEM_PROMPT_CHARS,
    _PROMPT_SECTION_SEPARATOR,
    _fit_sections_to_budget_preserving_suffix,
    build_internal_llm_prompt,
)
from .native_chatlog_bridge import is_step_agent_id
from .options import ConversationRuntimeConfig
from .response_format import sanitize_response_text

LOGGER = logging.getLogger(__name__)
_MAX_BASE_PROMPT_CHARS = _MAX_SYSTEM_PROMPT_CHARS

_OUTPUT_MODE_GUIDANCE = {
    "brief": "Keep the reply short, decisive, and free of filler.",
    "detailed": "Use compact structure, but include key reasoning and concrete next steps.",
    "list": "Prefer short bullet lists over paragraphs when it improves clarity.",
    "code": "Prefer structured blocks, exact payloads, and literal snippets when relevant.",
}


def _build_runtime_preference_sections(
    runtime_config: ConversationRuntimeConfig,
) -> list[str]:
    sections: list[str] = []

    output_mode = runtime_config.output_mode.strip().lower()
    if output_mode in _OUTPUT_MODE_GUIDANCE:
        sections.append(
            "## Reply Style\n"
            f"- Active mode: {output_mode}\n"
            f"- Guidance: {_OUTPUT_MODE_GUIDANCE[output_mode]}"
        )

    global_inject = runtime_config.global_inject.strip()
    if global_inject:
        sections.append(f"## Persistent Runtime Guidance\n{global_inject}")

    return sections


def _join_prompt_sections(*sections: str) -> str:

    return _PROMPT_SECTION_SEPARATOR.join(section for section in sections if section.strip())


def _fit_base_prompt(
    core_prompt: str,
    appended_sections: list[str],
    *,
    max_chars: int = _MAX_BASE_PROMPT_CHARS,
) -> str:

    kept_sections = [section for section in appended_sections if section.strip()]
    prompt = _join_prompt_sections(core_prompt, *kept_sections)
    if len(prompt) <= max_chars:
        return prompt

    core_prompt = core_prompt.strip()
    if len(core_prompt) >= max_chars:
        return core_prompt[:max_chars].rstrip()

    remaining_chars = max_chars - len(core_prompt)
    if core_prompt and kept_sections:
        remaining_chars -= len(_PROMPT_SECTION_SEPARATOR)
    if remaining_chars <= 0:
        return core_prompt

    fitted_tail = _fit_sections_to_budget_preserving_suffix(
        kept_sections,
        max_chars=remaining_chars,
    )
    return _join_prompt_sections(core_prompt, fitted_tail)


def _build_unified_context(
    hass: HomeAssistant, conversation_id: str | None
) -> list[str]:

    try:
        from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS

        all_chat_logs = hass.data.get(DATA_CHAT_LOGS, {})
        if not conversation_id or conversation_id not in all_chat_logs:
            return []
        chat_log = all_chat_logs[conversation_id]
        msgs: list[str] = []
        for content in chat_log.content[-6:]:
            if content.role == "user":
                msgs.append(f"User: {content.content[:500]}")
            elif content.role == "assistant" and content.content:
                if is_step_agent_id(getattr(content, "agent_id", None)):
                    continue
                cleaned = sanitize_response_text(content.content)
                if cleaned:
                    msgs.append(f"Assistant: {cleaned[:500]}")
            elif content.role == "tool_result":
                status = (
                    "OK"
                    if content.tool_result.get("success", True)
                    else "FAILED"
                )
                msgs.append(f"Tool[{content.tool_name}]: {status}")
        return msgs
    except Exception as err:
        LOGGER.debug("Failed to get internal chat log: %s", err)
        return []


def _build_peer_agents_section(
    hass: HomeAssistant,
    runtime_config: ConversationRuntimeConfig,
) -> str:
    from homeassistant.helpers import entity_registry as er
    from .state import get_runtime_store, get_conversation_status

    runtime_store = get_runtime_store(hass)
    entry = runtime_store.get("config_entry")
    if entry is None:
        return ""

    from ..const import CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT
    options = entry.options
    ent_reg = er.async_get(hass)
    current_aid = str(get_conversation_status(hass).get("current_agent_id", "") or "")

    entries: list[str] = []
    seen: set[str] = set()
    for key in (CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT):
        aid = str(options.get(key, "") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        if aid == current_aid:
            continue
        ent = ent_reg.async_get(aid)
        name = (ent.name or ent.original_name) if ent else aid.split(".")[-1]
        entries.append(f"- {name}")

    if not entries:
        return ""

    return (
        "## Peer AI Agents\n"
        "Peers via `AgentHandoff` (explicit ask/blocker/second opinion):\n"
        + "\n".join(entries)
    )


def _resolve_user_language(hass: HomeAssistant) -> str:
    from .state import get_conversation_status
    lang = str(get_conversation_status(hass).get("user_language") or "").strip()
    return lang if lang else ""


def _language_display_name(lang_code: str) -> str:
    code = lang_code.lower().replace("-", "_")
    if code.startswith("zh"):
        return "Chinese (中文)"
    if code.startswith("en"):
        return "English"
    if code.startswith("ja"):
        return "Japanese (日本語)"
    if code.startswith("ko"):
        return "Korean (한국어)"
    if code.startswith("de"):
        return "German (Deutsch)"
    if code.startswith("fr"):
        return "French (Français)"
    if code.startswith("es"):
        return "Spanish (Español)"
    return lang_code


def build_base_prompt(
    hass: HomeAssistant,
    *,
    text: str,
    conversation_id: str | None,
    runtime_config: ConversationRuntimeConfig,
) -> str:
    from .state import (
        get_channel_type, is_im_channel, get_conversation_status,
        is_companion_app, is_mobile_platform, get_platform_display_name,
    )
    from .official_websocket_hook import get_frontend_platform

    base_prompt = build_internal_llm_prompt(text)
    static_sections: list[str] = []
    dynamic_sections: list[str] = []

    user_lang = _resolve_user_language(hass)
    lang_instruction = ""
    if user_lang:
        display = _language_display_name(user_lang)
        lang_instruction = (
            f" The user's UI language is {display}."
            f" You MUST reply in {display} unless the user explicitly asks for another language."
        )

    ch_type = get_channel_type(conversation_id)
    platform = get_frontend_platform(hass)
    conv_status = get_conversation_status(hass)
    detected_platform = conv_status.get("detected_platform")
    if detected_platform:
        platform = detected_platform
    platform_name = get_platform_display_name(platform)
    is_voice = conv_status.get("is_voice_pipeline", False)
    pipeline_end_stage = conv_status.get("_pipeline_end_stage", "")
    if "tts" in pipeline_end_stage:
        is_voice = True

    if is_voice:
        pass
    elif is_im_channel(conversation_id):
        static_sections.append(
            f"## Channel\n"
            f"Type: {ch_type} (instant messaging).\n"
            f"You are chatting inside an IM bot (WeChat / QQ / etc.). "
            f"The user reads your reply as a text message on their phone or desktop. "
            f"Full markdown is supported: bold, italic, lists, tables, code blocks, etc. "
            f"If the user sends an image or file, acknowledge it and describe what you see."
            f"{lang_instruction}"
        )
    elif is_companion_app(platform):
        static_sections.append(
            f"## Channel\n"
            f"Type: ha (Home Assistant Companion App).\n"
            f"Platform: {platform_name}.\n"
            f"The user is using the official Home Assistant mobile app. "
            f"The chat interface is rendered in a WebView inside the app. "
            f"Full markdown is supported. Rich media (images, videos) can be displayed. "
            f"The user is on a mobile device with a smaller screen. "
            f"Keep responses concise and mobile-friendly when appropriate. "
            f"This is NOT a voice channel — the user is reading, not listening."
            f"{lang_instruction}"
        )
    elif is_mobile_platform(platform):
        static_sections.append(
            f"## Channel\n"
            f"Type: ha (Home Assistant mobile web).\n"
            f"Platform: {platform_name}.\n"
            f"The user is accessing Home Assistant via a mobile browser. "
            f"Full markdown is supported. Rich media can be displayed. "
            f"The user is on a mobile device with a smaller screen. "
            f"Keep responses concise and mobile-friendly when appropriate. "
            f"This is NOT a voice channel — the user is reading, not listening."
            f"{lang_instruction}"
        )
    else:
        platform_info = f"Platform: {platform_name}.\n" if platform else ""
        static_sections.append(
            "## Channel\n"
            "Type: ha (Home Assistant frontend chat panel).\n"
            f"{platform_info}"
            "You are inside the Home Assistant web UI Assist chat window. "
            "The user types text and reads your reply in a rich-markdown bubble. "
            "You may use full markdown: bold, italic, lists, tables, code blocks, etc. "
            "Write shareable media under `OUTPUT_DIR`; reply with `output_url(name)` "
            "or `[VIDEO:/local/...]`/`[IMAGE:...]`/`[GIF:...]`/`[FILE:...]` — auto-rendered. "
            "Camera/media specifics live in their tool descriptions. "
            "This is NOT a voice channel — the user is reading, not listening."
            f"{lang_instruction}"
        )

    static_sections.extend(_build_runtime_preference_sections(runtime_config))

    peer_section = _build_peer_agents_section(hass, runtime_config)
    if peer_section:
        static_sections.append(peer_section)

    topic_hint = build_homeassistant_topic_hint(text)
    if topic_hint:
        dynamic_sections.append(
            f"## Current Home Assistant Topic Hint\n{topic_hint}"
        )

    context_lines = _build_unified_context(hass, conversation_id)
    shared_context = get_conversation_history().get_recent_context(
        conversation_id or "default", max_turns=3, include_tools=True
    )

    if context_lines or shared_context:
        history_prompt = (
            "\n\n## Recent Conversation History (do not repeat completed work)\n"
        )
        if context_lines:
            history_prompt += "\n".join(context_lines) + "\n\n"
        if shared_context and not context_lines:
            history_prompt += f"### Shared Context\n{shared_context}\n"
        history_prompt += (
            f'\nFocus on the current request: "{text}"\n'
            "Reuse prior context only when it helps the current turn."
        )
        dynamic_sections.append(history_prompt.strip())

    config_prompt = build_config_approval_prompt_block(hass)
    if config_prompt:
        dynamic_sections.append(config_prompt)

    im_approval_prompt = build_im_approval_prompt_block(hass)
    if im_approval_prompt:
        dynamic_sections.append(im_approval_prompt)

    return _fit_base_prompt(base_prompt, static_sections + dynamic_sections)
