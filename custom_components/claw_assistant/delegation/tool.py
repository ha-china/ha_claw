
from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ..runtime.utils.i18n import t

_LOGGER = logging.getLogger(__name__)


class DelegateTaskTool(llm.Tool):
    
    name = "DelegateTask"
    description = (
        "Spawn a subagent to handle a task in an isolated context. "
        "The subagent has its own conversation, toolset, and session. "
        "Subagent runs ASYNC in background - this tool returns immediately "
        "with task_id. You can continue working while subagent runs.\n\n"
        "WORKFLOW:\n"
        "1. Call DelegateTask → get task_id (status: spawned)\n"
        "2. Call DelegateStatus(task_id, include_logs=true) to see progress\n"
        "3. Subagent can use DelegateAskParent to ask you questions\n\n"
        "WHEN TO USE:\n"
        "- Reasoning-heavy subtasks (debugging, code review, research)\n"
        "- Tasks that would flood your context with intermediate data\n"
        "- Parallel independent workstreams\n\n"
        "IMPORTANT: Pass all relevant context via 'context' field - "
        "subagents have NO memory of your conversation."
    )
    
    parameters = vol.Schema({
        vol.Required("goal"): str,
        vol.Optional("context"): str,
        vol.Optional("role", default="leaf"): vol.In(["leaf", "orchestrator"]),
        vol.Optional("timeout_seconds", default=1200): int,
    })
    
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> str:
        import asyncio
        from .executor import delegate_task_async, generate_task_id
        
        goal = tool_input.tool_args.get("goal", "")
        context = tool_input.tool_args.get("context")
        role = tool_input.tool_args.get("role", "leaf")
        timeout_seconds = tool_input.tool_args.get("timeout_seconds", 1200)
        
        if not goal or not goal.strip():
            return json.dumps({
                "error": t("delegation_goal_required", llm_context.language)
            })
        
        parent_conversation_id = getattr(llm_context, "conversation_id", None)
        parent_agent_id = getattr(llm_context, "assistant", None)
        
        task_id = generate_task_id()
        
        asyncio.create_task(delegate_task_async(
            hass,
            task_id=task_id,
            goal=goal,
            context=context,
            parent_agent_id=parent_agent_id,
            parent_conversation_id=parent_conversation_id,
            role=role,
            timeout_seconds=timeout_seconds,
            language=llm_context.language,
        ))
        
        return json.dumps({
            "task_id": task_id,
            "status": "spawned",
            "message": t("delegation_spawned", llm_context.language),
        }, ensure_ascii=False)


class DelegateBatchTool(llm.Tool):
    
    name = "DelegateBatch"
    description = (
        "Spawn multiple subagents in parallel. Each task gets its own "
        "isolated context. All tasks run concurrently and results are "
        "returned together.\n\n"
        "Use for work that can be decomposed into independent subtasks."
    )
    
    parameters = vol.Schema({
        vol.Required("tasks"): [{
            vol.Required("goal"): str,
            vol.Optional("context"): str,
            vol.Optional("role", default="leaf"): vol.In(["leaf", "orchestrator"]),
            vol.Optional("toolsets"): [str],
        }],
    })
    
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> str:
        from .executor import delegate_batch, SubagentTask
        from .config import load_delegation_config
        
        tasks_data = tool_input.tool_args.get("tasks", [])
        lang = llm_context.language
        
        if not tasks_data:
            return json.dumps({
                "error": t("delegation_goal_required", lang)
            })
        
        config = load_delegation_config(hass)
        if len(tasks_data) > config.max_concurrent_children:
            return json.dumps({
                "error": t("delegation_too_many_tasks", lang).format(
                    count=len(tasks_data),
                    max=config.max_concurrent_children,
                )
            })
        
        tasks = []
        for i, task_data in enumerate(tasks_data):
            goal = task_data.get("goal", "")
            if not goal or not goal.strip():
                return json.dumps({
                    "error": t("delegation_task_missing_goal", lang).format(index=i)
                })
            
            tasks.append(SubagentTask(
                task_id=f"batch-{i}",
                goal=goal,
                context=task_data.get("context"),
                role=task_data.get("role", "leaf"),
                toolsets=task_data.get("toolsets"),
            ))
        
        parent_conversation_id = getattr(llm_context, "conversation_id", None)
        parent_agent_id = getattr(llm_context, "assistant", None)
        
        results = await delegate_batch(
            hass,
            tasks=tasks,
            parent_agent_id=parent_agent_id,
            parent_conversation_id=parent_conversation_id,
            language=lang,
        )
        
        return json.dumps({
            "results": [
                {
                    "task_index": r.task_index,
                    "task_id": r.task_id,
                    "status": r.status,
                    "summary": r.summary,
                    "error": r.error,
                    "duration_seconds": round(r.duration_seconds, 2),
                    "tool_count": r.tool_count,
                }
                for r in results
            ],
            "total_tasks": len(results),
            "completed": sum(1 for r in results if r.status == "completed"),
            "failed": sum(1 for r in results if r.status in ("error", "timeout")),
        }, ensure_ascii=False)


class DelegateStatusTool(llm.Tool):
    
    name = "DelegateStatus"
    description = (
        "Check the status of a running or completed subagent.\n\n"
        "Use after calling DelegateTask to get the result.\n"
        "Set include_logs=true to see detailed execution steps.\n"
        "If task_id is omitted, returns status of all active subagents."
    )
    
    parameters = vol.Schema({
        vol.Optional("task_id"): str,
        vol.Optional("include_logs", default=False): bool,
        vol.Optional("log_limit", default=50): int,
    })
    
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> str:
        from .executor import get_subagent_status, list_active_subagents, get_subagent_logs
        
        task_id = tool_input.tool_args.get("task_id")
        include_logs = tool_input.tool_args.get("include_logs", False)
        log_limit = tool_input.tool_args.get("log_limit", 50)
        
        if task_id:
            status = get_subagent_status(task_id)
            if status is None:
                return json.dumps({
                    "task_id": task_id,
                    "status": "not_found",
                    "message": "Task not found or already cleaned up",
                })
            result = dict(status)
            if include_logs:
                logs = get_subagent_logs(task_id, log_limit)
                result["logs"] = logs or []
            return json.dumps(result, ensure_ascii=False)
        else:
            active = list_active_subagents()
            return json.dumps({
                "active_count": len(active),
                "subagents": active,
            }, ensure_ascii=False)


class DelegateAskParentTool(llm.Tool):
    
    name = "DelegateAskParent"
    description = (
        "Ask parent AI a question and wait for answer. "
        "Use when you need clarification or additional info from user. "
        "Parent receives question through DelegateStatus and can respond."
    )
    
    parameters = vol.Schema({
        vol.Required("question"): str,
        vol.Optional("timeout_seconds", default=300): int,
    })
    
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> str:
        from .executor import ask_parent_question, get_pending_questions
        
        question = tool_input.tool_args.get("question", "")
        timeout = tool_input.tool_args.get("timeout_seconds", 300)
        
        if not question or not question.strip():
            return json.dumps({"error": "Question cannot be empty"})
        
        task_id = getattr(llm_context, "conversation_id", None)
        if task_id and task_id.startswith("subagent:"):
            task_id = task_id[9:]
        
        if not task_id:
            return json.dumps({"error": "Cannot identify subagent task_id"})
        
        result = ask_parent_question(task_id, question, timeout)
        return json.dumps(result, ensure_ascii=False)


class DelegateGetPendingQuestionsTool(llm.Tool):
    
    name = "DelegateGetPendingQuestions"
    description = (
        "Get pending questions from subagents that need user answer. "
        "Parent AI uses this to check if any subagent is waiting for user input. "
        "Returns list of questions with question_id for answering."
    )
    
    parameters = vol.Schema({
        vol.Optional("task_id"): str,
    })
    
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> str:
        from .executor import get_pending_questions
        
        task_id = tool_input.tool_args.get("task_id")
        questions = get_pending_questions(task_id)
        return json.dumps({
            "pending_count": len(questions),
            "questions": questions,
        }, ensure_ascii=False)
