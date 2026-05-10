from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout
from bs4 import BeautifulSoup

from .web_extractor import ExtractedWebContent, extract_web_content

if TYPE_CHECKING:
    from .web_search import SearchResult

_LOGGER = logging.getLogger(__name__)

_MARKDOWN_EXTENSIONS = (".md", ".markdown", ".mdx", ".txt")


@dataclass(slots=True)
class FetchedPage:
    request_url: str
    final_url: str
    text: str
    content_type: str


def _looks_like_plaintext_document(url: str, response: str) -> bool:
    lowered_url = url.lower()
    if any(lowered_url.endswith(ext) for ext in _MARKDOWN_EXTENSIONS):
        return True

    html_marker_count = sum(
        response.count(token) for token in ("<html", "<body", "<div", "<article", "<p")
    )
    markdown_markers = sum(
        1
        for pattern in (
            r"^# .+",
            r"^## .+",
            r"^```",
            r"^\* .+",
            r"^- .+",
            r"^\d+\. .+",
        )
        if re.search(pattern, response, flags=re.MULTILINE)
    )
    return html_marker_count == 0 and markdown_markers >= 1


def _extract_plaintext_document(response: str, url: str) -> tuple[str, str] | None:
    lines = [line.rstrip() for line in response.splitlines()]
    cleaned_lines = [line.strip() for line in lines if line.strip()]
    if not cleaned_lines:
        return None

    title = url
    for line in cleaned_lines[:8]:
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            break
    else:
        title = cleaned_lines[0][:120]

    content = "\n".join(cleaned_lines)
    if len(content) < 20:
        return None
    return title, content


def _normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def _classify_fetched_page(page: FetchedPage) -> str:
    normalized_content_type = _normalize_content_type(page.content_type)
    lowered_url = page.final_url.lower()

    if normalized_content_type in {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    }:
        return "rss"
    if ".xml" in lowered_url or "feed" in lowered_url or "rss" in lowered_url:
        return "rss"
    if normalized_content_type.startswith("text/plain") or _looks_like_plaintext_document(
        page.final_url, page.text
    ):
        return "plaintext"
    return "html"


def _build_extracted_result(
    title: str,
    content: str,
    *,
    strategy: str,
) -> ExtractedWebContent:
    return ExtractedWebContent(
        title=title,
        content=content,
        strategy=strategy,
    )


class WebPageFetcher:


    def __init__(
        self,
        session: ClientSession,
        *,
        blocked_domains: set[str],
        get_headers,
    ) -> None:
        self.session = session
        self.blocked_domains = blocked_domains
        self._get_headers = get_headers

    def is_domain_blocked(self, url: str) -> bool:
        if not url:
            return True
        try:
            domain = urlparse(url).netloc.lower()
            return any(blocked in domain for blocked in self.blocked_domains)
        except Exception:
            return True

    async def make_request(self, url: str, **kwargs) -> FetchedPage | None:
        if "baidu.com/link" in url:
            real_url = await self.resolve_baidu_redirect(url)
            if real_url and real_url != url:
                _LOGGER.debug("Baidu redirect resolved: %s... -> %s...", url[:50], real_url[:50])
                url = real_url

        headers = kwargs.pop("headers", None) or self._get_headers()
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            domain = ""
        if "Referer" not in headers:
            headers["Referer"] = f"https://{domain}/" if domain else "https://www.google.com/"
        headers.setdefault("Upgrade-Insecure-Requests", "1")
        headers.setdefault("Sec-Fetch-Dest", "document")
        headers.setdefault("Sec-Fetch-Mode", "navigate")
        headers.setdefault("Sec-Fetch-Site", "none")
        headers.setdefault("Sec-Fetch-User", "?1")

        for attempt in range(3):
            try:
                async with self.session.get(
                    url,
                    timeout=ClientTimeout(total=30),
                    allow_redirects=True,
                    ssl=False,
                    headers=headers,
                ) as resp:
                    final_url = str(resp.url)
                    if final_url != url:
                        _LOGGER.debug("Redirected to: %s...", final_url[:80])
                    if resp.status == 200:
                        content = await resp.text(errors="ignore")
                        if len(content.strip()) > 20:
                            return FetchedPage(
                                request_url=url,
                                final_url=final_url,
                                text=content,
                                content_type=resp.headers.get("Content-Type", ""),
                            )
                    elif resp.status in [301, 302, 303, 307, 308]:
                        url = str(resp.headers.get("Location", ""))
                        if url:
                            continue
                    elif resp.status == 403 and attempt == 0:
                        headers["User-Agent"] = (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/125.0.0.0 Safari/537.36"
                        )
                        continue
                    else:
                        _LOGGER.debug("Request %s returned status %d", url[:60], resp.status)
                        break
            except asyncio.TimeoutError:
                _LOGGER.debug("Request timeout (attempt %d): %s", attempt, url[:60])
                await asyncio.sleep(1)
            except Exception as err:
                _LOGGER.debug("Request failed (attempt %d): %s", attempt, err)
                await asyncio.sleep(1)
        return None

    async def resolve_baidu_redirect(self, baidu_url: str) -> str | None:
        try:
            async with self.session.get(
                baidu_url,
                timeout=ClientTimeout(total=15),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                final_url = str(resp.url)
                if final_url and "baidu.com" not in final_url:
                    _LOGGER.debug(
                        "Baidu redirect success: %s... -> %s...",
                        baidu_url[:40],
                        final_url[:60],
                    )
                    return final_url
                if resp.status in [301, 302, 303, 307, 308]:
                    location = resp.headers.get("Location", "")
                    if location and "baidu.com" not in location:
                        return location
                html = await resp.text(errors="ignore")
                match = re.search(r"URL='?(https?://[^'\"\\s>]+)", html)
                if match:
                    real_url = match.group(1)
                    if "baidu.com" not in real_url:
                        _LOGGER.debug("Extracted real URL from HTML: %s...", real_url[:60])
                        return real_url
        except Exception:
            pass
        return baidu_url

    def extract_urls_from_query(self, query: str) -> list[str]:
        url_patterns = [
            r"@?(https?://[^\s]+)",
            r"@?(www\.[^\s]+)",
            r"@?([a-zA-Z0-9-]+\.[a-zA-Z]{2,}\.[a-zA-Z]{2,})",
        ]
        urls: list[str] = []
        for pattern in url_patterns:
            matches = re.finditer(pattern, query)
            for match in matches:
                url = match.group(1) if match.groups() else match.group(0)
                if url.startswith("@"):
                    url = url[1:]
                urls.append(url)
        return urls

    async def extract_content_with_response(
        self,
        url: str,
    ) -> tuple[FetchedPage, ExtractedWebContent] | None:
        if self.is_domain_blocked(url):
            _LOGGER.debug("Skipping blocked domain: %s", url)
            return None

        real_url = url
        if "baidu.com/link" in url:
            real_url = await self.resolve_baidu_redirect(url)
            if real_url != url:
                _LOGGER.debug("Content extraction: Baidu redirect -> %s...", real_url[:80])

        try:
            _LOGGER.debug("Starting content extraction: %s", real_url)
            page = await self.make_request(real_url, headers=self._get_headers())
            if not page:
                _LOGGER.debug("Failed to fetch page content: %s", real_url)
                return None

            kind = _classify_fetched_page(page)
            if kind == "plaintext":
                extracted_plaintext = _extract_plaintext_document(page.text, page.final_url)
                if extracted_plaintext:
                    title, content = extracted_plaintext
                    _LOGGER.debug(
                        "Successfully extracted plaintext document (length:%s): %s",
                        len(content),
                        page.final_url,
                    )
                    return page, _build_extracted_result(
                        title,
                        content,
                        strategy="plaintext_document",
                    )

            if kind == "rss":
                rss_content = self._extract_rss_content(page.text)
                if rss_content:
                    return page, _build_extracted_result(
                        page.final_url,
                        rss_content,
                        strategy="rss_feed",
                    )

            extracted = await asyncio.to_thread(
                extract_web_content,
                page.text,
                page.final_url,
            )
            if extracted is not None:
                if extracted.content:
                    _LOGGER.debug(
                        "Successfully extracted content (strategy:%s, length:%s): %s",
                        extracted.strategy,
                        len(extracted.content),
                        page.final_url,
                    )
                else:
                    _LOGGER.debug(
                        "Page has no reliable body content (strategy:%s): %s",
                        extracted.strategy,
                        page.final_url,
                    )
                return page, extracted

            _LOGGER.debug("Failed to extract valid content: %s", page.final_url)
        except Exception as err:
            _LOGGER.error("Content extraction failed %s: %s", url, err)
        return None

    def _extract_rss_content(self, response: str) -> str | None:
        try:
            soup = BeautifulSoup(response, "xml")
            items = soup.find_all(["item", "entry"])
            if not items:
                return None
            rss_content = []
            for item in items[:10]:
                title = item.find(["title"])
                desc = item.find(["description", "summary", "content"])
                title_text = title.get_text(strip=True) if title else ""
                desc_text = desc.get_text(strip=True) if desc else ""
                if title_text:
                    rss_content.append(f"【{title_text}】\n{desc_text[:500]}")
            if rss_content:
                _LOGGER.debug("RSS parsing success: %d items", len(items))
                return "\n\n".join(rss_content)
        except Exception as err:
            _LOGGER.debug("RSS parsing failed: %s", err)
        return None

    async def fetch_url_content(
        self,
        url: str,
        search_result_cls: type["SearchResult"],
    ) -> "SearchResult" | None:
        _LOGGER.debug("Directly fetching URL content: %s", url)
        try:
            platform_patterns = {
                "bilibili": r"bilibili\.com",
                "youtube": r"youtube\.com",
                "twitter": r"twitter\.com",
                "weibo": r"weibo\.com",
            }
            platform = next(
                (name for name, pattern in platform_patterns.items() if re.search(pattern, url)),
                "general",
            )

            content_tuple = await self.extract_content_with_response(url)
            if not content_tuple:
                _LOGGER.debug("Failed to extract URL content: %s", url)
                return None

            page, extracted = content_tuple
            metadata = {
                "source": "direct_url",
                "platform": platform,
                "timestamp": datetime.now().isoformat(),
                "has_content": bool(extracted.content),
                "extraction_strategy": extracted.strategy,
                **extracted.metadata,
            }

            if extracted.content:
                snippet = (
                    extracted.content[:150] + "..."
                    if len(extracted.content) > 150
                    else extracted.content
                )
            elif extracted.metadata.get("requires_browser"):
                snippet = (
                    "Page appears to require browser rendering; "
                    "no reliable server-rendered text was found."
                )
            else:
                snippet = ""

            return search_result_cls(
                title=extracted.title or page.final_url,
                url=page.final_url,
                snippet=snippet,
                content=extracted.content,
                metadata=metadata,
            )
        except Exception as err:
            _LOGGER.error("Failed to fetch URL content: %s, error: %s", url, err)
            return None
