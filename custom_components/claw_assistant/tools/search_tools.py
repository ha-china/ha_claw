from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from ..runtime import mark_tool_called
from ..services.web_formatter import format_search_results_text, prepare_web_text_for_ai
from ..services.web_search import WebSearch
from ..services.stock_api import StockAPI, format_stock_data

_LOGGER = logging.getLogger(__name__)


class WebSearchTool(llm.Tool):
    name = "WebSearch"
    description = (
        "Web search. Strategy: bing first, baidu fallback (auto). "
        "After getting results, use UrlFetch(url) to read page content, "
        "then WebReadChunk(doc_id, position) for more. "
        "Engines: google, bing, baidu, bing_cn. Leave engine empty for auto. "
        "Write query in the same language the user used."
    )
    parameters = vol.Schema({
        vol.Required("query"): str,
        vol.Optional("num_results", default=5): int,
        vol.Optional("engine", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        query = tool_input.tool_args.get("query", "")
        num = tool_input.tool_args.get("num_results", 5)
        engine = tool_input.tool_args.get("engine", "")
        mark_tool_called(hass, "WebSearch")
        try:
            async with WebSearch(hass=hass) as ws:
                results = await ws.search(query, num, engine=engine, fetch_content=False)
                if not results:
                    return {
                        "success": False,
                        "error": "No results found",
                        "hint": "Try: 1) rephrase query with different keywords, 2) set engine='bing' or engine='baidu' explicitly, 3) use English keywords for international topics.",
                    }

                items = []
                for i, r in enumerate(results[:num], 1):
                    items.append({
                        "index": i,
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet or "",
                    })

                return {
                    "success": True,
                    "count": len(items),
                    "results": items,
                    "hint": "Use UrlFetch(url) to read full page content. Then WebReadChunk(doc_id, position) for subsequent chunks.",
                }
        except Exception as e:
            _LOGGER.error("WebSearchTool error: %s", e)
            return {
                "success": False,
                "error": str(e),
                "hint": "Retry with engine='bing' or engine='baidu'. Or rephrase query.",
            }


_CHUNK_TARGET = 1500
_CHUNK_MAX = 2000
_CHUNK_CACHE_KEY = "claw_web_chunks"


def _get_chunk_cache(hass: HomeAssistant) -> dict:
    if _CHUNK_CACHE_KEY not in hass.data:
        hass.data[_CHUNK_CACHE_KEY] = {}
    return hass.data[_CHUNK_CACHE_KEY]


def _split_paragraphs_smart(text: str) -> list[str]:
    import re
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)
        if current and current_len + para_len + 2 > _CHUNK_TARGET:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        if para_len > _CHUNK_MAX:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            sentences = re.split(r"(?<=[.!?。！？\n])\s*", para)
            buf: list[str] = []
            buf_len = 0
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if buf and buf_len + len(s) + 1 > _CHUNK_TARGET:
                    chunks.append(" ".join(buf))
                    buf = []
                    buf_len = 0
                buf.append(s)
                buf_len += len(s) + 1
            if buf:
                chunks.append(" ".join(buf))
        else:
            current.append(para)
            current_len += para_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks if chunks else [""]


def _store_chunks(hass: HomeAssistant, url: str, title: str, full_text: str) -> tuple[str, list[str]]:
    import hashlib
    doc_id = hashlib.md5(url.encode()).hexdigest()[:10]
    cleaned = prepare_web_text_for_ai(full_text, max_chars=len(full_text) + 1)
    chunks = _split_paragraphs_smart(cleaned)
    cache = _get_chunk_cache(hass)
    cache[doc_id] = {"url": url, "title": title, "chunks": chunks}
    if len(cache) > 50:
        oldest = list(cache.keys())[0]
        del cache[oldest]
    return doc_id, chunks


class UrlFetchTool(llm.Tool):
    name = "UrlFetch"
    description = "Fetch a URL and return its content as chunk 0. If the page has more content, use WebReadChunk with the returned doc_id and position to read subsequent chunks."
    parameters = vol.Schema({
        vol.Required("url"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        url = tool_input.tool_args.get("url", "")
        mark_tool_called(hass, "UrlFetch")
        try:
            async with WebSearch(hass=hass) as ws:
                result = await ws.fetch_url_content(url)
                if result and result.content:
                    doc_id, chunks = _store_chunks(hass, url, result.title, result.content)
                    return {
                        "success": True,
                        "doc_id": doc_id,
                        "title": result.title,
                        "chunk": 0,
                        "total_chunks": len(chunks),
                        "content": chunks[0],
                        "has_more": len(chunks) > 1,
                    }
                return {"success": False, "error": "Failed to fetch URL"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class WebReadChunkTool(llm.Tool):
    name = "WebReadChunk"
    description = "Read a specific chunk of a previously fetched web page. Use the doc_id and position returned by UrlFetch."
    parameters = vol.Schema({
        vol.Required("doc_id"): str,
        vol.Required("position"): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        doc_id = tool_input.tool_args.get("doc_id", "")
        position = tool_input.tool_args.get("position", 0)
        cache = _get_chunk_cache(hass)
        doc = cache.get(doc_id)
        if not doc:
            return {"success": False, "error": f"Document {doc_id} not found. Use UrlFetch first."}
        chunks = doc["chunks"]
        if position < 0 or position >= len(chunks):
            return {"success": False, "error": f"Position {position} out of range (0-{len(chunks) - 1})"}
        return {
            "success": True,
            "doc_id": doc_id,
            "title": doc["title"],
            "chunk": position,
            "total_chunks": len(chunks),
            "content": chunks[position],
            "has_more": position < len(chunks) - 1,
        }


class StockQueryTool(llm.Tool):
    name = "StockQuery"
    description = """Use this tool for stocks, funds, China A-shares, US equities, and Hong Kong market quotes. Do not use WebSearch for quote lookup.

Typical triggers: stock, quote, market, A-shares, US stocks, Hong Kong stocks, funds, gain/loss, Tesla, Apple, Tencent, and similar.

Common code examples:
- China A-shares: 600519, 000001, 600036
- US stocks: TSLA, AAPL, NVDA, MSFT
- Hong Kong stocks: 00700, 09988
- Funds: 6-digit code

Returns real-time price, change, change percent, open, previous close, high, low, volume, P/E ratio, and related data."""
    parameters = vol.Schema({
        vol.Required("codes"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        codes_str = tool_input.tool_args.get("codes", "")
        codes = [c.strip() for c in codes_str.replace("，", ",").split(",") if c.strip()]

        if not codes:
            return {"success": False, "error": "Please provide one or more stock or fund codes"}

        mark_tool_called(hass, "StockQuery")

        _LOGGER.info(f"StockQueryTool: querying {codes}")

        try:
            async with StockAPI() as api:
                if len(codes) == 1:
                    data = await api.query_stock(codes[0])
                    if data:
                        return {
                            "success": True,
                            "count": 1,
                            "data": format_stock_data(data),
                            "raw": {
                                "code": data.code,
                                "name": data.name,
                                "price": data.price,
                                "change": data.change,
                                "change_percent": data.change_percent,
                                "market": data.market,
                            }
                        }
                    return {"success": False, "error": f"Stock or fund not found: {codes[0]}"}
                else:
                    results = await api.query_stocks(codes)
                    if results:
                        formatted = [format_stock_data(d) for d in results]
                        return {
                            "success": True,
                            "count": len(results),
                            "data": "\n\n---\n\n".join(formatted),
                        }
                    return {"success": False, "error": "No stock or fund data found"}
        except Exception as e:
            _LOGGER.error(f"StockQueryTool error: {e}")
            return {"success": False, "error": str(e)}
