
from __future__ import annotations
import logging
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)


class AgentLoopTool(llm.Tool):
    name = "AgentLoop"
    description = "启动迭代式Agent循环。用于复杂任务的多步骤执行。"
    parameters = vol.Schema({
        vol.Required("task"): str,
        vol.Optional("max_iterations", default=50): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..utils.agent_loop import create_agent_loop
        task = tool_input.tool_args.get("task", "")
        max_iter = tool_input.tool_args.get("max_iterations", 50)
        try:
            loop = await create_agent_loop(hass, max_iterations=max_iter)
            ctx = await loop.run(task)
            return {"success": True, "iterations": ctx.iteration, "results": [str(r) for r in ctx.results[-3:]]}
        except Exception as e:
            return {"success": False, "error": str(e)}


class RolePlayTool(llm.Tool):
    name = "RolePlay"
    description = "切换角色扮演模式。"
    parameters = vol.Schema({vol.Required("role"): str})

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..services.ai_skills import CURRENT_ROLE, ROLEPLAY_PRESETS
        role = tool_input.tool_args.get("role", "")
        for key, preset in ROLEPLAY_PRESETS.items():
            if role.lower() in [a.lower() for a in preset["aliases"]] or role.lower() == preset["name"].lower():
                CURRENT_ROLE["role"] = preset["name"]
                CURRENT_ROLE["prompt"] = preset["prompt"]
                return {"success": True, "role": preset["name"], "greeting": preset.get("greeting", f"我现在是{preset['name']}了")}
        CURRENT_ROLE["role"] = role
        CURRENT_ROLE["prompt"] = f"你现在扮演{role}"
        return {"success": True, "role": role}


class ExecutePythonTool(llm.Tool):
    name = "ExecutePython"
    description = """执行Python代码，用于复杂计算和数据处理（简单设备控制请用ServiceCall/BatchControl）

可用：math,datetime,json,re,random,asyncio,hass

示例：
1. 复杂计算：result = sum([i**2 for i in range(1,101)])
2. 时间计算：result = (datetime.datetime(2025,1,1) - datetime.datetime.now()).days
3. 数据分析：result = {s.domain: len([x for x in hass.states.async_all() if x.domain==s.domain]) for s in hass.states.async_all()}
4. 条件筛选：result = [s.entity_id for s in hass.states.async_all() if s.state=='unavailable']
5. 属性提取：result = [(s.entity_id, s.attributes.get('friendly_name')) for s in hass.states.async_all() if 'sensor' in s.entity_id][:10]
6. JSON处理：result = json.loads('{"a":1}')
7. 正则匹配：result = re.findall(r'\\d+', 'abc123def456')
8. 随机选择：result = random.choice(['a','b','c'])
9. 格式化输出：result = f"当前有{len(hass.states.async_all())}个实体"
10. 延时操作：await asyncio.sleep(1); result = "等待完成"

结果赋值给result，异步用await

高级操作：
11. 创建传感器：hass.states.async_set('sensor.ai_test','25',{'friendly_name':'AI测试','unit_of_measurement':'°C'}); result='已创建'
12. 删除传感器：hass.states.async_remove('sensor.ai_test'); result='已删除'
13. 获取所有集成：result = list(hass.data.get('integrations',{}).keys())
14. 内部缓存：result = {k:type(v).__name__ for k,v in list(hass.data.items())[:30]}
15. 实体注册表：er = hass.data['entity_registry']; result = [(e.entity_id,e.platform) for e in list(er.entities.values())[:20]]
16. 设备注册表：dr = hass.data['device_registry']; result = [(d.id,d.name) for d in list(dr.devices.values())[:20]]
17. 区域注册表：ar = hass.data['area_registry']; result = [(a.id,a.name) for a in ar.areas.values()]
18. 动态导入：mod = importlib.import_module('homeassistant.helpers.entity_registry'); result = dir(mod)[:10]"""
    parameters = vol.Schema({
        vol.Required("code"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        code = tool_input.tool_args.get("code", "")
        forbidden = ["subprocess", "__import__", "open(", "file(", "compile(", "globals(", "locals(", "os."]
        for f in forbidden:
            if f in code:
                return {"success": False, "error": f"禁止操作: {f}"}
        
        import math, datetime, json, re, random, asyncio, importlib
        safe_globals = {
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
            "len": len, "range": range, "enumerate": enumerate, "zip": zip,
            "int": int, "float": float, "str": str, "bool": bool, "list": list, "dict": dict, "set": set, "tuple": tuple,
            "sorted": sorted, "reversed": reversed, "map": map, "filter": filter,
            "pow": pow, "divmod": divmod, "hex": hex, "bin": bin, "oct": oct,
            "any": any, "all": all, "isinstance": isinstance, "type": type, "dir": dir, "getattr": getattr, "hasattr": hasattr,
            "print": lambda *args: None,
            "math": math, "datetime": datetime, "json": json, "re": re, "random": random, "asyncio": asyncio, "importlib": importlib,
            "hass": hass,
        }
        
        try:
            if "await " in code:
                wrapped = f"async def __run__():\n    result = None\n" + "\n".join(f"    {line}" for line in code.split("\n")) + "\n    return result"
                exec(wrapped, safe_globals)
                result = await safe_globals["__run__"]()
            else:
                local_vars = {"result": None}
                exec(code, safe_globals, local_vars)
                result = local_vars.get("result")
            
            if result is None:
                return {"success": True, "result": "代码已执行"}
            if isinstance(result, (list, dict)):
                return {"success": True, "result": result}
            return {"success": True, "result": str(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}


class SystemControlTool(llm.Tool):
    name = "SystemControl"
    description = "系统控制工具。用于设置全局注入、输出模式、清除角色等。"
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["set_global_inject", "set_output_mode", "clear_roleplay", "get_status"]),
        vol.Optional("value", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        value = tool_input.tool_args.get("value", "")
        if action == "set_global_inject":
            hass.data.setdefault("ha_crack_global", {})["inject"] = value
            return {"success": True, "message": f"Global inject set: {value[:50]}..."}
        elif action == "set_output_mode":
            if value in ["brief", "detailed", "list", "code", ""]:
                hass.data.setdefault("ha_crack_output", {})["mode"] = value
                return {"success": True, "message": f"Output mode set: {value or 'normal'}"}
            return {"success": False, "error": "Invalid mode"}
        elif action == "clear_roleplay":
            from ..services.ai_skills import CURRENT_ROLE
            CURRENT_ROLE["role"] = None
            CURRENT_ROLE["prompt"] = None
            hass.data["ha_crack_roleplay"] = {"role": None, "prompt": None}
            return {"success": True, "message": "Roleplay cleared"}
        elif action == "get_status":
            from ..services.ai_skills import CURRENT_ROLE
            return {
                "success": True,
                "roleplay": CURRENT_ROLE.get("role"),
                "global_inject": hass.data.get("ha_crack_global", {}).get("inject", "")[:100],
                "output_mode": hass.data.get("ha_crack_output", {}).get("mode", "normal"),
            }
        return {"success": False, "error": "Unknown action"}


class ConversationMemoryTool(llm.Tool):
    name = "ConversationMemory"
    description = "管理对话记忆。用于存储和检索重要信息。"
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["save", "get", "list", "clear"]),
        vol.Optional("key", default=""): str,
        vol.Optional("value", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        key = tool_input.tool_args.get("key", "")
        value = tool_input.tool_args.get("value", "")
        memory = hass.data.setdefault("ha_crack_memory", {})
        if action == "save" and key:
            memory[key] = value
            return {"success": True, "message": f"Saved: {key}"}
        elif action == "get" and key:
            return {"success": True, "value": memory.get(key, "")}
        elif action == "list":
            return {"success": True, "keys": list(memory.keys())}
        elif action == "clear":
            memory.clear()
            return {"success": True, "message": "Memory cleared"}
        return {"success": False, "error": "Invalid action or missing key"}


class TextCompressTool(llm.Tool):
    name = "TextCompress"
    description = "压缩长文本。用于压缩搜索结果或长内容以节省token。"
    parameters = vol.Schema({
        vol.Required("text"): str,
        vol.Optional("target_length", default=2000): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..utils.text_compressor import TextCompressor
        text = tool_input.tool_args.get("text", "")
        target = tool_input.tool_args.get("target_length", 2000)
        try:
            compressor = TextCompressor(target_length=target)
            result = compressor.compress(text)
            return {
                "success": True,
                "compressed_text": result.text,
                "original_length": result.original_length,
                "compressed_length": result.compressed_length,
                "ratio": f"{result.compression_ratio:.1%}"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


class ThinkContinueTool(llm.Tool):
    name = "ThinkContinue"
    description = """记录思考过程。用于在回复用户之前记录你的思考。

重要：thought 是你的内部思考过程，不是给用户的回复！

参数：
- thought: 你的思考过程（如"用户打招呼，我需要询问有什么可以帮助的"）
- next_action: 下一步计划（可选）
- stop: 是否终止循环（默认false）

示例：
- 用户说"你好" → thought="用户打招呼，我应该友好回应并询问需求"
- 用户问天气 → thought="用户想知道天气，我需要调用天气服务"

注意：thought 不是最终回复，是你的思考过程！"""
    parameters = vol.Schema({
        vol.Required("thought"): str,
        vol.Optional("next_action", default=""): str,
        vol.Optional("stop", default=False): bool,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        thought = tool_input.tool_args.get("thought", "")
        next_action = tool_input.tool_args.get("next_action", "")
        stop = tool_input.tool_args.get("stop", False)
        task_loop = hass.data.setdefault("ha_crack_task_loop", {})
        iteration = task_loop.get("iteration", 0) + 1
        max_iter = task_loop.get("max_iterations", 30)
        
        hass.data["ha_crack_current_thought"] = thought
        
        hass.bus.async_fire("ha_crack_thought", {"thought": thought})
        
        if stop:
            task_loop["iteration"] = 0
            task_loop["active"] = False
            task_loop["last_thought"] = thought
            _LOGGER.info(f"ThinkContinue: AI主动终止循环，原因: {thought[:100]}")
            return {
                "success": True,
                "stopped": True,
                "message": "循环已终止，等待用户下一步指令",
                "final_thought": thought[:200] if len(thought) > 200 else thought,
                "display_text": thought
            }
        
        if iteration >= max_iter:
            task_loop["iteration"] = 0
            task_loop["active"] = False
            return {
                "success": False,
                "stopped": True,
                "error": f"已达到最大迭代次数({max_iter})，请总结当前进度并结束",
                "iteration": iteration
            }
        
        task_loop["iteration"] = iteration
        task_loop["last_thought"] = thought
        task_loop["active"] = True
        
        return {
            "success": True,
            "stopped": False,
            "message": "思考已记录，请继续执行",
            "iteration": iteration,
            "max_iterations": max_iter,
            "thought_recorded": thought[:100] + "..." if len(thought) > 100 else thought,
            "suggested_next": next_action or "继续分析或执行下一步"
        }


class ParallelToolCallTool(llm.Tool):
    name = "ParallelToolCall"
    description = "并行调用多个工具。用于需要同时执行多个操作的场景。"
    parameters = vol.Schema({
        vol.Required("tools"): list,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        tools = tool_input.tool_args.get("tools", [])
        if not tools:
            return {"success": False, "error": "No tools specified"}
        
        results = []
        for t in tools:
            tool_name = t.get("name", "")
            tool_args = t.get("args", {})
            results.append({
                "tool": tool_name,
                "args": tool_args,
                "status": "queued"
            })
        
        return {
            "success": True,
            "message": "已记录需要调用的工具，请依次调用",
            "tools_to_call": results
        }


class GetConversationHistoryTool(llm.Tool):
    name = "GetConversationHistory"
    description = "获取当前对话的历史记录。用于回顾之前的对话内容。"
    parameters = vol.Schema({
        vol.Optional("max_turns", default=5): int,
        vol.Optional("include_tools", default=False): bool,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..conversation_utils import get_conversation_history
        
        max_turns = tool_input.tool_args.get("max_turns", 5)
        include_tools = tool_input.tool_args.get("include_tools", False)
        
        conv_id = llm_context.context.id if llm_context.context else "default"
        history = get_conversation_history()
        context_str = history.get_recent_context(conv_id, max_turns, include_tools)
        
        if not context_str:
            return {"success": True, "history": "", "message": "暂无对话历史"}
        
        return {"success": True, "history": context_str, "turns": len(history.get_history(conv_id))}


class ExecuteChainTool(llm.Tool):
    name = "ExecuteChain"
    description = """智能工具链执行器。根据意图自动选择并执行工具链组合。

可用工具链：
- device_control: 控制设备（SmartDiscovery → ServiceCall）
- batch_control: 批量控制（GetLiveContext → BatchControl）
- device_query: 查询状态（SmartDiscovery → EntityQuery）
- history_query: 历史趋势（SmartDiscovery → HistoryQuery）
- stock_query: 股票查询（StockQuery）
- news_search: 新闻搜索（NewsSearch）
- web_search: 网页搜索（WebSearch）
- hacs_install: HACS安装（HACS搜索 → HACS安装）

参数：
- intent: 用户意图描述（如"打开客厅灯"）
- chain_name: 可选，指定工具链名称
- context: 可选，额外上下文（area/domain/entity_id等）"""
    parameters = vol.Schema({
        vol.Required("intent"): str,
        vol.Optional("chain_name", default=""): str,
        vol.Optional("context", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from .tool_chains import get_tool_chain_selector, create_chain_executor, TOOL_CHAINS
        
        intent = tool_input.tool_args.get("intent", "")
        chain_name = tool_input.tool_args.get("chain_name", "")
        extra_context = tool_input.tool_args.get("context", {})
        
        executor = await create_chain_executor(hass, llm_context)
        
        if chain_name:
            chain = next((c for c in TOOL_CHAINS if c.name == chain_name), None)
            if chain:
                context = executor._extract_context_from_input(intent, chain)
                context.update(extra_context)
                result = await executor.execute_chain(chain, context)
                return result
            return {"success": False, "error": f"未找到工具链: {chain_name}"}
        
        result = await executor.auto_execute(intent)
        if result:
            return result
        
        return {
            "success": False, 
            "error": "未匹配到合适的工具链",
            "suggestion": "请使用具体工具或提供更明确的意图描述"
        }


class AnalyzeIntentTool(llm.Tool):
    name = "AnalyzeIntent"
    description = """分析用户意图，推荐最佳工具链。不执行，只返回建议。

用于复杂场景下先分析再决策。返回匹配的工具链、置信度、建议的工具组合。"""
    parameters = vol.Schema({
        vol.Required("user_input"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from .tool_chains import get_tool_chain_selector
        
        user_input = tool_input.tool_args.get("user_input", "")
        selector = get_tool_chain_selector()
        
        result = selector.suggest_tools(user_input)
        return result

