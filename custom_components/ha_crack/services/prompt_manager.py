from __future__ import annotations

import logging
import re
import urllib.parse
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
import datetime
from bs4 import BeautifulSoup
from ..services.content_processor import ContentProcessor
from ..utils.time_parser import parse_query_time

_LOGGER = logging.getLogger(__name__)

class QueryType(Enum):
    GENERAL = "general"
    WEATHER = "weather"
    STOCK = "stock"
    NEWS = "news"
    ENCYCLOPEDIA = "encyclopedia"
    TRAVEL = "travel"
    HEALTH = "health"
    URL = "url"
    
async def clean_text(text: str) -> str:
    if not text:
        return ""
    
    text = re.sub(r'[×…–—～]', lambda m: {'×': 'x', '…': '...', '–': '-', '—': '-', '～': '~'}[m.group()], text)
    text = re.sub(r'<[^>]+>|<script.*?</script>|<style.*?</style>', '', text)
    text = re.sub(r'首页\s*[|].*?[|]|登录\s*[|]\s*注册|Copyright © .*?All Rights Reserved|关于我们.*?联系我们|点击.*?详情|返回顶部|网站地图', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n{2,}', '\n', text)
    
    return text.strip()

class ContentExtractor:
    def __init__(self):
        self.site_specific_selectors = {
            "baike.baidu.com": [
                ".main-content",
                ".lemma-summary",
                "[class^='para-title']",
                ".basic-info",
                "#content_wrapper"
            ],
            "wikipedia.org": [
                "#mw-content-text",
                ".mw-parser-output",
                "#bodyContent"
            ],
            "zhihu.com": []  
        }
        
        self.general_selectors = [
            "article",
            "main",
            ".content",
            "#content",
            "[role='main']",
            "body"
        ]
    
    async def extract_content(self, url: str, response: str) -> Optional[str]:
        try:
            if not response or len(response) < 500:  
                return None
                
            soup = BeautifulSoup(response, "html.parser")
            [tag.decompose() for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe'])]
            
            domain = urllib.parse.urlparse(url).netloc.lower()
            specific_selectors = self.site_specific_selectors.get(domain, [])
            
            
            for selector in specific_selectors:
                content = soup.select_one(selector)
                if content and len(content.text) > 150:  
                    text = content.get_text(strip=True, separator=" ")
                    text = re.sub(r"\s+", " ", text)
                    
                    text = re.sub(r'编辑|锁定|播报|隐藏|展开全部|收起|参考资料|分享|举报|[\\[\]【】]', '', text)
                    text = re.sub(r'\(\d+\)', '', text)  
                    text = re.sub(r'(\d+)个赞同', '', text)  
                    return text[:8000] if len(text) > 150 else None
            
            
            for selector in self.general_selectors:
                content = soup.select_one(selector)
                if content and len(content.text) > 200:
                    text = content.get_text(strip=True, separator=" ")
                    text = re.sub(r"\s+", " ", text)
                    return text[:4000] if len(text) > 200 else None
                
        except Exception as e:
            _LOGGER.debug(f"Content extraction failed for {url}: {str(e)}")
        return None

class PromptManager:
    def __init__(self):
        self.content_processor = ContentProcessor()
        self.query_type_keywords = {
            QueryType.WEATHER: [
                r'天气',r'气温',r'气象',r'雨量',r'温度',r'台风',r'雪',r'雷雨',
                r'weather',r'temperature',r'forecast',r'气候',r'寒潮',r'降水',r'冷空气',r'暴雨'
            ],
            QueryType.STOCK: [
                r'股票',r'股价',r'大盘',r'指数',r'基金',r'证券',r'finance',r'stock',
                r'market',r'上证',r'深证',r'创业板',r'科创板'
            ],
            QueryType.NEWS: [
                r'新闻',r'消息',r'报道',r'事件',r'头条',r'时事',r'要闻',r'快讯',
                r'突发',r'breaking news',r'news'
            ],
            QueryType.ENCYCLOPEDIA: [
                r'是什么',r'定义',r'概念',r'解释',r'含义',r'百科',r'encyclopedia',
                r'definition',r'meaning',r'who is',r'what is',r'介绍',r'简介',
                r'说明',r'科普',r'知识',r'百度百科',r'维基百科',r'wiki',
                r'怎么样',r'有什么',r'如何',r'为什么'
            ],
            QueryType.TRAVEL: [
                r'旅游',r'景点',r'风景',r'门票',r'酒店',r'路线',r'游记',r'攻略',
                r'travel',r'tourism',r'hotel',r'scenic',r'观光',r'住宿',r'特产'
            ],
            QueryType.HEALTH: [
                r'健康',r'疾病',r'病症',r'医院',r'治疗',r'药物',r'医疗',r'诊断',
                r'health',r'disease',r'hospital',r'medical',r'symptoms',r'用药',r'处方'
            ]
        }
        
        self.content_features = {
            QueryType.WEATHER: [
                r'(\d{1,2}月\d{1,2}日|[\d-]+)\s*[:：]?\s*([晴阴雨雪多云雾霾沙尘]+)',
                r'(气温|温度)[：:]\s*(\d+[°℃]?\s*[~/～-]\s*\d+[°℃]?)',
                r'天气信息摘要'
            ],
            QueryType.STOCK: [
                r'([^\s\(\)]+)\s*\((\d{6}|\w+:\w+)\)',
                r'(涨跌幅|涨跌)[：:]\s*([+\-]?\d+\.\d+%?)',
                r'股票信息摘要'
            ],
            QueryType.NEWS: [
                r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?\s*\d{1,2}:\d{1,2})',
                r'来源[:：]\s*([^<>\s]+)',
                r'新闻信息摘要'
            ],
            QueryType.ENCYCLOPEDIA: [
                r'百科|百度百科|维基百科|encyclopedia|wiki',
                r'词条|entry|definition|概述|简介|基本信息',
                r'特点|功能|用途|分类|种类|历史|发展',
                r'参考资料|外部链接|相关条目',
                r'目录|索引|分类导航',
                r'编辑|讨论|查看历史',
                r'基本信息|基础知识|核心概念',
                r'[（(][^）)]{10,}[）)]',  
                r'：[^。]{10,}。',  
                r'[。；][^。；]{2,}是[^。；]{5,}[。；]'  
            ]
        }
        
        self.content_extractor = ContentExtractor()
    
    def _normalize_date_string(self, date_str: str) -> str:
        now = datetime.datetime.now()
        date_str = date_str.strip().lower()
        
        if "今天" in date_str or "今日" in date_str:
            return now.strftime('%Y-%m-%d')
        if "明天" in date_str or "明日" in date_str:
            return (now + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        if "后天" in date_str or "後天" in date_str:
            return (now + datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        if "昨天" in date_str or "昨日" in date_str:
             return (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

        match_md = re.search(r'(\d{1,2})月(\d{1,2})日?', date_str)
        if match_md:
            month, day = int(match_md.group(1)), int(match_md.group(2))
            year = now.year 
            try:
                parsed_date = datetime.datetime(year, month, day)
                if parsed_date < now - datetime.timedelta(days=180):
                     year += 1
                return datetime.datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                 pass

        match_ymd = re.search(r'(\d{4})[-/年]?(\d{1,2})[-/月]?(\d{1,2})日?', date_str)
        if match_ymd:
            try:
                 year, month, day = int(match_ymd.group(1)), int(match_ymd.group(2)), int(match_ymd.group(3))
                 return datetime.datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                 pass
                 
        weekdays_cn = ["一", "二", "三", "四", "五", "六", "日", "天"]
        match_weekday = re.search(r'周([一二三四五六日天])', date_str)
        if match_weekday:
            try:
                target_weekday_index = weekdays_cn.index(match_weekday.group(1))
                days_ahead = (target_weekday_index - now.weekday() + 7) % 7
                if days_ahead == 0 and "今天" not in date_str and "今日" not in date_str:
                    days_ahead = 7 
                return (now + datetime.timedelta(days=days_ahead)).strftime('%Y-%m-%d')
            except ValueError:
                pass

        return date_str


    def _extract_structured_weather(self, content: str) -> Optional[str]:
        if not content:
            return None
            
        structured_summary = []
        
        weather_pattern = re.compile(
            r"(?P<date>[今明后昨][日天]|[上下]周[一二三四五六日天]|\d{1,2}月\d{1,2}日?\s*(?:\([周星期][一二三四五六日天]\))?|\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)" 
            r"\s*"                                                          
            r"(?:\S*\s*)?"                                                 
            r"(?P<condition>[晴阴雨雪多云间转雷阵雾霾冰雹冻雨][^\d\s温℃°/～-]{0,15})" 
            r"(?:.*?)"                                                     
            r"(?P<temp>(?:最高气温|最低气温|气温)?\s?约?(-?\d{1,2})\s*(?:[~～度°至\-/\s]+)\s*(-?\d{1,2})\s*℃?)" 
            , re.IGNORECASE
        )

        temp_only_pattern = re.compile(r"(?:最高气温|气温)\s?约?(-?\d{1,2})\s*℃?")
        low_temp_only_pattern = re.compile(r"(?:最低气温)\s?约?(-?\d{1,2})\s*℃?")

        processed_indices = set()
        
        for match in weather_pattern.finditer(content):
            start_index = match.start()
            if any(abs(start_index - idx) < 10 for idx in processed_indices):
                continue
            
            data = match.groupdict()
            date_str = self._normalize_date_string(data.get("date","").strip())
            condition = data.get("condition","").strip().replace("转","转") 
            
            temp_part = data.get("temp","").strip()
            temp1_str = data.get("temp1") or match.group(match.lastindex -1) 
            temp2_str = data.get("temp2") or match.group(match.lastindex) 

            try:
                temp1 = int(temp1_str)
                temp2 = int(temp2_str)
                temp_high = max(temp1, temp2)
                temp_low = min(temp1, temp2)
                temp_str = f"{temp_high}°C / {temp_low}°C"
            except (ValueError, TypeError):
                 temp_str = "N/A"

            if date_str and condition and temp_str != "N/A":
                structured_summary.append(f"日期: {date_str}\n天气: {condition}\n温度: {temp_str}")
                processed_indices.add(start_index)

        if structured_summary:
            return "结构化天气摘要:\n" + "\n---\n".join(structured_summary)
        else:
            return None

    def identify_query_type(self, query: str) -> QueryType:
        query = query.lower()
        _LOGGER.debug(f"分析查询类型: '{query}'")
        
        for query_type, keywords in self.query_type_keywords.items():
            for keyword in keywords:
                if re.search(keyword, query, re.IGNORECASE):
                    _LOGGER.info(f"查询类型识别为: {query_type.value}")
                    return query_type
        
        if re.search(r'https?://|www\.|\.com|\.cn|\.net', query):
             _LOGGER.info(f"查询类型识别为: {QueryType.URL.value} based on pattern")
             return QueryType.URL
             
        _LOGGER.info("未识别特定查询类型，使用通用类型")
        return QueryType.GENERAL
    
    def detect_content_features(self, content: str, query_type: Optional[str] = None) -> List[QueryType]:
        if not content:
            return [QueryType.GENERAL]
        
        _LOGGER.debug(f"分析内容特征，内容长度: {len(content)}")
        
        
        processed_content = self.content_processor.process_content(content, query_type)
        
        
        if query_type:
            try:
                return [getattr(QueryType, query_type.upper())]
            except (AttributeError, ValueError):
                pass
        
        
        content_type = self.content_processor._detect_content_type(processed_content)
        if content_type:
            try:
                return [getattr(QueryType, content_type.upper())]
            except (AttributeError, ValueError):
                pass
        
        return [QueryType.GENERAL]
    
    async def process_content(self, url: str, content: str) -> Optional[str]:
        if not content:
            return None
            
        try:
            processed_content = self.content_processor.process_content(content)
            return processed_content if processed_content else None
                
        except Exception as e:
            _LOGGER.debug(f"Content processing failed for {url}: {str(e)}")
            return None
    
    def _create_enhanced_input_text(self, 
                                    original_query: str,
                                    modified_query: str,
                                    prompt: str, 
                                    search_results: Optional[str] = None,
                                    structured_weather_summary: Optional[str] = None) -> str:
        
        enhanced_text = [
            f"用户问题: {modified_query}\n",
            "系统提示词:\n",
            f"{prompt}\n"
        ]
        
        if search_results:
            if structured_weather_summary:
                enhanced_text.append("\n联网搜索结果:\n")
                enhanced_text.append(structured_weather_summary)
                enhanced_text.append("\n\n---\n\n原始搜索结果:\n")
                enhanced_text.append(search_results)
                enhanced_text.append("\n")
            else:
                enhanced_text.append("\n联网搜索结果:\n")
                enhanced_text.append(f"{search_results}\n")
            
        return "".join(enhanced_text)
    
    def generate_prompt(self, query: str, search_content: Optional[str] = None) -> Dict[str, Any]:
        
        modified_query, formatted_date, start_time, end_time = parse_query_time(query)
        _LOGGER.debug(f"Original query: '{query}', Modified query: '{modified_query}', Parsed date: {formatted_date}, Start: {start_time}, End: {end_time}")
        
        query_type = self.identify_query_type(modified_query)
        
        content_types = []
        structured_weather_summary = None

        if search_content:
            detected_types = self.detect_content_features(search_content, query_type.value if query_type != QueryType.GENERAL else None)
            content_types.extend(detected_types)

            if query_type != QueryType.GENERAL:
                _LOGGER.info(f"保持查询类型: {query_type.value}")
                if query_type not in content_types:
                    content_types.insert(0, query_type)
            elif content_types and content_types[0] != QueryType.GENERAL:
                 query_type = content_types[0]
                 _LOGGER.info(f"更新查询类型为内容类型: {query_type.value}")
            
            if query_type == QueryType.WEATHER or QueryType.WEATHER in content_types:
                 structured_weather_summary = self._extract_structured_weather(search_content)
                 if structured_weather_summary:
                     _LOGGER.info("成功提取结构化天气信息")
                     if query_type != QueryType.WEATHER:
                         content_types = [t for t in content_types if t != QueryType.WEATHER]
                         content_types.insert(0, QueryType.WEATHER)
                         query_type = QueryType.WEATHER

        else:
            content_types = [query_type]
        
        prompt_data = self._build_prompt(
            original_query=query, 
            modified_query=modified_query, 
            formatted_date=formatted_date, 
            query_type=query_type, 
            content_types=list(set(content_types)),
            structured_weather_summary=structured_weather_summary
        )
        
        prompt_data["parsed_start_time"] = start_time.isoformat() if start_time else None
        prompt_data["parsed_end_time"] = end_time.isoformat() if end_time else None
        prompt_data["structured_weather_summary"] = structured_weather_summary
        
        return prompt_data
    
    def _build_prompt(self, original_query: str, modified_query: str, formatted_date: Optional[str], query_type: QueryType, content_types: List[QueryType], structured_weather_summary: Optional[str]) -> Dict[str, Any]:
        general_prompt = f"""
请基于用户的问题"{modified_query}"和下面提供的搜索结果，给出全面且有见地的回答。以用户视角出发，直接回答问题的核心内容，保持客观准确，如果信息不完整要说明，使用清晰的结构和通俗的语言。
"""
        url_specific_prompt = f"""
请基于用户的问题"{modified_query}"，和以下网页链接内容。1. 提取视频/文章标题、时间等关键信息，2. 提取主要内容，忽略无关元素，3. 保持内容的完整性和结构性，4. 突出重要信息，简明扼要。
"""
        if query_type == QueryType.URL:
            combined_prompt = url_specific_prompt
        else:
            combined_prompt = general_prompt
            
            if formatted_date:
                 combined_prompt += f"\n请注意，用户问题中提及的时间已被解析为具体日期：{formatted_date}。请在回答中基于此日期进行。\n"

            type_specific_prompts = {
                QueryType.WEATHER: f"""
对于联网天气信息：
{"**请优先使用搜索结果中提供的'结构化天气摘要'部分来回答。**" if structured_weather_summary else ""}
1. 用户必须明确指出查询多天天气预报，才可以按日期顺序组织信息。
2. 如果只是问天气，请直接给出当天或最相关日期的天气信息。
3. 对每一天的天气，清晰列出：日期、天气状况、温度范围（最高/最低）。
4. 如果有风力、降水概率等其他重要信息，也请一并提供。
5. 如果结构化摘要不可用或信息不足，再尝试从原始搜索结果中提取。
""",
                QueryType.STOCK: """
对于股票/金融信息：
1. 清晰列出股票代码、名称、当前价格和涨跌幅
2. 如有成交量、市值等信息，也请一并提供
3. 如果是查询大盘指数，请提供指数名称、点位和涨跌幅
""",
                QueryType.NEWS: """
对于新闻信息：
1. 按时间顺序组织新闻内容
2. 提取每条新闻的关键信息：时间、地点、事件
3. 保持客观，不添加个人观点
4. 如果有多个来源，注意对比信息的一致性
5. 重点突出最新进展和重要影响
""",
                QueryType.ENCYCLOPEDIA: """
对于百科/定义信息：
1. 首先提供简明的定义或概念解释
2. 然后补充重要的背景信息或相关细节
3. 如果内容包含多个不同定义，请列出最相关的定义
""",
                QueryType.TRAVEL: """
对于旅游信息：
1. 如果是景点查询，请提供位置、特色、门票、开放时间等信息
2. 如果是旅游路线，请整理成简洁的行程安排
3. 如有交通、住宿相关信息，请做简要说明
4. 提取用户可能最关心的实用信息，如最佳季节、注意事项等
""",
                QueryType.HEALTH: """
对于健康/医疗信息：
1. 请注明信息仅供参考，不构成医疗建议
2. 提取症状描述、常见治疗方法等客观信息
3. 避免给出确定性的诊断或处方建议
4. 如有官方医疗机构的权威信息，请优先提供
"""
            }
            
            added_prompts = set()
            
            if query_type != QueryType.GENERAL and query_type in type_specific_prompts:
                combined_prompt += type_specific_prompts[query_type]
                added_prompts.add(query_type)

            for content_type in content_types:
                if isinstance(content_type, QueryType) and \
                   content_type != QueryType.GENERAL and \
                   content_type in type_specific_prompts and \
                   content_type not in added_prompts:
                    
                    combined_prompt += type_specific_prompts[content_type]
                    added_prompts.add(content_type)

        prompt_data = {
            "main_prompt": combined_prompt,
            "query_type": query_type.value,
            "content_types": [t.value for t in content_types if isinstance(t, QueryType)],
            "query": original_query,
            "modified_query": modified_query,
            "parsed_date": formatted_date,
        }
        
        return prompt_data 