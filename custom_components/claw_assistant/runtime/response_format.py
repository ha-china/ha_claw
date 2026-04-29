

from __future__ import annotations

import json
import re
from typing import Any

from ..const import (
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    CONVERSATION_MODE_NO_NAME,
)

_URL_CJK_BOUNDARY_RE = re.compile(
    r"(https?://[^\s<>\[\]()\"']+?)(?=[^\x00-\x7F])"
)
_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[[^\]\n]*\]\((https?://[^\s)]+)\)",
    re.IGNORECASE,
)

_LINK_REWRITE_RE = re.compile(
    r"(<a\s[^>]*>.*?</a>)"
    r"|(?<!\!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)"
    r"|(?<![\"'>=<])(https?://[^\s<>\[\]()\"']+)",
    re.DOTALL | re.IGNORECASE,
)


def _rewrite_external_links(match: "re.Match[str]") -> str:
    if match.group(1):
        return match.group(1)
    if match.group(3):
        return (
            f'<a href="{match.group(3)}" target="_blank" '
            f'rel="noopener noreferrer">{match.group(2)}</a>'
        )
    url = match.group(4)
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>'


def _normalize_response_links(text: str) -> str:
    if "://" not in text:
        return text
    image_tokens: list[str] = []

    def _stash_image(match: "re.Match[str]") -> str:
        image_tokens.append(match.group(0))
        return f"__CLAW_IMAGE_{len(image_tokens) - 1}__"

    protected = _IMAGE_MARKDOWN_RE.sub(_stash_image, text)
    spaced = _URL_CJK_BOUNDARY_RE.sub(r"\1 ", protected)
    rewritten = _LINK_REWRITE_RE.sub(_rewrite_external_links, spaced)
    for index, image_markdown in enumerate(image_tokens):
        rewritten = rewritten.replace(f"__CLAW_IMAGE_{index}__", image_markdown)
    return rewritten


def reply_labels(language: str | None) -> dict[str, str]:

    if isinstance(language, str) and language.lower().startswith("zh"):
        return {
            "reply": "回复",
            "failed_reply": "失败回复",
            "then": "然后",
            "web_search_summary": "网络搜索摘要",
            "summary": "总结",
        }
    return {
        "reply": "Reply",
        "failed_reply": "Failed reply",
        "then": "Then",
        "web_search_summary": "Web search summary",
        "summary": "Summary",
    }


def language_of(result: Any) -> str | None:

    response = getattr(result, "response", None) if result else None
    language = getattr(response, "language", None) if response else None
    return language if isinstance(language, str) and language else None


def _extract_json_payload(text: str) -> dict[str, Any] | None:

    stripped = text.strip()
    if not stripped:
        return None

    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def is_marshaled_tool_payload(text: str) -> bool:

    payload = _extract_json_payload(text)
    if not payload:
        return False

    mode = str(payload.get("mode", "")).lower()
    return mode in {"tool_calls", "toolcalls"} or isinstance(
        payload.get("tool_calls") or payload.get("toolcalls"), list
    )


def sanitize_response_text(text: str) -> str:

    stripped = text.strip()
    if not stripped:
        return ""

    payload = _extract_json_payload(stripped)
    if not payload:
        return _normalize_response_links(stripped)

    mode = str(payload.get("mode", "")).lower()
    if mode in {"tool_calls", "toolcalls"}:
        return ""

    if mode == "answer" and isinstance(payload.get("content"), str):
        return _normalize_response_links(payload["content"].strip())

    return _normalize_response_links(stripped)


def get_response_text(result: Any) -> str:

    if not result or not result.response or not result.response.speech:
        return ""
    plain = result.response.speech.get("plain", {})
    return sanitize_response_text(
        plain.get("original_speech", plain.get("speech", ""))
    )


def ensure_response_data(result: Any) -> None:

    if result and result.response and not hasattr(result.response, "data"):
        result.response.data = {
            "targets": [],
            "success": [],
            "failed": [],
        }


def apply_agent_response_format(
    result: Any,
    *,
    hass: Any = None,
    agent_name: str,
    agent_id: str,
    conversation_mode: str,
    response_text: str | None = None,
    previous_result: Any = None,
    search_results: str | None = None,
) -> Any:

    if not result or not result.response or not result.response.speech:
        return result

    plain = result.response.speech.setdefault("plain", {})
    if response_text is None:
        response_text = get_response_text(result)
    else:
        response_text = sanitize_response_text(response_text)

    plain["original_speech"] = response_text
    plain["agent_name"] = agent_name
    plain["agent_id"] = agent_id

    from .state import get_conversation_status
    frontend_lang = get_conversation_status(hass).get("user_language") if hass else None
    labels = reply_labels(frontend_lang or language_of(result))
    reply = labels["reply"]

    if conversation_mode == CONVERSATION_MODE_NO_NAME:
        plain["speech"] = response_text
        return result

    if conversation_mode == CONVERSATION_MODE_ADD_NAME:
        plain["speech"] = f"({agent_name}) {reply}: {response_text}"
        return result

    if conversation_mode == CONVERSATION_MODE_DETAILED:
        failed_reply = labels["failed_reply"]
        then_word = labels["then"]
        web_summary_label = labels["web_search_summary"]

        if (
            previous_result is not None
            and previous_result.response.response_type != "action_done"
        ):
            prev_plain = previous_result.response.speech.get("plain", {})
            prev_name = prev_plain.get("agent_name", "UNKNOWN")
            prev_text = prev_plain.get("original_speech", prev_plain.get("speech", ""))
            if search_results:
                search_summary = (
                    search_results[:500] + "..."
                    if len(search_results) > 500
                    else search_results
                )
                plain["speech"] = (
                    f"{web_summary_label}:\n{search_summary}\n\n"
                    f"({prev_name}) {failed_reply}: {prev_text}\n"
                    f"{then_word} ({agent_name}) {reply}: {response_text}"
                )
            else:
                plain["speech"] = (
                    f"({prev_name}) {failed_reply}: {prev_text}\n"
                    f"{then_word} ({agent_name}) {reply}: {response_text}"
                )
            return result

        if search_results:
            search_summary = (
                search_results[:500] + "..." if len(search_results) > 500 else search_results
            )
            plain["speech"] = (
                f"{web_summary_label}:\n{search_summary}\n\n"
                f"({agent_name}) {reply}: {response_text}"
            )
            plain["search_results"] = search_results
            return result

        plain["speech"] = f"({agent_name}) {reply}: {response_text}"

    return result
