from __future__ import annotations
import logging
from dataclasses import dataclass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm

from .search_tools import (
    WebSearchTool, UrlFetchTool, NewsSearchTool, DeepWebSearchTool, ZhihuHotTool, StockQueryTool
)
from .ha_tools import (
    GetSystemIndexTool, SetConversationStateTool, ValidateServiceTool, ServiceHelpTool,
    SmartDiscoveryTool, EntityQueryTool, ServiceCallTool, GetLiveContextTool, ListServicesTool, 
    AutomationTool, ScriptExecuteTool, HistoryQueryTool, AreaDevicesTool, 
    BatchControlTool, NotifyTool, FireEventTool, InjectJSTool, HAControlTool, FrontendControlTool,
    HACSTool
)
from .misc_tools import (
    AgentLoopTool, RolePlayTool, ExecutePythonTool, SystemControlTool,
    ConversationMemoryTool, TextCompressTool, ThinkContinueTool, ParallelToolCallTool,
    GetConversationHistoryTool, ExecuteChainTool, AnalyzeIntentTool
)

_LOGGER = logging.getLogger(__name__)

CUSTOM_API_ID = "ha_crack_enhanced"


@dataclass(slots=True, kw_only=True)
class EnhancedAPI(llm.API):
    id: str = CUSTOM_API_ID
    name: str = "HA Crack Enhanced API"

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        from ..const import HASS_LLM_SYSTEM_PROMPT
        from datetime import datetime, timezone, timedelta
        
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        current_datetime = now.strftime("今天是 %Y年%m月%d日 %A，当前时间 %H:%M:%S (北京时间)")
        
        tools = [
            ExecuteChainTool(), AnalyzeIntentTool(),
            GetSystemIndexTool(), SetConversationStateTool(), ValidateServiceTool(), ServiceHelpTool(),
            SmartDiscoveryTool(), GetConversationHistoryTool(), StockQueryTool(), WebSearchTool(), 
            EntityQueryTool(), ServiceCallTool(), NotifyTool(), AgentLoopTool(), GetLiveContextTool(), 
            UrlFetchTool(), NewsSearchTool(), RolePlayTool(), ExecutePythonTool(), DeepWebSearchTool(), 
            SystemControlTool(), AutomationTool(), ScriptExecuteTool(), ZhihuHotTool(), HistoryQueryTool(), 
            AreaDevicesTool(), BatchControlTool(), ConversationMemoryTool(), TextCompressTool(), FireEventTool(), 
            ThinkContinueTool(), ParallelToolCallTool(), ListServicesTool(), InjectJSTool(), FrontendControlTool(),
            HACSTool()
        ]
        prompt = f"""{HASS_LLM_SYSTEM_PROMPT.format(current_datetime=current_datetime)}


## 核心工具（优先使用）
- **ExecuteChain**: 智能工具链执行器，自动组合多个工具完成复杂任务
- **AnalyzeIntent**: 分析用户意图，推荐最佳工具链（不执行，只建议）

## 设备控制
- SmartDiscovery: 智能发现实体(按区域/域/模式/人员)
- ServiceCall: 调用HA服务控制设备
- BatchControl: 批量控制多个设备
- GetLiveContext: 获取实体状态列表

## 状态查询
- EntityQuery: 查询单个实体状态
- AreaDevices: 获取区域内所有设备
- HistoryQuery: 查询实体历史状态
- GetSystemIndex: 获取系统结构索引

## 信息搜索
- StockQuery: 股票/基金行情（禁止用WebSearch查股票！）
- NewsSearch: 财经新闻快讯
- WebSearch: 联网搜索实时信息
- DeepWebSearch: 深度搜索提取内容

## 系统管理
- Automation: 管理自动化
- HACS: HACS商店(必须先github_search再install)
- ExecutePython: Python脚本(创建传感器/复杂计算)

## 前端操作
- FrontendControl: 前端控制(导航/点击/填充)
- InjectJS: 注入JavaScript代码


## 思考模式（必须执行！）

**每次收到用户请求，必须先调用 ThinkContinue 记录思考过程！**

思考流程：
1. **ThinkContinue** → 记录你的分析思考（这会显示在UI对话框中）
2. **选择工具链** → 根据意图选择合适的工具组合
3. **执行工具** → 按工具链顺序执行
4. **回复用户** → 简洁告知结果

ThinkContinue 示例：
- 用户说"打开客厅灯" → thought="用户要控制客厅的灯，我需要先用SmartDiscovery找到客厅的灯实体，然后用ServiceCall打开"
- 用户说"茅台股价" → thought="用户查询股票，必须用StockQuery工具，禁止WebSearch"
- 用户说"你好" → thought="用户打招呼，我应该友好回应"

**重要：thought是你的思考过程，不是给用户的最终回复！**


**必须使用ExecutePython的场景：**
- 创建/修改传感器 → `hass.states.async_set('sensor.xxx','值',{'friendly_name':'名称'})`
- 统计分析实体 → `result = {d:len([s for s in hass.states.async_all() if s.domain==d]) for d in set(s.domain for s in hass.states.async_all())}`
- 查找离线设备 → `result = [s.entity_id for s in hass.states.async_all() if s.state=='unavailable']`
- 探索系统数据 → `result = list(hass.data.keys())[:30]`
- 访问注册表 → `er = hass.data['entity_registry']; result = [(e.entity_id,e.platform) for e in list(er.entities.values())[:20]]`

**示例场景：**
用户说"创建传感器" → 立即用ExecutePython: `hass.states.async_set('sensor.ai_test',datetime.datetime.now().strftime('%H:%M'),{'friendly_name':'AI测试'}); result='已创建'`
用户说"分析设备" → 用ExecutePython统计+筛选离线设备
用户说"探索系统" → 用ExecutePython查看hass.data


1. **股票行情必须用StockQuery**（禁止WebSearch）
2. 控制设备前先用GetLiveContext查询实体
3. 不确定服务参数时用ServiceHelp查询
4. 禁止编造数据，必须调用工具获取
5. 复杂数据处理优先用ExecutePython
"""
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=tools)


_unregister_api = None


@callback
def async_register_enhanced_api(hass: HomeAssistant) -> None:
    global _unregister_api
    if _unregister_api:
        return
    try:
        api = EnhancedAPI(hass=hass)
        _unregister_api = llm.async_register_api(hass, api)
        _LOGGER.info(f"Registered enhanced LLM API: {CUSTOM_API_ID}")
    except Exception as e:
        _LOGGER.error(f"Failed to register enhanced LLM API: {e}")


@callback
def async_unregister_enhanced_api() -> None:
    global _unregister_api
    if _unregister_api:
        _unregister_api()
        _unregister_api = None
        _LOGGER.info("Unregistered enhanced LLM API")


async def async_setup_llm_hook(hass: HomeAssistant) -> None:
    async_register_enhanced_api(hass)
    hass.data.setdefault("ha_crack", {})["llm_api_id"] = CUSTOM_API_ID
    _patch_assist_api_prompt(hass)


def _patch_assist_api_prompt(hass: HomeAssistant) -> None:
    from homeassistant.helpers import llm as llm_module
    from ..const import HASS_LLM_SYSTEM_PROMPT
    
    if hasattr(llm_module, '_ha_crack_patched'):
        return
    
    original_get_api_prompt = llm_module.AssistAPI._async_get_api_prompt
    original_get_tools = llm_module.AssistAPI._async_get_tools
    
    @callback
    def patched_get_api_prompt(self, llm_context, exposed_entities):
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=8))
        now = datetime.now(tz)
        current_datetime = now.strftime("今天是 %Y年%m月%d日 %A，当前时间 %H:%M:%S (北京时间)")
        
        ha_crack_prompt = f"""{HASS_LLM_SYSTEM_PROMPT.format(current_datetime=current_datetime)}
"""
        original_prompt = original_get_api_prompt(self, llm_context, exposed_entities)
        
        return ha_crack_prompt + "\n" + original_prompt
    
    @callback
    def patched_get_tools(self, llm_context, exposed_entities):
        original_tools = original_get_tools(self, llm_context, exposed_entities)
        original_names = {t.name for t in original_tools}
        
        ha_crack_tools = [
            ExecuteChainTool(),
            AnalyzeIntentTool(),
            ServiceCallTool(),
            EntityQueryTool(),
            GetLiveContextTool(),
            ListServicesTool(),
            HistoryQueryTool(),
            AreaDevicesTool(),
            BatchControlTool(),
            AutomationTool(),
            ScriptExecuteTool(),
            NotifyTool(),
            FireEventTool(),
            StockQueryTool(),
            WebSearchTool(),
            UrlFetchTool(),
            NewsSearchTool(),
            DeepWebSearchTool(),
            ZhihuHotTool(),
            ExecutePythonTool(),
            SystemControlTool(),
            ConversationMemoryTool(),
            TextCompressTool(),
            ThinkContinueTool(),
            ParallelToolCallTool(),
            AgentLoopTool(),
            RolePlayTool(),
            InjectJSTool(),
            HAControlTool(),
            FrontendControlTool(),
            HACSTool(),
        ]
        
        unique_tools = [t for t in ha_crack_tools if t.name not in original_names]
        _LOGGER.info(f"注入 {len(unique_tools)} 个工具到AssistAPI")
        
        return unique_tools + original_tools
    
    llm_module.AssistAPI._async_get_api_prompt = patched_get_api_prompt
    llm_module.AssistAPI._async_get_tools = patched_get_tools
    llm_module._ha_crack_patched = True
    _LOGGER.info("已彻底劫持AssistAPI系统提示词和工具列表")
