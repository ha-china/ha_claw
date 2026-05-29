
from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.components.conversation import ConversationResult

from .config import DelegationConfig, load_delegation_config
from ..runtime.utils.i18n import t

_LOGGER = logging.getLogger(__name__)


BLOCKED_TOOLS_FOR_SUBAGENT = frozenset({
    "DelegateTask",
    "DelegateBatch", 
    "AgentHandoff",
    "NextAgentHandoff",
})

DEFAULT_MAX_ITERATIONS = 50
DEFAULT_CHILD_TIMEOUT = 300.0
HEARTBEAT_INTERVAL = 30.0
MIN_SPAWN_DEPTH = 1
MAX_SPAWN_DEPTH_CAP = 3
DEFAULT_MAX_SPAWN_DEPTH = 1


class DelegateEvent(str, enum.Enum):
    TASK_SPAWNED = "delegate.task_spawned"
    TASK_PROGRESS = "delegate.task_progress"
    TASK_COMPLETED = "delegate.task_completed"
    TASK_FAILED = "delegate.task_failed"
    TASK_THINKING = "delegate.task_thinking"
    TASK_TOOL_STARTED = "delegate.tool_started"
    TASK_TOOL_COMPLETED = "delegate.tool_completed"


_active_subagents: dict[str, dict[str, Any]] = {}
_completed_subagents: dict[str, dict[str, Any]] = {}
_active_lock = asyncio.Lock()
_interrupt_flags: dict[str, bool] = {}
_spawn_paused: bool = False
_spawn_pause_lock = asyncio.Lock()
_pending_questions: dict[str, dict[str, Any]] = {}
_question_answers: dict[str, str] = {}


@dataclass
class SubagentTask:
    
    task_id: str
    goal: str
    context: str | None = None
    role: str = "leaf"
    toolsets: list[str] | None = None
    model_override: str | None = None


@dataclass
class SubagentResult:
    
    task_id: str
    task_index: int
    status: str
    summary: str | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    tool_count: int = 0
    api_calls: int = 0
    messages: list[dict] | None = None


class SubagentProgressReporter:
    
    def __init__(
        self,
        hass: HomeAssistant,
        task_id: str,
        goal: str,
        task_index: int = 0,
        task_count: int = 1,
        parent_callback: Callable[[str, dict], None] | None = None,
        depth: int = 1,
        model: str | None = None,
    ):
        self.hass = hass
        self.task_id = task_id
        self.goal = goal
        self.task_index = task_index
        self.task_count = task_count
        self.parent_callback = parent_callback
        self.depth = depth
        self.model = model
        self.tool_count = 0
        self.start_time = time.monotonic()
        self._batch: list[str] = []
        self._batch_size = 5
    
    def _identity_kwargs(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_index": self.task_index,
            "task_count": self.task_count,
            "goal": self.goal[:100],
            "depth": self.depth,
            "model": self.model,
            "tool_count": self.tool_count,
            "elapsed_seconds": round(time.monotonic() - self.start_time, 2),
        }
    
    def report(self, event_type: str, data: dict | None = None):
        payload = {
            **self._identity_kwargs(),
            **(data or {}),
        }
        
        rec = _active_subagents.get(self.task_id)
        if rec is not None:
            rec["logs"].append({
                "event": event_type,
                "timestamp": time.time(),
                **payload,
            })
        
        if self.parent_callback:
            try:
                self.parent_callback(event_type, payload)
            except Exception as e:
                pass
        self.hass.bus.async_fire(
            "claw_subagent_progress",
            {"event_type": event_type, **payload},
        )
    
    def on_tool_call(self, tool_name: str, tool_args: dict | None = None, preview: str | None = None):
        self.tool_count += 1
        
        rec = _active_subagents.get(self.task_id)
        if rec is not None:
            rec["tool_count"] = self.tool_count
            rec["last_tool"] = tool_name
        
        self.report("subagent.tool", {
            "tool_name": tool_name,
            "preview": (preview or "")[:100],
        })
        
        self._batch.append(tool_name)
        if len(self._batch) >= self._batch_size:
            self._flush_batch()
    
    def on_thinking(self, text: str):
        self.report("subagent.thinking", {
            "preview": text[:200] if text else "",
        })
    
    def _flush_batch(self):
        if self._batch:
            summary = ", ".join(self._batch)
            self.report("subagent.progress", {"preview": f"⚫️ {summary}"})
            self._batch.clear()
    
    def flush(self):
        self._flush_batch()


async def set_spawn_paused(paused: bool) -> bool:
    global _spawn_paused
    async with _spawn_pause_lock:
        _spawn_paused = bool(paused)
        return _spawn_paused


async def is_spawn_paused() -> bool:
    async with _spawn_pause_lock:
        return _spawn_paused


def get_subagent_status(task_id: str) -> dict[str, Any] | None:
    return _active_subagents.get(task_id)


def list_active_subagents() -> list[dict[str, Any]]:
    return [
        {
            "task_id": info["task_id"],
            "goal": info.get("goal", "")[:100],
            "status": info.get("status", "unknown"),
            "started_at": info.get("started_at"),
            "tool_count": info.get("tool_count", 0),
            "last_tool": info.get("last_tool"),
            "result": info.get("result"),
        }
        for info in _active_subagents.values()
    ]


def _normalize_role(r: str | None) -> str:
    if r is None or not r:
        return "leaf"
    r_norm = str(r).strip().lower()
    if r_norm in {"leaf", "orchestrator"}:
        return r_norm
    _LOGGER.warning("Unknown delegate_task role=%r, coercing to 'leaf'", r)
    return "leaf"


def generate_task_id() -> str:
    return f"sa-{uuid.uuid4().hex[:8]}"


async def delegate_task_async(
    hass: HomeAssistant,
    *,
    task_id: str,
    goal: str,
    context: str | None = None,
    parent_agent_id: str | None = None,
    parent_conversation_id: str | None = None,
    role: str = "leaf",
    timeout_seconds: float | None = None,
    model_override: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    parent_depth: int = 0,
    language: str | None = None,
) -> None:
    try:
        result = await delegate_task(
            hass,
            goal=goal,
            context=context,
            parent_agent_id=parent_agent_id,
            parent_conversation_id=parent_conversation_id,
            role=role,
            timeout_seconds=timeout_seconds,
            model_override=model_override,
            progress_callback=progress_callback,
            parent_depth=parent_depth,
            language=language,
            _task_id_override=task_id,
        )
    except Exception as e:
        hass.bus.async_fire("claw_subagent_progress", {
            "event": "subagent.complete",
            "task_id": task_id,
            "status": "error",
            "error": str(e),
        })


async def delegate_task(
    hass: HomeAssistant,
    *,
    goal: str,
    context: str | None = None,
    parent_agent_id: str | None = None,
    parent_conversation_id: str | None = None,
    role: str = "leaf",
    toolsets: list[str] | None = None,
    timeout_seconds: float | None = None,
    model_override: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    parent_depth: int = 0,
    language: str | None = None,
    _task_id_override: str | None = None,
) -> SubagentResult:
    config = load_delegation_config(hass)
    task_id = _task_id_override or f"sa-{uuid.uuid4().hex[:8]}"
    start_time = time.monotonic()
    lang = language or hass.config.language
    child_timeout = timeout_seconds or config.child_timeout_seconds
    
    if await is_spawn_paused():
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="error",
            error=t("delegation_spawn_paused", lang),
        )
    
    child_depth = parent_depth + 1
    if child_depth > config.max_spawn_depth:
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="error",
            error=t("delegation_depth_exceeded", lang).format(depth=config.max_spawn_depth),
        )
    
    effective_role = _normalize_role(role)
    if effective_role == "orchestrator" and not config.orchestrator_enabled:
        effective_role = "leaf"
    if effective_role == "orchestrator" and child_depth >= config.max_spawn_depth:
        effective_role = "leaf"
    
    _LOGGER.info("Delegating task %s (depth=%d, role=%s): %s", task_id, child_depth, effective_role, goal[:100])
    
    reporter = SubagentProgressReporter(
        hass, task_id, goal,
        task_index=0,
        task_count=1,
        parent_callback=progress_callback,
        depth=child_depth,
        model=model_override,
    )
    reporter.report("subagent.spawn_requested")
    
    async with _active_lock:
        _active_subagents[task_id] = {
            "task_id": task_id,
            "goal": goal,
            "status": "pending",
            "started_at": time.time(),
            "parent_agent_id": parent_agent_id,
            "parent_conversation_id": parent_conversation_id,
            "depth": child_depth,
            "role": effective_role,
            "tool_count": 0,
            "last_tool": None,
            "logs": [],
        }
    
    try:
        async with _active_lock:
            _active_subagents[task_id]["status"] = "running"
        
        reporter.report("subagent.start")
        
        result = await _run_subagent(
            hass,
            task_id=task_id,
            goal=goal,
            context=context,
            config=config,
            parent_agent_id=parent_agent_id,
            parent_conversation_id=parent_conversation_id,
            role=effective_role,
            toolsets=toolsets,
            model_override=model_override,
            reporter=reporter,
            depth=child_depth,
            language=lang,
        )
        
        duration = time.monotonic() - start_time
        result.duration_seconds = duration
        result.tool_count = reporter.tool_count
        
        async with _active_lock:
            if task_id in _active_subagents:
                _active_subagents[task_id]["status"] = result.status
                _active_subagents[task_id]["result"] = {
                    "summary": result.summary,
                    "error": result.error,
                    "duration_seconds": round(duration, 2),
                    "tool_count": result.tool_count,
                }
        
        reporter.flush()
        reporter.report("subagent.complete", {
            "status": result.status,
            "summary": (result.summary or "")[:500],
            "error": result.error,
            "duration_seconds": round(duration, 2),
        })
        
        return result
        
    except asyncio.TimeoutError:
        duration = time.monotonic() - start_time
        result = SubagentResult(
            task_id=task_id,
            task_index=0,
            status="timeout",
            error=f"Subagent timed out after {child_timeout}s",
            duration_seconds=duration,
            tool_count=reporter.tool_count,
        )
        reporter.flush()
        reporter.report("subagent.complete", {
            "status": "timeout",
            "error": result.error,
            "duration_seconds": round(duration, 2),
        })
        return result
        
    except asyncio.CancelledError:
        duration = time.monotonic() - start_time
        result = SubagentResult(
            task_id=task_id,
            task_index=0,
            status="cancelled",
            error="Task was cancelled",
            duration_seconds=duration,
            tool_count=reporter.tool_count,
        )
        reporter.flush()
        reporter.report("subagent.complete", {
            "status": "cancelled",
            "duration_seconds": round(duration, 2),
        })
        return result
        
    except Exception as exc:
        duration = time.monotonic() - start_time
        result = SubagentResult(
            task_id=task_id,
            task_index=0,
            status="error",
            error=str(exc),
            duration_seconds=duration,
            tool_count=reporter.tool_count,
        )
        reporter.flush()
        reporter.report("subagent.complete", {
            "status": "error",
            "error": str(exc),
            "duration_seconds": round(duration, 2),
        })
        return result
        
    finally:
        async with _active_lock:
            _active_subagents.pop(task_id, None)
        _interrupt_flags.pop(task_id, None)


async def delegate_batch(
    hass: HomeAssistant,
    *,
    tasks: list[SubagentTask],
    parent_agent_id: str | None = None,
    parent_conversation_id: str | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
    parent_depth: int = 0,
    language: str | None = None,
) -> list[SubagentResult]:
    config = load_delegation_config(hass)
    max_concurrent = config.max_concurrent_children
    lang = language or hass.config.language
    
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def run_with_semaphore(task: SubagentTask, index: int) -> SubagentResult:
        async with semaphore:
            result = await delegate_task(
                hass,
                goal=task.goal,
                context=task.context,
                parent_agent_id=parent_agent_id,
                parent_conversation_id=parent_conversation_id,
                role=task.role,
                toolsets=task.toolsets,
                model_override=task.model_override,
                progress_callback=progress_callback,
                parent_depth=parent_depth,
                language=lang,
            )
            result.task_index = index
            return result
    
    coros = [run_with_semaphore(task, i) for i, task in enumerate(tasks)]
    results = await asyncio.gather(*coros, return_exceptions=True)
    
    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final_results.append(SubagentResult(
                task_id=tasks[i].task_id,
                task_index=i,
                status="error",
                error=str(r),
            ))
        else:
            final_results.append(r)
    
    return final_results


async def _run_subagent(
    hass: HomeAssistant,
    *,
    task_id: str,
    goal: str,
    context: str | None,
    config: DelegationConfig,
    parent_agent_id: str | None,
    parent_conversation_id: str | None,
    role: str,
    toolsets: list[str] | None,
    model_override: str | None,
    reporter: SubagentProgressReporter,
    depth: int,
    language: str | None,
) -> SubagentResult:
    from ..const import DOMAIN
    
    child_conversation_id = f"subagent:{task_id}"
    
    data = hass.data.get(DOMAIN, {})
    entry = data.get("entry")
    if not entry:
        entries = hass.config_entries.async_entries(DOMAIN)
        entry = entries[0] if entries else None
    
    if not entry:
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="error",
            error="No claw_assistant config entry found",
        )
    
    subagent_prompt = _build_subagent_prompt(
        goal, context, role,
        depth=depth,
        max_spawn_depth=config.max_spawn_depth,
        language=language,
    )
    
    from homeassistant.components import conversation as ha_conversation
    from homeassistant.core import Context
    
    if model_override:
        agent_id = model_override
    elif parent_agent_id and "." in parent_agent_id:
        agent_id = parent_agent_id
    else:
        agent_id = f"conversation.{entry.entry_id}"
    
    from homeassistant.components.conversation import async_get_agent
    agent = async_get_agent(hass, agent_id)
    if agent is None:
        states = hass.states.async_all("conversation")
        for state in states:
            if DOMAIN in state.entity_id or "claw" in state.entity_id.lower():
                agent_id = state.entity_id
                agent = async_get_agent(hass, agent_id)
                if agent:
                    break
    
    if agent is None:
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="error",
            error=f"Agent {agent_id} not found",
        )
    
    lang = language or hass.config.language
    
    heartbeat_task: asyncio.Task | None = None
    
    async def heartbeat_loop():
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if _interrupt_flags.get(task_id):
                    break
                reporter.report("subagent.heartbeat", {
                    "tool_count": reporter.tool_count,
                })
        except asyncio.CancelledError:
            pass
    
    try:
        heartbeat_task = asyncio.create_task(heartbeat_loop())
        
        from ..runtime.core.state import get_active_conversation_state
        active_conv = get_active_conversation_state(hass)
        parent_conv_id = active_conv.get("id")
        
        result = await asyncio.wait_for(
            ha_conversation.async_converse(
                hass,
                text=goal,
                conversation_id=child_conversation_id,
                context=Context(),
                language=lang,
                agent_id=agent_id,
                device_id=None,
                satellite_id=None,
                extra_system_prompt=subagent_prompt,
            ),
            timeout=child_timeout,
        )
        
        if parent_conv_id:
            active_conv["id"] = parent_conv_id
        
        if _interrupt_flags.get(task_id):
            return SubagentResult(
                task_id=task_id,
                task_index=0,
                status="interrupted",
                error="Task was interrupted",
            )
        
        response_text = ""
        if result and result.response:
            resp = result.response
            if hasattr(resp, "speech") and resp.speech:
                speech_data = resp.speech
                if isinstance(speech_data, dict):
                    response_text = speech_data.get("plain", {}).get("speech", "")
        
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="completed",
            summary=response_text[:2000] if response_text else "Task completed",
        )
        
    except asyncio.TimeoutError:
        raise
    except asyncio.CancelledError:
        return SubagentResult(
            task_id=task_id,
            task_index=0,
            status="cancelled",
            error="Task was cancelled",
        )
    except Exception as exc:
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


def _build_subagent_prompt(
    goal: str,
    context: str | None,
    role: str,
    *,
    depth: int = 1,
    max_spawn_depth: int = 2,
    language: str | None = None,
) -> str:
    intro = t("delegation_subagent_prompt_intro", language)
    task_label = t("delegation_subagent_prompt_task", language)
    context_label = t("delegation_subagent_prompt_context", language)
    instructions = t("delegation_subagent_prompt_instructions", language)
    
    parts = [
        intro,
        "",
        f"## {task_label}\n{goal}",
    ]
    
    if context and context.strip():
        parts.append(f"\n## {context_label}\n{context}")
    
    parts.append(f"\n{instructions}")
    
    if role == "orchestrator":
        orchestrator_prompt = t("delegation_subagent_prompt_orchestrator", language)
        depth_note = f"\nNOTE: You are at depth {depth}. Max spawn depth is {max_spawn_depth}."
        parts.append(f"\n{orchestrator_prompt}{depth_note}")
    else:
        leaf_prompt = t("delegation_subagent_prompt_leaf", language)
        parts.append(f"\n{leaf_prompt}")
    
    return "\n".join(parts)


async def list_active_subagents() -> list[dict[str, Any]]:
    async with _active_lock:
        return [
            {
                "task_id": record["task_id"],
                "goal": record["goal"][:100],
                "status": record["status"],
                "started_at": record["started_at"],
                "depth": record.get("depth", 1),
                "role": record.get("role", "leaf"),
                "tool_count": record.get("tool_count", 0),
                "last_tool": record.get("last_tool"),
                "parent_agent_id": record.get("parent_agent_id"),
            }
            for record in _active_subagents.values()
        ]


async def interrupt_subagent(task_id: str) -> bool:
    async with _active_lock:
        record = _active_subagents.get(task_id)
        if not record:
            return False
        record["status"] = "interrupting"
        _interrupt_flags[task_id] = True
    
    return True


def is_subagent_interrupted(task_id: str) -> bool:
    return _interrupt_flags.get(task_id, False)


@callback
def get_subagent_status(task_id: str) -> dict[str, Any] | None:
    return _active_subagents.get(task_id)


@callback
def get_subagent_logs(task_id: str, limit: int = 100) -> list[dict] | None:
    rec = _active_subagents.get(task_id)
    if rec is None:
        return None
    logs = rec.get("logs", [])
    return logs[-limit:] if limit else logs


@callback
def ask_parent_question(task_id: str, question: str, timeout: float = 300.0) -> dict[str, Any]:
    from asyncio import Event
    question_id = f"{task_id}:{uuid.uuid4().hex[:8]}"
    event = Event()
    _pending_questions[question_id] = {
        "task_id": task_id,
        "question": question,
        "timestamp": time.time(),
        "timeout": timeout,
        "event": event,
    }
    rec = _active_subagents.get(task_id)
    if rec is not None:
        rec["pending_question"] = question_id
        rec["logs"].append({
            "event": "subagent.ask_parent",
            "timestamp": time.time(),
            "question": question,
            "question_id": question_id,
        })
    return {
        "question_id": question_id,
        "status": "pending",
        "message": "Waiting for parent to answer",
    }


@callback
def answer_subagent_question(question_id: str, answer: str) -> bool:
    if question_id not in _pending_questions:
        return False
    _question_answers[question_id] = answer
    q = _pending_questions[question_id]
    task_id = q.get("task_id")
    rec = _active_subagents.get(task_id)
    if rec is not None:
        rec["pending_question"] = None
        rec["logs"].append({
            "event": "subagent.answer_received",
            "timestamp": time.time(),
            "question_id": question_id,
            "answer": answer,
        })
    event = q.get("event")
    if event:
        event.set()
    del _pending_questions[question_id]
    return True


@callback
def get_pending_questions(task_id: str | None = None) -> list[dict[str, Any]]:
    if task_id:
        return [
            {"question_id": qid, **{k: v for k, v in q.items() if k != "event"}}
            for qid, q in _pending_questions.items()
            if q.get("task_id") == task_id
        ]
    return [
        {"question_id": qid, **{k: v for k, v in q.items() if k != "event"}}
        for qid, q in _pending_questions.items()
    ]
