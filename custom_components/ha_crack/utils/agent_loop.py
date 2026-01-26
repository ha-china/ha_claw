"""Agent Loop - 迭代式Agent执行逻辑
核心设计: gather context -> take action -> verify work -> repeat
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto

_LOGGER = logging.getLogger(__name__)

class AgentState(Enum):
    IDLE = auto()
    GATHERING = auto()
    ACTING = auto()
    VERIFYING = auto()
    DONE = auto()
    ERROR = auto()

@dataclass
class AgentContext:
    query: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    results: List[Any] = field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 50
    state: AgentState = AgentState.IDLE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_step(self, action: str, result: Any, success: bool = True):
        self.history.append({
            "iteration": self.iteration,
            "action": action,
            "result": result,
            "success": success,
            "timestamp": datetime.now().isoformat()
        })
        self.results.append(result)

    def should_continue(self) -> bool:
        if self.iteration >= self.max_iterations: return False
        if self.state == AgentState.ERROR: return False
        if self.state == AgentState.DONE and self.metadata.get("user_confirmed_done"): return False
        return True

    def is_user_satisfied(self, response: str) -> bool:
        if not response: return False
        response_lower = response.lower().strip()
        done_keywords = ["没问题", "ok", "可以了", "好的", "行", "完成", "结束", "done", "够了", "不用了", "就这样"]
        continue_keywords = ["继续", "还要", "再", "另外", "还有", "接着", "然后", "下一步", "不对", "错了", "重新"]
        if any(k in response_lower for k in continue_keywords):
            return False
        if any(k in response_lower for k in done_keywords) and len(response_lower) < 20:
            self.metadata["user_confirmed_done"] = True
            return True
        return False

class AgentLoop:
    def __init__(self, hass, tools: Dict[str, Callable] = None):
        self.hass = hass
        self.tools = tools or {}
        self.context = None

    def register_tool(self, name: str, func: Callable):
        self.tools[name] = func

    async def run(self, query: str, on_step: Callable = None) -> AgentContext:
        self.context = AgentContext(query=query)
        if hasattr(self, 'default_max_iterations'):
            self.context.max_iterations = self.default_max_iterations
        _LOGGER.info(f"AgentLoop started: {query}")
        
        while self.context.should_continue():
            self.context.iteration += 1
            try:
                self.context.state = AgentState.GATHERING
                context_data = await self._gather_context()
                
                self.context.state = AgentState.ACTING
                action_result = await self._take_action(context_data)
                
                self.context.state = AgentState.VERIFYING
                verified = await self._verify_work(action_result)
                
                self.context.add_step(
                    action=context_data.get("action", "unknown"),
                    result=action_result,
                    success=verified
                )
                
                if on_step:
                    feedback = await on_step(self.context, action_result)
                    if feedback:
                        self.context.metadata["last_feedback"] = feedback
                        if self.context.is_user_satisfied(feedback):
                            self.context.state = AgentState.DONE
                            _LOGGER.info(f"用户确认完成，结束迭代")
                            break
                        else:
                            self.context.state = AgentState.GATHERING
                            _LOGGER.info(f"用户有新需求，继续迭代: {feedback[:50]}...")
                            continue
                    
            except Exception as e:
                _LOGGER.error(f"AgentLoop error at iteration {self.context.iteration}: {e}")
                self.context.state = AgentState.ERROR
                self.context.add_step("error", str(e), success=False)
                break
        
        _LOGGER.info(f"AgentLoop finished after {self.context.iteration} iterations")
        return self.context

    async def _gather_context(self) -> Dict[str, Any]:
        context = {"query": self.context.query, "iteration": self.context.iteration}
        if self.context.history:
            context["last_result"] = self.context.history[-1]
        if self.context.metadata.get("last_feedback"):
            context["feedback"] = self.context.metadata["last_feedback"]
        if "gather_context" in self.tools:
            context.update(await self.tools["gather_context"](self.context))
        return context

    async def _take_action(self, context: Dict[str, Any]) -> Any:
        if "take_action" in self.tools:
            return await self.tools["take_action"](context, self.context)
        return {"status": "no_action_tool"}

    async def _verify_work(self, result: Any) -> bool:
        if "verify_work" in self.tools:
            return await self.tools["verify_work"](result, self.context)
        return result is not None

class HassAgentLoop(AgentLoop):
    def __init__(self, hass, llm_agent=None):
        super().__init__(hass)
        self.llm_agent = llm_agent
        self._setup_default_tools()

    def _setup_default_tools(self):
        self.register_tool("gather_context", self._hass_gather_context)
        self.register_tool("take_action", self._hass_take_action)
        self.register_tool("verify_work", self._hass_verify_work)

    async def _hass_gather_context(self, ctx: AgentContext) -> Dict[str, Any]:
        context = {}
        if ctx.metadata.get("entities"):
            context["entity_states"] = {
                e: self.hass.states.get(e) for e in ctx.metadata["entities"]
            }
        if ctx.metadata.get("search_query"):
            from ..services.web_search import WebSearch
            async with WebSearch() as ws:
                results = await ws.search(ctx.metadata["search_query"], 3)
                context["search_results"] = [r.__dict__ for r in results]
        return context

    async def _hass_take_action(self, context: Dict, ctx: AgentContext) -> Any:
        if self.llm_agent:
            from homeassistant.components import conversation
            user_input = conversation.ConversationInput(
                text=ctx.query,
                conversation_id=ctx.metadata.get("conversation_id"),
                language=ctx.metadata.get("language", "zh-Hans"),
                context=None,
                device_id=None,
                agent_id=None,
                satellite_id=None
            )
            result = await self.llm_agent.async_process(user_input)
            if result and result.response and result.response.speech:
                speech = result.response.speech.get("plain", {}).get("speech", "")
                ctx.metadata["has_next_task"] = self._detect_next_task(speech)
                return {"response": speech, "result": result}
        return {"status": "no_llm_agent"}

    async def _hass_verify_work(self, result: Any, ctx: AgentContext) -> bool:
        if not result: return False
        if isinstance(result, dict):
            if result.get("status") == "error": return False
            if result.get("response"): return True
        return True

    def _detect_next_task(self, response: str) -> bool:
        next_indicators = ["接下来", "然后", "还需要", "下一步", "继续", "另外"]
        return any(i in response for i in next_indicators)

async def create_agent_loop(hass, llm_agent=None, max_iterations: int = 50) -> HassAgentLoop:
    loop = HassAgentLoop(hass, llm_agent)
    loop.default_max_iterations = max_iterations
    return loop
