"""Unified speech prefix handling.

Single source of truth for adding/stripping agent reply prefixes.
Every callsite that touches plain["speech"] must go through these helpers.
"""

from __future__ import annotations

import re
from typing import Any

_AGENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:conversation\.[\w_]+\s*:\s*)?(\(.{1,200}?\)\s*(?:\u56de\u590d|Reply)\s*[:\uff1a]\s*)(.{0,50000})$",
    re.DOTALL,
)


def strip_reply_prefix(text: str) -> str:
    """Remove all nested (xxx) Reply: prefixes, return pure content."""
    while True:
        m = _AGENT_REPLY_PREFIX_RE.match(text)
        if not m:
            return text
        inner = m.group(2).strip()
        if not inner:
            return text
        text = inner


def is_chinese(language: str | None) -> bool:
    return isinstance(language, str) and language.lower().startswith("zh")


def reply_label(language: str | None) -> str:
    return "\u56de\u590d" if is_chinese(language) else "Reply"


def format_reply_speech(
    agent_name: str,
    text: str,
    language: str | None = None,
) -> str:
    """Build '(agent_name) Reply: text', stripping any existing prefix first."""
    clean = strip_reply_prefix(text)
    label = reply_label(language)
    return "({name}) {label}: {content}".format(
        name=agent_name, label=label, content=clean,
    )


def format_labeled_speech(
    agent_name: str,
    text: str,
    label_word: str,
) -> str:
    """Build '(agent_name) label_word: text' with prefix stripping."""
    clean = strip_reply_prefix(text)
    return "({name}) {label}: {content}".format(
        name=agent_name, label=label_word, content=clean,
    )


def format_detailed_speech(
    *,
    agent_name: str,
    response_text: str,
    language: str | None = None,
    prev_agent_name: str | None = None,
    prev_text: str | None = None,
    prev_label_word: str | None = None,
    search_summary: str | None = None,
    search_label: str | None = None,
    then_word: str | None = None,
    handoff_replies: list[tuple[str, str]] | None = None,
) -> str:
    """Build DETAILED mode speech with all sections joined by separators."""
    label = reply_label(language)
    clean_response = strip_reply_prefix(response_text)
    sep = "\n\n\u200b---\n\n"
    parts: list[str] = []

    if search_summary and search_label:
        parts.append(f"{search_label}:\n{search_summary}")

    if handoff_replies:
        for name, text in handoff_replies:
            parts.append(format_reply_speech(name, text, language))

    if prev_agent_name and prev_text and prev_label_word:
        clean_prev = strip_reply_prefix(prev_text)
        parts.append(f"({prev_agent_name}) {prev_label_word}: {clean_prev}")

    prefix = f"{then_word} " if then_word and (parts) else ""
    parts.append(f"{prefix}({agent_name}) {label}: {clean_response}")

    return sep.join(parts)


def stamp_plain(
    plain: dict[str, Any],
    *,
    agent_name: str,
    agent_id: str,
    text: str,
    language: str | None = None,
    add_prefix: bool = True,
) -> None:
    """One-stop helper: set original_speech, agent_name, agent_id, speech."""
    clean = strip_reply_prefix(text)
    plain["original_speech"] = clean
    plain["agent_name"] = agent_name
    plain["agent_id"] = agent_id
    if add_prefix:
        plain["speech"] = format_reply_speech(agent_name, clean, language)
    else:
        plain["speech"] = clean
