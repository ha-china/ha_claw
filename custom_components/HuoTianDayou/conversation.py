from __future__ import annotations

import logging
import asyncio  
import re

from homeassistant.components import assist_pipeline, conversation
from homeassistant.components.conversation import trace
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import ulid
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from home_assistant_intents import get_languages


from homeassistant.helpers import (
    config_validation as cv,
    intent,
)

from .const import (
    CONF_PRIMARY_AGENT,
    CONF_FALLBACK_AGENT,
    CONF_SECONDARY_FALLBACK_AGENT,
    CONF_CONVERSATION_MODE,
    CONF_SPEAKER_ENTITY,
    CONF_SPEAKER_TYPE,
    CONF_TTS_SERVICE,
    CONF_ENABLE_AI_SUMMARY,
    CONF_ENABLE_WEB_SEARCH,
    CONF_WEB_SEARCH_ENGINE,
    CONVERSATION_MODE_NO_NAME,
    CONVERSATION_MODE_ADD_NAME,
    CONVERSATION_MODE_DETAILED,
    SPEAKER_TYPE_DISABLED,
    SPEAKER_TYPE_XIAOMI,
    SPEAKER_TYPE_OTHER,
    SEARCH_ENGINE_GOOGLE,
    SEARCH_ENGINE_BAIDU,
    SEARCH_ENGINE_BING,
    DOMAIN,
    DEFAULT_ERROR_RESPONSES,
    DEFAULT_SEARCH_ENGINE,
)

from .web_search import WebSearch
from .content_processor import ContentProcessor
from .prompt_manager import PromptManager
from .ai_manager import AIManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

DATA_DEFAULT_ENTITY = "conversation_default_entity"

@callback
def get_default_agent(hass: HomeAssistant) -> conversation.default_agent.DefaultAgent:
    return hass.data[DATA_DEFAULT_ENTITY]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> bool:
    agent = FallbackConversationAgent(hass, entry)
    async_add_entities([agent])
    return True

class FallbackConversationAgent(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    last_used_agent: str | None
    entry: ConfigEntry
    hass: HomeAssistant
    _attr_has_entity_name = True
    _attr_chat_response: str | None = None
    _content_processor: ContentProcessor
    _prompt_manager: PromptManager
    _ai_manager: AIManager

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.last_used_agent = None
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._attr_supported_features = (
            conversation.ConversationEntityFeature.CONTROL
        )
        self.in_context_examples = None
        self._content_processor = ContentProcessor()
        self._prompt_manager = PromptManager()
        self._ai_manager = AIManager(hass)

    @property
    def supported_languages(self) -> list[str]:
        return get_languages()

    @property
    def state_attributes(self):
        attributes = super().state_attributes or {}
        attributes["entity"] = "HuoTianDaYou.ai"
        if self._attr_chat_response is not None:
            attributes["响应内容"] = self._attr_chat_response
        return attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        assist_pipeline.async_migrate_engine(
            self.hass, "conversation", self.entry.entry_id, self.entity_id
        )
        conversation.async_set_agent(self.hass, self.entry, self)
        self.entry.async_on_unload(
            self.entry.add_update_listener(self._async_entry_update_listener)
        )

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_entry_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        self._attr_supported_features = (
            conversation.ConversationEntityFeature.CONTROL
        )
        
    async def _call_speaker_service(self, text: str) -> None:
        speaker_entity = self.entry.options.get(CONF_SPEAKER_ENTITY)
        speaker_type = self.entry.options.get(CONF_SPEAKER_TYPE, SPEAKER_TYPE_DISABLED)
        
        if not speaker_entity or speaker_type == SPEAKER_TYPE_DISABLED:
            return
            
        try:
            if speaker_type == SPEAKER_TYPE_XIAOMI:
                await self.hass.services.async_call(
                    "xiaomi_miot",
                    "intelligent_speaker",
                    {
                        "entity_id": speaker_entity,
                        "execute": False,
                        "silent": False,
                        "text": text
                    },
                    blocking=True
                )
            elif speaker_type == SPEAKER_TYPE_OTHER:
                tts_service = self.entry.options.get(CONF_TTS_SERVICE)
                if tts_service:
                    service_parts = tts_service.split('.')
                    if len(service_parts) == 2:
                        domain, service = service_parts
                        await self.hass.services.async_call(
                            domain,
                            service,
                            {
                                "entity_id": speaker_entity,
                                "message": text
                            },
                            blocking=True
                        )
        except Exception as e:
            pass

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        with trace.async_conversation_trace() as conversation_trace:
            agent_manager = conversation.get_agent_manager(self.hass)
            default_agent = get_default_agent(self.hass)
            agent_names = self._convert_agent_info_to_dict(
                agent_manager.async_get_agent_info()
            )
            agent_names[conversation.const.HOME_ASSISTANT_AGENT] = default_agent.name
        
            trace.async_conversation_trace_append(
                trace.ConversationTraceEventType.ASYNC_PROCESS,
                {
                    "text": user_input.text,
                    "conversation_id": user_input.conversation_id,
                    "language": user_input.language,
                    "component": DOMAIN
                }
            )
        
            primary_agent = self.entry.options.get(CONF_PRIMARY_AGENT)
            fallback_agent = self.entry.options.get(CONF_FALLBACK_AGENT)
            secondary_fallback_agent = self.entry.options.get(CONF_SECONDARY_FALLBACK_AGENT)
        
            context = getattr(user_input, "context", {})
            is_service_call = False
            agent_id = getattr(user_input, "agent_id", None)
            
            if context and hasattr(context, "parent_id") and context.parent_id:
                try:
                    parent_context = self.hass.states.async_get_context(context.parent_id)
                    if parent_context and parent_context.origin and isinstance(parent_context.origin, dict):
                        if (parent_context.origin.get("type") == "execute_script" and 
                            "action" in str(parent_context.origin).lower() and 
                            "conversation.process" in str(parent_context.origin).lower()):
                            is_service_call = True
                except (AttributeError, KeyError) as e:
                    pass
            
            if agent_id and agent_id == self.entity_id:
                is_service_call = True
            
            agents = []
            if is_service_call:
                if primary_agent:
                    agents.append(primary_agent)
                if fallback_agent:
                    agents.append(fallback_agent)
                if secondary_fallback_agent:
                    agents.append(secondary_fallback_agent)
                
                if not agents:
                    agents.append(conversation.const.HOME_ASSISTANT_AGENT)
                    
                enable_ai_summary = False
                enable_web_search = False
            else:
                if primary_agent:
                    agents.append(primary_agent)
                if fallback_agent:
                    agents.append(fallback_agent)
                if secondary_fallback_agent:
                    agents.append(secondary_fallback_agent)
        
            if not agents:
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_error(
                    intent.IntentResponseErrorCode.NO_INTENT_MATCH,
                    "未配置对话代理，请在配置中添加至少一个对话代理。"
                )
                return conversation.ConversationResult(
                    conversation_id=user_input.conversation_id or ulid.ulid(),
                    response=intent_response
                )

            conversation_mode = self.entry.options.get(CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME)
            enable_ai_summary = self.entry.options.get(CONF_ENABLE_AI_SUMMARY, False)
            enable_web_search = self.entry.options.get(CONF_ENABLE_WEB_SEARCH, False)

            if user_input.conversation_id is None:
                user_input.conversation_id = ulid.ulid()
            is_summary_request = False
            if "请根据用户的问题" in user_input.text and "以及以下AI的回复进行总结和优化" in user_input.text:
                is_summary_request = True
        
            if is_summary_request:
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"detail": "Processing summary request"}
                )
                result = await self._process_summary_request(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode)
                conversation_trace.set_result(result=result.as_dict())
                return result
            
            
            web_search_results = None
            if enable_web_search:
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"detail": "Performing web search"}
                )
                try:
                    web_search_results = await self._execute_web_search(user_input.text, None, user_input.conversation_id)
                except Exception as e:
                    _LOGGER.error(f"Web search error: {e}")
                    trace.async_conversation_trace_append(
                        trace.ConversationTraceEventType.AGENT_DETAIL,
                        {"detail": "Web search failed", "error": str(e)}
                    )
        
            if enable_ai_summary and len(agents) >= 2:
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"detail": "Processing with AI summary", "agents": agents}
                )
                result = await self._process_with_summary(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode, web_search_results)
                conversation_trace.set_result(result=result.as_dict())
                return result
            elif enable_web_search and web_search_results:
                
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"detail": "Processing with web search only", "agents": agents}
                )
                result = await self._process_with_web_search(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode, web_search_results)
                conversation_trace.set_result(result=result.as_dict())
                return result
            else:
                trace.async_conversation_trace_append(
                    trace.ConversationTraceEventType.AGENT_DETAIL,
                    {"detail": "Processing with fallback", "agents": agents}
                )
                result = await self._process_with_fallback(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode)
                conversation_trace.set_result(result=result.as_dict())
                return result

    async def _execute_web_search(self, query, search_engine=None, conversation_id=None):
        
        _LOGGER.info(f"开始执行网络搜索，原始查询: '{query}'")
        
        
        should_search, detected_engine = self._ai_manager.check_should_search(query)
        if not should_search:
            _LOGGER.info(f"未检测到搜索关键词，跳过网络搜索: '{query}'")
            return None
            
        
        valid_engines = {
            SEARCH_ENGINE_GOOGLE: "谷歌",
            SEARCH_ENGINE_BAIDU: "百度",
            SEARCH_ENGINE_BING: "必应"
        }
        
        
        if not search_engine:
            configured_engine = self.entry.options.get(CONF_WEB_SEARCH_ENGINE)
            if configured_engine in valid_engines:
                search_engine = configured_engine
                _LOGGER.info(f"使用配置的搜索引擎: {valid_engines[search_engine]}")
            else:
                search_engine = DEFAULT_SEARCH_ENGINE
                _LOGGER.info(f"使用默认引擎: {valid_engines[DEFAULT_SEARCH_ENGINE]}")
            
        
        if detected_engine and detected_engine not in ('default', 'auto'):
            if detected_engine in valid_engines:
                search_engine = detected_engine
                _LOGGER.info(f"使用检测到的搜索引擎: {valid_engines[search_engine]}")
            else:
                _LOGGER.info(f"检测到的搜索引擎 '{detected_engine}' 无效，使用当前引擎: {valid_engines[search_engine]}")
        
        
        if conversation_id and await self._ai_manager._has_sufficient_context(query, conversation_id):
            _LOGGER.info(f"查询 '{query}' 依赖上下文且已有足够上下文信息，跳过网络搜索")
            return None
        
        
        if query.lower().startswith(("百度 ", "百度：", "百度:", "baidu ", "baidu:", "baidu：")):
            search_engine = SEARCH_ENGINE_BAIDU
            query = re.sub(r'^(百度|baidu)[：: ]', '', query, flags=re.IGNORECASE).strip()
            _LOGGER.info(f"检测到百度搜索前缀，使用百度搜索引擎，处理后查询: '{query}'")
        elif query.lower().startswith(("谷歌 ", "谷歌：", "谷歌:", "google ", "google:", "google：")):
            search_engine = SEARCH_ENGINE_GOOGLE
            query = re.sub(r'^(谷歌|google)[：: ]', '', query, flags=re.IGNORECASE).strip()
            _LOGGER.info(f"检测到谷歌搜索前缀，使用谷歌搜索引擎，处理后查询: '{query}'")
        elif query.lower().startswith(("必应 ", "必应：", "必应:", "bing ", "bing:", "bing：")):
            search_engine = SEARCH_ENGINE_BING
            query = re.sub(r'^(必应|bing)[：: ]', '', query, flags=re.IGNORECASE).strip()
            _LOGGER.info(f"检测到必应搜索前缀，使用必应搜索引擎，处理后查询: '{query}'")
        
        try:
            if not search_engine or search_engine not in valid_engines:
                _LOGGER.info(f"搜索引擎 '{search_engine}' 无效，使用默认引擎: {valid_engines[DEFAULT_SEARCH_ENGINE]}")
                search_engine = DEFAULT_SEARCH_ENGINE
                
            _LOGGER.info(f"创建 WebSearch 实例，搜索引擎类型: {valid_engines[search_engine]}")
            web_search = WebSearch(search_engine)
            
            _LOGGER.info(f"开始执行搜索并获取网页内容，查询: '{query}'，结果数量: 3")
            search_results = await web_search.get_search_results_text(query, num_results=3)
            
            await web_search.close()
            
            if not search_results:
                _LOGGER.info("搜索结果为空")
                return "未找到相关搜索结果。"
            
            result_preview = search_results[:300] + "..." if len(search_results) > 300 else search_results
            _LOGGER.info(f"搜索和内容获取完成，结果预览: {result_preview}")
            
            query_type = self._prompt_manager.identify_query_type(query)
            processed_results = self._content_processor.process_content(search_results, query_type.value)
            
            formatted_results = f"\n{processed_results}"
            
            _LOGGER.info(f"搜索结果已处理，内容类型: {query_type.value}, 处理后长度: {len(formatted_results)}")
            
            return formatted_results
        except Exception as e:
            error_msg = f"执行网络搜索时出错: {str(e)}"
            _LOGGER.error(error_msg, exc_info=True)
            return error_msg

    async def _process_with_web_search(self, user_input, agent_manager, agents, agent_names, default_agent, conversation_mode, web_search_results):
        
        _LOGGER.info(f"开始处理联网搜索结果，会话模式: {conversation_mode}")
        
        if not agents:
            _LOGGER.info("未配置任何对话代理，无法处理联网搜索结果")
            intent_response = intent.IntentResponse(language=user_input.language)
            response_text = "请配置至少一个对话代理。"
            intent_response.async_set_speech(response_text)
            result = conversation.ConversationResult(
                conversation_id=user_input.conversation_id,
                response=intent_response
            )
            self._attr_chat_response = response_text
            self.async_write_ha_state()
            asyncio.create_task(self._call_speaker_service(response_text))
            return result
        
        
        result = await self._ai_manager.process_query(
            user_input=user_input,
            agent_manager=agent_manager,
            agents=agents,
            agent_names=agent_names,
            default_agent=default_agent,
            conversation_mode=conversation_mode,
            web_search_results=web_search_results
        )
        
        
        if result and result.response and result.response.speech and 'plain' in result.response.speech:
            self._attr_chat_response = result.response.speech['plain']['speech']
            self.async_write_ha_state()
            asyncio.create_task(self._call_speaker_service(result.response.speech['plain']['speech']))
        
        return result

    async def _process_with_summary(self, user_input, agent_manager, agents, agent_names, default_agent, conversation_mode, web_search_results=None):
        
        _LOGGER.info(f"开始处理带AI总结的查询，代理数量: {len(agents)}")
        
        if len(agents) <= 1:
            return await self._process_with_fallback(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode)
        
        
        if len(agents) == 2:
            processing_agents = [agents[0]]
            summary_agent = agents[1]
        else:
            processing_agents = agents[:-1]
            summary_agent = agents[-1]
        
        
        summary_agent_name = agent_names.get(summary_agent, "UNKNOWN")
        if summary_agent == conversation.const.HOME_ASSISTANT_AGENT:
            summary_agent_name = default_agent.name
            
        _LOGGER.info(f"使用代理 {[agent_names.get(a, 'UNKNOWN') for a in processing_agents]} 处理查询，"
                   f"使用代理 '{summary_agent_name}' 进行总结")
        
        
        primary_responses = []
        all_results = []
        
        for agent_id in processing_agents:
            if not agent_id:
                continue
                
            agent_name = default_agent.name if agent_id == conversation.const.HOME_ASSISTANT_AGENT else agent_names.get(agent_id, "UNKNOWN")
                
            if not isinstance(agent_id, str):
                agent_id = conversation.const.HOME_ASSISTANT_AGENT if (hasattr(agent_id, "__class__") and agent_id.__class__.__name__ == "DefaultAgent") else (str(agent_id) if hasattr(agent_id, "__str__") else conversation.const.HOME_ASSISTANT_AGENT)
            
            try:
                _LOGGER.info(f"使用代理 '{agent_name}' 处理用户输入")
                
                
                if web_search_results:
                    
                    prompt_data = self._prompt_manager.generate_prompt(
                        user_input.text, 
                        web_search_results
                    )
                    
                    
                    enhanced_text = f"{prompt_data['main_prompt']}\n\n联网搜索结果：\n{web_search_results}"
                    
                    
                    cleaned_text = await self._content_processor.clean_text_for_api(enhanced_text)
                    
                    
                    device_id = getattr(user_input, "device_id", None)
                    original_context = getattr(user_input, "context", {})
                    
                    enhanced_input = conversation.ConversationInput(
                        text=cleaned_text,
                        conversation_id=user_input.conversation_id,
                        language=user_input.language,
                        context=original_context,
                        device_id=device_id,
                        agent_id=agent_id
                    )
                    
                    
                    result = await self._async_process_agent(
                        agent_manager,
                        agent_id,
                        agent_name,
                        enhanced_input,
                        CONVERSATION_MODE_NO_NAME,  
                        None,
                    )
                else:
                    
                    result = await self._async_process_agent(
                        agent_manager,
                        agent_id,
                        agent_name,
                        user_input,
                        CONVERSATION_MODE_NO_NAME,  
                        None,
                    )
                
                
                response_text = ""
                is_action_done = False
                
                if (result and result.response and result.response.speech and 'plain' in result.response.speech):
                    response_text = result.response.speech['plain'].get('original_speech', result.response.speech['plain'].get('speech', "")).strip()
                    
                    
                    response_text = self._ai_manager._clean_agent_response(response_text, agent_name)
                    
                    if result.response.response_type == intent.IntentResponseType.ACTION_DONE:
                        is_action_done = True
                        
                        
                        if hasattr(result.response, 'data') and result.response.data:
                            targets = result.response.data.get('targets', [])
                            success = result.response.data.get('success', [])
                            failed = result.response.data.get('failed', [])
                            
                            response_text += "".join([
                                f" (目标: {', '.join(targets)})" if targets else "",
                                f" (成功: {', '.join(success)})" if success else "",
                                f" (失败: {', '.join(failed)})" if failed else ""
                            ])
                
                
                primary_responses.append({
                    "agent_name": agent_name,
                    "response": response_text,
                    "is_error": not (is_action_done or (response_text and 
                              not response_text.lower().startswith('python') and
                              response_text not in DEFAULT_ERROR_RESPONSES and
                              len(response_text) > 10))
                })
                
                all_results.append(result)
            except Exception as e:
                _LOGGER.error(f"使用代理 '{agent_name}' 处理输入时出错: {e}", exc_info=True)
                primary_responses.append({
                    "agent_name": agent_name,
                    "response": f"处理错误: {str(e)}",
                    "is_error": True
                })
        
        
        if not primary_responses or not summary_agent:
            _LOGGER.info("没有获得有效的代理响应或未指定总结代理，回退到标准处理方法")
            return await self._process_with_fallback(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode)
        
        
        summary_prompt = f"""<AI_SUMMARY_REQUEST>
请根据用户的问题：'{user_input.text}'，以及以下AI的回复进行总结和优化：

"""
        
        for resp in primary_responses:
            summary_prompt += f"- {resp['agent_name']}：{resp['response']}\n"
        
        
        if web_search_results:
            summary_prompt += f"\n联网搜索参考信息：\n{'-' * 40}\n{web_search_results}\n{'-' * 40}\n"
            
        summary_prompt += """
请你首先进行多维度的思考分析，然后给出最终的回复结果，最终结果不要解释自己为什么这样回复。你的回复必须严格按照以下格式：

<ANALYSIS_SECTION>
在这里分析各个AI回复的优缺点，评估它们的准确性、完整性和相关性。如果有联网搜索结果，也请结合搜索结果进行分析。
</ANALYSIS_SECTION>

<SUMMARY_SECTION>
在这里直接提供你的最终答案，用你自己的语言回答用户的问题。总结应该综合考虑AI回复和搜索结果（如果有）。
</SUMMARY_SECTION>

严格注意：
- 你的回复必须包含且仅包含上述两个部分，并使用指定的标记，必须使用中文
- 禁止使用JSON格式输出
- 不要在回复中添加任何其他前言、说明或额外内容
- 不要使用"这里是我的分析"等引导语
- 分析部分必须在标记内，总结部分也必须在标记内
- 总结应该综合各个信息源的内容，并添加你自己的见解
- 控制你的总结和回复在450字之内，不可以超出这个限制

**请严格按照上述格式回复，不要有任何偏差。**
</AI_SUMMARY_REQUEST>
"""
        
        
        try:
            _LOGGER.info(f"使用代理 '{summary_agent_name}' 处理总结请求")
            
            
            device_id = getattr(user_input, "device_id", None)
            original_context = getattr(user_input, "context", {})
            
            
            cleaned_prompt = await self._content_processor.clean_text_for_api(summary_prompt)
            
            
            summary_input = conversation.ConversationInput(
                text=cleaned_prompt,
                conversation_id=user_input.conversation_id,
                language=user_input.language,
                context=original_context, 
                device_id=device_id,
                agent_id=summary_agent
            )
            
            
            result = await self._async_process_agent(
                agent_manager,
                summary_agent,
                summary_agent_name, 
                summary_input,
                CONVERSATION_MODE_NO_NAME,  
                None,
            )
            
            
            if not (result and result.response and result.response.speech and 'plain' in result.response.speech):
                raise Exception("无效的总结响应格式")
                
            
            response_text = result.response.speech['plain'].get('original_speech', result.response.speech['plain'].get('speech', "")).strip()
            
            
            cleaned_response = self._clean_ai_response(response_text)
            
            analysis_part = cleaned_response.get('analysis', '')
            summary_part = cleaned_response.get('summary', '')
            
            
            if summary_part:
                summary_lines = summary_part.strip().split('\n')
                if len(summary_lines) > 1:
                    final_summary = summary_lines[-1].strip()
                else:
                    final_summary = summary_part.strip()
            else:
                final_summary = response_text
            
            
            if conversation_mode == CONVERSATION_MODE_NO_NAME:
                result.response.speech['plain']['speech'] = final_summary
            elif conversation_mode == CONVERSATION_MODE_ADD_NAME:
                result.response.speech['plain']['speech'] = f"({summary_agent_name}) 回复: {final_summary}"
            elif conversation_mode == CONVERSATION_MODE_DETAILED:
                detailed_response = ""
                for resp in primary_responses:
                    detailed_response += f"({resp['agent_name']}) 回复: {resp['response']}\n"
                detailed_response += "\n"
                if analysis_part:
                    detailed_response += f"{analysis_part}\n\n"
                if summary_part:
                    detailed_response += f"({summary_agent_name}) 回复: {final_summary}"
                else:
                    detailed_response += f"({summary_agent_name}) 回复: {final_summary}"
                result.response.speech['plain']['speech'] = detailed_response
            
            
            if (final_summary and 
                not final_summary.lower().startswith('python') and
                final_summary not in DEFAULT_ERROR_RESPONSES and
                len(final_summary) > 10):
                
                self._attr_chat_response = result.response.speech['plain']['speech']
                self.async_write_ha_state()
                asyncio.create_task(self._call_speaker_service(result.response.speech['plain']['speech']))
                return result
            
            all_results.append(result)
        except Exception as e:
            _LOGGER.error(f"使用总结代理处理时出错: {e}", exc_info=True)
            
        
        _LOGGER.info("总结处理失败，回退到标准处理方法")
        return await self._process_with_fallback(user_input, agent_manager, agents, agent_names, default_agent, conversation_mode)

    async def _process_with_fallback(self, user_input, agent_manager, agents, agent_names, default_agent, conversation_mode):
        
        _LOGGER.info(f"开始使用标准处理方法处理查询，代理数量: {len(agents)}")
        
        if not agents:
            intent_response = intent.IntentResponse(language=user_input.language)
            response_text = "请配置至少一个对话代理。"
            intent_response.async_set_speech(response_text)
            result = conversation.ConversationResult(
                conversation_id=user_input.conversation_id,
                response=intent_response
            )
            self._attr_chat_response = response_text
            self.async_write_ha_state()
            asyncio.create_task(self._call_speaker_service(response_text))
            return result
        
        
        result = await self._ai_manager.process_query(
            user_input=user_input,
            agent_manager=agent_manager,
            agents=agents,
            agent_names=agent_names,
            default_agent=default_agent,
            conversation_mode=conversation_mode
        )
        
        
        if result and result.response and result.response.speech and 'plain' in result.response.speech:
            self._attr_chat_response = result.response.speech['plain']['speech']
            self.async_write_ha_state()
            asyncio.create_task(self._call_speaker_service(result.response.speech['plain']['speech']))
        
        return result

    def _clean_ai_response(self, response_text):
        result = {
            'analysis': '',
            'summary': ''
        }
        
        if '<AI_SUMMARY_REQUEST>' in response_text:
            response_text = response_text.replace('<AI_SUMMARY_REQUEST>', '').strip()
        if '</AI_SUMMARY_REQUEST>' in response_text:
            response_text = response_text.replace('</AI_SUMMARY_REQUEST>', '').strip()
        
        if '<ANALYSIS_SECTION>' in response_text and '</ANALYSIS_SECTION>' in response_text:
            try:
                analysis = response_text.split('<ANALYSIS_SECTION>', 1)[1].split('</ANALYSIS_SECTION>', 1)[0].strip()
                result['analysis'] = analysis
            except IndexError:
                pass

        if '<SUMMARY_SECTION>' in response_text and '</SUMMARY_SECTION>' in response_text:
            try:
                summary = response_text.split('<SUMMARY_SECTION>', 1)[1].split('</SUMMARY_SECTION>', 1)[0].strip()
                result['summary'] = summary
            except IndexError:
                pass
        elif not result['summary']:
            if result['analysis'] and '```' in response_text:
                try:
                    summary_part = response_text.split('```', 2)[2].strip()
                    result['summary'] = summary_part
                except IndexError:
                    pass
            if not result['summary']:
                clean_text = response_text
                for tag in ['<ANALYSIS_SECTION>', '</ANALYSIS_SECTION>', '<SUMMARY_SECTION>', '</SUMMARY_SECTION>']:
                    clean_text = clean_text.replace(tag, '')
                result['summary'] = clean_text.strip()
        
        return result

    async def _async_process_agent(
        self,
        agent_manager: conversation.AgentManager,
        agent_id: str,
        agent_name: str,
        user_input: conversation.ConversationInput,
        conversation_mode: str,
        previous_result,
    ) -> conversation.ConversationResult:
        trace.async_conversation_trace_append(
            trace.ConversationTraceEventType.AGENT_DETAIL,
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "text": user_input.text
            }
        )
        
        try:
            agent = None
            try:
                agent = conversation.agent_manager.async_get_agent(self.hass, agent_id)
            except Exception:
                for entity_id in self.hass.states.async_entity_ids("conversation"):
                    state = self.hass.states.get(entity_id)
                    if state:
                        friendly_name = state.attributes.get("friendly_name", entity_id.split('.')[-1])
                        entity_id_to_name = {}
                        name_to_entity_id = {}
                        
                        for entity_id in self.hass.states.async_entity_ids("conversation"):
                            state = self.hass.states.get(entity_id)
                            if state:
                                friendly_name = state.attributes.get("friendly_name", entity_id.split('.')[-1])
                                entity_id_to_name[entity_id] = friendly_name
                                name_to_entity_id[friendly_name] = entity_id
                        
                        if agent_id in entity_id_to_name:
                            agent_id = entity_id_to_name[agent_id]
                        elif agent_name in name_to_entity_id:
                            entity_id = name_to_entity_id[agent_name]
                            agent_id = entity_id
                            break
            
            if not agent:
                raise ValueError(f"无法找到代理: {agent_id} / {agent_name}")
                
            result = await agent.async_process(user_input)
            
            trace.async_conversation_trace_append(
                trace.ConversationTraceEventType.AGENT_DETAIL,
                {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "response": result.response.speech['plain']['speech'] if result.response.speech and 'plain' in result.response.speech else "No response"
                }
            )
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
            
        r = result.response.speech['plain']['speech']
        result.response.speech['plain']['original_speech'] = r
        result.response.speech['plain']['agent_name'] = agent_name
        result.response.speech['plain']['agent_id'] = agent_id
        
        is_summary = False
        if "请根据用户的问题" in user_input.text and "以及以下AI的回复进行总结和优化" in user_input.text:
            is_summary = True
        
        if conversation_mode == CONVERSATION_MODE_NO_NAME:
            result.response.speech['plain']['speech'] = r
        elif conversation_mode == CONVERSATION_MODE_ADD_NAME:
            if is_summary:
                result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
            else:
                result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
        elif conversation_mode == CONVERSATION_MODE_DETAILED:
            if is_summary:
                result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
            elif previous_result is not None:
                if previous_result.response.response_type == intent.IntentResponseType.ERROR:
                    prev_name = previous_result.response.speech['plain'].get('agent_name', 'UNKNOWN')
                    prev_text = previous_result.response.speech['plain'].get('original_speech', previous_result.response.speech['plain']['speech'])
                    result.response.speech['plain']['speech'] = f"({prev_name}) 失败，回复: {prev_text}\n然后 ({agent_name}) 回复: {r}"
                else:
                    result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
            else:
                result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {r}"
        
        return result

    def _clean_agent_response(self, response_text, agent_name):
        
        if not response_text:
            return response_text
            
        
        _LOGGER.debug(f"清理前的响应文本: {response_text[:100]}...")
        
        
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
                _LOGGER.info(f"移除了响应前缀 '{pattern}'")
                return cleaned_text
                
        return response_text

    def _convert_agent_info_to_dict(self, agents_info: list[conversation.AgentInfo]) -> dict[str, str]:
        r = {}
        entity_id_to_name = {}
        name_to_entity_id = {}
        
        for entity_id in self.hass.states.async_entity_ids("conversation"):
            state = self.hass.states.get(entity_id)
            if state:
                friendly_name = state.attributes.get("friendly_name", entity_id.split('.')[-1])
                entity_id_to_name[entity_id] = friendly_name
                name_to_entity_id[friendly_name] = entity_id
        
        for agent_info in agents_info:
            try:
                agent = conversation.agent_manager.async_get_agent(self.hass, agent_info.id)
                agent_id = agent_info.id
                
                if hasattr(agent, "registry_entry"):
                    agent_id = agent.registry_entry.entity_id
                
                if agent_id in entity_id_to_name:
                    r[agent_id] = entity_id_to_name[agent_id]
                elif agent_info.name in name_to_entity_id:
                    entity_id = name_to_entity_id[agent_info.name]
                    r[entity_id] = agent_info.name
                else:
                    r[agent_id] = agent_info.name
            except Exception as e:
                _LOGGER.info(f"获取代理信息失败: {agent_info.id} - {e}")
                if agent_info.name in name_to_entity_id:
                    entity_id = name_to_entity_id[agent_info.name]
                    r[entity_id] = agent_info.name
                else:
                    r[agent_info.id] = agent_info.name
                
        return r

    async def _process_summary_request(self, user_input, agent_manager, agents, agent_names, default_agent, conversation_mode):
        
        _LOGGER.info("处理总结请求")
        
        
        if not agents:
            intent_response = intent.IntentResponse(language=user_input.language)
            response_text = "请配置至少一个对话代理。"
            intent_response.async_set_speech(response_text)
            result = conversation.ConversationResult(
                conversation_id=user_input.conversation_id,
                response=intent_response
            )
            self._attr_chat_response = response_text
            self.async_write_ha_state()
            asyncio.create_task(self._call_speaker_service(response_text))
            return result
        
        
        summary_agent_id = agents[0]
        if not isinstance(summary_agent_id, str):
            summary_agent_id = conversation.const.HOME_ASSISTANT_AGENT if (hasattr(summary_agent_id, "__class__") and summary_agent_id.__class__.__name__ == "DefaultAgent") else (str(summary_agent_id) if hasattr(summary_agent_id, "__str__") else conversation.const.HOME_ASSISTANT_AGENT)
        
        summary_agent_name = agent_names.get(summary_agent_id, "UNKNOWN")
        if summary_agent_id == conversation.const.HOME_ASSISTANT_AGENT:
            summary_agent_name = default_agent.name
        
        try:
            _LOGGER.info(f"使用代理 '{summary_agent_name}' 处理总结请求")
            
            
            cleaned_text = await self._content_processor.clean_text_for_api(user_input.text)
            
            
            device_id = getattr(user_input, "device_id", None)
            original_context = getattr(user_input, "context", {})
            
            processed_input = conversation.ConversationInput(
                text=cleaned_text,
                conversation_id=user_input.conversation_id,
                language=user_input.language,
                context=original_context,
                device_id=device_id,
                agent_id=summary_agent_id
            )
            
            
            result = await self._async_process_agent(
                agent_manager,
                summary_agent_id,
                summary_agent_name,
                processed_input,
                CONVERSATION_MODE_NO_NAME,
                None,
            )
            
            
            if result and result.response and result.response.speech and 'plain' in result.response.speech:
                response_text = result.response.speech['plain'].get('original_speech', result.response.speech['plain'].get('speech', "")).strip()
                
                
                if conversation_mode == CONVERSATION_MODE_NO_NAME:
                    result.response.speech['plain']['speech'] = response_text
                elif conversation_mode == CONVERSATION_MODE_ADD_NAME:
                    result.response.speech['plain']['speech'] = f"({summary_agent_name}) 回复: {response_text}"
                elif conversation_mode == CONVERSATION_MODE_DETAILED:
                    result.response.speech['plain']['speech'] = f"({summary_agent_name}) 回复: {response_text}"
                
                
                self._attr_chat_response = result.response.speech['plain']['speech']
                self.async_write_ha_state()
                asyncio.create_task(self._call_speaker_service(result.response.speech['plain']['speech']))
                return result
            
        except Exception as e:
            _LOGGER.error(f"处理总结请求时出错: {e}", exc_info=True)
        
        
        intent_response = intent.IntentResponse(language=user_input.language)
        response_text = "处理总结请求失败，请稍后重试。"
        intent_response.async_set_speech(response_text)
        result = conversation.ConversationResult(
            conversation_id=user_input.conversation_id,
            response=intent_response
        )
        self._attr_chat_response = response_text
        self.async_write_ha_state()
        asyncio.create_task(self._call_speaker_service(response_text))
        return result