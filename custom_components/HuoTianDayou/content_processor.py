from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Tuple, Any, Pattern, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict

_LOGGER = logging.getLogger(__name__)

@dataclass
class ContentConfig:
    clean_pattern: str = r'<script.*?</script>|<style.*?</style>|https?://\S+|www\.\S+|\s{3,}|\n{4,}'
    chars_map: Dict[str, str] = None
    max_cache_size: int = 20
    cache_ttl: int = 600
    max_content_length: int = 3000
    def __post_init__(self):
        if self.chars_map is None:
            self.chars_map = {'…': '...', '–': '-', '—': '-', '　': ' ', '，': ',', '。': '.', '！': '!', '？': '?', '；': ';', '：': ':'}

class ContentCache:
    def __init__(self, max_size: int = 20, ttl: int = 600):
        self.max_size, self.ttl = max_size, ttl
        self.cache: Dict[str, tuple[Any, float]] = {}
    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache: return None
        value, timestamp = self.cache[key]
        if time.time() - timestamp > self.ttl:
            del self.cache[key]
            return None
        return value
    def set(self, key: str, value: Any):
        if len(self.cache) >= self.max_size:
            del self.cache[min(self.cache.items(), key=lambda x: x[1][1])[0]]
        self.cache[key] = (value, time.time())
    def clean(self):
        current = time.time()
        self.cache = {k:v for k,v in self.cache.items() if current - v[1] <= self.ttl}

class ContentStats:
    def __init__(self):
        self.processed_count = 0
        self.error_count = 0
        self.type_counts = defaultdict(int)
        self.avg_processing_time = 0.0
        self.total_processing_time = 0.0
        self.start_time = time.time()
    def update(self, content_type: str, processing_time: float, success: bool = True):
        self.processed_count += 1
        if not success: self.error_count += 1
        self.type_counts[content_type] += 1
        self.total_processing_time += processing_time
        self.avg_processing_time = self.total_processing_time / self.processed_count
    def get_stats(self) -> Dict[str, Any]:
        return {"processed_count": self.processed_count, "error_count": self.error_count, "type_counts": dict(self.type_counts), "avg_processing_time": self.avg_processing_time, "uptime": time.time() - self.start_time}

class ContentValidator:
    def __init__(self, config: ContentConfig):
        self.config = config
    def validate_content(self, content: str) -> bool:
        if not content: return False
        content_length = len(content)
        return self.config.min_content_length <= content_length <= self.config.max_content_length
    def validate_type(self, content_type: str, valid_types: Set[str]) -> bool:
        return content_type in valid_types

class ContentProcessor:
    def __init__(self):
        self.config = ContentConfig()
        self.cache = ContentCache()
        self.stats = ContentStats()
        self.validator = ContentValidator(self.config)
        self.type_patterns = {
            'weather': r'(天气|气温|降水|湿度)',
            'stock': r'(股票|股价|指数|基金)',
            'news': r'(新闻|报道|快讯|头条)'
        }
        self.extraction_patterns = {
            'weather': r'(\d{1,2}月\d{1,2}日|[\d-]+)\s*([晴阴雨雪多云]+)\s*(\d+[°℃])',
            'stock': r'(\w+)\s*(\d+\.?\d*)\s*([+\-＋－]?\d+\.?\d*%)',
            'news': r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*([^。\n]+)'
        }

    def clean_text(self, text: str) -> str:
        if not text: return ""
        
        # Platform-specific cleaning patterns
        platform_patterns = {
            'bilibili': r'(首页|番剧|直播|游戏中心|会员购|漫画|赛事|投稿).*?(?=\d|$)',
            'youtube': r'(Subscribe|Like|Share|Views).*?(?=\d|$)',
            'twitter': r'(Retweet|Like|Share).*?(?=\d|$)'
        }
        
        # Clean platform-specific elements
        for platform, pattern in platform_patterns.items():
            text = re.sub(pattern, '', text)
            
        # Clean general social media elements    
        text = re.sub(r'(关注|订阅|播放量|点赞|转发|评论).*?(?=\d|$)', '', text)
        
        # Clean metadata and navigation elements
        text = re.sub(r'(简介|合集|自动连播|播放|备注|合作).*?(?=\d|$)', '', text)
        
        # Clean standard elements
        text = re.sub(r'[×…–—～]', lambda m: {'×': 'x', '…': '...', '–': '-', '—': '-', '～': '~'}[m.group()], text)
        text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text)
        text = re.sub(r'首页\s*[|].*?[|]|登录\s*[|]\s*注册|Copyright © .*?All Rights Reserved|关于我们.*?联系我们|点击.*?详情|返回顶部|网站地图', '', text)
        
        # Clean excessive whitespace
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\n{2,}', '\n', text)
        
        return text.strip()

    def process_content(self, content: str, query_type: str = None) -> str:
        if not content: return ""
        try:
            cleaned = self.clean_text(content)
            if not cleaned: return ""
            
            if not query_type:
                query_type = self._detect_content_type(cleaned)
                
            structured = ""
            if query_type in self.extraction_patterns:
                structured = self._extract_structured_data(cleaned, query_type)
                
            paragraphs = self._split_paragraphs(cleaned)
            
            result = []
            current_length = 0
            
            if structured:
                result.append(structured)
                result.append("---")
                current_length = len(structured) + 4
                
            for p in paragraphs:
                if current_length + len(p) + 1 > self.config.max_content_length:
                    break
                result.append(p)
                current_length += len(p) + 1
                
            return "\n".join(result)
            
        except Exception as e:
            _LOGGER.error(f"处理内容时出错: {e}")
            return self._safe_truncate(content)

    def _detect_content_type(self, content: str) -> Optional[str]:
        for type_name, pattern in self.type_patterns.items():
            if re.search(pattern, content, re.I):
                return type_name
        return None

    def _extract_structured_data(self, content: str, query_type: str) -> str:
        if not content or query_type not in self.extraction_patterns: return ""
        try:
            matches = re.finditer(self.extraction_patterns[query_type], content)
            data = [' '.join(m.groups()) for m in matches]
            return '\n'.join(data[:5])  
        except Exception as e:
            _LOGGER.error(f"提取结构化数据时出错: {e}")
            return ""

    async def clean_text_for_api(self, text: str) -> str:
        return self.clean_text(text)

    def get_stats(self) -> Dict[str, Any]:
        return self.stats.get_stats()

    def clear_cache(self):
        self.cache = ContentCache(self.config.max_cache_size, self.config.cache_ttl)

    def update_config(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def _split_paragraphs(self, text: str) -> List[str]:
        # 按句子分隔符分段
        splits = re.split(r'([。！？.!?]\s*)', text)
        paragraphs = []
        current = ""
        
        # 重组句子并按段落整理
        for i in range(0, len(splits)-1, 2):
            sentence = splits[i] + (splits[i+1] if i+1 < len(splits) else "")
            if len(current) + len(sentence) > 200:  # 段落长度限制
                if current:
                    paragraphs.append(current)
                current = sentence
            else:
                current += sentence
                
        if current:
            paragraphs.append(current)
            
        return paragraphs
        
    def _safe_truncate(self, text: str) -> str:
        if len(text) <= 1000:
            return text
        # 在句子边界截断
        truncated = text[:1000]
        last_sentence = max(
            truncated.rfind('.'), 
            truncated.rfind('。'),
            truncated.rfind('!'),
            truncated.rfind('！'),
            truncated.rfind('?'),
            truncated.rfind('？')
        )
        return truncated[:last_sentence+1] if last_sentence > 0 else truncated
