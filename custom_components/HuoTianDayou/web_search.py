from __future__ import annotations
import logging
import aiohttp
import asyncio
import re
import random
import urllib.parse
import json
from bs4 import BeautifulSoup
from typing import List, Dict, Optional, Union, Any
from dataclasses import dataclass
import datetime
from aiohttp import ClientTimeout, TCPConnector, ClientSession, ClientError, ClientConnectorError, ClientSSLError
from aiohttp.client_exceptions import (
    ServerDisconnectedError, ClientPayloadError, ContentTypeError,
    ClientResponseError, TooManyRedirects
)
from .const import DEFAULT_SEARCH_ENGINE
import os
import ssl
from urllib.parse import unquote
from requests import get

_LOGGER = logging.getLogger(__name__)

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: Optional[str] = None
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
            "metadata": self.metadata
        }

class RateLimiter:
    def __init__(self, max_calls: int, period: int):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = datetime.datetime.now()
            self.calls = [t for t in self.calls if (now - t).seconds < self.period]
            
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - min(self.calls)).seconds + 0.1
                await asyncio.sleep(sleep_time)
                return await self.acquire()
            
            self.calls.append(now)

class TimeParser:
    TIME_PERIODS = {
        "dawn": (0, 5, 59),
        "morning": (6, 11, 59),
        "noon": (12, 13, 59),
        "afternoon": (14, 17, 59),
        "evening": (18, 23, 59)
    }

    @staticmethod
    def parse_time_query(query: str) -> tuple[datetime.datetime, datetime.datetime]:
        now = datetime.datetime.now()
        start_time = now - datetime.timedelta(days=1)
        end_time = now

        time_patterns = {
            r'(?:前|最近)?(\d+)(?:分钟)(?:前|内)?': lambda m: (now - datetime.timedelta(minutes=int(m.group(1))), now),
            r'(?:前|最近)?(\d+)(?:小时)(?:前|内)?': lambda m: (now - datetime.timedelta(hours=int(m.group(1))), now),
            r'(?:今日|今天)': lambda _: (now.replace(hour=0, minute=0, second=0, microsecond=0), now),
            r'(?:昨日|昨天)': lambda _: ((now - datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
                                    (now - datetime.timedelta(days=1)).replace(hour=23, minute=59, second=59)),
            r'本周': lambda _: ((now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), now),
            r'上周': lambda _: ((now - datetime.timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0),
                            (now - datetime.timedelta(days=now.weekday() + 1)).replace(hour=23, minute=59, second=59)),
            r'本月': lambda _: (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now),
            r'上月': lambda _: ((now.replace(month=now.month-1, day=1) if now.month > 1 else now.replace(year=now.year-1, month=12, day=1)),
                            ((now.replace(day=1) - datetime.timedelta(days=1)).replace(hour=23, minute=59, second=59)))
        }

        for pattern, time_func in time_patterns.items():
            match = re.search(pattern, query)
            if match:
                return time_func(match)

        return start_time, end_time

    @staticmethod
    def get_time_periods(start_time: datetime.datetime, end_time: datetime.datetime) -> List[tuple[datetime.datetime, datetime.datetime]]:
        periods = []
        base_date = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        
        for period_name, (start_hour, end_hour, end_minute) in TimeParser.TIME_PERIODS.items():
            period_start = base_date.replace(hour=start_hour, minute=0, second=0)
            period_end = base_date.replace(hour=end_hour, minute=end_minute, second=59)
            
            if period_start >= start_time and period_end <= end_time:
                periods.append((period_start, period_end))
                
        return periods

class NewsAPI:
    def __init__(self):
        self.timeout = ClientTimeout(total=30)
        self.session = None
        self.cache = {}
        self.cache_ttl = 300
        self.last_check_time = datetime.datetime.now()
        self.displayed_news = set()
        self.time_parser = TimeParser()
        self.api_url = "https://flash-api.jin10.com/get_flash_list"
        self.api_headers = {
            "x-app-id": "SO1EJGmNgCtmpcPF",
            "x-version": "1.0.0"
        }

    async def __aenter__(self):
        if self.session is None:
            self.session = ClientSession(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)

    def parse_time_query(self, query: str) -> tuple[datetime.datetime, datetime.datetime]:
        return self.time_parser.parse_time_query(query)

    def _get_cache_key(self, start_time: datetime.datetime, end_time: datetime.datetime) -> str:
        return f"jin10:{start_time.isoformat()}:{end_time.isoformat()}"

    def _get_cached_results(self, start_time: datetime.datetime, end_time: datetime.datetime) -> Optional[List[Dict]]:
        key = self._get_cache_key(start_time, end_time)
        if key in self.cache:
            timestamp, results = self.cache[key]
            if (datetime.datetime.now() - timestamp).seconds < self.cache_ttl:
                return results
            del self.cache[key]
        return None
            
    def _cache_results(self, start_time: datetime.datetime, end_time: datetime.datetime, results: List[Dict]):
        key = self._get_cache_key(start_time, end_time)
        self.cache[key] = (datetime.datetime.now(), results)

    async def _fetch_period_news(self, end_time: datetime.datetime, limit: int) -> List[Dict]:
        params = {
            "max_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": "-8200",
            "vip": "1",
            "limit": str(limit)
        }
        try:
            async with self.session.get(self.api_url, params=params, headers=self.api_headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", []) if data else []
        except Exception as e:
            _LOGGER.info(f"Jin10 API error: {e}")
        return []

    async def fetch_news(self, query: str = "", limit: int = 15) -> List[Dict]:
        start_time, end_time = self.parse_time_query(query)
        
        if "今日" in query or "今天" in query or "昨日" in query or "昨天" in query:
            periods = self.time_parser.get_time_periods(start_time, end_time)
            all_results = []
            
            for period_start, period_end in periods:
                period_data = await self._fetch_period_news(period_end, limit * 2)
                filtered_data = [
                    item for item in period_data
                    if period_start <= datetime.datetime.strptime(item["time"], "%Y-%m-%d %H:%M:%S") <= period_end
                ]
                all_results.extend(filtered_data[:limit])
            
            return all_results
        else:
            news_data = await self._fetch_period_news(end_time, limit)
            return [
                item for item in news_data
                if start_time <= datetime.datetime.strptime(item["time"], "%Y-%m-%d %H:%M:%S") <= end_time
            ][:limit]

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
                    
        if re.search(r"<div|<img|<a |http|\.jpg|\.png|\.jpeg", content.lower()):
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
        async with self:
            start_time, end_time = self.parse_time_query(query)
            
            cached = self._get_cached_results(start_time, end_time)
            if cached:
                return [
                    SearchResult(
                        title=item.get("data", {}).get("title", ""),
                        url="",
                        snippet=self.clean_news_content(item.get("data", {}).get("content", "")),
                        metadata={
                            "source": "jin10",
                            "time": item.get("time"),
                            "pic": item.get("data", {}).get("pic"),
                            "query_start": start_time.isoformat(),
                            "query_end": end_time.isoformat()
                        }
                    )
                    for item in cached
                    if self.clean_news_content(item.get("data", {}).get("content", ""))
                ]

            news_data = await self.fetch_news(query, limit)
            if news_data:
                self._cache_results(start_time, end_time, news_data)
                return [
                    SearchResult(
                        title=item.get("data", {}).get("title", ""),
                        url="",
                        snippet=self.clean_news_content(item.get("data", {}).get("content", "")),
                        metadata={
                            "source": "jin10",
                            "time": item.get("time"),
                            "pic": item.get("data", {}).get("pic"),
                            "query_start": start_time.isoformat(),
                            "query_end": end_time.isoformat()
                        }
                    )
                    for item in news_data
                    if self.clean_news_content(item.get("data", {}).get("content", ""))
                ]

            return []

class WebSearch:
    def __init__(self, engine_type: Optional[str] = None, api_key: Optional[str] = None):
        self.engine_type = (engine_type or DEFAULT_SEARCH_ENGINE).lower()
        self.api_key = api_key
        self.timeout = ClientTimeout(total=30, connect=10, sock_read=10)
        self.connector = TCPConnector(limit=10, force_close=True, ssl=False)  
        self.session = None
        self.news_api = NewsAPI()
        self.max_retries = 10
        self.retry_delay = 1
        self.engines = ["google", "bing", "baidu"]  
        self.current_engine = self.engines.index(self.engine_type) if self.engine_type in self.engines else 0
        self.results_per_page = 3
        self.cache = {}
        self.cache_ttl = 3600
        
        
        self.blocked_domains = {
            "zhihu.com",
            "zhihu.cn"
        }
        
        self.rate_limiters = {
            "google": RateLimiter(max_calls=50, period=60),
            "bing": RateLimiter(max_calls=50, period=60),
            "baidu": RateLimiter(max_calls=40, period=60)
        }
        self.zhihu_api_url = "https://hot.imsyy.top/api/hot/zhihu"

    def add_blocked_domain(self, domain: str) -> None:
        self.blocked_domains.add(domain.lower())
        
    def remove_blocked_domain(self, domain: str) -> None:
        self.blocked_domains.discard(domain.lower())
        
    def is_domain_blocked(self, url: str) -> bool:
        if not url:
            return True
        try:
            domain = urllib.parse.urlparse(url).netloc.lower()
            return any(blocked in domain for blocked in self.blocked_domains)
        except:
            return True
            
    async def __aenter__(self):
        if self.session is None:
            self.session = ClientSession(
                timeout=self.timeout,
                connector=self.connector,
                headers=self._get_headers()
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self._get_random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

    def _get_random_ua(self) -> str:
        def get_version():
            return f"{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}"
        
        platforms = [
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{get_version()} Safari/537.36",
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{random.randint(10,15)}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{get_version()} Safari/537.36",
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{random.randint(80,110)}.0) Gecko/20100101 Firefox/{get_version()}",
            f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{get_version()} Safari/537.36"
        ]
        return random.choice(platforms)
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)

    def _get_cache_key(self, query: str, engine: str) -> str:
        return f"{engine}:{query}"

    def _get_cached_results(self, query: str, engine: str) -> Optional[List[SearchResult]]:
        key = self._get_cache_key(query, engine)
        if key in self.cache:
            timestamp, results = self.cache[key]
            if (datetime.datetime.now() - timestamp).seconds < self.cache_ttl:
                return results
            del self.cache[key]
        return None

    def _cache_results(self, query: str, engine: str, results: List[SearchResult]):
        key = self._get_cache_key(query, engine)
        self.cache[key] = (datetime.datetime.now(), results)

    async def _make_request(self, url: str, method: str = "get", **kwargs) -> Optional[str]:
        _LOGGER.debug(f"Making request to {url}")
        for _ in range(2):
            try:
                async with getattr(self.session, method)(
                    url, 
                    timeout=ClientTimeout(total=30, connect=10),
                    allow_redirects=True,  
                    verify_ssl=False,  
                    **kwargs
                ) as response:
                    if response.status == 200:
                        content = await response.text(encoding='utf-8', errors='ignore')
                        if len(content) > 100:
                            _LOGGER.debug(f"Received response from {url} with length {len(content)}")
                            return content
                    elif response.status in [301, 302, 303, 307, 308]:
                        url = str(response.headers.get('Location', ''))
                        if url:
                            _LOGGER.debug(f"Following redirect to {url}")
                            continue
            except Exception as e:
                _LOGGER.debug(f"Request failed for {url}: {str(e)}")
                await asyncio.sleep(5)
        return None

    async def _search_with_google(self, query: str, num_results: int = 3) -> List[SearchResult]:
        _LOGGER.info(f"Starting Google search: {query}")
        await self.rate_limiters["google"].acquire()
        
        cached = self._get_cached_results(query, "google")
        if cached:
            _LOGGER.info(f"Returning {len(cached)} cached results")
            return cached
        
        results = []
        try:
            ssl._create_default_https_context = ssl._create_unverified_context
            proxy = os.environ.get("https_proxy")
            proxies = {"https": proxy, "http": proxy} if proxy else None
            
            params = {
                "q": query,
                "num": num_results + 2,
                "hl": "zh-CN",
                "safe": "off",
                "start": 0
            }
            
            headers = {
                "User-Agent": self._get_random_ua(),
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache"
            }
            
            cookies = {
                "CONSENT": "PENDING+987",
                "SOCS": "CAESHAgBEhIaAB"
            }
            
            resp = await asyncio.to_thread(lambda: get(
                url="https://www.google.com/search",
                headers=headers,
                params=params,
                proxies=proxies,
                timeout=30,
                verify=False,
                cookies=cookies
            ))
            
            _LOGGER.info(f"Google response status: {resp.status_code}")
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for result in soup.select("div.ezO2md"):
                link_tag = result.find("a", href=True)
                title_tag = link_tag.find("span", class_="CVA68e") if link_tag else None
                desc_tag = result.find("span", class_="FrIlee")
                
                if link_tag and title_tag:
                    url = link_tag.get("href", "")
                    if url.startswith("/url?q="):
                        url = unquote(url.split("&")[0].replace("/url?q=", ""))
                    
                    if not url.startswith(("/search", "/imgres")):
                        title = title_tag.get_text(strip=True)
                        desc = desc_tag.get_text(strip=True) if desc_tag else ""
                        
                        _LOGGER.info(f"Found result - Title: {title[:30]}...")
                        
                        results.append(SearchResult(
                            title=title,
                            url=url,
                            snippet=desc,
                            metadata={"engine": "google", "timestamp": datetime.datetime.now().isoformat()}
                        ))
                        
                        if len(results) >= num_results:
                            break
                        
            if results:
                _LOGGER.info(f"Found {len(results)} Google results")
                self._cache_results(query, "google", results)
            else:
                _LOGGER.info("No results found in Google response")
            
        except Exception as e:
            _LOGGER.error(f"Google search error: {str(e)}")
            self.current_engine = (self.current_engine + 1) % len(self.engines)
        finally:
            ssl._create_default_https_context = ssl.create_default_context
        
        return results

    async def _search_with_bing(self, query: str, num_results: int = 3) -> List[SearchResult]:
        _LOGGER.info(f"Starting Bing search for query: {query}")
        await self.rate_limiters["bing"].acquire()
        
        cached = self._get_cached_results(query, "bing")
        if cached:
            _LOGGER.info(f"Returning {len(cached)} cached results for Bing query: {query}")
            return cached

        results = []
        try:
            params = {
                "q": query,
                "count": str(num_results),
                "setlang": "zh-CN",
                "ensearch": "0", 
                "cc": "CN"        
            }
            url = "https://cn.bing.com/search"  
            _LOGGER.info(f"Making Bing request to {url} with params: {params}")
            
            headers = {
                **self._get_headers(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://cn.bing.com/"
            }
            _LOGGER.info(f"Using headers: {headers}")
            
            response = await self._make_request(
                url,
                params=params,
                headers=headers
            )
            
            if response:
                _LOGGER.info(f"Received Bing response with length: {len(response)}")
                soup = BeautifulSoup(response, "html.parser")
                
                
                _LOGGER.debug(f"Bing response HTML: {response[:1000]}...")
                
                results_found = soup.select(".b_algo")
                _LOGGER.info(f"Found {len(results_found)} results in Bing response")
                
                for div in results_found[:num_results]:
                    title_elem = div.select_one("h2 a")
                    caption_elem = div.select_one(".b_caption p")
                    
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        url = title_elem.get("href", "")
                        snippet = caption_elem.get_text(strip=True) if caption_elem else ""
                        _LOGGER.info(f"Extracted Bing result - Title: {title}, URL: {url}")
                        
                        results.append(SearchResult(
                            title=title,
                            url=url,
                            snippet=snippet,
                            metadata={
                                "engine": "bing",
                                "timestamp": datetime.datetime.now().isoformat()
                            }
                        ))
                
                if results:
                    _LOGGER.info(f"Caching {len(results)} Bing results")
                    self._cache_results(query, "bing", results)
                else:
                    _LOGGER.info("No valid results found in Bing response")
            else:
                _LOGGER.info("Received empty response from Bing")
                
        except Exception as e:
            _LOGGER.error(f"Bing search error: {str(e)}")
            _LOGGER.info(f"Bing search exception details: {type(e).__name__}: {str(e)}")
            await asyncio.sleep(self.retry_delay)
            self.current_engine = (self.current_engine + 1) % len(self.engines)
            
        _LOGGER.info(f"Returning {len(results)} results from Bing search")
        return results

    async def _search_with_baidu(self, query: str, num_results: int = 3) -> List[SearchResult]:
        await self.rate_limiters["baidu"].acquire()
        
        cached = self._get_cached_results(query, "baidu")
        if cached:
            return cached

        results = []
        try:
            params = {
                "wd": query,
                "rn": str(num_results),
                "ie": "utf-8"
            }
            response = await self._make_request(
                "https://www.baidu.com/s",
                params=params,
                headers={
                    **self._get_headers(),
                    "Accept": "text/html",
                    "Referer": "https://www.baidu.com/"
                }
            )
            if response:
                soup = BeautifulSoup(response, "html.parser")
                for div in soup.select(".result.c-container")[:num_results]:
                    title_elem = div.select_one(".t a")
                    abstract_elem = div.select_one(".c-abstract")
                    
                    if title_elem:
                        url = title_elem.get("href", "")
                        if url:
                            
                            try:
                                async with self.session.get(url, allow_redirects=True) as resp:
                                    url = str(resp.url)
                            except:
                                pass
                                
                        results.append(SearchResult(
                            title=title_elem.get_text(strip=True),
                            url=url,
                            snippet=abstract_elem.get_text(strip=True) if abstract_elem else "",
                            metadata={
                                "engine": "baidu",
                                "timestamp": datetime.datetime.now().isoformat()
                            }
                        ))
                self._cache_results(query, "baidu", results)
        except Exception as e:
            _LOGGER.error(f"Baidu search error: {e}")
            await asyncio.sleep(self.retry_delay)
            self.current_engine = (self.current_engine + 1) % len(self.engines)
        return results

    def _is_news_related(self, query: str) -> bool:
        news_keywords = [
            "新闻", "news", "报道", "report", "突发", "breaking"
        ]
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in news_keywords)

    async def _fetch_zhihu_hot(self) -> List[SearchResult]:
        try:
            async with self.session.get(self.zhihu_api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    results = []
                    for item in data.get('data', [])[:20]:
                        title = item.get('title', '')
                        url = item.get('url', '')
                        hot = item.get('hot', '')
                        results.append(SearchResult(
                            title=title,
                            url=url,
                            snippet=f"热度: {hot}",
                            metadata={
                                "source": "zhihu_hot",
                                "hot": hot,
                                "timestamp": datetime.datetime.now().isoformat()
                            }
                        ))
                    return results
        except Exception as e:
            _LOGGER.error(f"获取知乎热榜失败: {e}")
        return []

    def _is_zhihu_hot_query(self, query: str) -> bool:
        keywords = ['知乎热榜']
        return any(k in query for k in keywords)

    def _extract_urls_from_query(self, query: str) -> List[str]:
        url_patterns = [
            r'@?(https?://[^\s]+)',
            r'@?(www\.[^\s]+)',
            r'@?([a-zA-Z0-9-]+\.[a-zA-Z]{2,}\.[a-zA-Z]{2,})'
        ]
        urls = []
        for pattern in url_patterns:
            matches = re.finditer(pattern, query)
            for match in matches:
                url = match.group(1) if match.groups() else match.group(0)
                if url.startswith('@'):
                    url = url[1:]
                urls.append(url)
        return urls

    async def fetch_url_content(self, url: str) -> Optional[SearchResult]:
        _LOGGER.info(f"直接获取URL内容: {url}")
        try:
            platform_patterns = {
                'bilibili': r'bilibili\.com',
                'youtube': r'youtube\.com',
                'twitter': r'twitter\.com',
                'weibo': r'weibo\.com'
            }
            
            platform = next((name for name, pattern in platform_patterns.items() 
                           if re.search(pattern, url)), 'general')
            
            content_tuple = await self._extract_content_with_response(url)
            if content_tuple:
                response, content = content_tuple
                soup = BeautifulSoup(response, 'html.parser')
                
                title_selectors = {
                    'bilibili': '.video-title, .title',
                    'youtube': '.title.ytd-video-primary-info-renderer',
                    'twitter': '.tweet-text',
                    'weibo': '.weibo-text'
                }
                
                title = None
                if platform in title_selectors:
                    title_elem = soup.select_one(title_selectors[platform])
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                
                title = title or soup.title.string if soup.title else url
                
                time_selectors = {
                    'bilibili': '.video-time, .time',
                    'youtube': '.date.ytd-video-primary-info-renderer',
                    'twitter': '.tweet-timestamp',
                    'weibo': '.time'
                }
                
                timestamp = None
                if platform in time_selectors:
                    time_elem = soup.select_one(time_selectors[platform])
                    if time_elem:
                        timestamp = time_elem.get_text(strip=True)
                
                metadata = {
                    "source": "direct_url",
                    "platform": platform,
                    "timestamp": timestamp or datetime.datetime.now().isoformat(),
                    "has_content": True
                }
                
                return SearchResult(
                    title=title,
                    url=url,
                    snippet=content[:150] + "..." if len(content) > 150 else content,
                    content=content,
                    metadata=metadata
                )
            else:
                _LOGGER.info(f"无法提取URL内容: {url}")
        except Exception as e:
            _LOGGER.error(f"获取URL内容失败: {url}, 错误: {str(e)}")
        return None

    async def process_direct_urls(self, query: str) -> List[SearchResult]:
        urls = self._extract_urls_from_query(query)
        if not urls:
            return []
        
        _LOGGER.info(f"从查询中提取到URLs: {urls}")
        results = []
        
        for url in urls:
            result = await self.fetch_url_content(url)
            if result:
                results.append(result)
        
        return results

    async def search(self, query: str, num_results: int = 5, include_news: bool = True) -> List[SearchResult]:
        async with self:
            try:
                direct_url_results = await self.process_direct_urls(query)
                if direct_url_results:
                    _LOGGER.info(f"成功处理直接URL，获取 {len(direct_url_results)} 个结果")
                    return direct_url_results
            except Exception as e:
                _LOGGER.error(f"处理直接URL失败: {str(e)}")
                
            if self._is_zhihu_hot_query(query):
                try:
                    results = await self._fetch_zhihu_hot()
                    if results:
                        return results
                except Exception as e:
                    _LOGGER.error(f"获取知乎热榜失败: {str(e)}")
                
            _LOGGER.info(f"开始搜索: {query} (引擎: {self.engine_type}, 结果数: {num_results})")
            
            search_functions = {
                "google": self._search_with_google,
                "bing": self._search_with_bing,
                "baidu": self._search_with_baidu
            }

            all_results = []
            original_engine = self.engine_type
            engines_tried = set()
            
            if self.engine_type in search_functions:
                try:
                    results = await search_functions[self.engine_type](query, num_results)
                    if results:
                        all_results.extend(results)
                    engines_tried.add(self.engine_type)
                except Exception as e:
                    _LOGGER.error(f"{self.engine_type}搜索失败: {str(e)}")
                    
            # 如果默认引擎失败,尝试其他引擎
            if not all_results:
                for engine, search_func in search_functions.items():
                    if engine in engines_tried:
                        continue
                        
                    try:
                        self.engine_type = engine
                        results = await search_func(query, num_results)
                        if results:
                            all_results.extend(results)
                            break
                    except Exception as e:
                        _LOGGER.error(f"{engine}搜索失败: {str(e)}")
                        continue
                        
            self.engine_type = original_engine
            
            if include_news and self._is_news_related(query):
                try:
                    news_results = await self.news_api.get_news(query, num_results)
                    if news_results:
                        all_results.extend(news_results)
                except Exception as e:
                    _LOGGER.error(f"获取新闻失败: {str(e)}")

            if all_results:
                content_tasks = []
                for result in all_results:
                    if result.url:
                        content_tasks.append(self._extract_content_with_response(result.url))
                
                if content_tasks:
                    contents = await asyncio.gather(*content_tasks, return_exceptions=True)
                    for result, content_tuple in zip(all_results, contents):
                        try:
                            if isinstance(content_tuple, tuple):
                                response, content = content_tuple
                                if content:
                                    result.content = content
                                    result.metadata["has_content"] = True
                                else:
                                    result.metadata["has_content"] = False
                            else:
                                result.metadata["has_content"] = False
                        except Exception as e:
                            _LOGGER.error(f"处理内容失败: {str(e)}")
                            result.metadata["has_content"] = False

            return all_results

    async def _extract_content_with_response(self, url: str) -> Optional[tuple[str, str]]:
        if self.is_domain_blocked(url):
            _LOGGER.info(f"跳过被屏蔽的域名: {url}")
            return None
            
        try:
            _LOGGER.info(f"开始提取内容: {url}")
            response = await self._make_request(url)
            if not response:
                _LOGGER.info(f"无法获取页面内容: {url}")
                return None

            
            soup = BeautifulSoup(response, 'html.parser')
            _LOGGER.info(f"成功解析HTML: {url}")
            
            
            for element in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'iframe', 'noscript', 'aside']):
                element.decompose()
                
            
            content = None
            main_selectors = [
                'article', 'main', '.article', '.post', '.content', '#content',
                '.article-content', '.post-content', '.entry-content', '.main-content',
                '#article', '#main-content', '.article-body', '.article_content'
            ]
            
            for selector in main_selectors:
                main_content = soup.select_one(selector)
                if main_content:
                    content = main_content.get_text(separator='\n', strip=True)
                    break
                    
            
            if not content:
                _LOGGER.info(f"未找到主要内容区域,尝试提取段落: {url}")
                paragraphs = []
                for p in soup.find_all(['p', 'div']):
                    text = p.get_text(strip=True)
                    if len(text) > 50:  
                        paragraphs.append(text)
                content = '\n\n'.join(paragraphs)
            
            
            if content:
                _LOGGER.info(f"清理提取的内容: {url}")
                content = await clean_text(content)
                
                
                if len(content) > 3500:
                    sentences = re.split(r'[。！？.!?]', content)
                    content = ''.join(sentences[:50]) + '...'  
                    _LOGGER.info(f"内容已截断 (保留前50句): {url}")
                    
                _LOGGER.info(f"成功提取内容 (长度:{len(content)}): {url}")
                return (response, content)
            else:
                _LOGGER.info(f"未能提取到有效内容: {url}")
                
        except Exception as e:
            _LOGGER.error(f"内容提取失败 {url}: {str(e)}")
        return None

    async def get_search_results_text(self, query: str, num_results: int = 10, include_news: bool = True) -> str:
        results = await self.search(query, num_results, include_news)
        
        if not results: return "未找到相关结果。"
        
        output = []
        
        direct_url_results = [r for r in results if r.metadata.get("source") == "direct_url"]
        if direct_url_results:
            output.append("直接URL内容提取结果:")
            for i, result in enumerate(direct_url_results, 1):
                output.append(f"\n[URL {i}]")
                output.append(f"标题: {result.title}")
                output.append(f"来源: {result.url}")
                if result.content:
                    content = result.content
                    if len(content) > 500:
                        paragraphs = content.split('\n\n')
                        output.append("内容摘要:")
                        for p in paragraphs[:3]:
                            if p.strip():
                                output.append(f"  {p.strip()}")
                        output.append("  ...")
                    else:
                        output.append(f"内容: {content}")
                output.append("-" * 30)
            return "\n".join(output)
        
        if any(r.metadata.get('source') == 'zhihu_hot' for r in results):
            output.append("知乎热榜:")
            for i, result in enumerate(results, 1):
                if result.metadata.get('source') == 'zhihu_hot':
                    output.append(f"\n[{i}] {result.title}")
                    output.append(f"热度: {result.metadata.get('hot', 'N/A')}")
                    output.append(f"链接: {result.url}")
                    output.append("-" * 30)
            return "\n".join(output)
        
        news_results = []
        search_results = []
        
        for result in results:
            if result.metadata.get("source") == "jin10": news_results.append(result)
            else: search_results.append(result)
                
        output.append(f"搜索引擎: {self.engine_type}")
        output.append(f"查询内容: '{query}'")
        output.append(f"结果数量: {len(results)}")
        output.append("-" * 50)
        
        if news_results:
            output.append("\n实时新闻相关结果:")
            for i, result in enumerate(news_results, 1):
                output.append(f"\n[新闻 {i}]")
                output.append(f"标题: {result.title or '实时新闻'}")
                if result.metadata.get("time"): output.append(f"时间: {result.metadata['time']}")
                if result.snippet: output.append(f"摘要: {result.snippet}")
                if result.content:
                    content = result.content
                    paragraphs = content.split('\n\n')
                    if len(paragraphs) > 2:
                        output.append("详细内容:")
                        for p in paragraphs[:3]:
                            if p.strip(): output.append(f"  {p.strip()}")
                    else: output.append(f"详细内容: {content}")
                output.append("-" * 30)
        
        if search_results:
            output.append("\n网页搜索结果:")
            for i, result in enumerate(search_results, 1):
                output.append(f"\n[{i}]")
                output.append(f"标题: {result.title}")
                output.append(f"来源: {result.url}")
                if result.snippet:
                    cleaned_snippet = re.sub(r'\s+', ' ', result.snippet).strip()
                    output.append(f"描述: {cleaned_snippet}")
                if result.content:
                    content = result.content
                    content = re.sub(r'\s+', ' ', content)
                    sentences = re.split(r'(?<=[.!?。！？])\s+', content)
                    if len(sentences) > 3:
                        relevant_sentences = []
                        query_terms = set(query.lower().split())
                        for sentence in sentences:
                            sentence = sentence.strip()
                            if not sentence: continue
                            sentence_terms = set(sentence.lower().split())
                            if query_terms & sentence_terms:
                                relevant_sentences.append(sentence)
                            if len(relevant_sentences) >= 3: break
                        if relevant_sentences:
                            output.append("相关内容:")
                            for sent in relevant_sentences:
                                output.append(f"  {sent}")
                        else:
                            output.append("内容预览:")
                            for sent in sentences[:3]:
                                if sent.strip(): output.append(f"  {sent.strip()}")
                    else: output.append(f"内容: {content}")
                output.append("-" * 50)
        
        return "\n".join(output)

    def _create_enhanced_input_text(self, 
                                    query: str, 
                                    prompt: str, 
                                    search_results: Optional[str] = None) -> str:
        
        enhanced_text = [
            f"用户问题: {query}\n",
            "系统提示词:\n",
            f"{prompt}\n"
        ]
        
        if search_results:
            enhanced_text.append("\n联网搜索结果:\n")
            enhanced_text.append(f"{search_results}\n")
            
        return "".join(enhanced_text)

async def clean_text(text: str) -> str:
    if not text:
        return ""
    
    text = re.sub(r'[×…–—～]', lambda m: {'×': 'x', '…': '...', '–': '-', '—': '-', '～': '~'}[m.group()], text)
    text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text)
    text = re.sub(r'首页\s*[|].*?[|]|登录\s*[|]\s*注册|Copyright © .*?All Rights Reserved|关于我们.*?联系我们|点击.*?详情|返回顶部|网站地图', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n{2,}', '\n', text)
    
    return text.strip()