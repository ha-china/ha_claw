from __future__ import annotations
import logging
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)


class WebSearchTool(llm.Tool):
    name = "WebSearch"
    description = "联网搜索实时信息。当用户询问新闻、天气、股票、最新消息等需要实时数据时使用。"
    parameters = vol.Schema({
        vol.Required("query"): str,
        vol.Optional("num_results", default=3): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.web_search import WebSearch
        
        query = tool_input.tool_args.get("query", "")
        num = tool_input.tool_args.get("num_results", 3)
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "WebSearch"
        try:
            async with WebSearch() as ws:
                results = await ws.search(query, num)
                if not results:
                    return {"success": False, "error": "未找到搜索结果"}
                
                output_parts = []
                for r in results[:num]:
                    content = r.content or r.snippet or ""
                    if content:
                        output_parts.append(f"【{r.title}】\n{content}")
                    else:
                        output_parts.append(f"【{r.title}】\n{r.snippet or '无内容'}")
                
                return {"success": True, "count": len(results), "results": "\n\n---\n\n".join(output_parts)}
        except Exception as e:
            _LOGGER.error(f"WebSearchTool error: {e}")
            return {"success": False, "error": str(e)}


class UrlFetchTool(llm.Tool):
    name = "UrlFetch"
    description = "获取URL内容。用于读取网页、API等。"
    parameters = vol.Schema({
        vol.Required("url"): str,
        vol.Optional("max_length", default=2000): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.web_search import WebSearch
        from ..utils.text_compressor import TextCompressor
        url = tool_input.tool_args.get("url", "")
        max_len = tool_input.tool_args.get("max_length", 2000)
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "UrlFetch"
        try:
            async with WebSearch() as ws:
                result = await ws.fetch_url_content(url)
                if result and result.content:
                    compressor = TextCompressor(target_length=max_len)
                    compressed = compressor.compress(result.content)
                    return {"success": True, "title": result.title, "content": compressed.text, "compressed": True, "ratio": f"{compressed.compression_ratio:.1%}"}
                return {"success": False, "error": "Failed to fetch URL"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class NewsSearchTool(llm.Tool):
    name = "NewsSearch"
    description = """获取金十数据财经新闻快讯。

参数：
- category: 分类筛选（可选）
  - all: 全部（默认）
  - stock: A股相关
  - forex: 外汇
  - futures: 期货
  - gold: 贵金属
  - important: 仅重要新闻
- limit: 返回条数（默认15，最多30）
- query: 关键词筛选（可选）

返回：实时财经快讯，包含主力资金流向、政策新闻、市场动态等"""
    parameters = vol.Schema({
        vol.Optional("category", default="all"): str,
        vol.Optional("limit", default=15): int,
        vol.Optional("query", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from aiohttp import ClientSession, ClientTimeout
        from datetime import datetime
        
        category = tool_input.tool_args.get("category", "all").lower()
        limit = min(tool_input.tool_args.get("limit", 15), 30)
        query = tool_input.tool_args.get("query", "")
        
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "NewsSearch"
        
        category_map = {
            "all": "-8200", "stock": "1", "forex": "2", 
            "futures": "3", "gold": "4", "important": "-8200"
        }
        channel = category_map.get(category, "-8200")
        only_important = category == "important"
        
        _LOGGER.info(f"NewsSearchTool: category={category}, channel={channel}, limit={limit}, query={query}")
        
        try:
            api_url = "https://flash-api.jin10.com/get_flash_list"
            headers = {"x-app-id": "SO1EJGmNgCtmpcPF", "x-version": "1.0.0"}
            now = datetime.now()
            params = {
                "max_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": channel,
                "vip": "1",
                "limit": str(limit * 2)
            }
            
            async with ClientSession(timeout=ClientTimeout(total=15)) as session:
                async with session.get(api_url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        return {"success": False, "error": f"API返回错误: {resp.status}"}
                    
                    data = await resp.json()
                    items = data.get("data", [])
                    _LOGGER.info(f"NewsSearchTool: Jin10返回 {len(items)} 条原始数据")
                    
                    news_list = []
                    for item in items:
                        if len(news_list) >= limit:
                            break
                        
                        item_data = item.get("data", {})
                        content = item_data.get("content", "").strip()
                        if not content or len(content) < 10:
                            continue
                        
                        if "<" in content or "点击" in content or "VIP" in content:
                            continue
                        
                        important = item.get("important", 0)
                        if only_important and important == 0:
                            continue
                        
                        if query and query not in content:
                            continue
                        
                        time_str = item.get("time", "")
                        importance_mark = "[重要] " if important == 1 else ""
                        
                        content = content.replace("<b>", "").replace("</b>", "")
                        content = content.replace("<br />", " ").replace("<br/>", " ")
                        content = content.replace('<span class="section-news">', "").replace("</span>", "")
                        content = content.replace("金十数据", "").replace("金十", "")
                        
                        news_list.append(f"{importance_mark}{time_str} - {content[:600]}")
                    
                    if news_list:
                        category_names = {
                            "all": "财经", "stock": "A股", "forex": "外汇",
                            "futures": "期货", "gold": "贵金属", "important": "重要"
                        }
                        cat_name = category_names.get(category, "财经")
                        
                        formatted_news = []
                        for i, news in enumerate(news_list, 1):
                            formatted_news.append(f"{i}. {news}")
                        
                        return {
                            "success": True,
                            "category": cat_name,
                            "count": len(news_list),
                            "news": "\n\n".join(formatted_news),
                            "instruction": "请根据以上新闻内容回答用户，用简洁易读的格式整理。"
                        }
                    
                    return {"success": False, "error": "未获取到符合条件的新闻"}
                    
        except Exception as e:
            _LOGGER.error(f"NewsSearchTool error: {e}")
            return {"success": False, "error": str(e)}


class DeepWebSearchTool(llm.Tool):
    name = "DeepWebSearch"
    description = "深度网页搜索。搜索并提取多个网页内容，进行综合分析。"
    parameters = vol.Schema({
        vol.Required("query"): str,
        vol.Optional("num_results", default=3): int,
        vol.Optional("extract_content", default=True): bool,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.web_search import WebSearch
        from ..utils.text_compressor import TextCompressor, compress_search_results
        query = tool_input.tool_args.get("query", "")
        num = tool_input.tool_args.get("num_results", 3)
        extract = tool_input.tool_args.get("extract_content", True)
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "DeepWebSearch"
        compressor = TextCompressor(target_length=1500)
        try:
            async with WebSearch() as ws:
                results = await ws.search(query, num)
                if extract and results:
                    contents = []
                    for r in results[:num]:
                        if r.content:
                            compressed = compressor.compress(r.content)
                            contents.append(f"【{r.title}】\n{compressed.text}")
                    if contents:
                        return {"success": True, "count": len(contents), "contents": "\n\n---\n\n".join(contents)}
                if results:
                    return {"success": True, "results": [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results]}
                return {"success": False, "error": "No results"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class ZhihuHotTool(llm.Tool):
    name = "ZhihuHot"
    description = "获取知乎热榜。"
    parameters = vol.Schema({vol.Optional("limit", default=20): int})

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.web_search import WebSearch
        limit = tool_input.tool_args.get("limit", 20)
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "ZhihuHot"
        try:
            async with WebSearch() as ws:
                results = await ws._fetch_zhihu_hot()
                return {"success": True, "hot": [{"title": r.title, "hot": r.metadata.get("hot")} for r in results[:limit]]}
        except Exception as e:
            return {"success": False, "error": str(e)}


class StockQueryTool(llm.Tool):
    name = "StockQuery"
    description = """🚨必须用此工具查询股票/基金/A股/美股行情！禁止用WebSearch/NewsSearch搜索股票！

触发关键词：股票、股价、行情、A股、美股、港股、基金、涨跌、茅台、特斯拉、苹果、腾讯等

常用代码：
- A股: 茅台600519 平安000001 招商银行600036
- 美股: 特斯拉TSLA 苹果AAPL 英伟达NVDA 微软MSFT
- 港股: 腾讯00700 阿里09988
- 基金: 输入6位代码

返回：实时价格、涨跌、涨跌幅、今开、昨收、最高、最低、成交量、市盈率等"""
    parameters = vol.Schema({
        vol.Required("codes"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.stock_api import StockAPI, format_stock_data
        
        codes_str = tool_input.tool_args.get("codes", "")
        codes = [c.strip() for c in codes_str.replace("，", ",").split(",") if c.strip()]
        
        if not codes:
            return {"success": False, "error": "请提供股票/基金代码"}
        
        hass.data["ha_crack_tool_called"] = True
        hass.data["ha_crack_last_tool"] = "StockQuery"
        
        _LOGGER.info(f"StockQueryTool: 查询 {codes}")
        
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
                    return {"success": False, "error": f"未找到股票/基金: {codes[0]}"}
                else:
                    results = await api.query_stocks(codes)
                    if results:
                        formatted = [format_stock_data(d) for d in results]
                        return {
                            "success": True,
                            "count": len(results),
                            "data": "\n\n---\n\n".join(formatted),
                        }
                    return {"success": False, "error": "未找到任何股票/基金数据"}
        except Exception as e:
            _LOGGER.error(f"StockQueryTool error: {e}")
            return {"success": False, "error": str(e)}
