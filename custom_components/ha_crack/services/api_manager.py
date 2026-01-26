from __future__ import annotations

import logging
import datetime
import re
import json
import aiohttp
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from bs4 import BeautifulSoup

from ..models import SearchResult, clean_text
from ..utils.time_parser import parse_query_time
from ..services.content_processor import ContentProcessor

_LOGGER = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, max_calls: int, period: int):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = datetime.datetime.now().timestamp()
            self.calls = [t for t in self.calls if now - t < self.period]

            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            self.calls.append(datetime.datetime.now().timestamp())

class NewsAPI:
    def __init__(self):
        self.session = None
        self.rate_limiter = RateLimiter(10, 60)
        self.cache = {}
        self.cache_ttl = 300
        self.api_url = "https://flash-api.jin10.com/get_flash_list"
        self.api_headers = {
            "x-app-id": "SO1EJGmNgCtmpcPF",
            "x-version": "1.0.0"
        }

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _get_cache_key(self, start_time: datetime.datetime, end_time: datetime.datetime) -> str:
        return f"news:{start_time.isoformat()}:{end_time.isoformat()}"

    def _get_cached_results(self, start_time: datetime.datetime, end_time: datetime.datetime) -> Optional[List[Dict]]:
        cache_key = self._get_cache_key(start_time, end_time)
        cached = self.cache.get(cache_key)
        if cached:
            cache_time, results = cached
            if datetime.datetime.now().timestamp() - cache_time < self.cache_ttl:
                return results
        return None

    def _cache_results(self, start_time: datetime.datetime, end_time: datetime.datetime, results: List[Dict]):
        cache_key = self._get_cache_key(start_time, end_time)
        self.cache[cache_key] = (datetime.datetime.now().timestamp(), results)

    async def _fetch_period_news(self, end_time: datetime.datetime, limit: int) -> List[Dict]:
        try:
            await self.rate_limiter.acquire()
            cached_results = self._get_cached_results(end_time - datetime.timedelta(hours=24), end_time)
            if cached_results:
                return cached_results[:limit]

            params = {
                "max_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": "-8200",
                "vip": "1",
                "limit": str(limit)
            }

            async with self.session.get(self.api_url, params=params, headers=self.api_headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data") and isinstance(data["data"], list):
                        results = data["data"][:limit]
                        self._cache_results(end_time - datetime.timedelta(hours=24), end_time, results)
                        return results
            return []
        except Exception as e:
            _LOGGER.error(f"获取时段新闻失败: {str(e)}")
            return []

    async def fetch_news(self, query: str = "", limit: int = 15) -> List[Dict]:
        try:
            await self.rate_limiter.acquire()
            modified_query, formatted_date, start_time, end_time = parse_query_time(query)
            cached_results = self._get_cached_results(start_time, end_time)
            if cached_results:
                return cached_results[:limit]

            params = {
                "max_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": "-8200",
                "vip": "1",
                "limit": str(limit)
            }
            if query:
                params["keyword"] = query

            async with self.session.get(self.api_url, params=params, headers=self.api_headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("data") and isinstance(data["data"], list):
                        results = data["data"][:limit]
                        self._cache_results(start_time, end_time, results)
                        return results
            return []
        except Exception as e:
            _LOGGER.error(f"获取新闻失败: {str(e)}")
            return []

    def clean_news_content(self, content: str) -> Optional[str]:
        if not content:
            return None

        title = ""
        title_match = re.match(r"【(.+?)】", content)
        if title_match:
            title = title_match[1]
            content = content.replace(f"【{title}】", "").strip()

        filter_keywords = [
            "每日市场观察", "点击观看", "点击", "专属", "神器", "直播间",
            "font class", "important", "text-font",
            "href", "target_blank", "height", "src",
            "盯盘", "相关", "营销", "每日", "速递"
        ]

        if any(keyword in content.lower() or (title and keyword in title.lower())
               for keyword in filter_keywords):
            return None

        content = (content
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<br />", "")
            .replace("<br/>", "")
            .replace('<span class="section-news">', "")
            .replace("</span>", " "))

        content = (content
            .replace("国外", "\n**国外**\n")
            .replace("国内", "\n**国内**\n"))

        content = (content
            .replace("；", "。")
            .replace(";", "。")
            .replace("！", "。")
            .replace("!", "。")
            .replace(":", "："))

        content = re.sub(r'[。，：]{2,}', '。', content)
        content = re.sub(r'[^\w\s，。：%【】\n\u4e00-\u9fff]', '', content)
        content = re.sub(r'\s+', ' ', content)
        content = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', content)
        content = re.sub(r'([\u4e00-\u9fff])\s+(\d)', r'\1\2', content)
        content = re.sub(r'(\d)\s+([\u4e00-\u9fff])', r'\1\2', content)
        content = re.sub(r'(\d)\s*%', r'\1%', content)
        content = re.sub(r'\n\s*\n', '\n', content).strip()

        if len(content) < 10:
            return None

        return content

    async def get_news(self, query: str = "", limit: int = 100) -> List[SearchResult]:
        try:
            news_items = await self.fetch_news(query, limit)
            results = []

            for item in news_items:
                content = self.clean_news_content(item.get("data", {}).get("content", ""))
                if not content:
                    continue

                title = item.get("data", {}).get("title", "")
                if not title:
                    title = content[:50] + "..." if len(content) > 50 else content

                result = SearchResult(
                    title=title,
                    url="",
                    snippet=content[:150] + "..." if len(content) > 150 else content,
                    content=content,
                    metadata={
                        "source": "jin10",
                        "time": item.get("time"),
                    }
                )
                results.append(result)

            return results
        except Exception as e:
            _LOGGER.error(f"获取和处理新闻失败: {str(e)}")
            return []

class NewsDigest:
    def __init__(self):
        self.news_api = None

    async def get_news_by_time_periods(self, query: str) -> List[SearchResult]:
        modified_query, formatted_date, start_time, end_time = parse_query_time(query)

        time_periods = [
            ("凌晨", 0, 5),
            ("清晨", 5, 8),
            ("上午", 8, 11),
            ("中午", 11, 13),
            ("下午", 13, 17),
            ("傍晚", 17, 19),
            ("晚上", 19, 23)
        ]

        news_per_period = 3
        base_date = start_time.date()
        all_results = []

        async with NewsAPI() as news_api:
            self.news_api = news_api

            for period_name, start_hour, end_hour in time_periods:
                period_end = datetime.datetime.combine(base_date, datetime.time(end_hour, 59))

                if period_end > datetime.datetime.now():
                    period_end = datetime.datetime.now()

                period_news = await news_api._fetch_period_news(period_end, news_per_period)

                for item in period_news[:news_per_period]:
                    content = news_api.clean_news_content(item.get('data', {}).get('content', ''))
                    if content:
                        title = f"{period_name}: {item.get('data', {}).get('title', '') or content[:50]}"

                        result = SearchResult(
                            title=title,
                            url="",
                            snippet=content[:150] + "..." if len(content) > 150 else content,
                            content=content,
                            metadata={
                                "source": "jin10",
                                "time": item.get("time"),
                                "time_period": period_name
                            }
                        )
                        all_results.append(result)

        return all_results

    async def get_recent_minutes_news(self, query: str, minutes: int = 20) -> List[SearchResult]:
        now = datetime.datetime.now()
        start_time = now - datetime.timedelta(minutes=minutes)

        all_results = []

        async with NewsAPI() as news_api:
            self.news_api = news_api
            recent_news = await news_api.fetch_news(query, 20)

            for item in recent_news[:20]:
                try:
                    news_time = datetime.datetime.strptime(item.get("time", ""), "%Y-%m-%d %H:%M:%S")
                    if news_time < start_time:
                        continue
                except (ValueError, TypeError):
                    continue

                content = news_api.clean_news_content(item.get('data', {}).get('content', ''))
                if content:
                    result = SearchResult(
                        title=item.get('data', {}).get('title', '') or '最新动态',
                        url="",
                        snippet=content[:150] + "..." if len(content) > 150 else content,
                        content=content,
                        metadata={
                            "source": "jin10",
                            "time": item.get("time"),
                            "recent": True
                        }
                    )
                    all_results.append(result)

        return all_results

class ZhihuAPI:
    def __init__(self):
        self.session = None
        self.rate_limiter = RateLimiter(5, 60)
        self.cache = {}
        self.cache_ttl = 600

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _get_cache_key(self) -> str:
        return f"zhihu_hot:{datetime.date.today().isoformat()}"

    def _get_cached_results(self) -> Optional[List[SearchResult]]:
        cache_key = self._get_cache_key()
        cached = self.cache.get(cache_key)
        if cached:
            cache_time, results = cached
            if datetime.datetime.now().timestamp() - cache_time < self.cache_ttl:
                return results
        return None

    def _cache_results(self, results: List[SearchResult]):
        cache_key = self._get_cache_key()
        self.cache[cache_key] = (datetime.datetime.now().timestamp(), results)

    async def fetch_zhihu_hot(self) -> List[SearchResult]:
        try:
            cached_results = self._get_cached_results()
            if cached_results:
                return cached_results

            await self.rate_limiter.acquire()

            url = "https://www.zhihu.com/hot"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }

            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    hot_items = soup.select(".HotList-item")

                    results = []
                    for item in hot_items[:10]:
                        title_elem = item.select_one(".HotItem-title")
                        excerpt_elem = item.select_one(".HotItem-excerpt")
                        link_elem = item.select_one(".HotItem-content a")

                        if title_elem and link_elem:
                            title = title_elem.text.strip()
                            url = link_elem.get("href", "")
                            snippet = excerpt_elem.text.strip() if excerpt_elem else ""

                            result = SearchResult(
                                title=title,
                                url=url,
                                snippet=snippet,
                                content=f"{title}\n\n{snippet}",
                                metadata={"source": "zhihu_hot"}
                            )
                            results.append(result)

                    self._cache_results(results)
                    return results
            return []
        except Exception as e:
            _LOGGER.error(f"获取知乎热榜失败: {str(e)}")
            return []

class WeatherAPI:
    def __init__(self):
        self.session = None
        self.rate_limiter = RateLimiter(10, 60)
        self.cache = {}
        self.cache_ttl = 1800

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _get_cache_key(self, location: str) -> str:
        return f"weather:{location}:{datetime.date.today().isoformat()}"

    def _get_cached_results(self, location: str) -> Optional[SearchResult]:
        cache_key = self._get_cache_key(location)
        cached = self.cache.get(cache_key)
        if cached:
            cache_time, result = cached
            if datetime.datetime.now().timestamp() - cache_time < self.cache_ttl:
                return result
        return None

    def _cache_results(self, location: str, result: SearchResult):
        cache_key = self._get_cache_key(location)
        self.cache[cache_key] = (datetime.datetime.now().timestamp(), result)

    def extract_location(self, query: str) -> str:
        location_match = re.search(r'([\u4e00-\u9fa5]+)天气', query)
        if location_match:
            return location_match.group(1)
        return ""

    def process_weather_content(self, content: str) -> str:
        if not content:
            return ""

        content = re.sub(r'<[^>]+>', '', content)
        content = re.sub(r'\s{2,}', ' ', content)

        important_sections = []

        temp_match = re.search(r'温度[:\s：]+([^,，。]+)', content)
        if temp_match:
            important_sections.append(f"温度: {temp_match.group(1).strip()}")

        forecast_match = re.search(r'天气预报[:\s：]+([^,，。]+)', content) or re.search(r'天气状况[:\s：]+([^,，。]+)', content)
        if forecast_match:
            important_sections.append(f"天气: {forecast_match.group(1).strip()}")

        wind_match = re.search(r'风力[/风向]*[:\s：]+([^,，。]+)', content)
        if wind_match:
            important_sections.append(f"风: {wind_match.group(1).strip()}")

        humidity_match = re.search(r'湿度[:\s：]+([^,，。]+)', content)
        if humidity_match:
            important_sections.append(f"湿度: {humidity_match.group(1).strip()}")

        aqi_match = re.search(r'空气质量[:\s：]+([^,，。]+)', content) or re.search(r'AQI[:\s：]+([^,，。]+)', content)
        if aqi_match:
            important_sections.append(f"空气质量: {aqi_match.group(1).strip()}")

        if important_sections:
            return "\n".join(important_sections)
        return content[:500]

    async def fetch_weather(self, query: str) -> Optional[SearchResult]:
        location = self.extract_location(query)
        if not location:
            return None

        cached_result = self._get_cached_results(location)
        if cached_result:
            return cached_result

        try:
            await self.rate_limiter.acquire()
            url = f"https://tianqi.2345.com/t/{location}"

            async with self.session.get(url) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")

                    content_parts = []

                    temp_elem = soup.select_one(".cur-t")
                    if temp_elem:
                        content_parts.append(f"温度: {temp_elem.text.strip()}")

                    weather_elem = soup.select_one(".cur-wea")
                    if weather_elem:
                        content_parts.append(f"天气: {weather_elem.text.strip()}")

                    details = soup.select(".wea-about span")
                    for detail in details:
                        detail_text = detail.text.strip()
                        if detail_text:
                            content_parts.append(detail_text)

                    if content_parts:
                        content = "\n".join(content_parts)
                        result = SearchResult(
                            title=f"{location}天气",
                            url=url,
                            snippet=content[:150],
                            content=content,
                            metadata={"type": "weather", "location": location}
                        )
                        self._cache_results(location, result)
                        return result
            return None
        except Exception as e:
            _LOGGER.error(f"获取天气信息失败: {str(e)}")
            return None

async def get_news_digest(query: str, web_search_results: List[SearchResult] = None) -> List[SearchResult]:
    digest = NewsDigest()
    all_results = []

    if "新闻" in query:
        time_period_news = await digest.get_news_by_time_periods(query)
        all_results.extend(time_period_news)

        if web_search_results:
            remaining = 20 - len(all_results)
            if remaining > 0:
                all_results.extend(web_search_results[:remaining])

    elif re.search(r'(\d+)\s*分钟', query):
        match = re.search(r'(\d+)\s*分钟', query)
        minutes = int(match.group(1))

        recent_news = await digest.get_recent_minutes_news(query, minutes)
        all_results.extend(recent_news)

        if web_search_results:
            remaining = 20 - len(all_results)
            if remaining > 0:
                all_results.extend(web_search_results[:remaining])

    return all_results[:20]

async def get_zhihu_hot() -> List[SearchResult]:
    async with ZhihuAPI() as zhihu_api:
        return await zhihu_api.fetch_zhihu_hot()

def is_zhihu_hot_query(query: str) -> bool:
    return any(keyword in query for keyword in ["知乎热榜", "知乎热门", "知乎热搜"])

async def get_weather_info(query: str) -> Optional[SearchResult]:
    if not re.search(r'[\u4e00-\u9fa5]+天气', query):
        return None

    async with WeatherAPI() as weather_api:
        return await weather_api.fetch_weather(query)

def is_weather_query(query: str) -> bool:
    return re.search(r'[\u4e00-\u9fa5]+天气', query) is not None