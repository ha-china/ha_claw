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
    max_content_length: int = 8000
    max_segment_length: int = 3000
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
        return content_length > 0 and content_length <= self.config.max_content_length
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
        self.priority_patterns = {
            'weather': [
                r'\d{1,2}月\d{1,2}日.{0,10}(晴|阴|雨|雪|多云)',
                r'气温.{0,5}\d+.{1,5}\d+[°℃]',
                r'今[日天].{0,20}(晴|阴|雨|雪|多云)',
                r'明[日天].{0,20}(晴|阴|雨|雪|多云)',
                r'后[日天].{0,20}(晴|阴|雨|雪|多云)'
            ],
            'news': [
                r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]',
                r'最新消息',
                r'最新动态',
                r'突发新闻'
            ],
            'stock': [
                r'\w+[股票指数].{0,10}\d+\.\d+',
                r'涨幅.{0,5}[+\-]\d+\.\d+%',
                r'跌幅.{0,5}[+\-]\d+\.\d+%'
            ]
        }

    def clean_text(self, text: str) -> str:
        if not text: return ""
        
        platform_patterns = {
            'bilibili': r'(首页|番剧|直播|游戏中心|会员购|漫画|赛事|投稿).*?(?=\d|$)',
            'youtube': r'(Subscribe|Like|Share|Views).*?(?=\d|$)',
            'twitter': r'(Retweet|Like|Share).*?(?=\d|$)'
        }
        
        for platform, pattern in platform_patterns.items():
            text = re.sub(pattern, '', text)
            
        text = re.sub(r'(关注|订阅|播放量|点赞|转发|评论).*?(?=\d|$)', '', text)
        
        text = re.sub(r'(简介|合集|自动连播|播放|备注|合作).*?(?=\d|$)', '', text)
        
        text = re.sub(r'[×…–—～]', lambda m: {'×': 'x', '…': '...', '–': '-', '—': '-', '～': '~'}[m.group()], text)
        text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text)
        text = re.sub(r'首页\s*[|].*?[|]|登录\s*[|]\s*注册|Copyright © .*?All Rights Reserved|关于我们.*?联系我们|点击.*?详情|返回顶部|网站地图', '', text)
        
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\n{2,}', '\n', text)
        
        return text.strip()

    def process_content(self, content: str, query_type: str = None) -> str:
        if not content: return ""
        try:
            start_time = time.time()
            cleaned = self.clean_text(content)
            if not cleaned: return ""
            
            if not query_type:
                query_type = self._detect_content_type(cleaned)
                
            structured = ""
            if query_type in self.extraction_patterns:
                structured = self._extract_structured_data(cleaned, query_type)
            
            segments = self._segment_content(cleaned, query_type)
            
            result = []
            current_length = 0
            
            if structured:
                result.append(structured)
                result.append("---")
                current_length = len(structured) + 4
            
            for segment in segments:
                if current_length + len(segment) + 1 > self.config.max_content_length:
                    result.append("\n...(内容已精简，显示重要部分)...")
                    break
                result.append(segment)
                current_length += len(segment) + 1
                
            self.stats.update(query_type or "general", time.time() - start_time)
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
            return '\n'.join(data[:10])
        except Exception as e:
            _LOGGER.error(f"提取结构化数据时出错: {e}")
            return ""

    def _priority_score(self, segment: str, query_type: str) -> int:
        if not query_type or query_type not in self.priority_patterns:
            return 0
            
        score = 0
        for pattern in self.priority_patterns[query_type]:
            matches = re.findall(pattern, segment, re.I)
            score += len(matches) * 10
            
        if query_type == 'weather':
            if '今天' in segment or '今日' in segment:
                score += 30
            if '明天' in segment or '明日' in segment:
                score += 20
                
        return score

    def _segment_content(self, content: str, query_type: str = None) -> List[str]:
        if not content:
            return []
            
        raw_segments = re.split(r'\n{2,}|\r\n{2,}|<br\s*/?>|<p>|</p>', content)
        
        segments = [s.strip() for s in raw_segments if s.strip()]
        
        refined_segments = []
        for segment in segments:
            if len(segment) <= self.config.max_segment_length:
                refined_segments.append(segment)
            else:
                sentences = re.split(r'([。！？.!?;；]+)', segment)
                temp_segment = ""
                
                for i in range(0, len(sentences)-1, 2):
                    if i+1 < len(sentences):
                        sentence = sentences[i] + sentences[i+1]
                    else:
                        sentence = sentences[i]
                        
                    if len(temp_segment) + len(sentence) > self.config.max_segment_length:
                        if temp_segment:
                            refined_segments.append(temp_segment)
                        temp_segment = sentence
                    else:
                        temp_segment += sentence
                        
                if temp_segment:
                    refined_segments.append(temp_segment)
        
        if query_type:
            segment_scores = [(segment, self._priority_score(segment, query_type)) 
                             for segment in refined_segments]
            
            segment_scores.sort(key=lambda x: x[1], reverse=True)
            
            high_priority = [s for s, score in segment_scores if score > 0]
            normal_priority = [s for s, score in segment_scores if score == 0]
            
            result = high_priority + normal_priority[:min(5, len(normal_priority))]
            
            if len(result) < len(refined_segments):
                result.append(f"...(还有{len(refined_segments)-len(result)}个部分未显示)...")
                
            return result
            
        return refined_segments

    async def clean_text_for_api(self, text: str) -> str:
        if not text:
            return ""
            
        search_results_start = text.find("联网搜索结果:")
        prompt_end = text.find("系统提示词:") 
        
        if search_results_start > 0 and prompt_end > 0:
            header = text[:prompt_end]
            prompt = text[prompt_end:search_results_start]
            results = text[search_results_start:]
            
            header = self.clean_text(header)
            prompt = re.sub(r'\s{2,}', ' ', prompt).strip()
            
            is_weather_query = '天气' in header.lower() or '气温' in header.lower()
            is_news_query = '新闻' in header.lower() or '报道' in header.lower()
            
            has_structured_data = False
            if "结构化" in results or "摘要:" in results:
                parts = re.split(r'---\n+原始搜索结果:', results, 1)
                if len(parts) > 1:
                    structured_part = parts[0]
                    original_results = parts[1]
                    has_structured_data = True
                    
                    combined = f"{header}\n{prompt}\n{structured_part}\n"
                    
                    reserved_length = 16000 - len(combined) - 100
                    if len(original_results) > reserved_length:
                        original_results = self._smart_truncate_content(
                            original_results, 
                            reserved_length,
                            is_weather=is_weather_query,
                            is_news=is_news_query
                        )
                    
                    combined += f"---\n\n原始搜索结果:{original_results}"
                    return combined if combined.strip() else text.strip()
            
            max_results_length = 16000 - len(header) - len(prompt) - 100
            
            if len(results) > max_results_length:
                results = self._smart_truncate_content(
                    results,
                    max_results_length,
                    is_weather=is_weather_query,
                    is_news=is_news_query
                )
            
            combined = f"{header}\n{prompt}\n{results}"
            return combined if combined.strip() else text.strip()
        
        cleaned = self.clean_text(text)
        return cleaned if cleaned.strip() else text.strip()
        
    def _smart_truncate_content(self, content: str, max_length: int, is_weather=False, is_news=False) -> str:

        if len(content) <= max_length:
            return content
            
        paragraphs = re.split(r'\n{2,}', content)
        
        scored_paragraphs = []
        
        keywords = []
        if is_weather:
            keywords = ['天气', '温度', '湿度', '风向', '气温', '降水', '预报', 
                        '今天', '明天', '后天', '℃', '度']
        elif is_news:
            keywords = ['报道', '消息', '新闻', '发布', '宣布', '表示', '称', 
                        '最新', '突发', '紧急', '重要']
            
        for i, para in enumerate(paragraphs):
            score = 0
            
            if i < 3:
                score += 15
            elif i >= len(paragraphs) - 3:
                score += 10
                
            para_len = len(para)
            if 50 <= para_len <= 300:
                score += 10
            elif para_len > 300:
                score += 5
                
            for keyword in keywords:
                if keyword in para:
                    score += 15
                    
            if re.search(r'\d+', para):
                score += 8
                
            scored_paragraphs.append((para, score))
            
        scored_paragraphs.sort(key=lambda x: x[1], reverse=True)
        
        preserved = []
        total_length = 0
        
        front_paragraphs = paragraphs[:3]
        for para in front_paragraphs:
            if total_length + len(para) + 2 <= max_length:
                preserved.append(para)
                total_length += len(para) + 2
        
        for para, _ in scored_paragraphs:
            if para in preserved:
                continue
                
            if total_length + len(para) + 2 <= max_length:
                preserved.append(para)
                total_length += len(para) + 2
            else:
                break
                
        indices = [paragraphs.index(p) for p in preserved]
        indices.sort()
        
        ordered_preserved = [paragraphs[i] for i in indices]
        
        if len(paragraphs) > len(ordered_preserved):
            result = []
            last_index = -1
            
            for index in indices:
                if index - last_index > 1:
                    skipped = index - last_index - 1
                    if skipped > 0:
                        result.append(f"...(跳过了{skipped}个段落)...")
                        
                result.append(paragraphs[index])
                last_index = index
                
            return "\n\n".join(result)
        else:
            return "\n\n".join(ordered_preserved)

    def get_stats(self) -> Dict[str, Any]:
        return self.stats.get_stats()

    def clear_cache(self):
        self.cache = ContentCache(self.config.max_cache_size, self.config.cache_ttl)

    def update_config(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    def _split_paragraphs(self, text: str) -> List[str]:
        splits = re.split(r'([。！？.!?]\s*)', text)
        paragraphs = []
        current = ""
        
        for i in range(0, len(splits)-1, 2):
            sentence = splits[i] + (splits[i+1] if i+1 < len(splits) else "")
            if len(current) + len(sentence) > 200:  
                if current:
                    paragraphs.append(current)
                current = sentence
            else:
                current += sentence
                
        if current:
            paragraphs.append(current)
            
        return paragraphs
        
    def _safe_truncate(self, text: str) -> str:
        if len(text) <= 3000:
            return text
            
        paragraphs = text.split('\n\n')
        
        if len(paragraphs) > 10:
            preserved = paragraphs[:3]
            preserved.append("...(中间内容已省略)...")
            
            middle_sample = paragraphs[3:-3:len(paragraphs)//10]
            preserved.extend(middle_sample)
            
            preserved.extend(paragraphs[-3:])
            return '\n\n'.join(preserved)
        
        truncated = text[:3000]
        last_sentence = max(
            truncated.rfind('.'), 
            truncated.rfind('。'),
            truncated.rfind('!'),
            truncated.rfind('！'),
            truncated.rfind('?'),
            truncated.rfind('？')
        )
        
        if last_sentence > 0:
            return truncated[:last_sentence+1] + "\n...(后续内容已省略)..."
        return truncated + "\n...(后续内容已省略)..."

    async def get_search_results_text(self, query: str, num_results: int = 10) -> str:
        results = await self.search(query, num_results)
        
        if not results:
            return "未找到相关结果。"
        
        output = []
        output.append(f"搜索引擎: {self.engine_type}")
        output.append(f"查询内容: '{query}'")
        output.append(f"结果数量: {len(results)}")
        output.append("-" * 30)
        
        for i, result in enumerate(results[:3], 1):
            output.append(f"\n[{i}] {result.title}")
            output.append(f"来源: {result.url}")
            
            if result.snippet:
                cleaned_snippet = re.sub(r'\s+', ' ', result.snippet).strip()
                output.append(f"摘要: {cleaned_snippet[:200]}")
                
            if result.content:
                content = self._clean_content(result.content)
                preview = self._get_content_preview(content, query)
                if preview:
                    output.append("内容预览:")
                    output.append(preview)
            output.append("-" * 30)
        
        return "\n".join(output)

    def _clean_content(self, content: str) -> str:
        content = re.sub(r'\s+', ' ', content)
        content = re.sub(r'[\r\n]+', '\n', content)
        return content.strip()

    def _get_content_preview(self, content: str, query: str) -> str:
        sentences = re.split(r'(?<=[.!?。！？])\s+', content)
        query_terms = set(query.lower().split())
        
        relevant = []
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            
            sent_terms = set(sent.lower().split())
            if query_terms & sent_terms:
                relevant.append(sent)
            
            if len(relevant) >= 2:
                break
            
        if not relevant and sentences:
            relevant = [s.strip() for s in sentences[:2] if s.strip()]
        
        return "\n".join(f"  {s}" for s in relevant)

    async def get_processed_results(self, query: str, num_results: int = 10) -> str:
        results = await self.search(query, num_results)
        
        if not results:
            return "未找到相关结果。"
        
        processed = []
        for result in results[:5]:
            section = []
            section.append(f"# {result.title}")
            
            if result.snippet:
                section.append(result.snippet.strip())
            
            if result.content:
                content = self._clean_content(result.content)
                paragraphs = content.split('\n')
                filtered_paras = []
                
                for para in paragraphs:
                    para = para.strip()
                    if len(para) > 50:
                        filtered_paras.append(para)
                    if len(filtered_paras) >= 3:
                        break
                    
                if filtered_paras:
                    section.append("\n".join(filtered_paras))
                
            processed.append("\n".join(section))
        
        return "\n\n---\n\n".join(processed)
