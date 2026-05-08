

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
from .i18n import t

_URL_CJK_BOUNDARY_RE = re.compile(
    r"(https?://[^\s<>\[\]()\"']+?)(?=[^\x00-\x7F])"
)
_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[[^\]\n]*\]\((https?://[^\s)]+)\)",
    re.IGNORECASE,
)

_LINK_REWRITE_RE = re.compile(
    r"(<a\s[^>]*>[\s\S]{0,5000}?</a>)"
    r"|(?<!\!)\[([^\]\n]{0,500})\]\((https?://[^\s)]{0,2000})\)"
    r"|(?<![\"'>=<])(https?://[^\s<>\[\]()\"']{1,2000})",
    re.IGNORECASE,
)
_HA_RICH_MEDIA_TAG_RE = re.compile(r"\[(IMAGE|GIF|VIDEO|FILE):(.+?)\]")
# Bare claw_assistant media paths the AI may embed verbatim. Both the backend
# absolute form (``/config/www/claw_assistant/...``) and the public HA URL form
# (``/local/claw_assistant/...`` — what ``output_url()`` returns) are accepted
# so the renderer no longer depends on the AI sticking to a single style.
_HA_LOCAL_PATH_RE = re.compile(
    r"(?<![\w/(])((?:/config/www|/local)/claw_assistant/[^\s<>\"\[\]()]+)"
)
# Full http(s) URL pointing at a frontend-served claw_assistant media file
# (e.g. ``http://192.168.x.x:8123/local/claw_assistant/foo.mp4`` produced by
# ``absolute_output_url``). Captured in group(1).
_HA_LOCAL_FULL_URL_RE = re.compile(
    r"(?<![\w/(])(https?://[^\s<>\"\[\]()]*?/local/claw_assistant/[^\s<>\"\[\]()]+)",
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


_LARGE_TEXT_THRESHOLD = 2_000_000

def _normalize_response_links(text: str) -> str:
    if len(text) > _LARGE_TEXT_THRESHOLD:
        return text
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
    r"<video\b[^>]*>[\s\S]{0,10000}?</video>"
    r"|<audio\b[^>]*>[\s\S]{0,10000}?</audio>"
    r"|<a\b[^>]*>[\s\S]{0,5000}?</a>"
    r"|<img\b[^>]*>"
    r"|<source\b[^>]*/?>",
    re.IGNORECASE,
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

    if len(text) > _LARGE_TEXT_THRESHOLD:
        return text, tokens, prefix

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
            return _render_ha_frontend_blank_link(url, name)
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
            return _render_ha_frontend_blank_link(url, name)
        return url

    return _HA_LOCAL_FULL_URL_RE.sub(_replace, text)


def _route_claw_text_url(url: str) -> str:
    """Rewrite a ``/local/claw_assistant/<name>`` path to ``/claw_file/<name>``
    so the file is served by ``ClawFileView`` with an explicit
    ``charset=utf-8`` header. Without this rewrite, browsers running in a
    Chinese locale render text files (.md/.txt/.json/...) as GBK and produce
    mojibake.

    Non-claw_assistant URLs are returned unchanged so external links continue
    to function. Media files (images/videos) keep using ``/local/...`` because
    binary content-types are unaffected by charset.
    """
    if not url:
        return url
    prefix = "/local/claw_assistant/"
    if url.startswith(prefix):
        return "/claw_file/" + url[len(prefix):]
    # Full URL form: ``http(s)://host/local/claw_assistant/<name>``.
    if "://" in url and "/local/claw_assistant/" in url:
        return url.replace("/local/claw_assistant/", "/claw_file/", 1)
    return url


def _render_ha_frontend_blank_link(url: str, label: str) -> str:
    """Render a claw_assistant file link that opens in a new browser tab.

    The HA frontend chat card renders raw HTML, so emitting a fully formed
    ``<a target="_blank">`` tag (rather than a ``[label](url)`` markdown link)
    is the only reliable way to force a new-window open without intercepting
    the user's chat navigation.
    """
    routed = _route_claw_text_url(url)
    safe_label = label.replace('"', '&quot;')
    safe_url = routed.replace('"', '&quot;')
    return (
        f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
        f'{safe_label}</a>'
    )


# Markdown link the LLM may already have produced (``[label](/local/claw_assistant/x.md)``
# or full URL form). Converted in-place to ``<a target="_blank">`` so it opens
# in a new browser window rather than navigating away from the HA chat view.
_LOCAL_MD_LINK_RE = re.compile(
    r"(?<!\!)\[([^\]\n]{1,500})\]\("
    r"((?:https?://[^\s)]{0,2000}?)?(?:/local|/config/www)/claw_assistant/[^\s)]{1,2000})"
    r"\)"
)


def _convert_local_md_links_to_new_window(text: str) -> str:
    if "/local/claw_assistant/" not in text and "/config/www/claw_assistant/" not in text:
        return text

    def _replace(match: "re.Match[str]") -> str:
        label = match.group(1).strip() or "file"
        raw_url = match.group(2).strip()
        url = raw_url
        if url.startswith("http"):
            inner = re.search(r"/local/claw_assistant/[^\s)]+", url)
            if inner:
                url = inner.group(0)
        if url.startswith("/config/www/"):
            url = "/local/" + url.removeprefix("/config/www/").lstrip("/")
        return _render_ha_frontend_blank_link(url, label)

    return _LOCAL_MD_LINK_RE.sub(_replace, text)


def _render_ha_frontend_video(url: str, label: str) -> str:
    safe_label = label.replace('"', "&quot;")
    safe_url = url.replace('"', "&quot;")
    return (
        f'<video src="{safe_url}" controls playsinline width="240" height="180" '
        'style="display:block; width:240px; max-width:100%; height:auto; object-fit:contain; background:#000;"'
        f' title="{safe_label}"></video>'
    )


def reply_labels(language: str | None) -> dict[str, str]:
    from .reply_formatter import is_chinese

    if is_chinese(language):
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
    r"<\s*memory-context\s*>[\s\S]{0,50000}?</\s*memory-context\s*>",
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


_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_HR_RE = re.compile(r"^\s*([-*_])\s*\1\s*\1[\s\-*_]*$")
_TABLE_PIPE_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\|?[\s:]*-[-|:\s]*\|")
_BLOCKQUOTE_RE = re.compile(r"^>\s")
_TABLE_HEADER_RE = re.compile(r"^\s*\|(.+\|)\s*$")
_TABLE_EMPTY_SEP_RE = re.compile(r"^\s*\|[\s|]*\|\s*$")

_INLINE_HEADING_RE = re.compile(r"(?<=[^\s#])(#{1,6}\s+)")
_INLINE_LIST_RE = re.compile(r"(?<=\S)(- (?:\[.\] )?\S)")
_INLINE_ORDERED_LIST_RE = re.compile(r"(?<=[^\s\d])(\d+\.\s+\S)")
_INLINE_BLOCKQUOTE_RE = re.compile(r"(?<=\S)(> )")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_INLINE_LINK_RE = re.compile(r"\[[^\]]{0,500}\]\([^)]{0,2000}\)")
_INLINE_HTML_A_RE = re.compile(r"<a\s[^>]{0,500}>[\s\S]{0,5000}?</a>")


def _presplit_inline_markdown(text: str) -> str:
    if len(text) > _LARGE_TEXT_THRESHOLD:
        return text
    lines = text.splitlines()
    result: list[str] = []
    in_fence = False
    for raw in lines:
        line = raw.rstrip()
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue

        if _TABLE_PIPE_RE.match(line):
            result.append(line)
            continue

        guards: list[str] = []

        def _guard(m: re.Match) -> str:
            guards.append(m.group(0))
            return f"\x00G{len(guards) - 1}\x00"

        protected = _INLINE_HTML_A_RE.sub(_guard, line)
        protected = _INLINE_LINK_RE.sub(_guard, protected)
        protected = _INLINE_CODE_RE.sub(_guard, protected)

        if _INLINE_HEADING_RE.search(protected):
            protected = _INLINE_HEADING_RE.sub(r"\n\1", protected)
        if _INLINE_LIST_RE.search(protected):
            protected = _INLINE_LIST_RE.sub(r"\n\1", protected)
        if _INLINE_ORDERED_LIST_RE.search(protected):
            protected = _INLINE_ORDERED_LIST_RE.sub(r"\n\1", protected)
        if _INLINE_BLOCKQUOTE_RE.search(protected):
            protected = _INLINE_BLOCKQUOTE_RE.sub(r"\n\1", protected)

        for i, g in enumerate(guards):
            protected = protected.replace(f"\x00G{i}\x00", g)

        for part in protected.split("\n"):
            result.append(part)
    return "\n".join(result)


def _fix_markdown_tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_table = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if in_table:
            if _TABLE_PIPE_RE.match(line):
                out.append(line)
                i += 1
                continue
            in_table = False
            out.append(line)
            i += 1
            continue
        if _TABLE_HEADER_RE.match(line):
            col_count = len([c for c in line.strip().strip("|").split("|")])
            out.append(line)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if _TABLE_SEP_RE.match(next_line):
                    out.append(lines[i + 1])
                    i += 2
                    in_table = True
                    continue
                if _TABLE_EMPTY_SEP_RE.match(next_line) or (
                    next_line.replace("|", "").replace(" ", "") == ""
                    and "|" in next_line
                ):
                    out.append("| " + " | ".join(["---"] * col_count) + " |")
                    i += 2
                    in_table = True
                    continue
                if _TABLE_PIPE_RE.match(next_line):
                    out.append("| " + " | ".join(["---"] * col_count) + " |")
                    i += 1
                    in_table = True
                    continue
            else:
                out.append("| " + " | ".join(["---"] * col_count) + " |")
                i += 1
                continue
            i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _normalize_markdown(text: str) -> str:
    if len(text) > _LARGE_TEXT_THRESHOLD:
        return text
    text = _fix_markdown_tables(text)
    text = _presplit_inline_markdown(text)
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    prev_blank = False
    prev_was_fence_close = False

    for raw in lines:
        line = raw.rstrip()

        if _FENCE_RE.match(line):
            if not in_fence:
                if out and out[-1].strip():
                    out.append("")
                out.append(line)
                in_fence = True
            else:
                out.append(line)
                in_fence = False
                prev_was_fence_close = True
            prev_blank = False
            continue

        if in_fence:
            out.append(raw.rstrip())
            prev_blank = False
            continue

        if prev_was_fence_close:
            prev_was_fence_close = False
            if line.strip():
                out.append("")

        if not line.strip():
            if not prev_blank and out:
                out.append("")
            prev_blank = True
            continue
        prev_blank = False

        if _HEADING_RE.match(line):
            title = re.sub(r"^#{1,6}\s+", "", line).strip()
            if title:
                title = re.sub(r"\*\*(.+?)\*\*", r"\1", title)
                line = f"**{title}**"
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            continue

        if _HR_RE.match(line):
            if out and out[-1].strip():
                out.append("")
            out.append(line)
            continue

        if _TABLE_PIPE_RE.match(line):
            if out and out[-1].strip() and not _TABLE_PIPE_RE.match(out[-1]):
                out.append("")
            out.append(line)
            continue

        if _BLOCKQUOTE_RE.match(line):
            if out and out[-1].strip() and not _BLOCKQUOTE_RE.match(out[-1]):
                out.append("")
            out.append(line)
            continue

        if out and _TABLE_PIPE_RE.match(out[-1]):
            out.append("")

        if out and _HR_RE.match(out[-1]):
            out.append("")

        out.append(line)

    while out and not out[-1].strip():
        out.pop()
    while out and not out[0].strip():
        out.pop(0)

    return "\n".join(out)


_AGENT_ERROR_PREFIX_RE = re.compile(
    r"(?:"
    r"Error\s+getting\s+response"
    r"|获取响应[时出]*[错误]*"
    r"|The\s+AI\s+service\s+returned\s+an\s+error"
    r"|AI\s*服务返回错误"
    r"|RuntimeError\s*:"
    r"|Exception\s*:"
    r"|APIError\s*:"
    r"|ConnectionError\s*:"
    r"|TimeoutError\s*:"
    r")\s*[:：]?\s*",
    re.IGNORECASE,
)
_API_ERROR_JSON_RE = re.compile(
    r"(?:API\s*error|RuntimeError|Error)\s*[:：]\s*(\{[^\n]{0,5000})",
    re.IGNORECASE,
)
_AGENT_REPLY_PREFIX_RE = re.compile(
    r"^(?:conversation\.[\w_]+\s*:\s*)?(\(.{1,200}?\)\s*(?:回复|Reply)\s*[:：]\s*)(.{0,50000})$",
    re.DOTALL,
)
_ERROR_SIGNAL_PATTERNS = (
    "error getting response",
    "runtimeerror",
    "tool_failure:",
    "agent_not_found",
    "agent not found",
    "api error",
    "api_error",
    "connection reset",
    "server disconnected",
    "serverdisconnected",
    "timed out",
    "timeout",
    "broken pipe",
    "eof occurred",
    "cannot connect",
    "ssl:",
    "clientconnectorerror",
    "rate limit",
    "rate_limit",
    "insufficient_quota",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "model_not_found",
    "context_length_exceeded",
    "content_filter",
    "temporarily unavailable",
    "服务暂时不可用",
    "获取响应",
    "无法连接",
    "连接失败",
    "网络错误",
    "请检查网络",
    "ai 服务",
    "服务不可用",
    "请稍后再试",
    "连接超时",
    "服务器断开",
)


def _looks_like_error(text: str) -> bool:
    low = text.lower()
    return any(pat in low for pat in _ERROR_SIGNAL_PATTERNS)


def _shorten_model_name(value: str) -> str:
    value = value.strip().strip('"').strip("'").strip(";").strip()
    if len(value) > 80:
        value = value[:77] + "..."
    return value


def _parse_api_error_json(blob: str) -> tuple[str, str, str]:
    code = message = model = ""
    try:
        payload = json.loads(blob)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or err.get("status") or "").strip()
            message = str(err.get("message") or err.get("msg") or err.get("detail") or "").strip()
            model = str(err.get("model") or err.get("param") or "").strip()
    if not message:
        m = re.search(r'"(?:message|msg|detail)"\s*:\s*"([^"]+)"', blob)
        if m:
            message = m.group(1).strip()
    if not code:
        m = re.search(r'"(?:code|type|status|err_code)"\s*:\s*"?([^",}\s]+)', blob)
        if m:
            code = m.group(1).strip()
    if not model:
        m = re.search(r"model[\"'\s:]+([A-Za-z0-9._/\-]+)", blob)
        if m:
            model = _shorten_model_name(m.group(1))
    return code, message, model


def _classify_error(code: str, message: str, body: str) -> str | None:
    code_l = code.lower()
    msg_l = message.lower()
    all_l = f"{code_l} {msg_l} {body.lower()}"

    if code_l in ("model_not_found", "model_not_exist", "no_model"):
        return "err_model_not_found"
    if "no available channel" in msg_l or "no available channel" in all_l:
        return "err_model_not_found"
    if "does not exist" in msg_l and "model" in msg_l:
        return "err_model_not_found"

    if code_l in ("rate_limit_exceeded", "rate_limit", "429", "too_many_requests"):
        return "err_rate_limited"
    if "rate limit" in msg_l or "too many request" in msg_l or "请求过于频繁" in msg_l:
        return "err_rate_limited"
    if "throttl" in msg_l:
        return "err_rate_limited"

    if code_l in ("invalid_api_key", "authentication_error", "auth_failed", "401", "unauthorized"):
        return "err_auth_failed"
    if "invalid api key" in msg_l or "authentication" in msg_l or "unauthorized" in msg_l:
        return "err_auth_failed"
    if "api key" in msg_l and ("invalid" in msg_l or "expired" in msg_l or "incorrect" in msg_l):
        return "err_auth_failed"

    if code_l in ("insufficient_quota", "quota_exceeded", "billing_limit", "402"):
        return "err_quota_exceeded"
    if "quota" in msg_l or "insufficient" in msg_l or "balance" in msg_l or "余额" in msg_l:
        return "err_quota_exceeded"
    if "billing" in msg_l or "payment" in msg_l:
        return "err_quota_exceeded"

    if code_l in ("context_length_exceeded", "max_tokens", "token_limit"):
        return "err_context_too_long"
    if "context length" in msg_l or ("token" in msg_l and ("max" in msg_l or "limit" in msg_l or "exceed" in msg_l)):
        return "err_context_too_long"
    if "too long" in msg_l and ("input" in msg_l or "context" in msg_l or "message" in msg_l):
        return "err_context_too_long"

    if code_l in ("content_filter", "content_policy", "content_blocked", "responsibleai"):
        return "err_content_filtered"
    if "content filter" in msg_l or "content policy" in msg_l or ("safety" in msg_l and "blocked" in msg_l):
        return "err_content_filtered"

    if "temporarily unavailable" in msg_l or "服务暂时不可用" in message:
        return "err_service_unavailable"
    if code_l in ("503", "service_unavailable", "overloaded", "server_error", "500", "502"):
        return "err_service_unavailable"
    if "overloaded" in msg_l or "capacity" in msg_l or "503" in code_l:
        return "err_service_unavailable"

    if "timed out" in all_l or "timeout" in all_l:
        return "err_timeout"

    if any(kw in all_l for kw in ("connection reset", "server disconnected", "broken pipe",
                                   "eof occurred", "cannot connect", "clientconnector")):
        return "err_connection"

    return None


def _classify_plaintext_error(text: str) -> str | None:
    low = text.lower()
    if "timed out" in low or "timeout" in low or "超时" in low:
        return "err_timeout"
    if any(kw in low for kw in ("connection reset", "server disconnected", "broken pipe",
                                 "eof occurred", "cannot connect", "clientconnector",
                                 "serverdisconnected", "ssl:")):
        return "err_connection"
    if "rate limit" in low or "rate_limit" in low or "429" in low or "too many request" in low:
        return "err_rate_limited"
    if "unauthorized" in low or "invalid api key" in low or "authentication" in low or "401" in low:
        return "err_auth_failed"
    if "quota" in low or "insufficient" in low or "余额" in low:
        return "err_quota_exceeded"
    if "temporarily unavailable" in low or "服务暂时不可用" in low or "503" in low or "overloaded" in low:
        return "err_service_unavailable"
    if "context length" in low or ("token" in low and ("limit" in low or "exceed" in low)):
        return "err_context_too_long"
    if "content filter" in low or "content policy" in low:
        return "err_content_filtered"
    return None


def prettify_agent_error(text: str, *, language: str | None = None) -> str | None:
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if not _looks_like_error(candidate):
        return None

    body = candidate
    while True:
        stripped = _AGENT_ERROR_PREFIX_RE.sub("", body, count=1).strip()
        if stripped == body:
            break
        body = stripped

    json_match = _API_ERROR_JSON_RE.search(body)
    if json_match:
        code, message, model = _parse_api_error_json(json_match.group(1))
        category = _classify_error(code, message, body)
        if category == "err_model_not_found":
            target = model or t("err_model_placeholder", language)
            return t(category, language).replace("{model}", target)
        if category:
            return t(category, language)
        detail = message or code or body
        if not detail:
            return None
        if len(detail) > 120:
            detail = detail[:117] + "..."
        return t("err_generic_api", language).replace("{detail}", detail)

    category = _classify_plaintext_error(body)
    if category:
        return t(category, language)

    detail = body
    if not detail:
        return None
    if len(detail) > 120:
        detail = detail[:117] + "..."
    return t("err_generic_api", language).replace("{detail}", detail)


def _rewrite_chained_agent_errors(text: str, *, language: str | None = None) -> str:
    if not text:
        return text
    if not _looks_like_error(text):
        return text

    parts = re.split(r"\s*;\s*|\n+", text)
    rewritten: list[str] = []
    seen: set[str] = set()
    changed = False
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        head = ""
        body = segment
        m = _AGENT_REPLY_PREFIX_RE.match(segment)
        if m:
            head, body = m.group(1), m.group(2)
        friendly = prettify_agent_error(body, language=language)
        if friendly is None:
            rewritten.append(segment)
            continue
        changed = True
        combined = f"{head}{friendly}" if head else friendly
        key = friendly
        if key in seen:
            continue
        seen.add(key)
        rewritten.append(combined)

    if not changed:
        return text
    return "\n".join(rewritten) if rewritten else text


def sanitize_response_text(text: str, *, language: str | None = None) -> str:

    def _finalize(value: str) -> str:
        value = value.replace("]┊", "]\n┊")
        value = re.sub(r"(\[[^\]\n]{1,80}\])(?=\S)", r"\1\n", value)
        value = re.sub(r"([^\n])(?=┊)", r"\1\n", value)
        return re.sub(r"\n{3,}", "\n\n", value)

    if len(text) > _LARGE_TEXT_THRESHOLD:
        return _finalize(text.strip())
    stripped = _strip_memory_context(text).strip()
    if not stripped:
        return ""

    rewritten = _rewrite_chained_agent_errors(stripped, language=language)
    if rewritten is not stripped and rewritten != stripped:
        return _finalize(_normalize_markdown(_normalize_response_links(rewritten)))

    payload = _extract_json_payload(stripped)
    if not payload:
        return _finalize(_normalize_markdown(_normalize_response_links(stripped)))

    mode = str(payload.get("mode", "")).lower()
    if mode in {"tool_calls", "toolcalls"}:
        return ""

    if mode == "answer" and isinstance(payload.get("content"), str):
        return _finalize(_normalize_markdown(_normalize_response_links(payload["content"].strip())))

    return _finalize(_normalize_markdown(_normalize_response_links(stripped)))


def get_response_text(result: Any, *, language: str | None = None) -> str:

    if not result or not result.response or not result.response.speech:
        return ""
    from .reply_formatter import strip_reply_prefix
    plain = result.response.speech.get("plain", {})
    raw = plain.get("original_speech", plain.get("speech", ""))
    return sanitize_response_text(
        strip_reply_prefix(raw),
        language=language or language_of(result),
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
    handoff_replies: list[tuple[str, str]] | None = None,
) -> Any:

    if not result or not result.response or not result.response.speech:
        return result

    plain = result.response.speech.setdefault("plain", {})
    from .state import get_channel_type, get_conversation_status
    frontend_lang = get_conversation_status(hass).get("user_language") if hass else None
    effective_lang = frontend_lang or language_of(result)
    if response_text is None:
        response_text = get_response_text(result, language=effective_lang)
    else:
        response_text = sanitize_response_text(response_text, language=effective_lang)
    conversation_id = str(get_conversation_status(hass).get("last_conversation_id", "") or "") if hass else ""
    channel_type = get_channel_type(conversation_id)
    if hass and channel_type == "ha":
        response_text = _expand_ha_frontend_media_tags(hass, response_text)
        # Convert any LLM-emitted ``[label](/local/claw_assistant/x.md)`` style
        # markdown links into ``<a target="_blank">`` HTML so they open in a
        # new browser window. Must run before ``_stash_existing_html_media`` so
        # the freshly emitted ``<a>`` tags are protected from URL/path
        # expansion in the subsequent passes.
        response_text = _convert_local_md_links_to_new_window(response_text)
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
    plain["agent_name"] = agent_name
    plain["agent_id"] = agent_id
    labels = reply_labels(frontend_lang or language_of(result))
    reply = labels["reply"]

    from .reply_formatter import strip_reply_prefix, format_reply_speech, format_detailed_speech
    response_text = strip_reply_prefix(response_text)
    plain["original_speech"] = response_text

    if conversation_mode == CONVERSATION_MODE_NO_NAME:
        plain["speech"] = response_text
        return result

    if conversation_mode == CONVERSATION_MODE_ADD_NAME:
        plain["speech"] = format_reply_speech(agent_name, response_text, frontend_lang or language_of(result))
        return result

    if conversation_mode == CONVERSATION_MODE_DETAILED:
        failed_reply = labels["failed_reply"]
        then_word = labels["then"]
        web_summary_label = labels["web_search_summary"]
        lang = frontend_lang or language_of(result)

        prev_name: str | None = None
        prev_text: str | None = None
        if (
            previous_result is not None
            and previous_result.response.response_type != "action_done"
        ):
            prev_plain = previous_result.response.speech.get("plain", {})
            prev_name = prev_plain.get("agent_name", "UNKNOWN")
            prev_text = prev_plain.get("original_speech", prev_plain.get("speech", ""))

        trunc_search = None
        if search_results:
            trunc_search = search_results[:500] + "..." if len(search_results) > 500 else search_results
            plain["search_results"] = search_results

        plain["speech"] = format_detailed_speech(
            agent_name=agent_name,
            response_text=response_text,
            language=lang,
            prev_agent_name=prev_name,
            prev_text=prev_text,
            prev_label_word=failed_reply if prev_name else None,
            search_summary=trunc_search,
            search_label=web_summary_label if trunc_search else None,
            then_word=then_word if prev_name else None,
            handoff_replies=handoff_replies,
        )
        return result

    plain["speech"] = format_reply_speech(agent_name, response_text, frontend_lang or language_of(result))
    return result
