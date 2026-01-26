from __future__ import annotations

import logging
import asyncio
import re
import json
from typing import Dict, List, Optional, Any, Tuple
import datetime
import time

from homeassistant.components import conversation
from homeassistant.util import ulid
from homeassistant.core import HomeAssistant
from homeassistant.components.conversation import intent
from ..const import DEFAULT_ERROR_RESPONSES

from ..services.prompt_manager import PromptManager, QueryType
from ..services.content_processor import ContentProcessor

_LOGGER = logging.getLogger(__name__)

class AIManager:
    
    CONTEXT_TTL = 1800
    
    def __init__(self, hass: HomeAssistant):
        self.hass = hass
        self.prompt_manager = PromptManager()
        self.content_processor = ContentProcessor()
        self.context_cache = {}
        
        self.search_keywords = {
            'search_engines': {
                'baidu': [r'百度', r'baidu', r'用百度'],
                'google': [r'谷歌', r'google', r'用谷歌'],
                'bing': [r'必应', r'bing', r'用必应']
            },
            'search_actions': [r'百度一下','搜一下','查一下', r'了解', r'联网', r'网上'],
            'auto_search': [r'天气', r'新闻', r'股市', '股票', 'a股',  r'位置']
        }
        
        self.context_keywords = {
            'reference': {
                'zh': [r'这[个是]', r'那[个是]', r'[它他她]们?[的是]', r'为什么', r'怎么[样办]', r'然后[呢的]', r'还有[呢吗]'],
                'en': [r'what.*?(?:is|are)', r'why.*?(?:is|are)', r'how.*?(?:is|are|to)', r'(?:tell|explain).*?(?:more|about)']
            },
            'navigation': {
                'zh': [r'[点击]?查看.*?链接', r'[点击]?打开.*?链接', r'看看.*?[链接地址]', r'下一[个条]', r'换[一个]*链接'],
                'en': [r'(?:click|open|view).*?link', r'(?:next|previous).*?(?:one|result)', r'(?:other|different).*?(?:options|results)']
            }
        }
        
        asyncio.create_task(self._cleanup_context_cache())
        
    async def _cleanup_context_cache(self):
        
        while True:
            try:
                current_time = datetime.datetime.now()
                expired_contexts = [
                    conv_id for conv_id, context in self.context_cache.items()
                    if (current_time - context['timestamp']).total_seconds() > self.CONTEXT_TTL
                ]
                
                for conv_id in expired_contexts:
                    del self.context_cache[conv_id]
                
                await asyncio.sleep(300)  
            except Exception as e:
                await asyncio.sleep(60)  
    
    def _is_context_dependent(self, query: str) -> bool:
        
        
        for pattern in self.context_keywords['reference']['zh']:
            if re.search(pattern, query):
                return True
                
        
        for pattern in self.context_keywords['reference']['en']:
            if re.search(pattern, query, re.IGNORECASE):
                return True
                
        return False
    
    def _is_navigation_query(self, query: str) -> Tuple[bool, str]:
        
        
        for pattern in self.context_keywords['navigation']['zh']:
            if re.search(pattern, query):
                if '下一' in query or '换' in query:
                    return True, 'next'
                elif '上一' in query:
                    return True, 'previous'
                else:
                    return True, 'view'
                    
        
        for pattern in self.context_keywords['navigation']['en']:
            if re.search(pattern, query, re.IGNORECASE):
                if re.search(r'next|another|more', query, re.IGNORECASE):
                    return True, 'next'
                elif re.search(r'previous|back|last', query, re.IGNORECASE):
                    return True, 'previous'
                else:
                    return True, 'view'
                    
        return False, ''
    
    def _get_conversation_context(self, conversation_id: str) -> Optional[Dict]:
        return self.context_cache.get(conversation_id)
    
    def _update_conversation_context(self, conversation_id: str, context_data: Dict):
        self.context_cache[conversation_id] = {
            'timestamp': datetime.datetime.now(),
            'data': context_data
        }
        
    def _get_next_result(self, current_results: List[Dict], current_index: int) -> Tuple[Optional[Dict], int]:
        
        if not current_results:
            return None, -1
        next_index = (current_index + 1) % len(current_results)
        return current_results[next_index], next_index
    
    def _get_previous_result(self, current_results: List[Dict], current_index: int) -> Tuple[Optional[Dict], int]:
        
        if not current_results:
            return None, -1
        prev_index = (current_index - 1) % len(current_results)
        return current_results[prev_index], prev_index
    
    def check_should_search(self, query: str, web_search_enabled: bool = True) -> Tuple[bool, str]:
        if not web_search_enabled:
            return False, ''
        
        if self._has_url(query):
            return True, 'default'
        
        return self._should_perform_search(query)
        
    def _clean_search_query(self, query: str) -> str:
        for engine_patterns in self.search_keywords['search_engines'].values():
            for pattern in engine_patterns:
                query = re.sub(pattern, '', query).strip()
        
        for pattern in self.search_keywords['search_actions']:
            query = re.sub(pattern, '', query).strip()
        
        for keyword in self.search_keywords['auto_search']:
            if keyword in query and len(query) > len(keyword) + 5:
                continue
            query = re.sub(f'^{keyword}\\s+', '', query).strip()
            query = re.sub(f'\\s+{keyword}$', '', query).strip()
            
        return query

    def _should_perform_search(self, query: str) -> Tuple[bool, str]:
        query = query.lower()
        
        for engine, patterns in self.search_keywords['search_engines'].items():
            if any(p.lower() in query for p in patterns):
                return True, engine
        
        if any(action in query for action in self.search_keywords['search_actions']):
            return True, 'default'
        
        if any(keyword in query for keyword in self.search_keywords['auto_search']):
            return True, 'auto'
            
        return False, ''

    def _has_url(self, query: str) -> bool:
        url_patterns = [
            r'@?https?://[^\s]+',
            r'@?www\.[^\s]+',
            r'@?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}\.[a-zA-Z]{2,}'
        ]
        return any(re.search(pattern, query) for pattern in url_patterns)

    async def process_query(self, 
                          user_input: conversation.ConversationInput,
                          agent_manager: Any,
                          agents: List[str],
                          agent_names: Dict[str, str],
                          default_agent: Any,
                          conversation_mode: str,
                          web_search_results: Optional[str] = None,
                          web_search_enabled: bool = True) -> conversation.ConversationResult:

        if not agents:
            return self._create_error_response(user_input, "请配置至少一个对话代理。")
        
        api_result = await self.detect_and_process_api_queries(
            user_input, agents, agent_names, default_agent, conversation_mode, agent_manager
        )
        if api_result:
            return api_result
        
        should_search = False
        search_engine = ''
        original_query = user_input.text
        cleaned_query = None
        
        if web_search_enabled:
            if self._has_url(user_input.text):
                should_search = True
                search_engine = 'default'
            else:
                should_search, search_engine = self._should_perform_search(user_input.text)
                if should_search:
                    for engine, patterns in self.search_keywords['search_engines'].items():
                        for pattern in patterns:
                            cleaned_query = re.sub(f'(?i){pattern}\\s*', '', original_query).strip()
                    
                    for action in self.search_keywords['search_actions']:
                        cleaned_query = re.sub(f'(?i){action}\\s*', '', cleaned_query).strip()
                    
                    if cleaned_query and cleaned_query != original_query:
                        user_input = conversation.ConversationInput(
                            text=cleaned_query,
                            conversation_id=user_input.conversation_id,
                            language=user_input.language,
                            context=getattr(user_input, "context", {}),
                            device_id=getattr(user_input, "device_id", None),
                            agent_id=getattr(user_input, "agent_id", None),
                            satellite_id=getattr(user_input, "satellite_id", None)
                        )
                elif web_search_results:
                    web_search_results = None
        elif web_search_results:
            web_search_results = None
        
        is_context_dependent = self._is_context_dependent(user_input.text)
        is_navigation, nav_type = self._is_navigation_query(user_input.text)
        conversation_id = user_input.conversation_id
        
        if is_context_dependent and conversation_id:
            context = self._get_conversation_context(conversation_id)
            if context:
                if is_navigation and 'all_results' in context['data']:
                    current_index = context['data'].get('current_index', 0)
                    all_results = context['data']['all_results']
                    
                    if nav_type == 'next':
                        result, new_index = self._get_next_result(all_results, current_index)
                    elif nav_type == 'previous':
                        result, new_index = self._get_previous_result(all_results, current_index)
                    else:  
                        result = all_results[current_index] if current_index < len(all_results) else None
                        new_index = current_index
                        
                    if result:
                        web_search_results = result.get('content', '')
                        context['data']['current_index'] = new_index
                        self._update_conversation_context(conversation_id, context['data'])
                else:
                    web_search_results = context['data'].get('search_results')
        
        processed_search_results = None
        if web_search_results and (should_search or is_context_dependent):
            query_type = self.prompt_manager.identify_query_type(user_input.text)
            
            processed_search_results = self.content_processor.process_content(
                web_search_results, 
                query_type.value
            )
            
            if conversation_id:
                context_data = {
                    'query': user_input.text,
                    'query_type': query_type.value,
                    'search_results': web_search_results,
                    'processed_results': processed_search_results,
                    'current_index': 0,
                    'search_engine': search_engine
                }
                
                if isinstance(web_search_results, list):
                    context_data['all_results'] = web_search_results
                    
                self._update_conversation_context(conversation_id, context_data)
        
        prompt_data = self.prompt_manager.generate_prompt(
            user_input.text, 
            processed_search_results or web_search_results if should_search or is_context_dependent else None
        )
        
        if not isinstance(prompt_data['main_prompt'], str):
            try:
                if isinstance(prompt_data['main_prompt'], dict):
                    prompt_data['main_prompt'] = json.dumps(prompt_data['main_prompt'], ensure_ascii=False)
                else:
                    prompt_data['main_prompt'] = str(prompt_data['main_prompt'])
            except Exception as e:
                prompt_data['main_prompt'] = f"用户查询: {user_input.text}"
        
        result = await self._process_with_agents(
            user_input,
            agent_manager,
            agents,
            agent_names,
            default_agent,
            conversation_mode,
            prompt_data,
            processed_search_results or web_search_results if should_search or is_context_dependent else None
        )
        
        return result
        
    async def _process_with_agents(self,
                                user_input: conversation.ConversationInput,
                                agent_manager: Any,
                                agents: List[str],
                                agent_names: Dict[str, str],
                                default_agent: Any,
                                conversation_mode: str,
                                prompt_data: Dict[str, Any],
                                search_results: Optional[str] = None) -> conversation.ConversationResult:

        
        for agent_id in agents:
            if not agent_id:
                continue
                
            agent_name = self._get_agent_name(agent_id, agent_names)
            if agent_id == conversation.const.HOME_ASSISTANT_AGENT:
                agent_name = default_agent.name
            
            if not isinstance(agent_id, str):
                if hasattr(agent_id, "__class__") and agent_id.__class__.__name__ == "DefaultAgent":
                    agent_id = conversation.const.HOME_ASSISTANT_AGENT
                else:
                    try:
                        agent_id = str(agent_id)
                    except:
                        agent_id = conversation.const.HOME_ASSISTANT_AGENT
            
            try:
                if search_results:
                    enhanced_text = self._create_enhanced_input_text(
                        user_input.text,
                        prompt_data["main_prompt"],
                        search_results
                    )
                    cleaned_text = await self.content_processor.clean_text_for_api(enhanced_text)
                else:
                    cleaned_text = user_input.text
                
                device_id = getattr(user_input, "device_id", None)
                original_context = getattr(user_input, "context", {})
                satellite_id = getattr(user_input, "satellite_id", None)
                
                enhanced_input = conversation.ConversationInput(
                    text=cleaned_text,
                    conversation_id=user_input.conversation_id,
                    language=user_input.language,
                    context=original_context,
                    device_id=device_id,
                    agent_id=agent_id,
                    satellite_id=satellite_id
                )
                
                result = await self._async_process_agent(
                    agent_manager,
                    agent_id,
                    agent_name,
                    enhanced_input,
                    conversation_mode,
                    None,
                    search_results,
                )
                
                if result and result.response:
                    _LOGGER.debug("Agent %s 返回 response_type: %s", agent_id, result.response.response_type)
                    if result.response.response_type == intent.IntentResponseType.ACTION_DONE:
                        response_text = result.response.speech['plain'].get('original_speech', 
                                                                          result.response.speech['plain'].get('speech', '')).strip()
                        
                        if conversation_mode == "no_name":
                            result.response.speech['plain'].update({
                                'speech': response_text,
                                'extra_data': None,
                                'original_speech': response_text,
                                'agent_name': agent_name,
                                'agent_id': agent_id
                            })
                        elif conversation_mode == "add_name":
                            result.response.speech['plain'].update({
                                'speech': f"({agent_name}) 回复: {response_text}",
                                'extra_data': None,
                                'original_speech': response_text,
                                'agent_name': agent_name,
                                'agent_id': agent_id
                            })
                        else:  
                            if search_results:
                                search_summary = search_results[:1000] + "..." if len(search_results) > 1000 else search_results
                                result.response.speech['plain'].update({
                                    'speech': f"联网搜索结果摘要:\n{search_summary}\n\n({agent_name}) 回复: {response_text}",
                                    'extra_data': None,
                                    'original_speech': response_text,
                                    'agent_name': agent_name,
                                    'agent_id': agent_id,
                                    'search_results': search_results
                                })
                            else:
                                result.response.speech['plain'].update({
                                    'speech': f"({agent_name}) 回复: {response_text}",
                                    'extra_data': None,
                                    'original_speech': response_text,
                                    'agent_name': agent_name,
                                    'agent_id': agent_id
                                })
                        
                        if not hasattr(result.response, 'data'):
                            result.response.data = {
                                'targets': [],
                                'success': [],
                                'failed': []
                            }
                        
                        return result
                        
                _LOGGER.warning("Agent %s 返回非 ACTION_DONE 响应，尝试下一个", agent_id)
                continue
                
            except Exception as e:
                _LOGGER.error("Agent %s 处理失败: %s", agent_id, e)
                continue
        
        return self._create_error_response(user_input, "抱歉，我无法处理这个请求。")
    
    def _create_enhanced_input_text(self, 
                                    query: str, 
                                    prompt: str, 
                                    search_results: Optional[str] = None) -> str:
        
        if not search_results:
            return prompt
            
        enhanced_text = f"{prompt}\n\n联网搜索结果：\n{search_results}"
        return enhanced_text
    
    def _clean_agent_response(self, response_text: str, agent_name: str) -> str:
        
        if not response_text:
            return response_text
            
        
        patterns = [
            
            rf"{re.escape(agent_name)}\)\s*回复\s*:",
            
            rf"{re.escape(agent_name)}\s*回复\s*:",
            rf"{re.escape(agent_name)}\s*:",
            r"AI\s*回复\s*:",
            r"回复\s*:"
        ]
        
        for pattern in patterns:
            if re.match(pattern, response_text, re.IGNORECASE):
                cleaned_text = re.sub(pattern, "", response_text, flags=re.IGNORECASE).strip()
                return cleaned_text
                
        return response_text
    
    async def _async_process_agent(self,
                                agent_manager: Any,
                                agent_id: str,
                                agent_name: str,
                                user_input: conversation.ConversationInput,
                                conversation_mode: str,
                                previous_result: Optional[Any],
                                search_results: Optional[str] = None) -> conversation.ConversationResult:
        
        from homeassistant.components.conversation import trace, intent
        if not user_input.text or not user_input.text.strip():
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                "没有收到有效内容，请再试一次。",
            )
            return conversation.ConversationResult(
                conversation_id=user_input.conversation_id or ulid.ulid(),
                response=intent_response,
            )
        
        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "text": user_input.text[:100] + "..." if len(user_input.text) > 100 else user_input.text
            }
        )
        
        try:
            agent = conversation.agent_manager.async_get_agent(self.hass, agent_id)
            result = await agent.async_process(user_input)
            
            trace.async_conversation_trace_append(
                trace.ConversationTraceEventType.AGENT_DETAIL,
                {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "response": result.response.speech['plain']['speech'] if result.response.speech and 'plain' in result.response.speech else "No response"
                }
            )
            
            
            if result.response.speech and 'plain' in result.response.speech:
                r = result.response.speech['plain']['speech']
                result.response.speech['plain']['original_speech'] = r
                result.response.speech['plain']['agent_name'] = agent_name
                result.response.speech['plain']['agent_id'] = agent_id
                
                
                if conversation_mode == "no_name":
                    result.response.speech['plain']['speech'] = r
                elif conversation_mode == "add_name":
                    result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
                elif conversation_mode == "detailed":
                    if previous_result is not None and previous_result.response.response_type == intent.IntentResponseType.ERROR:
                        prev_name = previous_result.response.speech['plain'].get('agent_name', 'UNKNOWN')
                        prev_text = previous_result.response.speech['plain'].get('original_speech', previous_result.response.speech['plain']['speech'])
                        if search_results:
                            search_summary = search_results[:500] + "..." if len(search_results) > 500 else search_results
                            result.response.speech['plain']['speech'] = f"联网搜索结果摘要:\n{search_summary}\n\n({prev_name}) 失败，回复: {prev_text}\n然后 ({agent_name}) 回复: {r}"
                        else:
                            result.response.speech['plain']['speech'] = f"({prev_name}) 失败，回复: {prev_text}\n然后 ({agent_name}) 回复: {r}"
                    else:
                        if search_results:
                            search_summary = search_results[:500] + "..." if len(search_results) > 500 else search_results
                            result.response.speech['plain']['speech'] = f"联网搜索结果摘要:\n{search_summary}\n\n({agent_name}) 回复: {r}"
                        else:
                            result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
            
            return result
            
        except Exception as e:
            trace.async_conversation_trace_append(
                trace.ConversationTraceEventType.AGENT_DETAIL,
                {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "error": str(e)
                }
            )
            raise
    
    def _create_detailed_response(self,
                                user_input: conversation.ConversationInput,
                                all_results: List[Dict[str, Any]],
                                successful_result: Optional[Any],
                                search_results: Optional[str] = None) -> conversation.ConversationResult:
        from homeassistant.components.conversation import intent
        _LOGGER.info(f"详细模式: 整合所有代理的响应，代理数量: {len(all_results)}")
        detailed_response = []
        if search_results:
            search_summary = search_results[:500] + "..." if len(search_results) > 500 else search_results
            detailed_response.append(f"联网搜索结果摘要:\n{search_summary}")
        successful_responses = []
        for resp in all_results:
            response_text = resp['response']
            if response_text and len(response_text.strip()) > 10:
                successful_responses.append(f"\n({resp['agent_name']}) 回复:\n{response_text}\n")
        if successful_responses:
            detailed_response.extend(successful_responses)
        final_response = "\n".join(filter(None, detailed_response))
        if successful_result:
            successful_result.response.speech['plain']['speech'] = final_response
            return self._update_state_and_return(successful_result)
        else:
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_speech(final_response)
            result = conversation.ConversationResult(
                conversation_id=user_input.conversation_id,
                response=intent_response
            )
            return self._update_state_and_return(result)
    
    def _create_error_response(self, 
                             user_input: conversation.ConversationInput, 
                             error_message: str) -> conversation.ConversationResult:
        from homeassistant.components.conversation import intent
        is_extraction_failure = self._is_entity_extraction_failure(error_message)
        clean_error = self._clean_error_message(error_message, is_extraction_failure)
        intent_response = intent.IntentResponse(language=user_input.language)
        intent_response.async_set_speech(clean_error)
        result = conversation.ConversationResult(
            conversation_id=user_input.conversation_id or ulid.ulid(),
            response=intent_response
        )
        return self._update_state_and_return(result)
    
    def _update_state_and_return(self, result: conversation.ConversationResult) -> conversation.ConversationResult:
        
        return result 

    def _is_entity_extraction_failure(self, error_message: str) -> bool:
        extraction_keywords = ["entity", "extraction", "识别", "提取", "解析"]
        return any(keyword in error_message.lower() for keyword in extraction_keywords)

    def _clean_error_message(self, error_message: str, is_extraction_failure: bool) -> str:
        if is_extraction_failure:
            return "抱歉，我无法理解您的请求，请尝试换一种方式表达。"
        return error_message

    def _get_agent_name(self, agent_id: str, agent_names: Dict[str, str]) -> str:
        return agent_names.get(agent_id, agent_id) 

    async def _has_sufficient_context(self, query: str, conversation_id: str) -> bool:
        
        if not self._is_context_dependent(query):
            return False
            
        
        context = self._get_conversation_context(conversation_id)
        if not context or not context.get('data'):
            return False
            
        
        context_data = context['data']
        if not context_data.get('search_results') and not context_data.get('processed_results'):
            return False
            
        
        current_time = datetime.datetime.now()
        if (current_time - context['timestamp']).total_seconds() > self.CONTEXT_TTL:
            return False
            
        return True 

    async def detect_and_process_api_queries(self, 
                                           user_input: conversation.ConversationInput,
                                           agents: List[str],
                                           agent_names: Dict[str, str],
                                           default_agent: Any,
                                           conversation_mode: str,
                                           agent_manager: Any = None) -> Optional[conversation.ConversationResult]:
        from ..services.api_manager import (
            is_weather_query, get_weather_info,
            is_zhihu_hot_query, get_zhihu_hot, 
            get_news_digest
        )
        
        query = user_input.text
        result = None
        
        if is_weather_query(query):
            weather_result = await get_weather_info(query)
            if weather_result:
                processed_content = self.content_processor.process_content(weather_result.content, "weather")
                query_type = self.prompt_manager.identify_query_type(query)
                
                prompt_data = self.prompt_manager.generate_prompt(
                    query, 
                    processed_content
                )
                
                return await self._process_with_agents(
                    user_input,
                    agent_manager,
                    agents,
                    agent_names,
                    default_agent,
                    conversation_mode,
                    prompt_data,
                    processed_content
                )
                
        elif is_zhihu_hot_query(query):
            zhihu_results = await get_zhihu_hot()
            if zhihu_results:
                combined_content = "\n".join([
                    f"# {i+1}. {result.title}\n{result.snippet}"
                    for i, result in enumerate(zhihu_results[:10])
                ])
                
                processed_content = self.content_processor.process_content(combined_content, "news")
                query_type = self.prompt_manager.identify_query_type(query)
                
                prompt_data = self.prompt_manager.generate_prompt(
                    query, 
                    processed_content
                )
                
                return await self._process_with_agents(
                    user_input,
                    agent_manager,
                    agents,
                    agent_names,
                    default_agent,
                    conversation_mode,
                    prompt_data,
                    processed_content
                )
                
        elif "新闻摘要" in query or re.search(r'(\d+)\s*分钟', query):
            news_results = await get_news_digest(query)
            if news_results:
                combined_content = ""
                for i, news in enumerate(news_results[:15]):
                    if news.metadata and news.metadata.get("time_period"):
                        combined_content += f"\n## {news.metadata['time_period']}新闻\n"
                    combined_content += f"{i+1}. {news.title or '最新动态'}\n{news.content}\n---\n"
                    
                processed_content = self.content_processor.process_content(combined_content, "news")
                query_type = self.prompt_manager.identify_query_type(query)
                
                prompt_data = self.prompt_manager.generate_prompt(
                    query, 
                    processed_content
                )
                
                return await self._process_with_agents(
                    user_input,
                    agent_manager,
                    agents,
                    agent_names,
                    default_agent,
                    conversation_mode,
                    prompt_data,
                    processed_content
                )
        
        return result 
