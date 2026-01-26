
from __future__ import annotations
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum, auto

_LOGGER = logging.getLogger(__name__)


class IntentCategory(Enum):
    DEVICE_CONTROL = auto()
    DEVICE_QUERY = auto()
    BATCH_CONTROL = auto()
    AREA_QUERY = auto()
    HISTORY_QUERY = auto()
    STOCK_QUERY = auto()
    NEWS_SEARCH = auto()
    WEB_SEARCH = auto()
    DEEP_SEARCH = auto()
    SYSTEM_MANAGE = auto()
    AUTOMATION_MANAGE = auto()
    HACS_INSTALL = auto()
    FRONTEND_NAVIGATE = auto()
    FRONTEND_INTERACT = auto()
    CREATE_ENTITY = auto()
    PYTHON_COMPUTE = auto()
    ROLEPLAY = auto()
    GENERAL_CHAT = auto()


@dataclass
class ToolChain:

    name: str
    category: IntentCategory
    description: str
    tools: List[str]
    keywords: List[str]
    patterns: List[str] = field(default_factory=list)
    priority: int = 0
    requires_discovery: bool = False
    example: str = ""


TOOL_CHAINS: List[ToolChain] = [
    ToolChain(
        name="single_device_control",
        category=IntentCategory.DEVICE_CONTROL,
        description="控制单个设备（开/关/调节）",
        tools=["SmartDiscovery", "ServiceCall"],
        keywords=["打开", "关闭", "开灯", "关灯", "开关", "调", "设置", "调节", "调到", "调成"],
        patterns=[r"(打开|关闭|开|关)(.*?)(灯|空调|风扇|窗帘|开关|插座)"],
        priority=10,
        requires_discovery=True,
        example="打开客厅灯 → SmartDiscovery找实体 → ServiceCall控制",
    ),
    ToolChain(
        name="batch_device_control",
        category=IntentCategory.BATCH_CONTROL,
        description="批量控制多个设备",
        tools=["GetLiveContext", "BatchControl"],
        keywords=["所有", "全部", "都", "一起", "批量"],
        patterns=[r"(关闭|打开)(所有|全部|所有的)(灯|空调|设备)"],
        priority=15,
        requires_discovery=True,
        example="关闭所有灯 → GetLiveContext获取所有灯 → BatchControl批量关闭",
    ),
    ToolChain(
        name="device_state_query",
        category=IntentCategory.DEVICE_QUERY,
        description="查询设备当前状态",
        tools=["SmartDiscovery", "EntityQuery"],
        keywords=["状态", "怎么样", "开着吗", "关着吗", "是多少", "多少度", "温度", "湿度"],
        patterns=[r"(.*?)(开着|关着|状态|怎么样|是多少|多少度)"],
        priority=8,
        requires_discovery=True,
        example="客厅温度多少 → SmartDiscovery找温度传感器 → EntityQuery查状态",
    ),
    ToolChain(
        name="area_devices_query",
        category=IntentCategory.AREA_QUERY,
        description="查询区域内的设备",
        tools=["AreaDevices"],
        keywords=["有哪些", "有什么", "设备列表", "区域"],
        patterns=[r"(.*?)(有哪些|有什么)(设备|灯|传感器)"],
        priority=5,
        example="卧室有哪些设备 → AreaDevices",
    ),
    ToolChain(
        name="history_trend_query",
        category=IntentCategory.HISTORY_QUERY,
        description="查询历史状态和趋势",
        tools=["SmartDiscovery", "HistoryQuery"],
        keywords=["历史", "趋势", "变化", "过去", "之前", "记录"],
        patterns=[r"(.*?)(历史|趋势|变化|过去|记录)"],
        priority=7,
        requires_discovery=True,
        example="温度变化趋势 → SmartDiscovery找传感器 → HistoryQuery查历史",
    ),
    ToolChain(
        name="stock_query",
        category=IntentCategory.STOCK_QUERY,
        description="查询股票/基金行情（禁止用WebSearch）",
        tools=["StockQuery"],
        keywords=["股票", "股价", "行情", "A股", "美股", "港股", "基金", "涨跌", "茅台", "特斯拉", "苹果", "腾讯", "阿里"],
        patterns=[r"(.*?)(股票|股价|行情|涨|跌|基金)"],
        priority=20,
        example="茅台股价 → StockQuery（禁止WebSearch）",
    ),
    ToolChain(
        name="news_search",
        category=IntentCategory.NEWS_SEARCH,
        description="获取财经新闻快讯",
        tools=["NewsSearch"],
        keywords=["新闻", "快讯", "财经", "资讯", "消息", "头条"],
        patterns=[r"(今天|最新|最近)?(新闻|快讯|消息|资讯)"],
        priority=12,
        example="今天有什么新闻 → NewsSearch",
    ),
    ToolChain(
        name="web_search",
        category=IntentCategory.WEB_SEARCH,
        description="联网搜索实时信息",
        tools=["WebSearch"],
        keywords=["搜索", "搜一下", "查一下", "天气", "百度", "谷歌"],
        patterns=[r"(搜索|搜一下|查一下|联网)"],
        priority=5,
        example="北京天气 → WebSearch",
    ),
    ToolChain(
        name="deep_web_search",
        category=IntentCategory.DEEP_SEARCH,
        description="深度搜索并提取网页内容",
        tools=["DeepWebSearch", "TextCompress"],
        keywords=["详细", "深入", "全面", "了解", "研究"],
        patterns=[r"(详细|深入|全面)(了解|搜索|查找)"],
        priority=8,
        example="详细了解某话题 → DeepWebSearch → TextCompress",
    ),
    ToolChain(
        name="system_overview",
        category=IntentCategory.SYSTEM_MANAGE,
        description="获取系统概览和统计",
        tools=["GetSystemIndex"],
        keywords=["系统", "概览", "统计", "有多少", "区域", "域"],
        patterns=[r"(系统|家里)(有多少|概览|统计|情况)"],
        priority=6,
        example="系统有多少设备 → GetSystemIndex",
    ),
    ToolChain(
        name="automation_manage",
        category=IntentCategory.AUTOMATION_MANAGE,
        description="管理自动化",
        tools=["Automation"],
        keywords=["自动化", "触发", "启用", "禁用", "自动"],
        patterns=[r"(自动化|触发|启用|禁用)(.*?)"],
        priority=10,
        example="触发回家自动化 → Automation(trigger)",
    ),
    ToolChain(
        name="hacs_install",
        category=IntentCategory.HACS_INSTALL,
        description="HACS安装集成（必须先搜索）",
        tools=["HACS"],
        keywords=["安装", "HACS", "集成", "插件", "商店"],
        patterns=[r"(安装|下载)(.*?)(集成|插件)"],
        priority=15,
        example="安装某集成 → HACS(github_search) → HACS(install)",
    ),
    ToolChain(
        name="frontend_navigate",
        category=IntentCategory.FRONTEND_NAVIGATE,
        description="导航到页面",
        tools=["FrontendControl"],
        keywords=["导航", "跳转", "打开页面", "去", "进入"],
        patterns=[r"(导航|跳转|打开|去|进入)(.*?)(页面|设置|配置)"],
        priority=8,
        example="打开设置页面 → FrontendControl(navigate)",
    ),
    ToolChain(
        name="frontend_interact",
        category=IntentCategory.FRONTEND_INTERACT,
        description="前端交互（点击/填充）",
        tools=["FrontendControl", "InjectJS"],
        keywords=["点击", "填写", "输入", "按钮"],
        patterns=[r"(点击|填写|输入)(.*?)(按钮|输入框)"],
        priority=10,
        example="点击保存按钮 → FrontendControl(get_clickables) → FrontendControl(click_by_text)",
    ),
    ToolChain(
        name="create_entity",
        category=IntentCategory.CREATE_ENTITY,
        description="创建/修改传感器实体",
        tools=["ExecutePython"],
        keywords=["创建", "新建", "添加", "传感器", "实体"],
        patterns=[r"(创建|新建|添加)(.*?)(传感器|实体)"],
        priority=15,
        example="创建传感器 → ExecutePython(hass.states.async_set)",
    ),
    ToolChain(
        name="python_compute",
        category=IntentCategory.PYTHON_COMPUTE,
        description="复杂计算和数据分析",
        tools=["ExecutePython"],
        keywords=["计算", "统计", "分析", "筛选", "过滤", "离线", "不可用"],
        patterns=[r"(计算|统计|分析|筛选|哪些.*离线|哪些.*不可用)"],
        priority=12,
        example="哪些设备离线 → ExecutePython筛选unavailable",
    ),
    ToolChain(
        name="roleplay",
        category=IntentCategory.ROLEPLAY,
        description="角色扮演",
        tools=["RolePlay"],
        keywords=["扮演", "假装", "模仿", "当", "变成"],
        patterns=[r"(扮演|假装|模仿|当|变成)(.*?)"],
        priority=5,
        example="扮演管家 → RolePlay",
    ),
]


class ToolChainSelector:

    
    def __init__(self):
        self._chains = sorted(TOOL_CHAINS, key=lambda x: -x.priority)
    
    def analyze_intent(self, user_input: str) -> Tuple[Optional[ToolChain], float]:

        user_input_lower = user_input.lower()
        best_chain: Optional[ToolChain] = None
        best_score = 0.0
        
        for chain in self._chains:
            score = self._calculate_match_score(user_input_lower, chain)
            if score > best_score:
                best_score = score
                best_chain = chain
        
        return best_chain, best_score
    
    def _calculate_match_score(self, text: str, chain: ToolChain) -> float:

        score = 0.0
        
        keyword_matches = sum(1 for kw in chain.keywords if kw in text)
        if keyword_matches > 0:
            score += keyword_matches * 10
        
        for pattern in chain.patterns:
            if re.search(pattern, text):
                score += 30
                break
        
        score += chain.priority * 0.5
        
        return score
    
    def get_chain_prompt(self, chain: ToolChain) -> str:

        tools_str = " → ".join(chain.tools)
        return f"【{chain.name}】{chain.description}\n工具链: {tools_str}\n示例: {chain.example}"
    
    def get_all_chains_summary(self) -> str:

        lines = ["## 工具链组合指南\n"]
        
        categories = {}
        for chain in self._chains:
            cat = chain.category.name
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(chain)
        
        category_names = {
            "DEVICE_CONTROL": "设备控制",
            "BATCH_CONTROL": "批量控制",
            "DEVICE_QUERY": "状态查询",
            "AREA_QUERY": "区域查询",
            "HISTORY_QUERY": "历史查询",
            "STOCK_QUERY": "股票查询",
            "NEWS_SEARCH": "新闻搜索",
            "WEB_SEARCH": "网页搜索",
            "DEEP_SEARCH": "深度搜索",
            "SYSTEM_MANAGE": "系统管理",
            "AUTOMATION_MANAGE": "自动化管理",
            "HACS_INSTALL": "HACS安装",
            "FRONTEND_NAVIGATE": "前端导航",
            "FRONTEND_INTERACT": "前端交互",
            "CREATE_ENTITY": "创建实体",
            "PYTHON_COMPUTE": "Python计算",
            "ROLEPLAY": "角色扮演",
        }
        
        for cat, chains in categories.items():
            cat_name = category_names.get(cat, cat)
            lines.append(f"\n### {cat_name}")
            for chain in chains:
                tools_str = " → ".join(chain.tools)
                lines.append(f"- **{chain.description}**: `{tools_str}`")
                if chain.example:
                    lines.append(f"  例: {chain.example}")
        
        return "\n".join(lines)
    
    def suggest_tools(self, user_input: str) -> Dict[str, Any]:

        chain, score = self.analyze_intent(user_input)
        
        if chain and score > 20:
            return {
                "matched": True,
                "chain_name": chain.name,
                "category": chain.category.name,
                "description": chain.description,
                "tools": chain.tools,
                "requires_discovery": chain.requires_discovery,
                "example": chain.example,
                "confidence": min(score / 100, 1.0),
            }
        
        return {
            "matched": False,
            "suggestion": "使用通用对话或尝试更具体的描述",
            "available_categories": list(set(c.category.name for c in self._chains)),
        }


_selector: Optional[ToolChainSelector] = None


def get_tool_chain_selector() -> ToolChainSelector:

    global _selector
    if _selector is None:
        _selector = ToolChainSelector()
    return _selector


def get_tool_chains_prompt() -> str:

    return get_tool_chain_selector().get_all_chains_summary()


class ToolChainExecutor:

    
    def __init__(self, hass, llm_context):
        self.hass = hass
        self.llm_context = llm_context
        self.selector = get_tool_chain_selector()
        self._tool_instances = {}
    
    def _get_tool_instance(self, tool_name: str):

        if tool_name in self._tool_instances:
            return self._tool_instances[tool_name]
        
        from .ha_tools import (
            SmartDiscoveryTool, EntityQueryTool, ServiceCallTool, GetLiveContextTool,
            BatchControlTool, AreaDevicesTool, HistoryQueryTool, AutomationTool,
            GetSystemIndexTool, FrontendControlTool, InjectJSTool, HACSTool
        )
        from .search_tools import (
            StockQueryTool, NewsSearchTool, WebSearchTool, DeepWebSearchTool
        )
        from .misc_tools import ExecutePythonTool, TextCompressTool
        
        tool_map = {
            "SmartDiscovery": SmartDiscoveryTool,
            "EntityQuery": EntityQueryTool,
            "ServiceCall": ServiceCallTool,
            "GetLiveContext": GetLiveContextTool,
            "BatchControl": BatchControlTool,
            "AreaDevices": AreaDevicesTool,
            "HistoryQuery": HistoryQueryTool,
            "Automation": AutomationTool,
            "GetSystemIndex": GetSystemIndexTool,
            "FrontendControl": FrontendControlTool,
            "InjectJS": InjectJSTool,
            "HACS": HACSTool,
            "StockQuery": StockQueryTool,
            "NewsSearch": NewsSearchTool,
            "WebSearch": WebSearchTool,
            "DeepWebSearch": DeepWebSearchTool,
            "ExecutePython": ExecutePythonTool,
            "TextCompress": TextCompressTool,
        }
        
        if tool_name in tool_map:
            self._tool_instances[tool_name] = tool_map[tool_name]()
            return self._tool_instances[tool_name]
        return None
    
    async def execute_chain(
        self, 
        chain: ToolChain, 
        context: Dict[str, Any]
    ) -> Dict[str, Any]:

        results = []
        chain_context = context.copy()
        
        for tool_name in chain.tools:
            tool = self._get_tool_instance(tool_name)
            if not tool:
                results.append({"tool": tool_name, "error": "工具不存在"})
                continue
            
            tool_args = self._prepare_tool_args(tool_name, chain_context)
            
            try:
                from homeassistant.helpers.llm import ToolInput
                tool_input = ToolInput(
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
                result = await tool.async_call(self.hass, tool_input, self.llm_context)
                results.append({"tool": tool_name, "result": result})
                
                chain_context = self._update_context(chain_context, tool_name, result)
                
            except Exception as e:
                _LOGGER.error(f"工具链执行错误 {tool_name}: {e}")
                results.append({"tool": tool_name, "error": str(e)})
                break
        
        return {
            "chain": chain.name,
            "category": chain.category.name,
            "steps": results,
            "success": all("error" not in r for r in results),
        }
    
    def _prepare_tool_args(self, tool_name: str, context: Dict[str, Any]) -> Dict[str, Any]:

        args = {}
        
        if tool_name == "SmartDiscovery":
            if "area" in context:
                args["area"] = context["area"]
            if "domain" in context:
                args["domain"] = context["domain"]
            if "name_pattern" in context:
                args["name_pattern"] = context["name_pattern"]
            if "inferred_type" in context:
                args["inferred_type"] = context["inferred_type"]
        
        elif tool_name == "EntityQuery":
            if "entity_id" in context:
                args["entity_id"] = context["entity_id"]
            elif "discovered_entities" in context and context["discovered_entities"]:
                args["entity_id"] = context["discovered_entities"][0]
        
        elif tool_name == "ServiceCall":
            if "domain" in context:
                args["domain"] = context["domain"]
            if "service" in context:
                args["service"] = context["service"]
            args["data"] = {}
            if "entity_id" in context:
                args["data"]["entity_id"] = context["entity_id"]
            elif "discovered_entities" in context and context["discovered_entities"]:
                args["data"]["entity_id"] = context["discovered_entities"][0]
        
        elif tool_name == "GetLiveContext":
            if "domain" in context:
                args["domain"] = context["domain"]
            if "area" in context:
                args["area"] = context["area"]
        
        elif tool_name == "BatchControl":
            if "entity_ids" in context:
                args["entity_ids"] = context["entity_ids"]
            elif "discovered_entities" in context:
                args["entity_ids"] = context["discovered_entities"]
            if "action" in context:
                args["action"] = context["action"]
        
        elif tool_name == "HistoryQuery":
            if "entity_id" in context:
                args["entity_id"] = context["entity_id"]
            elif "discovered_entities" in context and context["discovered_entities"]:
                args["entity_id"] = context["discovered_entities"][0]
            args["hours"] = context.get("hours", 24)
        
        elif tool_name == "StockQuery":
            if "codes" in context:
                args["codes"] = context["codes"]
            elif "query" in context:
                args["codes"] = context["query"]
        
        elif tool_name in ["WebSearch", "DeepWebSearch", "NewsSearch"]:
            if "query" in context:
                args["query"] = context["query"]
        
        elif tool_name == "HACS":
            if "action" in context:
                args["action"] = context["action"]
            if "repository" in context:
                args["repository"] = context["repository"]
            if "query" in context:
                args["query"] = context["query"]
        
        elif tool_name == "FrontendControl":
            if "action" in context:
                args["action"] = context["action"]
            if "params" in context:
                args["params"] = context["params"]
        
        return args
    
    def _update_context(
        self, 
        context: Dict[str, Any], 
        tool_name: str, 
        result: Dict[str, Any]
    ) -> Dict[str, Any]:

        new_context = context.copy()
        
        if tool_name == "SmartDiscovery":
            entities = result.get("entities", [])
            if entities:
                new_context["discovered_entities"] = [e["entity_id"] for e in entities]
                new_context["entity_id"] = entities[0]["entity_id"]
        
        elif tool_name == "GetLiveContext":
            entities = result.get("entities", {})
            if entities:
                new_context["discovered_entities"] = list(entities.keys())
                new_context["entity_ids"] = list(entities.keys())
        
        elif tool_name == "HACS" and result.get("results"):
            if result["results"]:
                new_context["repository"] = result["results"][0].get("full_name", "")
        
        return new_context
    
    async def auto_execute(self, user_input: str) -> Optional[Dict[str, Any]]:

        chain, score = self.selector.analyze_intent(user_input)
        
        if not chain or score < 20:
            return None
        
        context = self._extract_context_from_input(user_input, chain)
        
        return await self.execute_chain(chain, context)
    
    def _extract_context_from_input(self, user_input: str, chain: ToolChain) -> Dict[str, Any]:

        context = {"raw_input": user_input}
        
        area_keywords = ["客厅", "卧室", "厨房", "卫生间", "阳台", "书房", "餐厅", "玄关"]
        for area in area_keywords:
            if area in user_input:
                context["area"] = area
                break
        
        domain_map = {
            "灯": "light",
            "开关": "switch",
            "空调": "climate",
            "风扇": "fan",
            "窗帘": "cover",
            "传感器": "sensor",
            "温度": "sensor",
            "湿度": "sensor",
        }
        for keyword, domain in domain_map.items():
            if keyword in user_input:
                context["domain"] = domain
                break
        
        if any(kw in user_input for kw in ["打开", "开", "启动"]):
            context["action"] = "turn_on"
            context["service"] = "turn_on"
        elif any(kw in user_input for kw in ["关闭", "关", "停止"]):
            context["action"] = "turn_off"
            context["service"] = "turn_off"
        
        if chain.category == IntentCategory.STOCK_QUERY:
            import re
            codes = re.findall(r'[A-Za-z]{2,5}|\d{6}', user_input)
            if codes:
                context["codes"] = ",".join(codes)
            else:
                context["query"] = user_input
        
        if chain.category in [IntentCategory.WEB_SEARCH, IntentCategory.NEWS_SEARCH, IntentCategory.DEEP_SEARCH]:
            context["query"] = user_input
        
        return context


async def create_chain_executor(hass, llm_context) -> ToolChainExecutor:

    return ToolChainExecutor(hass, llm_context)
