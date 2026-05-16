

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from ._html_selector import Selector

_trafilatura_loaded: bool = False
_trafilatura_extract = None
_trafilatura_extract_metadata = None
_trafilatura_config = None


def _lazy_load_trafilatura() -> None:
    global _trafilatura_loaded, _trafilatura_extract, _trafilatura_extract_metadata, _trafilatura_config
    if _trafilatura_loaded:
        return
    _trafilatura_loaded = True
    try:
        from copy import deepcopy
        from trafilatura import extract, extract_metadata
        from trafilatura.settings import DEFAULT_CONFIG
        _trafilatura_extract = extract
        _trafilatura_extract_metadata = extract_metadata
        _trafilatura_config = deepcopy(DEFAULT_CONFIG)
        _trafilatura_config['DEFAULT']['MIN_EXTRACTED_SIZE'] = '20'
        _trafilatura_config['DEFAULT']['MIN_OUTPUT_SIZE'] = '1'
    except ImportError:
        pass

_NOISE_HINTS = (
    "nav",
    "footer",
    "header",
    "toolbar",
    "comment",
    "share",
    "related",
    "recommend",
    "sidebar",
    "aside",
    "menu",
    "breadcrumb",
    "pager",
    "ad",
    "ads",
)
_FALLBACK_BLOCK_TAGS = ("h1", "h2", "h3", "p", "li", "blockquote", "pre")
_JS_SHELL_ROOT_SELECTORS = (
    "#__next",
    "#__nuxt",
    "#root",
    "#app",
    "[data-reactroot]",
    "[ng-app]",
)
_JS_SHELL_SCRIPT_MARKERS = (
    "webpack",
    "__next",
    "__nuxt",
    "hydration",
    "chunk",
    "vite",
    "gatsby",
    "apollo-state",
)
_TRAFILATURA_MIN_TEXT_EN = 80
_TRAFILATURA_MIN_TEXT_ZH = 20
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')


def _min_text_threshold(text: str) -> int:
    if not text:
        return _TRAFILATURA_MIN_TEXT_EN
    cjk = len(_CJK_RE.findall(text))
    return _TRAFILATURA_MIN_TEXT_ZH if cjk > len(text) * 0.1 else _TRAFILATURA_MIN_TEXT_EN


@dataclass(slots=True)
class ExtractedWebContent:
    title: str
    content: str
    strategy: str
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_web_content(html: str, url: str) -> ExtractedWebContent | None:

    page = Selector(content=html, url=url)
    title = _extract_title(page, url)

    if "weather.com.cn" in url:
        weather = _extract_weather_content(page)
        if weather:
            return ExtractedWebContent(
                title=title,
                content=weather,
                strategy="weather_specialized",
            )

    schema_article = _extract_schema_article(page, default_title=title)
    if schema_article is not None:
        return schema_article

    extracted = _extract_with_trafilatura(html, url, default_title=title)
    if extracted is not None:
        return extracted

    if _looks_like_javascript_shell(page):
        return ExtractedWebContent(
            title=title,
            content="",
            strategy="js_shell",
            metadata={"requires_browser": True},
        )

    fallback = _extract_fallback_text(page)
    if fallback:
        return ExtractedWebContent(
            title=title,
            content=fallback,
            strategy="fragment_fallback",
        )

    return None


def _extract_with_trafilatura(
    html: str,
    url: str,
    *,
    default_title: str,
) -> ExtractedWebContent | None:
    _lazy_load_trafilatura()
    if _trafilatura_extract is None:
        container_content = _extract_main_container_text(html)
        if not container_content:
            return None
        return ExtractedWebContent(
            title=default_title,
            content=container_content,
            strategy="trafilatura",
        )

    extracted = _trafilatura_extract(
        html,
        url=url,
        output_format="markdown",
        favor_recall=True,
        include_comments=False,
        include_tables=True,
        include_links=True,
        deduplicate=False,
        no_fallback=False,
        config=_trafilatura_config,
    )
    content = _normalize_text(extracted or "")
    if len(content) < _min_text_threshold(content):
        return None

    title = default_title
    if _trafilatura_extract_metadata is not None:
        metadata = _trafilatura_extract_metadata(html, default_url=url)
        title = getattr(metadata, "title", None) or default_title
    return ExtractedWebContent(
        title=str(title).strip() or default_title,
        content=content,
        strategy="trafilatura",
    )


def _extract_main_container_text(html: str) -> str:

    page = Selector(content=html)
    best_text = ""

    for selector in (
        "article",
        "main",
        "[role='main']",
        ".post-content",
        ".article-content",
        ".entry-content",
        ".post-body",
        ".article-body",
    ):
        for node in page.css(selector):
            candidate = _extract_node_text(node)
            if len(candidate) > len(best_text):
                best_text = candidate

    return best_text if len(best_text) >= _min_text_threshold(best_text) else ""


def _extract_node_text(node: Selector) -> str:

    return _normalize_text(
        node.get_all_text(
            separator="\n",
            strip=True,
            ignore_tags=("script", "style", "noscript", "iframe", "nav", "aside", "footer", "header"),
        )
    )


def _extract_title(page: Selector, url: str) -> str:
    for selector in (
        "meta[property='og:title']",
        "meta[name='title']",
        "title",
        "h1",
    ):
        nodes = page.css(selector)
        if not nodes:
            continue
        node = nodes[0]
        if node.tag == "meta":
            content = str(node.attrib.get("content", "")).strip()
            if content:
                return content
            continue
        text = str(node.get_all_text(strip=True))
        if text:
            return text
    return url


def _extract_schema_article(
    page: Selector,
    *,
    default_title: str,
) -> ExtractedWebContent | None:
    for script in page.css('script[type*="ld+json"]'):
        raw = str(script.text or script.get_all_text(separator=" ", strip=True))
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for article in _iter_json_objects(payload):
            article_body = str(article.get("articleBody", "")).strip()
            if len(article_body) < _min_text_threshold(article_body):
                continue
            content = _normalize_text(article_body)
            if len(content) < _min_text_threshold(content):
                continue
            title = str(article.get("headline") or default_title).strip() or default_title
            return ExtractedWebContent(
                title=title,
                content=content,
                strategy="schema_article_body",
            )
    return None


def _iter_json_objects(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(value, dict):
        objects.append(value)
        for nested in value.values():
            objects.extend(_iter_json_objects(nested))
    elif isinstance(value, list):
        for item in value:
            objects.extend(_iter_json_objects(item))
    return objects


def _extract_fallback_text(page: Selector) -> str:
    _noise_tags = (
        "script", "style", "noscript", "iframe", "svg",
        "canvas", "form", "button", "input", "template",
        "header", "footer", "nav", "aside",
    )
    _ancestor_noise = {"nav", "aside", "footer", "header"}

    fragments: list[str] = []
    seen: set[str] = set()
    selector = ", ".join(_FALLBACK_BLOCK_TAGS)
    for node in page.css(selector):
        if node.find_ancestor(lambda a: a.tag in _ancestor_noise):
            continue
        hint = _node_hint(node)
        if any(token in hint for token in _NOISE_HINTS):
            continue
        if _link_density(node) > 0.5:
            continue

        text = _normalize_text(str(node.get_all_text(separator=" ", strip=True, ignore_tags=_noise_tags)))
        if len(text) < 20 or _looks_like_noise_line(text) or text in seen:
            continue
        seen.add(text)
        fragments.append(f"- {text}" if node.tag == "li" else text)
        if len("\n".join(fragments)) >= 4000:
            break

    combined = "\n".join(fragments)
    return combined if len(combined) >= 60 else ""


def _node_hint(node: Selector) -> str:
    attrib = node.attrib
    raw_classes = attrib.get("class", "")
    classes = str(raw_classes)
    node_id = str(attrib.get("id", ""))
    return f"{node.tag} {classes} {node_id}".lower()


def _link_density(node: Selector) -> float:
    text = str(node.get_all_text(separator=" ", strip=True))
    if not text:
        return 0.0
    link_text = " ".join(
        str(link.get_all_text(separator=" ", strip=True)) for link in node.css("a")
    )
    return len(link_text) / max(len(text), 1)


def _looks_like_noise_line(text: str) -> bool:
    lowered = text.lower()
    if lowered.count("/") > 4 or lowered.count("|") > 3:
        return True
    if re.fullmatch(r"[\d\s:/\-\.]+", text):
        return True
    return any(token in lowered for token in _NOISE_HINTS)


def _normalize_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(
        r"[×…–—～]",
        lambda m: {"×": "x", "…": "...", "–": "-", "—": "-", "～": "~"}[m.group()],
        text,
    )

    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 5 or _looks_like_noise_line(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    normalized = "\n".join(lines)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _looks_like_javascript_shell(page: Selector) -> bool:
    visible_text = _normalize_text(
        str(page.get_all_text(
            separator="\n",
            strip=True,
            ignore_tags=("script", "style", "noscript", "template"),
        ))
    )
    visible_text_len = len(visible_text)
    script_nodes = page.css("script")
    script_count = len(script_nodes)
    script_text_sample = " ".join(
        str(s.text) for s in script_nodes[:12]
    ).lower()
    root_marker = any(page.css(selector) for selector in _JS_SHELL_ROOT_SELECTORS)
    semantic_content = bool(page.css("article, main"))
    paragraph_count = len(page.css("p"))

    if visible_text_len <= 180 and root_marker and script_count >= 4:
        return True
    if (
        visible_text_len <= 260
        and not semantic_content
        and paragraph_count <= 2
        and script_count >= 8
    ):
        return True
    return root_marker and any(
        marker in script_text_sample for marker in _JS_SHELL_SCRIPT_MARKERS
    )


def _extract_weather_content(page: Selector) -> str | None:
    weather_data = []

    city_nodes = page.css(".crumbs a, .city-name, h1, title")
    city = str(city_nodes[0].get_all_text(strip=True)) if city_nodes else "未知城市"
    weather_data.append(f"城市: {city}")

    all_text = str(page.get_all_text(separator="\n", strip=True))
    lines = all_text.split("\n")
    for line in lines:
        line = line.strip()
        if len(line) < 5 or len(line) > 200:
            continue
        if re.search(r"(周[一二三四五六日]|今天|明天|后天|\d{1,2}月\d{1,2}日)", line):
            if re.search(r"(晴|多云|阴|雨|雪|雾|霾|°|℃|\d+度)", line):
                weather_data.append(line)
        elif re.search(r"(-?\d+)[°℃]", line) and re.search(r"(晴|多云|阴|雨|雪)", line):
            weather_data.append(line)
        elif re.search(r"(气温|温度|风力|湿度|空气质量|AQI|紫外线)", line):
            weather_data.append(line)

    if len(weather_data) > 3:
        return "\n".join(weather_data[:30])
    return None
