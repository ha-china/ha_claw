
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from ..runtime import get_global_state, get_output_state, set_current_thought
from ..runtime.ha_guide_store import (
    get_homeassistant_guide_doc,
    get_homeassistant_guide_overview,
    list_homeassistant_guide_docs,
    search_homeassistant_guide,
)
from ..runtime.heartbeat_store import (
    async_clear_heartbeat_result,
    async_delete_heartbeat_task,
    async_list_heartbeat_tasks,
    async_record_heartbeat_result,
    async_upsert_heartbeat_task,
)
from ..runtime.loop_controller import get_loop_status, record_thought
from ..runtime.memory_store import (
    _transient_reason,
    async_clear_memory_entries,
    async_get_memory_entry,
    async_list_memory_entries,
    async_save_memory_entry_result,
)
from ..runtime.native_chatlog_bridge import emit_live_thinking_delta
from ..runtime.route_hints import build_next_action, build_route_envelope, build_route_hint
from ..runtime.skill_store import (
    async_install_skill,
    async_save_master_prompt,
    get_installed_skill,
    infer_skill_name,
    infer_skill_name_from_url,
    list_installed_skills,
    load_master_prompt,
)
from ..sensor import async_sync_heartbeat_sensor
from ..runtime.workspace_store import (
    async_save_workspace_doc,
    get_today_memory_doc,
    get_workspace_doc,
    list_workspace_docs,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_github_raw_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc == "raw.githubusercontent.com":
        return url
    if parsed.netloc == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _, ref = parts[:4]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{rest}"
    return url


async def _fetch_remote_skill_markdown(url: str) -> str:
    raw_url = _normalize_github_raw_url(url)
    async with aiohttp.ClientSession() as session:
        async with session.get(raw_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                raise ValueError(f"Failed to download skill markdown: HTTP {resp.status}")
            text = await resp.text()
    if not text.strip():
        raise ValueError("The skill markdown is empty")
    return text


def _looks_like_skill_markdown(markdown: str) -> bool:
    lowered = markdown.lower()
    return "skill.md" in lowered or "alwaysapply:" in lowered or "\nname:" in lowered or "\ndescription:" in lowered


def _tool_call_success(result: object) -> bool:
    if not isinstance(result, dict):
        return True
    if "success" in result:
        return bool(result.get("success"))
    return True


def _tool_call_error(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    error = result.get("error")
    return str(error) if error not in (None, "") else None


async def _execute_tool_spec(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    tool_name: str,
    tool_args: dict,
) -> dict[str, object]:
    from .registry import build_tool_map

    if tool_name == "ParallelToolCall":
        return {
            "tool": tool_name,
            "args": tool_args,
            "success": False,
            "error": "ParallelToolCall does not support recursive execution",
        }

    tool_cls = build_tool_map().get(tool_name)
    if tool_cls is None:
        return {
            "tool": tool_name,
            "args": tool_args,
            "success": False,
            "error": f"Unknown tool: {tool_name}",
        }

    tool = tool_cls()
    try:
        result = await tool.async_call(
            hass,
            llm.ToolInput(tool_name=tool_name, tool_args=tool_args),
            llm_context,
        )
    except Exception as err:
        return {
            "tool": tool_name,
            "args": tool_args,
            "success": False,
            "error": str(err),
        }

    return {
        "tool": tool_name,
        "args": tool_args,
        "success": _tool_call_success(result),
        "error": _tool_call_error(result),
        "result": result,
    }


_PY_RESULT_MAX_CHARS = 6000
_PY_STREAM_MAX_CHARS = 4000


def _cap_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"...[truncated, {len(value)} chars total]"


def _trim_stream(value: str, limit: int = _PY_STREAM_MAX_CHARS) -> str:
    """Trim stdout/stderr keeping the head and tail (loses middle).

    Useful for long output where both the setup and the final error matter.
    """
    if not value or len(value) <= limit:
        return value
    half = limit // 2
    head = value[:half]
    tail = value[-(limit - half):]
    dropped = len(value) - (len(head) + len(tail))
    return f"{head}\n...[truncated {dropped} chars]...\n{tail}"


def _cap_py_payload(payload: JsonObjectType) -> JsonObjectType:

    if not isinstance(payload, dict):
        return payload
    for key in ("stdout", "stderr"):
        val = payload.get(key)
        if isinstance(val, str):
            payload[key] = _trim_stream(val, _PY_STREAM_MAX_CHARS)
    result_val = payload.get("result")
    if isinstance(result_val, str):
        payload["result"] = _cap_string(result_val, _PY_RESULT_MAX_CHARS)
    elif isinstance(result_val, (list, dict)):
        try:
            rendered = json.dumps(result_val, ensure_ascii=False, default=str)
        except Exception:
            rendered = str(result_val)
        if len(rendered) > _PY_RESULT_MAX_CHARS:
            payload["result"] = (
                rendered[:_PY_RESULT_MAX_CHARS]
                + f"...[truncated, {len(rendered)} chars total]"
            )
    return payload


class ExecutePythonTool(llm.Tool):
    name = "ExecutePython"
    description = (
        "Execute Python code. Two modes. "
        "inline (default): runs inside the HA process with full builtins and a "
        "`hass` object; supports top-level `await`; stdout/stderr/traceback are "
        "captured and returned. If the last statement is an expression its value "
        "is returned as result (Jupyter-like). "
        "sandbox (sandbox=true or non-empty requirements): runs in an isolated "
        "child venv subprocess with optional env/cwd/stdin/pip_index_url. "
        "No `hass` there. "
        "Destructive operations (deleting files, rmtree, dropping tables, shell "
        "`rm -rf`, etc.) must be confirmed with the user before running."
    )
    parameters = vol.Schema(
        {
            vol.Required("code"): str,
            vol.Optional("sandbox", default=False): bool,
            vol.Optional("requirements", default=[]): list,
            vol.Optional("timeout", default=60): int,
            vol.Optional("dry_run", default=False): bool,
            # sandbox-only extras
            vol.Optional("env", default={}): dict,
            vol.Optional("cwd", default=""): str,
            vol.Optional("stdin", default=""): str,
            vol.Optional("pip_index_url", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        code = tool_input.tool_args.get("code", "")
        sandbox_flag = bool(tool_input.tool_args.get("sandbox", False))
        requirements = [
            str(item)
            for item in tool_input.tool_args.get("requirements", []) or []
            if str(item).strip()
        ]
        timeout = int(tool_input.tool_args.get("timeout", 60) or 60)
        dry_run = bool(tool_input.tool_args.get("dry_run", False))
        env = tool_input.tool_args.get("env") or {}
        cwd = str(tool_input.tool_args.get("cwd", "") or "")
        stdin_text = str(tool_input.tool_args.get("stdin", "") or "")
        pip_index_url = str(tool_input.tool_args.get("pip_index_url", "") or "")

        if sandbox_flag or requirements:
            from ..runtime.sandbox import run_in_sandbox

            try:
                return _cap_py_payload(
                    await run_in_sandbox(
                        hass,
                        code,
                        requirements=requirements,
                        timeout=max(5, min(timeout, 600)),
                        env={str(k): str(v) for k, v in (env or {}).items()},
                        cwd=cwd or None,
                        stdin=stdin_text or None,
                        pip_index_url=pip_index_url or None,
                        dry_run=dry_run,
                    )
                )
            except asyncio.TimeoutError:
                return {"success": False, "error": f"Sandbox execution exceeded {timeout}s"}
            except Exception as err:
                return {"success": False, "error": str(err)}

        return await self._run_inline(hass, code, timeout=timeout, dry_run=dry_run)

    async def _run_inline(
        self, hass: HomeAssistant, code: str, *, timeout: int, dry_run: bool
    ) -> JsonObjectType:
        """Inline execution with full builtins, stdout/stderr capture, top-level
        await, automatic trailing-expression result and structured errors."""
        import ast
        import builtins as _builtins
        import contextlib
        import io
        import sys
        import time
        import traceback

        PyCF_ALLOW_TOP_LEVEL_AWAIT = 0x2000  # compile flag, Py3.8+

        # Rewrite the trailing expression (if any) into an assignment to
        # __auto_result__ so we can surface the value the same way Jupyter does
        # — without breaking code that explicitly assigns to `result`.
        auto_key = "__auto_result__"
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as err:
            return {
                "success": False,
                "error": f"SyntaxError: {err.msg} (line {err.lineno})",
                "traceback": traceback.format_exc(),
            }

        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body[-1]
            assign = ast.Assign(
                targets=[ast.Name(id=auto_key, ctx=ast.Store())],
                value=last.value,
            )
            ast.copy_location(assign, last)
            tree.body[-1] = assign
            ast.fix_missing_locations(tree)

        try:
            compiled = compile(
                tree, "<execute_python>", "exec",
                flags=PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
        except SyntaxError as err:
            return {
                "success": False,
                "error": f"SyntaxError: {err.msg} (line {err.lineno})",
                "traceback": traceback.format_exc(),
            }

        if dry_run:
            return {"success": True, "dry_run": True, "message": "Compile OK; not executed"}

        # Full builtins, plus a few convenience handles. We intentionally do
        # NOT filter builtins — the AI is running as the HA admin here.
        globals_: dict[str, Any] = {
            "__builtins__": _builtins,
            "hass": hass,
            "asyncio": asyncio,
            "json": json,
        }

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        started = time.perf_counter()
        err_info: dict[str, Any] | None = None

        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                # With PyCF_ALLOW_TOP_LEVEL_AWAIT, the compiled module body may
                # evaluate to a coroutine when awaits are present. Use eval (not
                # exec) so we can capture and await it; regular modules just
                # return None.
                import inspect as _inspect

                maybe = eval(compiled, globals_)
                if _inspect.iscoroutine(maybe):
                    await asyncio.wait_for(maybe, timeout=max(1, timeout))
        except asyncio.TimeoutError:
            err_info = {
                "error": f"Inline execution exceeded {timeout}s",
                "traceback": traceback.format_exc(),
            }
        except SystemExit as err:
            err_info = {
                "error": f"SystemExit: {err.code}",
                "traceback": traceback.format_exc(),
            }
        except BaseException:  # noqa: BLE001 - surface everything to AI
            err_info = {
                "error": traceback.format_exception_only(*sys.exc_info()[:2])[-1].strip(),
                "traceback": traceback.format_exc(),
            }

        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout_text = _trim_stream(stdout_buf.getvalue())
        stderr_text = _trim_stream(stderr_buf.getvalue())

        if err_info is not None:
            return _cap_py_payload({
                "success": False,
                **err_info,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "duration_ms": duration_ms,
            })

        # Prefer explicit `result`, otherwise the trailing expression value.
        result = globals_.get("result")
        if result is None:
            result = globals_.get(auto_key)
        return _cap_py_payload({
            "success": True,
            "result": result,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_ms": duration_ms,
        })


class SystemControlTool(llm.Tool):
    name = "SystemControl"
    description = "System control tool. Use it to set global injection, output mode, and inspect runtime status."
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["set_global_inject", "set_output_mode", "get_status"]),
        vol.Optional("value", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        value = tool_input.tool_args.get("value", "")
        if action == "set_global_inject":
            get_global_state(hass)["inject"] = value
            return {"success": True, "message": f"Global inject set: {value[:50]}..."}
        if action == "set_output_mode":
            if value in ["brief", "detailed", "list", "code", ""]:
                get_output_state(hass)["mode"] = value
                return {"success": True, "message": f"Output mode set: {value or 'normal'}"}
            return {"success": False, "error": "Invalid mode"}
        if action == "get_status":
            return {
                "success": True,
                "global_inject": get_global_state(hass).get("inject", "")[:100],
                "output_mode": get_output_state(hass).get("mode", "normal"),
            }
        return {"success": False, "error": "Unknown action"}


class ConversationMemoryTool(llm.Tool):
    name = "ConversationMemory"
    description = "Save durable facts to persistent memory. Save proactively when you discover user preferences, corrections, or environment facts. Params: action(save/get/list/clear), key, value"
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["save", "get", "list", "clear"]),
        vol.Optional("key", default=""): str,
        vol.Optional("value", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        key = tool_input.tool_args.get("key", "")
        value = tool_input.tool_args.get("value", "")
        if action == "save" and key:
            (
                transient_reason,
                recommendation,
                suggested_tool,
                suggested_action,
                suggested_args,
            ) = _transient_reason(str(key), str(value))
            if transient_reason == "transient_progress_note":
                return {
                    "success": True,
                    "skipped": True,
                    "status": "skipped_transient",
                    "reason": transient_reason,
                    "message": recommendation,
                    "suggested_tool": suggested_tool,
                    "suggested_action": suggested_action,
                    "suggested_args": suggested_args or {},
                    **build_route_envelope(
                        transient_reason,
                        suggested_tool,
                        suggested_action,
                        args=suggested_args,
                    ),
                    "route_hint": build_route_hint(
                        transient_reason,
                        suggested_tool,
                        suggested_action,
                        args=suggested_args,
                        recommendation=recommendation,
                    ),
                }
            save_result = await async_save_memory_entry_result(hass, key, value)
            status = save_result["status"]
            if status in {"stored", "updated"}:
                return {
                    "success": True,
                    "message": f"Saved: {save_result['key']}",
                    **save_result,
                }
            if status == "skipped_duplicate":
                return {
                    "success": True,
                    "skipped": True,
                    "message": f"Memory already stored as {save_result['existing_key']}",
                    **save_result,
                }
            transient_message = (
                save_result["recommendation"]
                or "Skipped transient note; long-term memory keeps only stable facts"
            )
            return {
                "success": True,
                "skipped": True,
                "message": transient_message,
                **save_result,
            }
        if action == "get" and key:
            return {
                "success": True,
                "value": await async_get_memory_entry(hass, key),
                **build_route_envelope("memory_read", "ConversationMemory", "list"),
                "route_hint": build_route_hint("memory_read", "ConversationMemory", "list"),
            }
        if action == "list":
            entries = await async_list_memory_entries(hass)
            return {
                "success": True,
                "keys": [entry["key"] for entry in entries],
                "entries": entries,
                **(build_route_envelope("memory_index", "ConversationMemory", "get", args={"key": entries[0]["key"]}) if entries else build_route_envelope("memory_index", "ConversationMemory", "save", args={"key": "", "value": ""})),
                "route_hint": (build_route_hint("memory_index", "ConversationMemory", "get", args={"key": entries[0]["key"]}) if entries else build_route_hint("memory_index", "ConversationMemory", "save", args={"key": "", "value": ""})),
            }
        if action == "clear":
            await async_clear_memory_entries(hass)
            return {
                "success": True,
                "message": "Memory cleared",
                **build_route_envelope("memory_cleared", "ConversationMemory", "list"),
                "route_hint": build_route_hint("memory_cleared", "ConversationMemory", "list"),
            }
        return {"success": False, "error": "Invalid action or missing key"}


class InstallSkillTool(llm.Tool):
    name = "InstallSkill"
    description = "Install Markdown skills. Supports direct name+markdown input, or source_url from a GitHub/blob/raw link. Params: name?, markdown?, source_url?, overwrite(false)"
    parameters = vol.Schema(
        {
            vol.Optional("name", default=""): str,
            vol.Optional("markdown", default=""): str,
            vol.Optional("source_url", default=""): str,
            vol.Optional("overwrite", default=False): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        try:
            source_url = tool_input.tool_args.get("source_url", "").strip()
            markdown = tool_input.tool_args.get("markdown", "")
            name = tool_input.tool_args.get("name", "").strip()

            if source_url:
                markdown = await _fetch_remote_skill_markdown(source_url)
                if not _looks_like_skill_markdown(markdown):
                    return {
                        "success": False,
                        "error": "The remote document does not look like installable skill markdown",
                        "source_url": source_url,
                    }
                if not name:
                    name = infer_skill_name(infer_skill_name_from_url(source_url), markdown)

            if not markdown.strip():
                return {"success": False, "error": "Missing markdown or source_url"}

            if not name:
                name = infer_skill_name("skill", markdown)

            skill_path = await async_install_skill(
                hass,
                name,
                markdown,
                overwrite=tool_input.tool_args.get("overwrite", False),
            )
            return {
                "success": True,
                "name": name,
                "file": skill_path.name,
                "path": str(skill_path),
                "message": f"Skill installed: {skill_path.stem}",
                **({"source_url": source_url} if source_url else {}),
            }
        except FileExistsError as err:
            return {"success": False, "error": str(err)}
        except Exception as err:
            return {"success": False, "error": str(err)}


class ListInstalledSkillsTool(llm.Tool):
    name = "ListInstalledSkills"
    description = "List installed Markdown skills."
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        skills = list_installed_skills()
        return {
            "success": True,
            "count": len(skills),
            "skills": skills,
        }


class GetInstalledSkillTool(llm.Tool):
    name = "GetInstalledSkill"
    description = "Read the full content of one installed Markdown skill. Params: name/slug/file"
    parameters = vol.Schema({vol.Required("name"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        try:
            return {"success": True, **get_installed_skill(tool_input.tool_args.get("name", ""))}
        except ValueError as err:
            return {"success": False, "error": str(err)}


class HomeAssistantGuideTool(llm.Tool):
    name = "HomeAssistantGuide"
    description = (
        "Read the bundled Home Assistant guide inside claw_assistant. "
        "Supports action=overview/list/get/search, prioritizes runtime docs adapted to the integration permission model, "
        "and preserves source docs for full teaching reference."
    )
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(["overview", "list", "get", "search"]),
            vol.Optional("name", default=""): str,
            vol.Optional("query", default=""): str,
            vol.Optional("limit", default=5): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        if action == "overview":
            return get_homeassistant_guide_overview()
        if action == "list":
            docs = list_homeassistant_guide_docs()
            return {"success": True, "count": len(docs), "docs": docs}
        if action == "get":
            return get_homeassistant_guide_doc(tool_input.tool_args.get("name", ""))
        return search_homeassistant_guide(
            tool_input.tool_args.get("query", ""),
            limit=max(1, int(tool_input.tool_args.get("limit", 5))),
        )


class SetMasterPromptTool(llm.Tool):
    name = "SetMasterPrompt"
    description = "Set the global Master Prompt markdown. Params: markdown"
    parameters = vol.Schema({vol.Required("markdown"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        markdown = tool_input.tool_args.get("markdown", "")
        if not markdown.strip():
            return {"success": False, "error": "Master prompt markdown is empty"}
        path = await async_save_master_prompt(hass, markdown)
        return {
            "success": True,
            "path": str(path),
            "message": "Master prompt updated",
        }


class GetMasterPromptTool(llm.Tool):
    name = "GetMasterPrompt"
    description = "Read the current global Master Prompt markdown."
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        return {
            "success": True,
            "markdown": load_master_prompt(),
        }


class ListWorkspaceDocsTool(llm.Tool):
    name = "ListWorkspaceDocs"
    description = "List workspace markdown documents and their state."
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        return {"success": True, "docs": list_workspace_docs()}


class GetWorkspaceDocTool(llm.Tool):
    name = "GetWorkspaceDoc"
    description = "Read one workspace markdown document. Params: name(AGENTS/BOOTSTRAP/HEARTBEAT/IDENTITY/MEMORY/SOUL/TOOLS/USER/TODAY_MEMORY)"
    parameters = vol.Schema({vol.Required("name"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        try:
            name = tool_input.tool_args.get("name", "")
            if str(name).strip().upper() == "TODAY_MEMORY":
                return {"success": True, **get_today_memory_doc()}
            return {"success": True, **get_workspace_doc(tool_input.tool_args.get("name", ""))}
        except ValueError as err:
            return {"success": False, "error": str(err)}


class HeartbeatManagerTool(llm.Tool):
    name = "HeartbeatManager"
    description = (
        "Manage heartbeat follow-up tasks. "
        "schedule: cron ('*/30 * * * *', '0 9 * * *') or interval ('30m', '2h', 'every 1d'). "
        "notify_channel: where to push results, e.g. 'wechat:account_id:user_id' (from conversation_id). "
        "Params: action(list/upsert/delete/record/clear_state), slug/title/schedule/objective/steps/notes/status/note/enabled/delete_after_success/notify_channel."
    )
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(["list", "upsert", "delete", "record", "clear_state"]),
            vol.Optional("slug", default=""): str,
            vol.Optional("title", default=""): str,
            vol.Optional("schedule", default=""): str,
            vol.Optional("objective", default=""): str,
            vol.Optional("steps", default=""): str,
            vol.Optional("notes", default=""): str,
            vol.Optional("status", default=""): str,
            vol.Optional("note", default=""): str,
            vol.Optional("enabled", default=True): bool,
            vol.Optional("delete_after_success", default=False): bool,
            vol.Optional("notify_channel", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        result = await self._do_action(hass, tool_input, action)
        if action in ("upsert", "delete", "record", "clear_state"):
            await async_sync_heartbeat_sensor(hass)
        return result

    async def _do_action(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, action: str
    ) -> JsonObjectType:
        if action == "list":
            tasks = await async_list_heartbeat_tasks(hass)
            return {
                "success": True,
                "count": len(tasks),
                "tasks": tasks,
                **build_route_envelope("follow_up_task_index", "HeartbeatManager", "record" if tasks else "upsert"),
                "route_hint": build_route_hint("follow_up_task_index", "HeartbeatManager", "record" if tasks else "upsert"),
            }

        if action == "upsert":
            title = tool_input.tool_args.get("title", "").strip()
            if not title:
                return {"success": False, "error": "Heartbeat title is required"}
            notify_channel = tool_input.tool_args.get("notify_channel", "")
            if not notify_channel:
                from ..runtime.state import _active_conversation_id, is_im_channel
                conv_id = _active_conversation_id.get()
                if is_im_channel(conv_id):
                    notify_channel = conv_id
            path = await async_upsert_heartbeat_task(
                hass,
                slug=tool_input.tool_args.get("slug", ""),
                title=title,
                schedule=tool_input.tool_args.get("schedule", ""),
                objective=tool_input.tool_args.get("objective", ""),
                steps=tool_input.tool_args.get("steps", ""),
                notes=tool_input.tool_args.get("notes", ""),
                enabled=bool(tool_input.tool_args.get("enabled", True)),
                delete_after_success=bool(tool_input.tool_args.get("delete_after_success", False)),
                notify_channel=notify_channel,
            )
            slug = tool_input.tool_args.get("slug", "").strip() or title
            return {
                "success": True,
                "path": str(path),
                "message": f"Heartbeat follow-up saved: {title}",
                **build_route_envelope(
                    "follow_up_task_saved",
                    "HeartbeatManager",
                    "record",
                    args={"slug": slug, "status": "success", "note": ""},
                ),
                "route_hint": build_route_hint(
                    "follow_up_task_saved",
                    "HeartbeatManager",
                    "record",
                    args={"slug": slug, "status": "success", "note": ""},
                ),
            }

        if action == "delete":
            slug = tool_input.tool_args.get("slug", "").strip()
            if not slug:
                return {"success": False, "error": "Heartbeat slug is required"}
            path = await async_delete_heartbeat_task(hass, slug)
            return {
                "success": True,
                "path": str(path),
                "message": f"Heartbeat follow-up deleted: {slug}",
                **build_route_envelope("follow_up_task_deleted", "HeartbeatManager", "list"),
                "route_hint": build_route_hint("follow_up_task_deleted", "HeartbeatManager", "list"),
            }

        if action == "record":
            slug = tool_input.tool_args.get("slug", "").strip()
            status = tool_input.tool_args.get("status", "").strip()
            if not slug or not status:
                return {"success": False, "error": "Heartbeat slug and status are required"}
            record_result = await async_record_heartbeat_result(
                hass,
                slug=slug,
                status=status,
                note=tool_input.tool_args.get("note", ""),
            )
            return {
                "success": True,
                "path": record_result["state_path"],
                "heartbeat_path": record_result["heartbeat_path"],
                "task_deleted": record_result["task_deleted"],
                "message": (
                    f"Heartbeat follow-up completed and removed: {slug}"
                    if record_result["task_deleted"]
                    else f"Heartbeat follow-up state updated: {slug}"
                ),
                **(
                    build_route_envelope("follow_up_task_completed", "HeartbeatManager", "list")
                    if record_result["task_deleted"]
                    else build_route_envelope(
                        "follow_up_task_updated",
                        "HeartbeatManager",
                        "record",
                        args={"slug": slug, "status": "success", "note": ""},
                    )
                ),
                "route_hint": (
                    build_route_hint("follow_up_task_completed", "HeartbeatManager", "list")
                    if record_result["task_deleted"]
                    else build_route_hint(
                        "follow_up_task_updated",
                        "HeartbeatManager",
                        "record",
                        args={"slug": slug, "status": "success", "note": ""},
                    )
                ),
            }

        state_path = await async_clear_heartbeat_result(
            hass,
            tool_input.tool_args.get("slug", ""),
        )
        return {
            "success": True,
            "path": str(state_path),
            "message": "Heartbeat state cleared",
            **build_route_envelope("follow_up_state_cleared", "HeartbeatManager", "list"),
            "route_hint": build_route_hint("follow_up_state_cleared", "HeartbeatManager", "list"),
        }


class SetWorkspaceDocTool(llm.Tool):
    name = "SetWorkspaceDoc"
    description = "Write one workspace markdown document. Params: name, markdown"
    parameters = vol.Schema(
        {
            vol.Required("name"): str,
            vol.Required("markdown"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        try:
            path = await async_save_workspace_doc(
                hass,
                tool_input.tool_args.get("name", ""),
                tool_input.tool_args.get("markdown", ""),
            )
            return {"success": True, "path": str(path)}
        except ValueError as err:
            return {"success": False, "error": str(err)}


class ThinkContinueTool(llm.Tool):
    name = "ThinkContinue"
    description = """Record internal reasoning steps (optional).

Parameters:
- thought: reasoning content
- next_action: next planned action (optional)"""
    parameters = vol.Schema({
        vol.Required("thought"): str,
        vol.Optional("next_action", default=""): str,
        vol.Optional("stop", default=False): bool,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        thought = tool_input.tool_args.get("thought", "")
        next_action = tool_input.tool_args.get("next_action", "")
        stop = tool_input.tool_args.get("stop", False)

        loop_status = get_loop_status(hass)
        if loop_status.get("thought_count", 0) >= 1 and not stop:
            return {
                "success": True,
                "skipped": True,
                "message": "Thought already recorded for this turn; reply to the user directly",
            }

        set_current_thought(hass, thought)
        hass.bus.async_fire("ha_crack_thought", {"thought": thought})
        await emit_live_thinking_delta(
            hass,
            agent_id=llm_context.platform or "conversation.aiwai_gua_2",
            thought=thought,
        )

        task_loop = record_thought(
            hass,
            thought=thought,
            next_action=next_action,
            stop=stop,
        )
        loop_status = get_loop_status(hass)

        if stop:
            _LOGGER.debug(f"ThinkContinue: AI terminated the loop proactively, reason: {thought[:100]}")
            return {
                "success": True,
                "stopped": True,
                "message": "Loop stopped; waiting for the user's next instruction",
                "final_thought": thought[:200] if len(thought) > 200 else thought,
                "display_text": thought,
                "loop_status": loop_status,
            }

        if loop_status["budget_exhausted"]:
            task_loop["active"] = False
            task_loop["phase"] = "budget_exhausted"
            return {
                "success": False,
                "stopped": True,
                "error": f"Reached the maximum iteration budget ({loop_status['max_iterations']}); summarize current progress and stop",
                "loop_status": loop_status,
            }

        return {
            "success": True,
            "thought_recorded": True,
        }


class ParallelToolCallTool(llm.Tool):
    name = "ParallelToolCall"
    description = "Call multiple independent tools in parallel. Best for querying multiple entities or information sources at once. Params: tools=[{name,args}]. Prefer using it with EntityQuery, HistoryQuery, ListServices, SmartDiscovery, and WebSearch. Do not replace name/args with natural language."
    parameters = vol.Schema({
        vol.Required("tools"): list,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        tools = tool_input.tool_args.get("tools", [])
        if not tools:
            return {"success": False, "error": "No tools specified"}

        deduped_specs: list[tuple[str, dict]] = []
        seen_specs: set[str] = set()
        skipped_duplicates = 0

        for raw_spec in tools:
            if not isinstance(raw_spec, dict):
                deduped_specs.append(("", {}))
                continue
            tool_name = str(raw_spec.get("name", "")).strip()
            tool_args = raw_spec.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            dedupe_key = json.dumps(
                {"name": tool_name, "args": tool_args},
                ensure_ascii=False,
                sort_keys=True,
            )
            if dedupe_key in seen_specs:
                skipped_duplicates += 1
                continue
            seen_specs.add(dedupe_key)
            deduped_specs.append((tool_name, tool_args))

        results = await asyncio.gather(
            *[
                _execute_tool_spec(hass, llm_context, tool_name, tool_args)
                for tool_name, tool_args in deduped_specs
            ]
        )
        success_count = sum(1 for item in results if item.get("success"))

        return {
            "success": success_count == len(results),
            "message": "Parallel tool execution completed",
            "count": len(results),
            "success_count": success_count,
            "failure_count": len(results) - success_count,
            "skipped_duplicates": skipped_duplicates,
            "results": results,
        }


def _compress_camera_frame(
    raw_bytes: bytes,
    max_dim: int,
    target_kb: int,
    min_quality: int = 35,
) -> tuple[bytes, int, int, int]:

    from io import BytesIO
    from PIL import Image as PILImage

    img = PILImage.open(BytesIO(raw_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > max_dim:
        scale = max_dim / longest
        w = int(w * scale)
        h = int(h * scale)
        img = img.resize((w, h), PILImage.LANCZOS)

    target_bytes = target_kb * 1024
    data = b""
    for quality in (75, 65, 55, 45, min_quality):
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= target_bytes or quality == min_quality:
            return data, w, h, quality
    return data, w, h, min_quality


class CameraAnalyzeTool(llm.Tool):
    name = "CameraAnalyze"
    description = (
        "Camera tool: discover cameras and fetch frames. "
        "camera_entity='list' → list all available cameras (works even if not exposed). "
        "mode=snapshot → returns snapshot_url + markdown_hint (for HA frontend display only). "
        "mode=analyze → returns base64 JPEG for vision analysis (describe what you see). "
        "IMPORTANT: On IM channels, do NOT call this tool to display a snapshot — use [IMAGE:camera.entity_id] directly. "
        "Only call this tool on IM when you need to analyze image content or discover available cameras. "
        "Params: camera_entity (entity_id / friendly name / 'list'), mode (snapshot|analyze, default snapshot), "
        "max_dim (default 640), target_kb (default 40)."
    )
    parameters = vol.Schema({
        vol.Optional("camera_entity", default=""): str,
        vol.Optional("mode", default="snapshot"): vol.In(["snapshot", "analyze"]),
        vol.Optional("max_dim", default=640): int,
        vol.Optional("target_kb", default=40): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        camera_entity = (tool_input.tool_args.get("camera_entity", "") or "").strip()
        mode = (tool_input.tool_args.get("mode", "snapshot") or "snapshot").strip().lower()
        max_dim = max(160, int(tool_input.tool_args.get("max_dim", 640) or 640))
        target_kb = max(10, int(tool_input.tool_args.get("target_kb", 40) or 40))

        camera_states = hass.states.async_all("camera")
        if not camera_states:
            return {"success": False, "error": "No camera entities registered in Home Assistant"}

        cameras = [
            {
                "entity_id": state.entity_id,
                "friendly_name": state.attributes.get("friendly_name", state.entity_id),
                "state": state.state,
            }
            for state in camera_states
        ]
        camera_entities = [item["entity_id"] for item in cameras]

        if not camera_entity or camera_entity.lower() == "list":
            return {
                "success": True,
                "action": "list",
                "count": len(cameras),
                "cameras": cameras,
                "message": f"Found {len(cameras)} camera(s). Call CameraAnalyze again with camera_entity set to a specific entity_id.",
            }

        target_camera = None
        if camera_entity.startswith("camera."):
            target_camera = camera_entity if camera_entity in camera_entities else None
        if not target_camera:
            needle = camera_entity.lower()
            for item in cameras:
                if needle in item["friendly_name"].lower() or needle in item["entity_id"].lower():
                    target_camera = item["entity_id"]
                    break
        if not target_camera:
            return {
                "success": False,
                "error": f"Camera '{camera_entity}' not found",
                "available_cameras": cameras,
            }

        try:
            from homeassistant.components.camera import ENTITY_IMAGE_URL
            from homeassistant.helpers.network import get_url

            snapshot_url = ""
            try:
                cam_state = hass.states.get(target_camera)
                token = cam_state.attributes.get("access_token", "") if cam_state else ""
                if token:
                    base = get_url(hass, prefer_external=False)
                    snapshot_url = f"{base}{ENTITY_IMAGE_URL.format(target_camera, token)}"
            except Exception:
                pass

            if mode == "snapshot":
                if not snapshot_url:
                    return {"success": False, "error": f"Cannot build snapshot URL for {target_camera}"}
                return {
                    "success": True,
                    "camera_entity": target_camera,
                    "mode": "snapshot",
                    "snapshot_url": snapshot_url,
                    "markdown_hint": f"![{target_camera}]({snapshot_url})",
                }

            from homeassistant.components.camera import async_get_image
            import base64

            image = await async_get_image(hass, target_camera)
            raw_bytes = image.content
            if not raw_bytes:
                return {"success": False, "error": f"Camera {target_camera} returned empty image"}

            jpeg_bytes, final_w, final_h, final_q = await hass.async_add_executor_job(
                _compress_camera_frame, raw_bytes, max_dim, target_kb
            )
            base64_data = base64.b64encode(jpeg_bytes).decode("utf-8")

            result: dict[str, Any] = {
                "success": True,
                "camera_entity": target_camera,
                "mode": "analyze",
                "image_base64": base64_data,
                "content_type": "image/jpeg",
                "width": final_w,
                "height": final_h,
                "jpeg_quality": final_q,
                "size_bytes": len(jpeg_bytes),
                "message": (
                    f"Captured {target_camera}: {final_w}x{final_h} JPEG q={final_q}, "
                    f"{len(jpeg_bytes)} bytes"
                ),
            }
            if snapshot_url:
                result["snapshot_url"] = snapshot_url
                result["markdown_hint"] = f"![{target_camera}]({snapshot_url})"
            return result
        except Exception as err:
            _LOGGER.error("CameraAnalyzeTool error: %s", err)
            return {"success": False, "error": f"Failed to capture camera frame: {err}"}


class GetConversationHistoryTool(llm.Tool):
    name = "GetConversationHistory"
    description = (
        "Inspect/manage conversation history. "
        "action=get (default): current conversation's recent turns. "
        "action=recent: turns from ALL conversations touched within the last N minutes — "
        "use this to recall what was just discussed even after the user closed the window / got a new conversation_id. "
        "action=clear: delete history for current conversation, a specific conversation_id, or all (scope=all). "
        "action=stats: counts and oldest/newest timestamps. "
        "Params: action, max_turns(default 5), include_tools(bool), recent_minutes(default 20), "
        "conversation_id(optional, override target), scope(current|all for clear)."
    )
    parameters = vol.Schema({
        vol.Optional("action", default="get"): vol.In(["get", "recent", "clear", "stats"]),
        vol.Optional("max_turns", default=5): int,
        vol.Optional("include_tools", default=False): bool,
        vol.Optional("recent_minutes", default=20): vol.Any(int, float),
        vol.Optional("conversation_id", default=""): str,
        vol.Optional("scope", default="current"): vol.In(["current", "all"]),
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..conversation_utils import get_conversation_history
        from ..runtime.state import get_active_conversation_state, get_conversation_status

        args = tool_input.tool_args
        action = args.get("action", "get")
        max_turns = int(args.get("max_turns", 5) or 5)
        include_tools = bool(args.get("include_tools", False))
        recent_minutes = float(args.get("recent_minutes", 20) or 20)
        explicit_conv_id = (args.get("conversation_id") or "").strip()
        scope = args.get("scope", "current")

        history = get_conversation_history()

        def _resolve_conv_id() -> str:
            if explicit_conv_id:
                return explicit_conv_id
            active = get_active_conversation_state(hass).get("id")
            if active:
                return active
            last = get_conversation_status(hass).get("last_conversation_id")
            return last or "default"

        if action == "stats":
            stats = history.get_stats()
            stats["success"] = True
            return stats

        if action == "recent":
            conversations = history.get_recent_across_conversations(
                minutes=recent_minutes,
                max_turns_per_conv=max_turns,
            )
            return {
                "success": True,
                "window_minutes": recent_minutes,
                "conversation_count": len(conversations),
                "conversations": conversations,
                "message": (
                    "No conversations touched in the last "
                    f"{recent_minutes} minutes"
                ) if not conversations else None,
            }

        if action == "clear":
            if scope == "all":
                removed = history.clear()
                return {
                    "success": True,
                    "scope": "all",
                    "removed_turns": removed,
                }
            target = _resolve_conv_id()
            removed = history.clear(target)
            return {
                "success": True,
                "scope": "conversation",
                "conversation_id": target,
                "removed_turns": removed,
            }

        # action == "get"
        conv_id = _resolve_conv_id()
        context_str = history.get_recent_context(conv_id, max_turns, include_tools)
        turns = history.get_history(conv_id)
        if not context_str:
            return {
                "success": True,
                "conversation_id": conv_id,
                "history": "",
                "turns": 0,
                "message": (
                    "No history for the current conversation. Try action=recent "
                    "to look across conversations touched in the last 20 minutes."
                ),
            }
        return {
            "success": True,
            "conversation_id": conv_id,
            "history": context_str,
            "turns": len(turns),
        }
