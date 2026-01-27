from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent
from .const import DOMAIN
from homeassistant.components import conversation
from homeassistant.util import ulid
from home_assistant_intents import get_languages
from homeassistant.components.conversation.agent_manager import async_get_agent
from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT

LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = (Platform.CONVERSATION,)
DATA_AGENT = "agent"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    from .services.ai_skills import async_setup_skills
    from .tools.llm_hook import async_setup_llm_hook
    await async_setup_skills(hass)
    await async_setup_llm_hook(hass)
    _install_conversation_hook(hass, entry)
    _setup_ai_coordinator(hass, entry)
    await _register_frontend(hass)
    LOGGER.info("HA Crack initialized with 10 intents + 23 LLM tools + AI Coordinator + Frontend")
    return True

async def _register_frontend(hass: HomeAssistant) -> None:

    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.websocket_api import async_register_command, websocket_command
    import voluptuous as vol
    import os
    
    www_path = os.path.join(os.path.dirname(__file__), "frontend")
    import time
    js_url = f"/ha_crack_static/ha_crack.js?v={int(time.time())}"
    
    await hass.http.async_register_static_paths([
        StaticPathConfig("/ha_crack_static", www_path, cache_headers=False)
    ])
    
    add_extra_js_url(hass, js_url)
    
    pending_js = hass.data.setdefault("ha_crack_pending_js", [])
    
    @websocket_command({vol.Required("type"): "ha_crack/get_pending_js"})
    def ws_get_pending_js(hass, connection, msg):
        js_list = list(pending_js)
        pending_js.clear()
        connection.send_result(msg["id"], {"js_codes": js_list})
    
    async_register_command(hass, ws_get_pending_js)
    
    frontend_state = hass.data.setdefault("ha_crack_frontend_state", {})
    
    @websocket_command({
        vol.Required("type"): "ha_crack/report_state",
        vol.Required("data"): dict
    })
    def ws_report_state(hass, connection, msg):
        frontend_state.update(msg["data"])
        frontend_state["_updated"] = True
        connection.send_result(msg["id"], {"success": True})
    
    async_register_command(hass, ws_report_state)
    
    @websocket_command({vol.Required("type"): "ha_crack/get_frontend_state"})
    def ws_get_frontend_state(hass, connection, msg):
        connection.send_result(msg["id"], frontend_state)
    
    async_register_command(hass, ws_get_frontend_state)
    
    LOGGER.info(f"HA Crack 前端资源已自动加载: {js_url}")

def _setup_ai_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> None:

    from .const import CONF_FALLBACK_AGENT
    
    if hass.data.get("ha_crack_coordinator_installed"):
        return
    
    async def handle_ai_response(event):

        data = event.data
        response_text = data.get("response", "")
        user_request = data.get("user_request", "")
        conversation_id = data.get("conversation_id")
        iteration = data.get("iteration", 0)
        
        should_end_flag = hass.data.setdefault("ha_crack_should_end_flag", {"value": False})
        task_loop = hass.data.get("ha_crack_task_loop", {})
        history = task_loop.get("history", [])
        
        LOGGER.debug(f"AI协调器：收到响应事件，iteration={iteration}, history_len={len(history)}")
        
        if iteration < 2:
            LOGGER.debug("AI协调器：迭代次数<2，继续执行")
            return
        
        if task_loop.get("waiting_choice"):
            LOGGER.debug("AI协调器：正在等待用户选择，跳过重复检测")
            return
        
        if len(history) >= 3:
            last_responses = [h.get("content", "") for h in history[-4:] if h.get("role") == "assistant"]
            if len(last_responses) >= 2:
                from difflib import SequenceMatcher
                prev = last_responses[-2] if len(last_responses) >= 2 else ""
                if prev and SequenceMatcher(None, prev[:300], response_text[:300]).ratio() > 0.85:
                    LOGGER.info("AI协调器：检测到高度重复回复(>85%)，设置结束标志")
                    should_end_flag["value"] = True
                    hass.bus.async_fire("ha_crack_should_end", {
                        "reason": "duplicate_response",
                        "conversation_id": conversation_id
                    })
                    return
        
        complete_indicators = ["综上所述", "希望对您有帮助", "以上就是", "总结如下"]
        if any(ind in response_text for ind in complete_indicators) and len(response_text) > 200:
            LOGGER.info("AI协调器：检测到明确完成指示词，设置结束标志")
            should_end_flag["value"] = True
            hass.bus.async_fire("ha_crack_should_end", {
                "reason": "complete_indicator",
                "conversation_id": conversation_id
            })
            return
        
        if iteration >= 50:
            LOGGER.info(f"AI协调器：迭代次数达到{iteration}，设置结束标志")
            should_end_flag["value"] = True
            hass.bus.async_fire("ha_crack_should_end", {
                "reason": "max_iteration",
                "conversation_id": conversation_id
            })
    
    hass.bus.async_listen("ha_crack_ai_response", handle_ai_response)
    hass.data["ha_crack_coordinator_installed"] = True
    LOGGER.info("AI协调器已安装：第二个AI将监听第一个AI的响应")

def _install_conversation_hook(hass: HomeAssistant, entry: ConfigEntry) -> None:

    from homeassistant.components.conversation import agent_manager
    from homeassistant.components.conversation import http as conv_http
    from homeassistant.components import conversation as conv_module
    from homeassistant.components.conversation import chat_log as chat_log_module
    from .const import (
        CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT,
        CONF_ERROR_RESPONSES, CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME
    )
    
    if hass.data.get("ha_crack_hook_installed"):
        return
    
    
    hass.data.setdefault("ha_crack_task_loop", {
        "active": False,
        "iteration": 0,
        "max_iterations": 50,
        "conversation_id": None,
        "pending_feedback": None,
        "history": [],
        "waiting_choice": False,
        "last_choice": None
    })
    
    async def handle_choice_selected(event):

        choice_id = event.data.get("choice_id")
        choice_label = event.data.get("choice_label")
        task_loop = hass.data.get("ha_crack_task_loop", {})
        
        task_loop["waiting_choice"] = False
        task_loop["last_choice"] = {"id": choice_id, "label": choice_label}
        LOGGER.info(f"收到用户选择事件: {choice_label} (id={choice_id})")
        
        pending = hass.data.get("ha_crack_pending_js", [])
        hass.data["ha_crack_pending_js"] = [js for js in pending if "showChoices" not in js]
    
    hass.bus.async_listen("ha_crack_choice_selected", handle_choice_selected)
    
    hass.data.setdefault("ha_crack_active_conversation", {"id": None})
    
    original_async_converse = agent_manager.async_converse
    
    def is_user_done(text: str) -> bool:

        from .conversation_utils import detect_user_ending_intent
        
        continue_keywords = ["继续", "还要", "再", "另外", "还有", "接着", "然后", "下一步", "不对", "错了", "重新", "?", "？"]
        text_lower = text.lower().strip()
        
        if any(k in text_lower for k in continue_keywords):
            return False
        
        return detect_user_ending_intent(text)
    
    def parse_choices_from_response(response_text: str) -> list:

        import re
        match = re.search(r'\[([^\]]+\|[^\]]+)\]', response_text)
        if match:
            options_str = match.group(1)
            options = [opt.strip() for opt in options_str.split('|') if opt.strip()]
            return [{"id": f"opt_{i}", "label": opt} for i, opt in enumerate(options)]
        return []
    
    def inject_choices_from_response(choices: list, title: str = "请选择"):

        import json
        js_code = f"HACrack.showChoices('{title}', {json.dumps(choices, ensure_ascii=False)})"
        pending = hass.data.setdefault("ha_crack_pending_js", [])
        pending.append(js_code)
        LOGGER.info(f"已注入{len(choices)}个选项供用户选择")
    
    def should_continue_loop(result, text: str) -> bool:
        task_loop = hass.data.get("ha_crack_task_loop", {})
        if task_loop.get("iteration", 0) >= task_loop.get("max_iterations", 30):
            return False
        if is_user_done(text):
            return False
        return True
    
    def analyze_response_state(response_text: str, history: list, hass_data: dict = None) -> dict:
        """ReAct风格的响应状态分析
        
        返回:
        - state: 'final' | 'need_action' | 'need_user' | 'wait_choice' | 'continue'
        - reason: 判断原因
        """
        
        if hass_data:
            ha_crack_data = hass_data.get("ha_crack", {})
            if "expecting_response" in ha_crack_data:
                expecting = ha_crack_data.pop("expecting_response")
                reason = ha_crack_data.pop("conversation_state_reason", "")
                if not expecting:
                    return {"state": "final", "reason": f"LLM明确表示任务完成: {reason}"}
                else:
                    return {"state": "need_user", "reason": f"LLM等待用户回复: {reason}", "continue": True}
        
        if hass_data and hass_data.get("ha_crack_tool_called"):
            hass_data["ha_crack_tool_called"] = False
            last_tool = hass_data.get("ha_crack_last_tool", "")
            if last_tool in ["WebSearch", "NewsSearch", "DeepWebSearch", "ZhihuHot", "SetConversationState"]:
                return {"state": "final", "reason": f"工具{last_tool}已调用，直接终止"}
        
        search_result_indicators = [
            "搜索结果", "根据搜索", "查询结果", "以下是", "找到了", "获取到",
            "新闻", "热榜", "来源:", "链接:", "http://", "https://",
            "根据以上", "综合以上", "根据网络", "根据查询",
            "天气", "温度", "气温", "湿度", "风力", "如下："
        ]
        if any(ind in response_text for ind in search_result_indicators):
            return {"state": "final", "reason": "包含搜索/查询结果，直接终止"}
        
        final_indicators = [
            "综上所述", "希望对您有帮助", "以上就是", "总结如下", "以上是我的回答",
            "已完成", "已执行", "已帮您", "操作成功", "已为您"
        ]
        if any(ind in response_text for ind in final_indicators) and len(response_text) > 100:
            return {"state": "final", "reason": "检测到完成指示词"}
        
        waiting_user_indicators = [
            "请告诉我", "请提供", "请问您", "您希望", "您想要", "需要您提供",
            "请指定", "请说明", "请确认", "您可以告诉我", "等待您的",
            "请输入", "请选择", "您需要", "请描述", "告诉我您的",
            "需要更多信息", "请问", "您想", "您要"
        ]
        if any(ind in response_text for ind in waiting_user_indicators):
            return {"state": "need_user", "reason": "AI需要用户提供更多信息", "continue": True}
        
        action_indicators = [
            "正在查询", "正在搜索", "正在执行", "让我", "我来", "稍等",
            "正在处理", "正在获取", "我需要先"
        ]
        if any(ind in response_text for ind in action_indicators) and len(response_text) < 200:
            return {"state": "need_action", "reason": "AI正在执行操作，可能需要继续"}
        
        if len(history) >= 2:
            last_responses = [h.get("content", "") for h in history[-3:] if h.get("role") == "assistant"]
            if len(last_responses) >= 2:
                from difflib import SequenceMatcher
                prev = last_responses[-2] if len(last_responses) >= 2 else ""
                if prev and SequenceMatcher(None, prev[:300], response_text[:300]).ratio() > 0.85:
                    return {"state": "final", "reason": "检测到重复回复，强制终止"}
        
        if len(response_text) > 300:
            return {"state": "final", "reason": "回复足够长，视为完成"}
        
        return {"state": "continue", "reason": "需要继续思考"}
    
    def is_response_complete(response_text: str, history: list) -> bool:

        state = analyze_response_state(response_text, history, hass.data)
        return state["state"] in ("final", "need_user")
    
    async def hooked_async_converse(
        hass: HomeAssistant,
        text: str,
        conversation_id,
        context,
        language=None,
        agent_id=None,
        device_id=None,
        satellite_id=None,
        extra_system_prompt=None,
    ):
        from .services.ai_skills import CURRENT_ROLE
        from homeassistant.helpers import entity_registry as er
        
        task_loop = hass.data.get("ha_crack_task_loop", {})
        
        if conversation_id and task_loop.get("conversation_id") != conversation_id:
            hass.data["ha_crack_task_loop"] = {
                "active": True,
                "iteration": 0,
                "max_iterations": 50,
                "conversation_id": conversation_id,
                "pending_feedback": None,
                "history": []
            }
            task_loop = hass.data["ha_crack_task_loop"]
        
        task_loop["iteration"] = task_loop.get("iteration", 0) + 1
        task_loop["history"].append({"role": "user", "content": text})
        LOGGER.info(f"Task loop iteration {task_loop['iteration']}: {text[:50]}...")
        
        active_conv = hass.data.get("ha_crack_active_conversation", {})
        if conversation_id and active_conv.get("id") != conversation_id:
            LOGGER.info(f"检测到新对话: {conversation_id[:20]}...")
            task_loop["waiting_choice"] = False
            active_conv["id"] = conversation_id
        
        options = entry.options
        fallback_agents = []
        if options.get(CONF_PRIMARY_AGENT):
            fallback_agents.append(options.get(CONF_PRIMARY_AGENT))
        if options.get(CONF_FALLBACK_AGENT):
            fallback_agents.append(options.get(CONF_FALLBACK_AGENT))
        if options.get(CONF_SECONDARY_FALLBACK_AGENT):
            fallback_agents.append(options.get(CONF_SECONDARY_FALLBACK_AGENT))
        
        error_responses = options.get(CONF_ERROR_RESPONSES, "")
        error_keywords = [kw.strip() for kw in error_responses.split(",") if kw.strip()] if error_responses else []
        conversation_mode = options.get(CONF_CONVERSATION_MODE, CONVERSATION_MODE_ADD_NAME)
        
        last_conv_id = hass.data.get("ha_crack_last_conversation_id")
        if conversation_id and last_conv_id and conversation_id != last_conv_id:
            CURRENT_ROLE["role"] = None
            CURRENT_ROLE["prompt"] = None
            hass.data["ha_crack_roleplay"] = {"role": None, "prompt": None}
            LOGGER.info("New conversation, roleplay cleared")
        hass.data["ha_crack_last_conversation_id"] = conversation_id
        
        original_text = text
        
        from .const import HASS_LLM_SYSTEM_PROMPT
        from .tools.llm_hook import CUSTOM_API_ID
        
        base_prompt = HASS_LLM_SYSTEM_PROMPT
        
        if CURRENT_ROLE.get("role"):
            role = CURRENT_ROLE.get("role")
            prompt = CURRENT_ROLE.get("prompt", "")
            roleplay_prompt = f"你现在扮演{role}。{prompt}。请用这个角色的语气和风格回复用户，不要提及你在扮演角色。"
            base_prompt = f"{base_prompt}\n\n{roleplay_prompt}"
            LOGGER.debug(f"Roleplay injected: {role}")
        
        global_inject = hass.data.get("ha_crack_global", {}).get("inject", "")
        if global_inject:
            base_prompt = f"{base_prompt}\n\n[全局上下文]{global_inject}"
        
        output_mode = hass.data.get("ha_crack_output", {}).get("mode", "")
        if output_mode:
            mode_prompts = {
                "brief": "回复要简洁，不超过50字",
                "detailed": "回复要详细，包含完整信息",
                "list": "用列表格式回复",
                "code": "用代码格式回复"
            }
            if output_mode in mode_prompts:
                base_prompt = f"{base_prompt}\n\n[输出模式]{mode_prompts[output_mode]}"
        
        if extra_system_prompt:
            extra_system_prompt = f"{base_prompt}\n\n{extra_system_prompt}"
        else:
            extra_system_prompt = base_prompt
        
        if not fallback_agents:
            return await original_async_converse(
                hass, text, conversation_id, context, language,
                agent_id, device_id, satellite_id, extra_system_prompt
            )
        
        ent_reg = er.async_get(hass)
        
        def get_agent_name(aid):
            ent = ent_reg.async_get(aid)
            if ent and ent.name:
                return ent.name
            if ent and ent.original_name:
                return ent.original_name
            state = hass.states.get(aid)
            if state:
                return state.attributes.get("friendly_name", aid.split('.')[-1])
            return aid.split('.')[-1].replace('_', ' ').title()
        
        def is_error_response(result):
            if not result or not result.response:
                return True
            if result.response.response_type == intent.IntentResponseType.ERROR:
                return True
            if result.response.speech and 'plain' in result.response.speech:
                speech = result.response.speech['plain'].get('speech', '')
                for kw in error_keywords:
                    if kw in speech:
                        return True
            return False
        
        agent_index = 0
        for current_agent_id in fallback_agents:
            agent_index += 1
            try:
                result = await original_async_converse(
                    hass, text, conversation_id, context, language,
                    current_agent_id, device_id, satellite_id, extra_system_prompt
                )
                
                if is_error_response(result):
                    continue
                
                if result.response.speech and 'plain' in result.response.speech:
                    response_text = result.response.speech['plain'].get('original_speech',
                        result.response.speech['plain'].get('speech', '')).strip()
                    
                    if not response_text:
                        LOGGER.warning("首轮回复为空，跳过此agent")
                        continue
                    
                    agent_name = get_agent_name(current_agent_id)
                    task_loop["history"].append({"role": "assistant", "content": response_text})
                    
                    from .conversation_utils import get_conversation_history
                    conv_history = get_conversation_history()
                    conv_history.add_turn(
                        conversation_id or "default",
                        original_text,
                        response_text,
                        tool_calls=hass.data.get("ha_crack_tool_calls", []),
                    )
                    hass.data["ha_crack_tool_calls"] = []
                    
                    loop_count = 1
                    max_loops = 2 if agent_index == 1 else 1
                    all_feedback = [response_text]
                    should_end_flag = hass.data.setdefault("ha_crack_should_end_flag", {"value": False})
                    should_end_flag["value"] = False
                    
                    hass.bus.async_fire("ha_crack_ai_response", {
                        "response": response_text,
                        "user_request": text,
                        "conversation_id": conversation_id,
                        "iteration": loop_count,
                        "agent_id": current_agent_id
                    })
                    
                    LOGGER.info(f"AI回复: {response_text[:100]}...")
                    
                    state = analyze_response_state(response_text, task_loop.get("history", []), hass.data)
                    LOGGER.info(f"首轮状态分析: {state}")
                    
                    if state["state"] == "final":
                        LOGGER.info(f"首轮即终止: {state['reason']}")
                        loop_count = max_loops
                    elif state["state"] == "need_user":
                        result.continue_conversation = True
                        LOGGER.info(f"AI询问用户，设置continue_conversation=True")
                        loop_count = max_loops
                    
                    display_text = response_text
                    
                    hass.data["ha_crack_current_thought"] = None
                    
                    if conversation_mode == "no_name":
                        result.response.speech['plain']['speech'] = display_text
                    elif conversation_mode == "add_name":
                        result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {display_text}"
                    elif conversation_mode == "detailed":
                        result.response.speech['plain']['speech'] = f"({agent_name}) 回复: {display_text}"
                    result.response.speech['plain']['original_speech'] = response_text
                    result.response.speech['plain']['agent_name'] = agent_name
                    result.response.speech['plain']['agent_id'] = current_agent_id
                    
                    hass.data["ha_crack_current_thought"] = None
                    
                    result.continue_conversation = not is_user_done(text)
                    
                LOGGER.info(f"Agent {current_agent_id} succeeded after {loop_count} think loops")
                return result
            except Exception as e:
                err_msg = str(e)
                if "content parts are required" in err_msg:
                    LOGGER.debug(f"Agent {current_agent_id}: Google AI SDK空响应（工具调用后无文本），尝试下一个agent")
                else:
                    import traceback
                    LOGGER.warning(f"Agent {current_agent_id} failed: {e}\n{traceback.format_exc()}")
                continue
        
        intent_response = intent.IntentResponse(language=language or hass.config.language)
        intent_response.async_set_error(
            intent.IntentResponseErrorCode.UNKNOWN,
            "所有AI代理都无法响应，请稍后重试。"
        )
        from homeassistant.components.conversation import ConversationResult
        return ConversationResult(
            response=intent_response,
            conversation_id=conversation_id
        )
    
    agent_manager.async_converse = hooked_async_converse
    conv_http.async_converse = hooked_async_converse
    conv_module.async_converse = hooked_async_converse
    hass.data["ha_crack_hook_installed"] = True
    LOGGER.info("Conversation hook installed to agent_manager, http, and conversation module")

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    return True

async def async_migrate_entry(hass, config_entry: ConfigEntry):
    if config_entry.version == 1:
        return False

    return True
