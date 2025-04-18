from __future__ import annotations

import logging
import re
import urllib.parse
from enum import Enum
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup
from .content_processor import ContentProcessor

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
    
    def identify_query_type(self, query: str) -> QueryType:
        query = query.lower()
        _LOGGER.debug(f"分析查询类型: '{query}'")
        
        for query_type, keywords in self.query_type_keywords.items():
            for keyword in keywords:
                if re.search(keyword, query, re.IGNORECASE):
                    _LOGGER.info(f"查询类型识别为: {query_type.value}")
                    return query_type
        
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
    
    def generate_prompt(self, query: str, search_content: Optional[str] = None) -> Dict[str, Any]:
        
        query_type = self.identify_query_type(query)
        
        
        content_types = []
        if search_content:
            content_types = self.detect_content_features(search_content, query_type.value)
            
            
            if query_type != QueryType.GENERAL:
                _LOGGER.info(f"保持查询类型: {query_type.value}")
                if query_type not in content_types:
                    content_types.insert(0, query_type)
            
            elif content_types and content_types[0] != QueryType.GENERAL:
                query_type = content_types[0]
                _LOGGER.info(f"更新查询类型为内容类型: {query_type.value}")
        else:
            content_types = [query_type]
        
        prompt_data = self._build_prompt(query, query_type, content_types)
        return prompt_data
    
    def _build_prompt(self, query: str, query_type: QueryType, content_types: List[QueryType]) -> Dict[str, Any]:
        general_prompt = f"""
请基于用户的问题"{query}"和下面提供的搜索结果，给出全面且有见地的回答。以用户视角出发，直接回答问题的核心内容，保持客观准确，如果信息不完整要说明，使用清晰的结构和通俗的语言。
"""
        url_specific_prompt = f"""
请基于用户的问题"{query}"，和以下网页链接内容。1. 提取视频/文章标题、时间等关键信息，2. 提取主要内容，忽略无关元素，3. 保持内容的完整性和结构性，4. 突出重要信息，简明扼要。
"""
        if query_type == QueryType.URL or re.search(r'https?://|www\.|\.com|\.cn|\.net', query):
            combined_prompt = url_specific_prompt
        else:
            combined_prompt = general_prompt
            type_specific_prompts = {
                QueryType.WEATHER: """
对于联网天气信息：
1. 用户必须明确指出查询多天天气预报，才可以按日期顺序组织信息
2. 如果只是问天气，请直接给出天气信息
2. 对每一天的天气，清晰列出：日期、天气状况、温度范围
3. 如果有风力、降水概率等信息，也请一并提供
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
            if query_type != QueryType.GENERAL and query_type in type_specific_prompts:
                combined_prompt += type_specific_prompts[query_type]
            for content_type in content_types:
                if (content_type != query_type and 
                    content_type != QueryType.GENERAL and 
                    content_type in type_specific_prompts):
                    combined_prompt += type_specific_prompts[content_type]
        
        prompt_data = {
            "main_prompt": combined_prompt,
            "query_type": query_type.value,
            "content_types": [t.value for t in content_types],
            "query": query
        }
        
        return prompt_data 