from __future__ import annotations
import logging, asyncio, re, random, json, os, ssl
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse
from bs4 import BeautifulSoup
from aiohttp import ClientSession, ClientTimeout, TCPConnector

_LOGGER = logging.getLogger(__name__)

@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

ENGINES = {
    "baidu": {"url": "https://www.baidu.com/s", "param": "wd", "sel": ".result.c-container, .c-container", "title": "h3 a, .t a", "link": "h3 a, .t a", "desc": ".c-abstract, .c-span-last"},
    "bing": {"url": "https://cn.bing.com/search", "param": "q", "sel": ".b_algo", "title": "h2 a", "link": "h2 a", "desc": ".b_caption p"},
}
SEARCH_ENGINES = list(ENGINES.keys())

BLOCKED_DOMAINS = {"zhihu.com", "zhihu.cn"}
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]

class TimeParser:
    TIME_PERIODS = {
        "dawn": (0, 5, 59),
        "morning": (6, 11, 59),
        "noon": (12, 13, 59),
        "afternoon": (14, 17, 59),
        "evening": (18, 23, 59)
    }

    @staticmethod
    def parse_time_query(query: str) -> tuple[datetime, datetime]:
        now = datetime.now()
        start_time = now - timedelta(days=1)
        end_time = now
        time_patterns = {
            r'(?:前|最近)?(\d+)(?:分钟)(?:前|内)?': lambda m: (now - timedelta(minutes=int(m.group(1))), now),
            r'(?:前|最近)?(\d+)(?:小时)(?:前|内)?': lambda m: (now - timedelta(hours=int(m.group(1))), now),
            r'(?:今日|今天)': lambda _: (now.replace(hour=0, minute=0, second=0, microsecond=0), now),
            r'(?:昨日|昨天)': lambda _: ((now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
                                    (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)),
            r'本周': lambda _: ((now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), now),
            r'上周': lambda _: ((now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0),
                            (now - timedelta(days=now.weekday() + 1)).replace(hour=23, minute=59, second=59)),
            r'本月': lambda _: (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now),
            r'上月': lambda _: ((now.replace(month=now.month-1, day=1) if now.month > 1 else now.replace(year=now.year-1, month=12, day=1)),
                            ((now.replace(day=1) - timedelta(days=1)).replace(hour=23, minute=59, second=59)))
        }
        for pattern, time_func in time_patterns.items():
            match = re.search(pattern, query)
            if match: return time_func(match)
        return start_time, end_time

    @staticmethod
    def get_time_periods(start_time: datetime, end_time: datetime) -> List[tuple[datetime, datetime]]:
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
        self.last_check_time = datetime.now()
        self.displayed_news = set()
        self.time_parser = TimeParser()
        self.api_url = "https://flash-api.jin10.com/get_flash_list"
        self.api_headers = {"x-app-id": "SO1EJGmNgCtmpcPF", "x-version": "1.0.0"}

    async def __aenter__(self):
        self.session = self.session or ClientSession(timeout=self.timeout)
        return self

    async def __aexit__(self, *args):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)

    def parse_time_query(self, query: str) -> tuple[datetime, datetime]:
        return self.time_parser.parse_time_query(query)

    def _cache_key(self, start: datetime, end: datetime) -> str:
        return f"jin10:{start.isoformat()}:{end.isoformat()}"

    def _get_cached(self, start: datetime, end: datetime) -> Optional[List[Dict]]:
        key = self._cache_key(start, end)
        if key in self.cache:
            ts, results = self.cache[key]
            if (datetime.now() - ts).seconds < self.cache_ttl: return results
            del self.cache[key]
        return None

    def _set_cache(self, start: datetime, end: datetime, results: List[Dict]):
        self.cache[self._cache_key(start, end)] = (datetime.now(), results)

    async def _fetch_period_news(self, end_time: datetime, limit: int) -> List[Dict]:
        params = {"max_time": end_time.strftime("%Y-%m-%d %H:%M:%S"), "channel": "-8200", "vip": "1", "limit": str(limit)}
        try:
            _LOGGER.info(f"获取Jin10新闻: {params}")
            async with self.session.get(self.api_url, params=params, headers=self.api_headers, timeout=ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    news_list = data.get("data", []) if data else []
                    _LOGGER.info(f"Jin10返回 {len(news_list)} 条新闻")
                    return news_list
                else:
                    _LOGGER.warning(f"Jin10 API返回状态码: {resp.status}")
        except Exception as e:
            _LOGGER.error(f"Jin10 API error: {e}")
        return []

    def _parse_news_time(self, time_str: str) -> datetime:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

    async def fetch_news(self, query: str = "", limit: int = 15) -> List[Dict]:
        start_time, end_time = self.parse_time_query(query)
        if any(k in query for k in ["今日", "今天", "昨日", "昨天"]):
            periods = self.time_parser.get_time_periods(start_time, end_time)
            all_results = []
            for ps, pe in periods:
                data = await self._fetch_period_news(pe, limit * 2)
                all_results.extend([i for i in data if ps <= self._parse_news_time(i["time"]) <= pe][:limit])
            return all_results
        news_data = await self._fetch_period_news(end_time, limit)
        return [i for i in news_data if start_time <= self._parse_news_time(i["time"]) <= end_time][:limit]

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
            
            cached = self._get_cached(start_time, end_time)
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
                self._set_cache(start_time, end_time, news_data)
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

class RateLimiter:
    def __init__(self, max_calls: int = 50, period: int = 60):
        self.max_calls, self.period, self.calls, self.lock = max_calls, period, [], asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = datetime.now()
            self.calls = [t for t in self.calls if (now - t).seconds < self.period]
            if len(self.calls) >= self.max_calls:
                await asyncio.sleep(self.period - (now - min(self.calls)).seconds + 0.1)
                return await self.acquire()
            self.calls.append(now)

class WebSearch:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.timeout = ClientTimeout(total=30, connect=10, sock_read=10)
        self.connector = TCPConnector(limit=10, force_close=True, ssl=False)
        self.session = None
        self.news_api = NewsAPI()
        self.cache = {}
        self.cache_ttl = 3600
        self.blocked_domains = BLOCKED_DOMAINS.copy()
        self.rate_limiters = {e: RateLimiter() for e in ENGINES}
        self.zhihu_api_url = "https://hot.imsyy.top/api/hot/zhihu"

    def add_blocked_domain(self, domain: str) -> None:
        self.blocked_domains.add(domain.lower())
        
    def remove_blocked_domain(self, domain: str) -> None:
        self.blocked_domains.discard(domain.lower())
        
    def is_domain_blocked(self, url: str) -> bool:
        if not url:
            return True
        try:
            domain = urlparse(url).netloc.lower()
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
        return random.choice(UA_LIST)
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)

    def _get_cache_key(self, query: str, engine: str) -> str:
        return f"{engine}:{query}"

    def _get_cached_results(self, query: str, engine: str) -> Optional[List[SearchResult]]:
        key = self._get_cache_key(query, engine)
        if key in self.cache:
            ts, results = self.cache[key]
            if (datetime.now() - ts).seconds < self.cache_ttl: return results
            del self.cache[key]
        return None

    def _cache_results(self, query: str, engine: str, results: List[SearchResult]):
        self.cache[self._get_cache_key(query, engine)] = (datetime.now(), results)

    async def _make_request(self, url: str, **kwargs) -> Optional[str]:
        if 'baidu.com/link' in url:
            real_url = await self._resolve_baidu_redirect(url)
            if real_url and real_url != url:
                _LOGGER.info(f"百度跳转解析: {url[:50]}... -> {real_url[:50]}...")
                url = real_url
        
        for _ in range(2):
            try:
                async with self.session.get(url, timeout=ClientTimeout(total=30), allow_redirects=True, ssl=False, **kwargs) as resp:
                    final_url = str(resp.url)
                    if final_url != url:
                        _LOGGER.info(f"跳转到: {final_url[:80]}...")
                    if resp.status == 200:
                        content = await resp.text(encoding='utf-8', errors='ignore')
                        if len(content) > 100: return content
                    elif resp.status in [301, 302, 303, 307, 308]:
                        url = str(resp.headers.get('Location', ''))
                        if url: continue
            except Exception as e:
                _LOGGER.debug(f"Request failed: {e}")
                await asyncio.sleep(2)
        return None
    
    async def _resolve_baidu_redirect(self, baidu_url: str) -> Optional[str]:

        try:
            async with self.session.get(baidu_url, timeout=ClientTimeout(total=15), allow_redirects=True, ssl=False) as resp:
                final_url = str(resp.url)
                if final_url and 'baidu.com' not in final_url:
                    _LOGGER.info(f"百度跳转成功: {baidu_url[:40]}... -> {final_url[:60]}...")
                    return final_url
                if resp.status in [301, 302, 303, 307, 308]:
                    location = resp.headers.get('Location', '')
                    if location and 'baidu.com' not in location:
                        return location
                html = await resp.text(errors='ignore')
                import re
                match = re.search(r'URL=\'?(https?://[^\'"\s>]+)', html)
                if match:
                    real_url = match.group(1)
                    if 'baidu.com' not in real_url:
                        _LOGGER.info(f"从HTML提取真实URL: {real_url[:60]}...")
                        return real_url
        except Exception as e:
            _LOGGER.warning(f"百度跳转解析失败: {e}")
        return baidu_url

    async def _search_engine(self, query: str, engine: str, num: int = 5) -> List[SearchResult]:
        cfg = ENGINES.get(engine)
        if not cfg: return []
        await self.rate_limiters.get(engine, RateLimiter()).acquire()
        cached = self._get_cached_results(query, engine)
        if cached: return cached
        results = []
        try:
            from urllib.parse import quote
            url = f"{cfg['url']}?{cfg['param']}={quote(query)}"
            _LOGGER.info(f"搜索引擎请求: {engine} - {url}")
            html = await self._make_request(url, headers=self._get_headers())
            if not html:
                _LOGGER.warning(f"{engine} 返回空响应")
                return []
            _LOGGER.info(f"{engine} 响应长度: {len(html)}")
            soup = BeautifulSoup(html, "html.parser")
            
            for selector in cfg["sel"].split(", "):
                items = soup.select(selector)[:num]
                if items:
                    _LOGGER.info(f"{engine} 使用选择器 {selector} 找到 {len(items)} 个结果")
                    break
            else:
                items = []
                _LOGGER.warning(f"{engine} 未找到匹配的结果选择器")
            
            for item in items:
                title_el = None
                for ts in cfg["title"].split(", "):
                    title_el = item.select_one(ts)
                    if title_el: break
                
                link_el = None
                for ls in cfg["link"].split(", "):
                    link_el = item.select_one(ls)
                    if link_el: break
                
                desc_el = None
                for ds in cfg["desc"].split(", "):
                    desc_el = item.select_one(ds)
                    if desc_el: break
                
                if not title_el or not link_el: continue
                href = link_el.get("href", "")
                if not href: continue
                if href.startswith("/"): href = f"https://{urlparse(cfg['url']).netloc}{href}"
                if self.is_domain_blocked(href): continue
                
                title = title_el.get_text(strip=True)
                snippet = desc_el.get_text(strip=True) if desc_el else ""
                
                if title and href:
                    results.append(SearchResult(
                        title=title,
                        url=href,
                        snippet=snippet,
                        metadata={"engine": engine, "timestamp": datetime.now().isoformat()}
                    ))
                    _LOGGER.debug(f"找到结果: {title[:30]}... -> {href[:50]}...")
            
            if results:
                self._cache_results(query, engine, results)
                _LOGGER.info(f"{engine} 成功获取 {len(results)} 个结果")
            else:
                _LOGGER.warning(f"{engine} 解析后无有效结果")
        except Exception as e:
            _LOGGER.error(f"{engine} search error: {e}", exc_info=True)
        return results

    def _is_news_related(self, query: str) -> bool:
        news_keywords = [
            "新闻", "news", "报道", "report", "突发", "breaking",
            "今日", "今天", "最新", "热点", "头条", "资讯", "快讯", "消息"
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
                                "timestamp": datetime.now().isoformat()
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
                    "timestamp": timestamp or datetime.now().isoformat(),
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
                
            all_results = []
            for engine in SEARCH_ENGINES:
                _LOGGER.info(f"搜索: {query} (引擎: {engine})")
                results = await self._search_engine(query, engine, num_results)
                if results:
                    all_results.extend(results)
                    break
            
            if include_news and self._is_news_related(query):
                try:
                    news_results = await self.news_api.get_news(query, num_results)
                    if news_results:
                        all_results.extend(news_results)
                except Exception as e:
                    _LOGGER.error(f"获取新闻失败: {str(e)}")

            if all_results:
                content_tasks = []
                valid_results = []
                for result in all_results:
                    if result.url:
                        content_tasks.append(self._extract_content_with_response(result.url))
                        valid_results.append(result)
                
                if content_tasks:
                    contents = await asyncio.gather(*content_tasks, return_exceptions=True)
                    for result, content_tuple in zip(valid_results, contents):
                        try:
                            if isinstance(content_tuple, tuple):
                                _, content = content_tuple
                                if content:
                                    result.content = content
                                    result.metadata["has_content"] = True
                                else:
                                    result.metadata["has_content"] = False
                            else:
                                result.metadata["has_content"] = False
                        except Exception:
                            result.metadata["has_content"] = False

            return all_results

    async def _extract_content_with_response(self, url: str) -> Optional[tuple[str, str]]:
        if self.is_domain_blocked(url):
            _LOGGER.info(f"跳过被屏蔽的域名: {url}")
            return None
        
        real_url = url
        if 'baidu.com/link' in url:
            real_url = await self._resolve_baidu_redirect(url)
            if real_url != url:
                _LOGGER.info(f"内容提取：百度跳转 -> {real_url[:80]}...")
            
        try:
            _LOGGER.info(f"开始提取内容: {real_url}")
            response = await self._make_request(real_url)
            if not response:
                _LOGGER.info(f"无法获取页面内容: {real_url}")
                return None

            soup = BeautifulSoup(response, 'html.parser')
            _LOGGER.info(f"成功解析HTML: {real_url}")
            
            if 'weather.com.cn' in real_url:
                content = self._extract_weather_content(soup, real_url)
                if content:
                    _LOGGER.info(f"天气网站内容提取成功: {real_url}")
                    return (response, content)
            
            
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
                    
            
            if not content or len(content) < 500:
                _LOGGER.info(f"未找到主要内容区域,尝试提取段落: {real_url}")
                paragraphs = []
                seen = set()
                for tag in soup.find_all(['p', 'div', 'section', 'li', 'td', 'h1', 'h2', 'h3', 'h4', 'article', 'span']):
                    text = tag.get_text(strip=True)
                    if len(text) > 20 and text not in seen:
                        seen.add(text)
                        paragraphs.append(text)
                if paragraphs:
                    content = '\n\n'.join(paragraphs)
            
            if not content or len(content) < 300:
                _LOGGER.info(f"段落提取不足,尝试body全文: {real_url}")
                body = soup.find('body')
                if body:
                    content = body.get_text(separator='\n', strip=True)
            
            
            if content:
                _LOGGER.info(f"清理提取的内容: {real_url}")
                content = await clean_text(content)
                
                if len(content) > 5000:
                    from ..utils.text_compressor import TextCompressor
                    compressor = TextCompressor(target_length=4000)
                    result = compressor.compress(content)
                    content = result.text
                    _LOGGER.info(f"内容已压缩 ({result.method}, {result.compression_ratio:.1%}): {real_url}")
                
                if len(content) >= 100:
                    _LOGGER.info(f"成功提取内容 (长度:{len(content)}): {real_url}")
                    return (response, content)
            
            _LOGGER.info(f"未能提取到有效内容: {real_url}")
                
        except Exception as e:
            _LOGGER.error(f"内容提取失败 {url}: {str(e)}")
        return None

    def _extract_weather_content(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        try:
            weather_data = []
            
            city_el = soup.select_one('.crumbs a, .city-name, h1, title')
            city = city_el.get_text(strip=True) if city_el else "未知城市"
            weather_data.append(f"城市: {city}")
            
            all_text = soup.get_text(separator='\n', strip=True)
            lines = all_text.split('\n')
            for line in lines:
                line = line.strip()
                if len(line) < 5 or len(line) > 200:
                    continue
                if re.search(r'(周[一二三四五六日]|今天|明天|后天|\d{1,2}月\d{1,2}日)', line):
                    if re.search(r'(晴|多云|阴|雨|雪|雾|霾|°|℃|\d+度)', line):
                        weather_data.append(line)
                elif re.search(r'(-?\d+)[°℃]', line) and re.search(r'(晴|多云|阴|雨|雪)', line):
                    weather_data.append(line)
                elif re.search(r'(气温|温度|风力|湿度|空气质量|AQI|紫外线)', line):
                    weather_data.append(line)
            
            if len(weather_data) > 3:
                return '\n'.join(weather_data[:30])
            
            items = soup.select('li, .day, .weather-item, [class*="day"], [class*="weather"]')
            current_day = None
            
            for item in items:
                text = item.get_text(separator=' ', strip=True)
                
                date_match = re.search(r'(\d{1,2}/\d{1,2})', text)
                weekday_match = re.search(r'(周[一二三四五六日]|今天|明天|后天)', text)
                weather_match = re.search(r'(晴|多云|阴|小雨|中雨|大雨|暴雨|小雪|中雪|大雪|雾|霾|雷阵雨|阵雨|转)', text)
                temp_match = re.search(r'(-?\d+)[°℃/~]+\s*(-?\d+)?[°℃]?', text)
                
                if date_match or weekday_match:
                    if current_day:
                        weather_data.append(current_day)
                    
                    date_str = date_match.group(1) if date_match else ""
                    weekday_str = weekday_match.group(1) if weekday_match else ""
                    current_day = f"{weekday_str} {date_str}".strip()
                    
                    if weather_match:
                        weather_text = text[weather_match.start():].split()[0] if weather_match else ""
                        current_day += f": {weather_text}"
                    
                    if temp_match:
                        high = temp_match.group(1)
                        low = temp_match.group(2) if temp_match.group(2) else ""
                        if low:
                            current_day += f" {high}°C/{low}°C"
                        else:
                            current_day += f" {high}°C"
            
            if current_day:
                weather_data.append(current_day)
            
            if len(weather_data) <= 1:
                all_text = soup.get_text(separator='\n', strip=True)
                lines = all_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if re.search(r'(周[一二三四五六日]|今天|\d{1,2}/\d{1,2}).*(晴|多云|阴|雨|雪)', line):
                        weather_data.append(line)
                    elif re.search(r'(-?\d+)[°℃].*(-?\d+)[°℃]', line):
                        weather_data.append(line)
            
            if weather_data:
                return '\n'.join(weather_data[:20])
            return None
        except Exception as e:
            _LOGGER.error(f"天气内容提取失败: {e}")
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
    text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text, flags=re.DOTALL)
    
    noise_patterns = [
        r'首页\s*[|].*?[|]', r'登录\s*[|]\s*注册', r'Copyright © .*?All Rights Reserved',
        r'关于我们.*?联系我们', r'点击.*?详情', r'返回顶部', r'网站地图',
        r'var\s+\w+\s*=', r'function\s*\(', r'\{\s*"', r'document\.', r'window\.',
        r'广告|推广|赞助|热门推荐|相关阅读|猜你喜欢|更多精彩',
        r'下载APP|扫码下载|关注公众号|分享到|转发|收藏|点赞|评论\d*',
        r'上一篇|下一篇|相关文章|热门文章|最新文章',
        r'ICP备\d+号|京公网安备|网络文化经营许可证',
        r'客服电话|联系客服|在线客服|售后服务',
        r'cookie|Cookie|COOKIE', r'localStorage|sessionStorage',
        r'undefined|null|NaN|true|false',
        r'\[\s*\]|\{\s*\}',
        r'http[s]?://[^\s]+\.js', r'http[s]?://[^\s]+\.css',
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    lines = text.split('\n')
    clean_lines = []
    for line in lines:
        line = line.strip()
        if len(line) < 5:
            continue
        if line.count('|') > 3 or line.count('/') > 5:
            continue
        if re.match(r'^[\d\s\-/:\.]+$', line):
            continue
        clean_lines.append(line)
    
    text = '\n'.join(clean_lines)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()