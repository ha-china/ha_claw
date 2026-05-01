

from __future__ import annotations

import json
from pathlib import Path
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
_HA_RICH_MEDIA_TAG_RE = re.compile(r"\[(IMAGE|GIF|VIDEO|FILE):(.+?)\]")
# Bare claw_assistant media paths the AI may embed verbatim. Both the backend
# absolute form (``/config/www/claw_assistant/...``) and the public HA URL form
# (``/local/claw_assistant/...`` — what ``output_url()`` returns) are accepted
# so the renderer no longer depends on the AI sticking to a single style.
_HA_LOCAL_PATH_RE = re.compile(
    r"(?<![\w/])((?:/config/www|/local)/claw_assistant/[^\s<>\"\[\]()]+)"
)
# Full http(s) URL pointing at a frontend-served claw_assistant media file
# (e.g. ``http://192.168.x.x:8123/local/claw_assistant/foo.mp4`` produced by
# ``absolute_output_url``). Captured in group(1).
_HA_LOCAL_FULL_URL_RE = re.compile(
    r"(https?://[^\s<>\"\[\]()]*?/local/claw_assistant/[^\s<>\"\[\]()]+)",
    re.IGNORECASE,
)
_HTML_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})
_LINK_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".log",
    }
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
    if "://" not in text and "/local/claw_assistant/" not in text and "/config/www/claw_assistant/" not in text:
        return text
    image_tokens: list[str] = []
    media_tokens: list[str] = []

    def _stash_image(match: "re.Match[str]") -> str:
        image_tokens.append(match.group(0))
        return f"__CLAW_IMAGE_{len(image_tokens) - 1}__"

    def _stash_claw_media(match: "re.Match[str]") -> str:
        # Protect claw_assistant media references (full URL or bare /local/
        # path) from being rewritten into ``<a>`` tags by ``_LINK_REWRITE_RE``.
        # They will be re-expanded into ``<video>``/``![img]`` later by
        # ``_expand_ha_frontend_local_paths`` / ``_expand_ha_frontend_full_urls``.
        media_tokens.append(match.group(0))
        return f"__CLAW_MEDIA_{len(media_tokens) - 1}__"

    protected = _IMAGE_MARKDOWN_RE.sub(_stash_image, text)
    # Order matters: full URL first (it contains a ``/local/...`` substring
    # that the bare-path regex would otherwise match inside the URL).
    protected = _HA_LOCAL_FULL_URL_RE.sub(_stash_claw_media, protected)
    protected = _HA_LOCAL_PATH_RE.sub(_stash_claw_media, protected)
    spaced = _URL_CJK_BOUNDARY_RE.sub(r"\1 ", protected)
    rewritten = _LINK_REWRITE_RE.sub(_rewrite_external_links, spaced)
    for index, image_markdown in enumerate(image_tokens):
        rewritten = rewritten.replace(f"__CLAW_IMAGE_{index}__", image_markdown)
    for index, media in enumerate(media_tokens):
        rewritten = rewritten.replace(f"__CLAW_MEDIA_{index}__", media)
    return rewritten


def _resolve_ha_frontend_media_url(hass: Any, source: str) -> str | None:
    candidate = source.strip()
    if not candidate:
        return None
    if candidate.startswith(("http://", "https://", "/local/")):
        return candidate
    if candidate.startswith("/config/www/"):
        return "/local/" + candidate.removeprefix("/config/www/").lstrip("/")

    config_dir = Path(getattr(getattr(hass, "config", None), "config_dir", "") or "")
    if not config_dir:
        return None

    source_path = Path(candidate)
    try:
        relative_local = source_path.relative_to(config_dir / "www")
    except ValueError:
        pass
    else:
        return "/local/" + relative_local.as_posix()
    return None


def _expand_ha_frontend_media_tags(hass: Any, text: str) -> str:
    def _replace(match: "re.Match[str]") -> str:
        kind = match.group(1).strip().upper()
        source = match.group(2).strip()
        url = _resolve_ha_frontend_media_url(hass, source)
        if not url:
            return match.group(0)
        label = Path(url.split("?", 1)[0]).name or kind.lower()
        if kind in {"IMAGE", "GIF"}:
            return f"![{label}]({url})"
        if kind == "VIDEO":
            return _render_ha_frontend_video(url, label)
        return f"[{label}]({url})"

    return _HA_RICH_MEDIA_TAG_RE.sub(_replace, text)


def _normalize_ha_frontend_html_media(text: str) -> str:
    def _replace_img(match: "re.Match[str]") -> str:
        tag = match.group(0)
        src_match = re.search(r'\bsrc=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if not src_match:
            return tag
        src = src_match.group(1).strip()
        alt_match = re.search(r'\balt=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        alt = (alt_match.group(1).strip() if alt_match else "") or Path(src.split("?", 1)[0]).name or "image"
        return f"![{alt}]({src})"

    return _HTML_IMG_TAG_RE.sub(_replace_img, text)


_EXISTING_HTML_MEDIA_RE = re.compile(
    r"<video\b[^>]*>.*?</video>"
    r"|<audio\b[^>]*>.*?</audio>"
    r"|<a\b[^>]*>.*?</a>"
    r"|<img\b[^>]*>"
    r"|<source\b[^>]*/?>",
    re.DOTALL | re.IGNORECASE,
)


def _stash_existing_html_media(
    text: str, *, prefix: str = "CLAW_HTML"
) -> tuple[str, list[str], str]:
    """Hide complete ``<video>``, ``<audio>``, ``<img>``, ``<a>``, ``<source>``
    tags from URL/path expansion.

    Without this, an AI reply that already contains a ``<video src=".../local/
    claw_assistant/foo.mp4">`` tag would have its ``src`` attribute matched by
    the bare-path/full-URL regexes and rewritten into a *nested* ``<video>``
    block — producing the broken
    ``<video src="<video src='...'></video>">`` output observed in the wild.

    ``prefix`` lets callers stash twice in the same pipeline without colliding
    on ``__CLAW_HTML_0__`` placeholders (the second pass would otherwise have
    its ``__CLAW_HTML_0__`` token collide with the first pass's, and
    ``str.replace`` during restore would overwrite both).
    """

    tokens: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        tokens.append(match.group(0))
        return f"__{prefix}_{len(tokens) - 1}__"

    return _EXISTING_HTML_MEDIA_RE.sub(_stash, text), tokens, prefix


def _restore_existing_html_media(
    text: str, tokens: list[str], prefix: str = "CLAW_HTML"
) -> str:
    for index, original in enumerate(tokens):
        text = text.replace(f"__{prefix}_{index}__", original)
    return text


def _expand_ha_frontend_local_paths(text: str) -> str:
    def _replace(match: "re.Match[str]") -> str:
        source = match.group(1).strip()
        # Normalise both ``/config/www/...`` and ``/local/...`` to the public
        # frontend-served URL form so the resulting markdown / video tag works
        # in the HA frontend. ``/local/...`` is already in the right form.
        if source.startswith("/config/www/"):
            url = "/local/" + source.removeprefix("/config/www/").lstrip("/")
        else:
            url = source
        name = Path(url.split("?", 1)[0]).name or "file"
        suffix = Path(name).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return f"![{name}]({url})"
        if suffix in _VIDEO_EXTENSIONS:
            return _render_ha_frontend_video(url, name)
        if suffix in _LINK_EXTENSIONS:
            return f"[{name}]({url})"
        return url

    return _HA_LOCAL_PATH_RE.sub(_replace, text)


def _expand_ha_frontend_full_urls(text: str) -> str:
    """Render claw_assistant media referenced by full http(s) URL.

    Handles strings produced by ``absolute_output_url`` such as
    ``http://192.168.x.x:8123/local/claw_assistant/foo.mp4``. The full URL is
    truncated to its frontend path (``/local/claw_assistant/...``) so the
    resulting ``<video>`` / ``![img]`` tag uses a same-origin reference.
    """

    def _replace(match: "re.Match[str]") -> str:
        full_url = match.group(0)
        path_match = re.search(
            r"/local/claw_assistant/[^\s<>\"\[\]()]+", full_url
        )
        if not path_match:
            return full_url
        url = path_match.group(0)
        name = Path(url.split("?", 1)[0]).name or "file"
        suffix = Path(name).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return f"![{name}]({url})"
        if suffix in _VIDEO_EXTENSIONS:
            return _render_ha_frontend_video(url, name)
        if suffix in _LINK_EXTENSIONS:
            return f"[{name}]({url})"
        return url

    return _HA_LOCAL_FULL_URL_RE.sub(_replace, text)


def _render_ha_frontend_video(url: str, label: str) -> str:
    safe_label = label.replace('"', "&quot;")
    safe_url = url.replace('"', "&quot;")
    return (
        f'<video src="{safe_url}" controls playsinline width="240" height="180" '
        'style="display:block; width:240px; max-width:100%; height:auto; object-fit:contain; background:#000;"'
        f' title="{safe_label}"></video>'
    )


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


_MEMORY_CONTEXT_BLOCK_RE = re.compile(
    r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>",
    flags=re.IGNORECASE,
)
_MEMORY_CONTEXT_NOTE_RE = re.compile(
    r"\[System note:\s*The following is recalled memory context,\s*NOT new user input\.\s*Treat as informational background data\.\]\s*",
    flags=re.IGNORECASE,
)
_MEMORY_CONTEXT_TAG_RE = re.compile(r"</?\s*memory-context\s*>", flags=re.IGNORECASE)


def _strip_memory_context(text: str) -> str:
    text = _MEMORY_CONTEXT_BLOCK_RE.sub("", text)
    text = _MEMORY_CONTEXT_NOTE_RE.sub("", text)
    text = _MEMORY_CONTEXT_TAG_RE.sub("", text)
    return text


def sanitize_response_text(text: str) -> str:

    stripped = _strip_memory_context(text).strip()
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

    from .state import get_channel_type, get_conversation_status
    frontend_lang = get_conversation_status(hass).get("user_language") if hass else None
    conversation_id = str(get_conversation_status(hass).get("last_conversation_id", "") or "") if hass else ""
    channel_type = get_channel_type(conversation_id)
    if hass and channel_type == "ha":
        response_text = _expand_ha_frontend_media_tags(hass, response_text)
        # Hide already-formed HTML media tags (``<video>``, ``<a>``, ``<img>``,
        # …) before the URL/path expanders run, otherwise the expanders will
        # match URLs *inside* a tag's ``src``/``href`` attribute and emit a
        # nested ``<video src="<video ...></video>">`` mess.
        response_text, _stashed_html, _prefix_a = _stash_existing_html_media(
            response_text, prefix="CLAW_HTML_A"
        )
        # Full URL must run before bare-path expansion: the bare regex would
        # otherwise match the ``/local/claw_assistant/...`` substring inside a
        # full URL and emit a half-rewritten string.
        response_text = _expand_ha_frontend_full_urls(response_text)
        # Re-stash: ``_expand_ha_frontend_full_urls`` may have just emitted
        # new ``<video>`` tags whose ``src="/local/claw_assistant/..."`` would
        # be matched a second time by the bare-path expander, producing the
        # same nested-tag corruption. Hiding them between the two passes is
        # the simplest way to keep each expander idempotent. Use a distinct
        # prefix so the second pass's ``__CLAW_HTML_0__`` does not collide
        # with the first pass's during restore.
        response_text, _stashed_emitted, _prefix_b = _stash_existing_html_media(
            response_text, prefix="CLAW_HTML_B"
        )
        response_text = _expand_ha_frontend_local_paths(response_text)
        response_text = _restore_existing_html_media(
            response_text, _stashed_emitted, _prefix_b
        )
        response_text = _restore_existing_html_media(
            response_text, _stashed_html, _prefix_a
        )
        response_text = _normalize_ha_frontend_html_media(response_text)
    plain["original_speech"] = response_text
    plain["agent_name"] = agent_name
    plain["agent_id"] = agent_id
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
