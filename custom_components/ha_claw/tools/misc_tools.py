
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


def _cap_py_payload(payload: JsonObjectType) -> JsonObjectType:

    if not isinstance(payload, dict):
        return payload
    for key in ("stdout", "stderr"):
        val = payload.get(key)
        if isinstance(val, str):
            payload[key] = _cap_string(val, _PY_STREAM_MAX_CHARS)
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
    description = """Execute Python code. Two modes:

MODE A (default, fast): run inline inside Home Assistant. Use for reading/writing
HA state via the `hass` object.
    code                  : Python, assign to `result`; can use `await`.
    Available modules     : math, datetime, json, re, random, asyncio, importlib.
    Examples              : `result = len(hass.states.async_all())`
                            `result = [s.entity_id for s in hass.states.async_all() if s.state=='unavailable']`

MODE B (sandbox, powerful): run in an isolated child venv via subprocess.
    sandbox=true          : force sandbox mode.
    requirements=[...]    : pip packages to install in the sandbox (auto-triggers
                            sandbox mode if non-empty). Installed packages are
                            cached across calls.
    timeout=60            : seconds.
    No `hass` object in sandbox mode. Use for numeric work, API calls with extra
    libraries, parsing, or anything that could crash/hang the main interpreter."""
    parameters = vol.Schema(
        {
            vol.Required("code"): str,
            vol.Optional("sandbox", default=False): bool,
            vol.Optional("requirements", default=[]): list,
            vol.Optional("timeout", default=60): int,
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

        if sandbox_flag or requirements:
            from ..runtime.sandbox import run_in_sandbox

            try:
                return _cap_py_payload(
                    await run_in_sandbox(
                        hass,
                        code,
                        requirements=requirements,
                        timeout=max(5, min(timeout, 600)),
                    )
                )
            except asyncio.TimeoutError:
                return {"success": False, "error": f"Sandbox execution exceeded {timeout}s"}
            except Exception as err:
                return {"success": False, "error": str(err)}

        forbidden = [
            "subprocess",
            "__import__",
            "file(",
            "compile(",
            "globals(",
            "locals(",
        ]
        if "open(" in code and "pathlib" not in code:
            forbidden.append("open(")
        if "os." in code and "os.path" not in code:
            forbidden.append("os.")
        for f in forbidden:
            if f in code:
                return {"success": False, "error": f"Forbidden operation (use sandbox=true to bypass): {f}"}

        import datetime
        import importlib
        import math
        import pathlib
        import random
        import re

        safe_globals = {
            "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
            "len": len, "range": range, "enumerate": enumerate, "zip": zip,
            "int": int, "float": float, "str": str, "bool": bool, "list": list,
            "dict": dict, "set": set, "tuple": tuple,
            "sorted": sorted, "reversed": reversed, "map": map, "filter": filter,
            "pow": pow, "divmod": divmod, "hex": hex, "bin": bin, "oct": oct,
            "any": any, "all": all, "isinstance": isinstance, "type": type,
            "dir": dir, "getattr": getattr, "hasattr": hasattr,
            "print": lambda *args: None,
            "math": math, "datetime": datetime, "json": json, "re": re,
            "random": random, "asyncio": asyncio, "importlib": importlib,
            "pathlib": pathlib, "open": open,
            "hass": hass,
        }

        try:
            if "await " in code:
                wrapped = (
                    "async def __run__():\n    result = None\n"
                    + "\n".join(f"    {line}" for line in code.split("\n"))
                    + "\n    return result"
                )
                exec(wrapped, safe_globals)
                result = await safe_globals["__run__"]()
            else:
                local_vars = {"result": None}
                exec(code, safe_globals, local_vars)
                result = local_vars.get("result")

            if result is None:
                return {"success": True, "result": "Code executed"}
            if isinstance(result, (list, dict)):
                return _cap_py_payload({"success": True, "result": result})
            return _cap_py_payload({"success": True, "result": str(result)})
        except Exception as e:
            return {"success": False, "error": str(e)}


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
                from ..runtime.state import _active_conversation_id
                conv_id = _active_conversation_id.get()
                if conv_id and conv_id.startswith("wechat:"):
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
        "Fetch a single frame from a camera and return it as base64 JPEG for vision analysis. "
        "Heavily compressed (default longest side 640px, target 40KB) so the upstream LLM server can accept the payload. "
        "Camera entities are accessible here even if not exposed to the assistant. "
        "Usage: pass camera_entity='list' (or empty) to enumerate cameras; then call again with a concrete entity_id. "
        "Params: camera_entity (entity_id / friendly name / 'list'), max_dim (default 640), target_kb (default 40)."
    )
    parameters = vol.Schema({
        vol.Optional("camera_entity", default=""): str,
        vol.Optional("max_dim", default=640): int,
        vol.Optional("target_kb", default=40): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        camera_entity = (tool_input.tool_args.get("camera_entity", "") or "").strip()
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

            return {
                "success": True,
                "camera_entity": target_camera,
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
        except Exception as err:
            _LOGGER.error("CameraAnalyzeTool error: %s", err)
            return {"success": False, "error": f"Failed to capture camera frame: {err}"}


class GetConversationHistoryTool(llm.Tool):
    name = "GetConversationHistory"
    description = "Get current conversation history to review previous dialogue content."
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
            return {"success": True, "history": "", "message": "No conversation history yet"}

        return {"success": True, "history": context_str, "turns": len(history.get_history(conv_id))}
