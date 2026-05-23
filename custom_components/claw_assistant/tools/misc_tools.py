
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
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
from ..runtime.events import fire_live_progress
from ..runtime.route_hints import build_next_action, build_route_envelope, build_route_hint
from ..runtime.skill_store import (
    async_get_installed_skill,
    async_install_skill,
    async_save_master_prompt,
    infer_skill_name,
    infer_skill_name_from_url,
    list_installed_skills,
    load_master_prompt,
)
from ..runtime.tool_progress import tool_progress_line
from ..sensor import async_sync_heartbeat_sensor
from ..runtime.workspace_store import (
    async_save_workspace_doc,
    async_set_bootstrap_active,
    get_today_memory_doc,
    get_workspace_doc,
    list_workspace_docs,
)

_LOGGER = logging.getLogger(__name__)

_OUTPUT_MODE_ALIASES = {
    "": "",
    "normal": "",
    "default": "",
    "auto": "",
    "brief": "brief",
    "concise": "brief",
    "short": "brief",
    "detailed": "detailed",
    "detail": "detailed",
    "verbose": "detailed",
    "list": "list",
    "bullets": "list",
    "bullet": "list",
    "code": "code",
}
_OUTPUT_MODE_VALUES = sorted(_OUTPUT_MODE_ALIASES)


def _has_top_level_async(tree) -> bool:
    """Return True when module code requires top-level await execution."""

    import ast

    class _Finder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.found = False

        def visit_Await(self, node) -> None:  # type: ignore[no-untyped-def]
            self.found = True

        def visit_AsyncFor(self, node) -> None:  # type: ignore[no-untyped-def]
            self.found = True

        def visit_AsyncWith(self, node) -> None:  # type: ignore[no-untyped-def]
            self.found = True

        def visit_FunctionDef(self, node) -> None:  # type: ignore[no-untyped-def]
            return

        def visit_AsyncFunctionDef(self, node) -> None:  # type: ignore[no-untyped-def]
            return

        def visit_ClassDef(self, node) -> None:  # type: ignore[no-untyped-def]
            return

    finder = _Finder()
    for stmt in tree.body:
        finder.visit(stmt)
        if finder.found:
            return True
    return False


class _MainLoopProxy:
    """Route HA core method calls back to the main event loop.

    Used by ExecutePython (inline mode, top-level-await branch) so that user
    code runs on a worker-thread event loop — synchronous I/O inside user
    code (e.g. ``Path.write_bytes``) no longer blocks HA's main loop.

    Rule for AI: every ``hass.*`` method call must be ``await``-ed, including
    ones that used to be synchronous (e.g. ``hass.config.path``,
    ``hass.states.get``). Non-callable attributes (``hass.config.config_dir``)
    pass through unchanged.
    """

    __slots__ = ("_t", "_loop")

    def __init__(self, target: Any, main_loop: "asyncio.AbstractEventLoop") -> None:
        object.__setattr__(self, "_t", target)
        object.__setattr__(self, "_loop", main_loop)

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"_MainLoopProxy({type(self._t).__name__})"

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._t, name)
        return _smart_wrap_for_main_loop(attr, self._loop)


def _smart_wrap_for_main_loop(attr: Any, main_loop: "asyncio.AbstractEventLoop") -> Any:
    """Wrap a HA attribute so it executes on ``main_loop``.

    - Callable + not a class: returns an async wrapper (user must ``await``).
      The wrapper schedules execution on ``main_loop`` via
      ``run_coroutine_threadsafe`` and transparently awaits any returned
      coroutine / Future before delivering the result back to the worker
      thread's event loop.
    - HA core sub-object (services / states / bus / config / ...): wrapped
      recursively as another ``_MainLoopProxy``.
    - Other values (str, primitives, dicts): returned untouched.
    """
    import inspect as _inspect

    if callable(attr) and not isinstance(attr, type):
        async def _route(*args: Any, **kwargs: Any) -> Any:
            async def _on_loop() -> Any:
                result = attr(*args, **kwargs)
                while _inspect.iscoroutine(result) or asyncio.isfuture(result):
                    result = await result
                return result

            future = asyncio.run_coroutine_threadsafe(_on_loop(), main_loop)
            return await asyncio.wrap_future(future)

        _route.__name__ = getattr(attr, "__name__", "main_loop_proxy")
        _route.__qualname__ = _route.__name__
        return _route

    module = getattr(type(attr), "__module__", "") or ""
    if module.startswith("homeassistant."):
        return _MainLoopProxy(attr, main_loop)
    return attr


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


def _ensure_json_serializable(obj: object) -> object:
    """Ensure an object is JSON serializable, converting non-serializable types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {k: _ensure_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ensure_json_serializable(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


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
        "result": _ensure_json_serializable(result),
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


def _verify_generated_file(path: Path) -> dict[str, object]:
    try:
        stat = path.stat()
    except OSError as err:
        return {
            "exists": False,
            "verified": False,
            "error": str(err),
        }

    return {
        "exists": True,
        "verified": stat.st_size > 0,
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


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
    elif result_val is not None:
        try:
            rendered = json.dumps(result_val, ensure_ascii=False, default=str)
            payload["result"] = _cap_string(rendered, _PY_RESULT_MAX_CHARS)
        except Exception:
            payload["result"] = _cap_string(str(result_val), _PY_RESULT_MAX_CHARS)
    return payload


async def _inline_install_requirements(
    hass: HomeAssistant,
    requirements: list[str],
    *,
    pip_index_url: str | None = None,
) -> dict[str, Any]:
    """Install pip ``requirements`` into the HA venv.

    Returns a structured report so the caller (and ultimately the AI) can tell
    *what happened at each phase*: which packages were already present, which
    were freshly installed, which failed and why. This is fed back through the
    tool result under the ``install`` key.
    """

    from homeassistant.requirements import pip_kwargs as _ha_pip_kwargs
    from homeassistant.util import package as _ha_pkg_util

    cleaned = [r.strip() for r in requirements if r and r.strip()]
    report: dict[str, Any] = {
        "requested": cleaned,
        "already_present": [],
        "installed": [],
        "failed": [],
        "ok": True,
    }
    if not cleaned:
        return report

    def _check_installed() -> tuple[list[str], list[str]]:
        already: list[str] = []
        missing: list[str] = []
        for req in cleaned:
            try:
                if _ha_pkg_util.is_installed(req):
                    already.append(req)
                    continue
            except Exception:
                pass
            missing.append(req)
        return already, missing

    already, missing = await hass.async_add_executor_job(_check_installed)
    report["already_present"] = already
    if not missing:
        return report

    kwargs = _ha_pip_kwargs(hass.config.config_dir)
    if pip_index_url:
        kwargs = dict(kwargs)
        kwargs["index_url"] = pip_index_url

    def _install_all() -> tuple[list[str], list[dict[str, str]]]:
        installed: list[str] = []
        failures: list[dict[str, str]] = []
        for req in missing:
            try:
                ok = _ha_pkg_util.install_package(req, **kwargs)
            except Exception as err:
                failures.append({"requirement": req, "error": str(err)})
                continue
            if ok:
                installed.append(req)
            else:
                failures.append(
                    {"requirement": req, "error": "pip returned non-zero exit code"}
                )
        return installed, failures

    installed, failures = await hass.async_add_executor_job(_install_all)
    report["installed"] = installed
    report["failed"] = failures

    if installed:
        import importlib
        await hass.async_add_executor_job(importlib.invalidate_caches)

        config_deps = Path(hass.config.config_dir) / "deps"
        if await hass.async_add_executor_job(config_deps.is_dir):
            deps_str = str(config_deps)
            if deps_str not in sys.path:
                sys.path.insert(0, deps_str)

    if failures:
        report["ok"] = False
    return report


class ExecutePythonTool(llm.Tool):
    name = "ExecutePython"
    description = (
        "Execute Python only when a native HA tool cannot do the job cleanly. "
        "Routing checklist: (1) normal entity state/control/service discovery "
        "→ prefer GetLiveContext, EntityQuery, ServiceCall, intent tools, "
        "Automation, Script, ConfigEntries, etc.; do NOT use Python just to "
        "turn devices on/off or query one entity. (2) Need HA runtime access "
        "(`hass.states`, services, registries, config dir, generating a file "
        "for the HA frontend) → use inline mode (default). (3) Need isolated "
        "or risky computation, third-party packages, large data processing, "
        "network/file experiments, or code that does NOT need `hass` → use "
        "sandbox=true. (4) Destructive operations (delete/rmtree/overwrite "
        "important config) require explicit user consent first. Inline mode: "
        "runs in HA process with top-level await; requirements are installed "
        "into the HA venv, so list only packages actually imported. Sandbox: "
        "child venv subprocess, no `hass`, can use requirements/env/cwd/stdin. "
        "Inline globals (do not shadow): `hass`; `OUTPUT_DIR` persistent "
        "browser-served `/local/claw_assistant/<file>` dir for shareable "
        "files; `TMP_DIR` ephemeral 24h dir for intermediates; "
        "`output_url(name)` returns absolute URL or `/local/...`; "
        "`list_outputs()` returns `{name,path,url,size,mtime}` entries; "
        "`list_tmp()` same without url. Put user-visible/shareable artefacts "
        "in OUTPUT_DIR, use ASCII filenames only (`A-Za-z0-9_.-`), then reply "
        "with `output_url(name)`. Put temporary/intermediate files in TMP_DIR; "
        "do not manually clean TMP_DIR. Writes to system scratch dirs through "
        "injected open() may be redirected to TMP_DIR and reported as "
        "`artefacts.redirects[]`. Return shape: `success`, `phase`, `result`, "
        "`stdout`, `stderr`, `duration_ms`, optional `install`, and "
        "`artefacts.output/tmp/redirects`. Inline event-loop rule: synchronous "
        "I/O like Path.write_bytes/open().write/subprocess is safe because it "
        "runs on a worker thread, but every callable `hass.*` access MUST be "
        "awaited (`await hass.states.get(...)`, `await hass.config.path(...)`, "
        "`await hass.async_add_executor_job(...)`). Non-callable attrs such as "
        "`hass.config.config_dir` are direct."
    )
    parameters = vol.Schema(
        {
            vol.Required("code"): str,
            vol.Optional("sandbox", default=False): bool,
            vol.Optional("requirements", default=[]): list,
            vol.Optional("timeout", default=60): int,
            vol.Optional("dry_run", default=False): bool,
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

        if sandbox_flag:
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

        install_report: dict[str, Any] | None = None
        if requirements:
            try:
                install_report = await _inline_install_requirements(
                    hass, requirements, pip_index_url=pip_index_url or None
                )
            except Exception as err:
                return {
                    "success": False,
                    "phase": "install",
                    "error": (
                        f"Inline pip install crashed before any package could "
                        f"be evaluated: {err}"
                    ),
                    "requirements": requirements,
                }

            if not install_report.get("ok", True):
                return {
                    "success": False,
                    "phase": "install",
                    "error": (
                        "Inline pip install failed for one or more packages; "
                        "execution aborted before running user code."
                    ),
                    "install": install_report,
                }

        result = await self._run_inline(hass, code, timeout=timeout, dry_run=dry_run)
        if isinstance(result, dict) and install_report is not None:
            result.setdefault("install", install_report)
        return result

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

        PyCF_ALLOW_TOP_LEVEL_AWAIT = 0x2000

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
        has_top_level_async = _has_top_level_async(tree)

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

        import os as _os
        import tempfile as _tempfile

        from ..runtime.data_path import (
            absolute_output_url as _absolute_output_url,
            output_dir_path as _output_dir_path,
            tmp_dir_path as _tmp_dir_path,
        )

        output_dir = _output_dir_path(hass)
        tmp_dir = _tmp_dir_path(hass)
        await asyncio.gather(
            hass.async_add_executor_job(
                lambda: output_dir.mkdir(parents=True, exist_ok=True)
            ),
            hass.async_add_executor_job(
                lambda: tmp_dir.mkdir(parents=True, exist_ok=True)
            ),
        )

        def _output_url_for(name: str) -> str:
            """Public URL helper injected as ``output_url`` in AI globals.

            Returns an absolute URL when HA has an internal/external URL
            configured so the AI can hand the link directly to chat apps,
            emails, etc.; falls back to the relative ``/local/...`` path
            otherwise.
            """

            return _absolute_output_url(hass, name)

        _system_tmp_candidates = [
            "/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp",
            _tempfile.gettempdir(),
        ]
        def _resolve_tmp_roots() -> tuple[list[Path], Path]:
            system_tmp_roots: list[Path] = []
            resolved_tmp = tmp_dir.resolve()
            resolved_output = output_dir.resolve()
            for raw in _system_tmp_candidates:
                try:
                    resolved_root = Path(raw).resolve(strict=False)
                except OSError:
                    continue
                too_broad = False
                for managed in (resolved_tmp, resolved_output):
                    try:
                        managed.relative_to(resolved_root)
                        too_broad = True
                        break
                    except ValueError:
                        continue
                if too_broad:
                    continue
                if resolved_root not in system_tmp_roots:
                    system_tmp_roots.append(resolved_root)
            return system_tmp_roots, resolved_output

        _system_tmp_roots, _resolved_output_dir = await hass.async_add_executor_job(
            _resolve_tmp_roots
        )

        redirects: list[dict[str, str]] = []

        def _redirect_if_system_tmp(candidate: Path) -> Path:
            """Return either ``candidate`` or a TMP_DIR equivalent.

            If the write target falls under a recognised system temp root we
            rewrite it to ``TMP_DIR/<leaf>`` (flattened — we do not mirror
            deep system paths). Every redirect is recorded for the AI.
            """

            try:
                resolved_cand = candidate.resolve(strict=False)
            except OSError:
                return candidate
            for root in _system_tmp_roots:
                try:
                    rel = resolved_cand.relative_to(root)
                except ValueError:
                    continue
                rel_posix = rel.as_posix()
                target = tmp_dir / Path(rel_posix).name
                if target.exists():
                    parent_hint = Path(rel_posix).parent.name
                    if parent_hint:
                        target = tmp_dir / f"{parent_hint}__{target.name}"
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return candidate
                redirects.append({
                    "from": str(candidate),
                    "to": str(target),
                    "reason": (
                        "system temp dir write redirected to TMP_DIR "
                        "(inline mode manages its own tmp so files are "
                        "visible to the user and auto-cleaned after 24h)"
                    ),
                })
                return target
            return candidate

        _real_open = _builtins.open
        _write_mode_chars = frozenset("waxWAX+")

        def _safe_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
            mode_str = mode if isinstance(mode, str) else "r"
            is_write = any(ch in _write_mode_chars for ch in mode_str)
            if not is_write or isinstance(file, int):
                return _real_open(file, mode, *args, **kwargs)
            try:
                decoded = _os.fsdecode(file)
            except (TypeError, ValueError):
                return _real_open(file, mode, *args, **kwargs)
            candidate = Path(decoded)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            effective = _redirect_if_system_tmp(candidate)
            if "b" not in mode_str and "encoding" not in kwargs:
                try:
                    effective.resolve(strict=False).relative_to(_resolved_output_dir)
                    kwargs["encoding"] = "utf-8-sig"
                except ValueError:
                    pass
            return _real_open(effective, mode, *args, **kwargs)

        def _list_dir_snapshot(path: Path) -> set[str]:
            try:
                return {
                    p.relative_to(path).as_posix()
                    for p in path.rglob("*")
                    if p.is_file()
                }
            except OSError:
                return set()

        def _describe_file(root: Path, path: Path, *, include_url: bool) -> dict[str, Any]:
            rel = path.relative_to(root).as_posix()
            try:
                stat = path.stat()
                size = stat.st_size
                mtime = int(stat.st_mtime)
            except OSError:
                size = 0
                mtime = 0
            entry: dict[str, Any] = {
                "name": rel,
                "path": str(path),
                "size": size,
                "mtime": mtime,
            }
            if include_url:
                entry["url"] = _output_url_for(rel)
            return entry

        def _list_outputs() -> list[dict[str, Any]]:
            return [
                _describe_file(output_dir, p, include_url=True)
                for p in sorted(output_dir.rglob("*"))
                if p.is_file()
            ]

        def _list_tmp() -> list[dict[str, Any]]:
            return [
                _describe_file(tmp_dir, p, include_url=False)
                for p in sorted(tmp_dir.rglob("*"))
                if p.is_file()
            ]

        before_output, before_tmp = await asyncio.gather(
            hass.async_add_executor_job(_list_dir_snapshot, output_dir),
            hass.async_add_executor_job(_list_dir_snapshot, tmp_dir),
        )

        safe_builtins = dict(vars(_builtins))
        safe_builtins["open"] = _safe_open

        globals_: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "hass": hass,
            "asyncio": asyncio,
            "json": json,
            "OUTPUT_DIR": output_dir,
            "TMP_DIR": tmp_dir,
            "output_url": _output_url_for,
            "list_outputs": _list_outputs,
            "list_tmp": _list_tmp,
        }

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        started = time.perf_counter()
        err_info: dict[str, Any] | None = None

        import pathlib as _pathlib
        _orig_path_write_text = _pathlib.Path.write_text
        _orig_path_read_text = _pathlib.Path.read_text

        def _utf8_write_text(self, data, encoding=None, errors=None, newline=None):  # type: ignore[no-untyped-def]
            if encoding is None:
                encoding = "utf-8"
            return _orig_path_write_text(
                self, data, encoding=encoding, errors=errors, newline=newline
            )

        def _utf8_read_text(self, encoding=None, errors=None, newline=None):  # type: ignore[no-untyped-def]
            if encoding is None:
                encoding = "utf-8"
            try:
                return _orig_path_read_text(
                    self, encoding=encoding, errors=errors, newline=newline
                )
            except TypeError:
                return _orig_path_read_text(self, encoding=encoding, errors=errors)

        _pathlib.Path.write_text = _utf8_write_text  # type: ignore[assignment]
        _pathlib.Path.read_text = _utf8_read_text  # type: ignore[assignment]

        try:
            import inspect as _inspect

            if has_top_level_async:
                main_loop = asyncio.get_running_loop()
                worker_globals = dict(globals_)
                worker_globals["hass"] = _MainLoopProxy(hass, main_loop)
                worker_error: list[BaseException] = []

                def _run_user_async() -> None:
                    worker_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(worker_loop)
                    try:
                        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                            maybe = eval(compiled, worker_globals)
                            if _inspect.iscoroutine(maybe):
                                worker_loop.run_until_complete(
                                    asyncio.wait_for(maybe, timeout=max(1, timeout))
                                )
                    except BaseException as err:
                        worker_error.append(err)
                    finally:
                        try:
                            pending = asyncio.all_tasks(worker_loop)
                            for task in pending:
                                task.cancel()
                            if pending:
                                worker_loop.run_until_complete(
                                    asyncio.gather(*pending, return_exceptions=True)
                                )
                        except Exception:
                            pass
                        try:
                            worker_loop.close()
                        except Exception:
                            pass
                        asyncio.set_event_loop(None)

                await asyncio.wait_for(
                    hass.async_add_executor_job(_run_user_async),
                    timeout=max(1, timeout) + 5,
                )
                if worker_error:
                    raise worker_error[0]
            else:
                def _run_sync() -> None:
                    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                        eval(compiled, globals_)

                await asyncio.wait_for(
                    hass.async_add_executor_job(_run_sync),
                    timeout=max(1, timeout),
                )
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
        except BaseException:
            err_info = {
                "error": traceback.format_exception_only(*sys.exc_info()[:2])[-1].strip(),
                "traceback": traceback.format_exc(),
            }
        finally:
            _pathlib.Path.write_text = _orig_path_write_text  # type: ignore[assignment]
            _pathlib.Path.read_text = _orig_path_read_text  # type: ignore[assignment]

        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout_text = _trim_stream(stdout_buf.getvalue())
        stderr_text = _trim_stream(stderr_buf.getvalue())

        after_output, after_tmp = await asyncio.gather(
            hass.async_add_executor_job(_list_dir_snapshot, output_dir),
            hass.async_add_executor_job(_list_dir_snapshot, tmp_dir),
        )
        new_output = sorted(after_output - before_output)
        new_tmp = sorted(after_tmp - before_tmp)

        artefacts: dict[str, Any] = {}
        if new_output:
            artefacts["output"] = [
                {
                    "name": name,
                    "path": str(output_dir / name),
                    "url": _output_url_for(name),
                    "verification": await hass.async_add_executor_job(
                        _verify_generated_file, output_dir / name
                    ),
                }
                for name in new_output
            ]
        if new_tmp:
            artefacts["tmp"] = [
                {
                    "name": name,
                    "path": str(tmp_dir / name),
                    "verification": await hass.async_add_executor_job(
                        _verify_generated_file, tmp_dir / name
                    ),
                }
                for name in new_tmp
            ]
        if redirects:
            artefacts["redirects"] = list(redirects)

        if err_info is not None:
            payload: dict[str, Any] = {
                "success": False,
                "phase": "exec",
                **err_info,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "duration_ms": duration_ms,
            }
            if artefacts:
                payload["artefacts"] = artefacts
            return _cap_py_payload(payload)

        result = globals_.get("result")
        if result is None:
            result = globals_.get(auto_key)
        payload = {
            "success": True,
            "phase": "exec",
            "result": result,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "duration_ms": duration_ms,
        }
        if artefacts:
            payload["artefacts"] = artefacts
        return _cap_py_payload(payload)


class SystemControlTool(llm.Tool):
    name = "SystemControl"
    description = "System control tool. Use it to set global injection, output mode, and inspect runtime status. For action=set_output_mode, value must be one of normal/default/auto/brief/detailed/list/code. normal/default/auto reset to normal output."
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["set_global_inject", "set_output_mode", "get_status"]),
        vol.Optional("value", default=""): vol.Any(vol.In(_OUTPUT_MODE_VALUES), str),
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        value = tool_input.tool_args.get("value", "")
        if action == "set_global_inject":
            get_global_state(hass)["inject"] = value
            return {"success": True, "message": f"Global inject set: {value[:50]}..."}
        if action == "set_output_mode":
            normalized = _OUTPUT_MODE_ALIASES.get(str(value).strip().lower())
            if normalized is not None:
                get_output_state(hass)["mode"] = normalized
                return {"success": True, "message": f"Output mode set: {normalized or 'normal'}"}
            return {
                "success": False,
                "error": "Invalid output mode. Use one of: normal, brief, detailed, list, code.",
            }
        if action == "get_status":
            return {
                "success": True,
                "global_inject": get_global_state(hass).get("inject", "")[:100],
                "output_mode": get_output_state(hass).get("mode", "normal"),
            }
        return {"success": False, "error": "Unknown action"}


class ConversationMemoryTool(llm.Tool):
    name = "ConversationMemory"
    description = (
        "[Self Memory] — your personal notebook for user preferences and "
        "simple facts. Automatically injected into every future system prompt. "
        "Keep entries compact and focused on facts that will still matter later.\n\n"
        "WHEN TO SAVE (proactive but high signal only):\n"
        "- The user corrects you or reveals a preference/personal detail.\n"
        "- A durable environment fact is discovered that prevents future errors.\n"
        "- A lesson, convention, or workflow the user would hate to repeat.\n"
        "You do NOT need to wait for the user to say 'remember this'. "
        "But ask yourself: 'is this a correction, preference, or reusable fact?' "
        "If it's just a one-off remark or routine task output, skip it.\n\n"
        "FREQUENCY LIMIT: at most 1-2 saves per conversation session. "
        "Do NOT save after every turn.\n\n"
        "DO NOT SAVE: task progress, completed-work logs, current TODO state, "
        "session-specific notes, 'what I just did' summaries, tool call results, "
        "trivial / obvious info, raw data dumps, anything already in HA's "
        "entity registry, hypotheticals, conversational filler, "
        "image/media descriptions, things the user said once casually, "
        "information you can re-derive from context. "
        "Use HeartbeatManager for reminders/follow-ups, "
        "GetConversationHistory for current task state, and skills for "
        "discovered procedures.\n\n"
        "TWO TARGETS (pick the correct one):\n"
        "- target='user': who the user is — name, role, pronouns, timezone, "
        "preferred address, communication style, pet peeves, habits.\n"
        "- target='memory' (default): environment facts, entity ids, notify "
        "targets, project conventions, tool quirks, lessons learned.\n\n"
        "Actions: save (add or update one fact), get (read one), list (read "
        "all), clear (wipe one target — only on explicit user request). "
        "Params: action, target ('memory' or 'user'), key (short stable "
        "identifier), value (the fact).\n\n"
        "IMPORTANT — DO NOT DOUBLE-SAVE: ConversationMemory and MemoryGraph "
        "are mutually exclusive for the same piece of information. "
        "Use ConversationMemory for simple user preferences and short facts. "
        "Use MemoryGraph for complex relational knowledge, decisions, and "
        "bug-fix records that need typed edges and graph traversal. "
        "NEVER call both tools for the same fact in one turn."
    )
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["save", "get", "list", "clear"]),
        vol.Optional("target", default="memory"): vol.In(["memory", "user"]),
        vol.Optional("key", default=""): str,
        vol.Optional("value", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        target = tool_input.tool_args.get("target", "memory") or "memory"
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
            save_result = await async_save_memory_entry_result(hass, key, value, target=target)
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
            if status == "rejected_unsafe":
                return {
                    "success": False,
                    "error": save_result["recommendation"],
                    **save_result,
                }
            if status == "rejected_full":
                return {
                    "success": False,
                    "error": save_result["recommendation"],
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
                "target": target,
                "value": await async_get_memory_entry(hass, key, target=target),
                **build_route_envelope("memory_read", "ConversationMemory", "list", args={"target": target}),
                "route_hint": build_route_hint("memory_read", "ConversationMemory", "list", args={"target": target}),
            }
        if action == "list":
            entries = await async_list_memory_entries(hass, target=target)
            return {
                "success": True,
                "keys": [entry["key"] for entry in entries],
                "entries": entries,
                **(build_route_envelope("memory_index", "ConversationMemory", "get", args={"key": entries[0]["key"]}) if entries else build_route_envelope("memory_index", "ConversationMemory", "save", args={"key": "", "value": ""})),
                "route_hint": (build_route_hint("memory_index", "ConversationMemory", "get", args={"key": entries[0]["key"]}) if entries else build_route_hint("memory_index", "ConversationMemory", "save", args={"key": "", "value": ""})),
            }
        if action == "clear":
            await async_clear_memory_entries(hass, target=target)
            return {
                "success": True,
                "target": target,
                "message": f"{target} memory cleared",
                **build_route_envelope("memory_cleared", "ConversationMemory", "list", args={"target": target}),
                "route_hint": build_route_hint("memory_cleared", "ConversationMemory", "list", args={"target": target}),
            }
        return {"success": False, "error": "Invalid action or missing key"}


class InstallSkillTool(llm.Tool):
    name = "InstallSkill"
    description = "Install Markdown skills into `.storage/claw_assistant/skills/` only. Supports direct name+markdown input, or source_url from a GitHub/blob/raw link. Legacy `~/.openclaw/workspace/skills/` and `config/skills/` are import-only and must not be used as install targets. Params: name?, markdown?, source_url?, overwrite(false)"
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
                "canonical_dir": str(skill_path.parent),
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
        skills = await hass.async_add_executor_job(list_installed_skills)
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
            result = await async_get_installed_skill(
                hass,
                tool_input.tool_args.get("name", ""),
            )
            try:
                from ..runtime.evolution_review import record_loaded_skill

                record_loaded_skill(hass, result.get("slug", ""))
            except Exception:
                pass
            return {"success": True, **result}
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
            doc = get_workspace_doc(tool_input.tool_args.get("name", ""))
            from ..runtime.workspace_store import _DOC_PURPOSES
            purpose = _DOC_PURPOSES.get(doc.get("name", ""), "")
            result: JsonObjectType = {"success": True, **doc}
            if purpose:
                result["doc_purpose"] = purpose
            return result
        except ValueError as err:
            return {"success": False, "error": str(err)}


class HeartbeatManagerTool(llm.Tool):
    name = "HeartbeatManager"
    description = (
        "Manage heartbeat follow-up tasks. "
        "schedule: cron ('*/30 * * * *', '0 9 * * *') or interval ('30m', '2h', 'every 1d'). "
        "notify_channel: where to push results, e.g. 'wechat:account_id:user_id' or 'qq:user:openid' (from conversation_id). "
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
                **build_route_envelope("follow_up_task_index"),
                "route_hint": build_route_hint("follow_up_task_index"),
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
                **build_route_envelope("follow_up_task_saved"),
                "route_hint": build_route_hint("follow_up_task_saved"),
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
                    else build_route_envelope("follow_up_task_updated")
                ),
                "route_hint": (
                    build_route_hint("follow_up_task_completed", "HeartbeatManager", "list")
                    if record_result["task_deleted"]
                    else build_route_hint("follow_up_task_updated")
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
    description = (
        "Write one workspace markdown document. Params: name, markdown. "
        "Each document is a strict typed store: "
        "SOUL=style/tone, IDENTITY=assistant identity, USER=user facts, "
        "MEMORY=user preferences/constraints, TOOLS=environment/credentials, "
        "HEARTBEAT=tasks. "
        "Always read the target doc first, apply the smallest confirmed change, "
        "never invent values to fill empty templates, and never duplicate facts across files."
    )
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
            from ..runtime.workspace_store import _DOC_PURPOSES, _normalize_doc_name
            normalized = _normalize_doc_name(tool_input.tool_args.get("name", ""))
            purpose = _DOC_PURPOSES.get(normalized, "")
            result: JsonObjectType = {"success": True, "path": str(path)}
            if purpose:
                result["doc_purpose"] = purpose
            return result
        except ValueError as err:
            return {"success": False, "error": str(err)}


class BootstrapControlTool(llm.Tool):
    name = "BootstrapControl"
    description = (
        "Toggle first-run bootstrap mode. Call with active=false to exit "
        "bootstrap and switch to normal conversation mode immediately, even "
        "if some IDENTITY.md or USER.md fields are still empty. The next turn "
        "will no longer inject BOOTSTRAP.md. Call with active=true to re-enter "
        "bootstrap. Use only when the user clearly signals the setup is done "
        "or wants to skip remaining setup."
    )
    parameters = vol.Schema(
        {
            vol.Required("active"): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        active = bool(tool_input.tool_args.get("active"))
        await async_set_bootstrap_active(hass, active)
        return {
            "success": True,
            "bootstrap_active": active,
            "message": (
                "Bootstrap mode disabled; switched to normal conversation."
                if not active
                else "Bootstrap mode enabled."
            ),
        }


class MemoryGraphTool(llm.Tool):
    name = "MemoryGraph"
    description = (
        "[Knowledge Graph] — long-term relational memory backed by SQLite "
        "(nodes + typed edges, BM25 ranking, time decay, dedup via content "
        "checksum). Use this for durable decisions, bug fixes, and their "
        "causal links — not for simple preferences (use ConversationMemory "
        "for those) or active turn's scratch state. "
        "FREQUENCY LIMIT: remember at most 1-2 nodes per conversation session. "
        "Only store facts with lasting value. If unsure, don't store. "
        "Workspace markdown "
        "files remain the human-readable source of truth and are auto-indexed "
        "into this graph; this tool also lets you write nodes/edges directly. "
        "Params: action (recall/remember/link/pin/forget/get/stats/cleanup), "
        "and action-specific fields. "
        "recall: query(required), kinds(list, optional), limit(int, default 8), expand(bool, default true). "
        "remember: kind(required, e.g. fact/preference/decision/bug_fix/event), "
        "title(required), body(required), source_doc(optional), "
        "confidence(0..1, default 1.0), pinned(bool, default false). "
        "link: src_id(int), dst_id(int), relation(required, e.g. related_to/caused_by/"
        "supersedes/refutes/resolved_by/blocked_by), weight(float, default 1.0). "
        "pin: id(int), pinned(bool, default true). "
        "forget: id(int). "
        "get: id(int). "
        "stats: no params. "
        "cleanup: no params — removes duplicates, junk nodes, and rebuilds missing edges.\n\n"
        "IMPORTANT — DO NOT DOUBLE-SAVE: MemoryGraph and ConversationMemory "
        "are mutually exclusive for the same piece of information. "
        "Use MemoryGraph for relational knowledge that needs graph traversal, "
        "typed edges, or BM25-ranked recall. "
        "Use ConversationMemory for simple key-value user preferences. "
        "NEVER call both tools for the same fact in one turn."
    )
    parameters = vol.Schema(
        {
            vol.Required("action"): str,
            vol.Optional("query"): str,
            vol.Optional("kinds"): list,
            vol.Optional("limit"): int,
            vol.Optional("expand"): bool,
            vol.Optional("kind"): str,
            vol.Optional("title"): str,
            vol.Optional("body"): str,
            vol.Optional("source_doc"): str,
            vol.Optional("confidence"): vol.Coerce(float),
            vol.Optional("pinned"): bool,
            vol.Optional("src_id"): vol.Coerce(int),
            vol.Optional("dst_id"): vol.Coerce(int),
            vol.Optional("relation"): str,
            vol.Optional("weight"): vol.Coerce(float),
            vol.Optional("id"): vol.Coerce(int),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..runtime.graph_service import (  # noqa: PLC0415
            async_get_node,
            async_link,
            async_recall,
            async_remember,
            get_graph_store,
        )

        args = tool_input.tool_args
        action = str(args.get("action", "")).strip().lower()
        store = get_graph_store(hass)
        if store is None:
            return {"success": False, "error": "graph store not initialised"}

        if action == "recall":
            query = str(args.get("query", "")).strip()
            if not query:
                return {"success": False, "error": "query is required"}
            kinds = args.get("kinds") or None
            if kinds is not None and not isinstance(kinds, list):
                return {"success": False, "error": "kinds must be a list"}
            limit = int(args.get("limit", 8))
            expand = bool(args.get("expand", True))
            hits = await async_recall(
                hass, query, kinds=kinds, limit=limit, expand=expand
            )
            return {
                "success": True,
                "count": len(hits),
                "hits": [
                    {
                        "id": h.node.id,
                        "kind": h.node.kind,
                        "title": h.node.title,
                        "body": h.node.body,
                        "source_doc": h.node.source_doc,
                        "confidence": h.node.confidence,
                        "pinned": h.node.pinned,
                        "access_count": h.node.access_count,
                        "score": round(h.score, 6),
                        "via": h.via,
                        "edges": [
                            {"to": dst, "relation": rel}
                            for dst, rel in h.related_edges
                        ],
                    }
                    for h in hits
                ],
            }

        if action == "remember":
            kind = str(args.get("kind", "")).strip()
            title = str(args.get("title", "")).strip()
            body = str(args.get("body", "")).strip()
            if not kind or not (title or body):
                return {
                    "success": False,
                    "error": "kind and (title or body) are required",
                }
            result = await async_remember(
                hass,
                kind=kind,
                title=title,
                body=body,
                source_doc=(str(args["source_doc"]) if args.get("source_doc") else None),
                confidence=float(args.get("confidence", 1.0)),
                pinned=bool(args.get("pinned", False)),
            )
            if result is None:
                return {"success": False, "error": "graph store unavailable"}
            node_id, was_new = result
            if was_new:
                try:
                    hits = await async_recall(
                        hass, f"{title} {body[:100]}", limit=3, expand=False
                    )
                    for h in hits:
                        if h.node.id != node_id:
                            await async_link(hass, node_id, h.node.id, "related_to")
                except Exception:
                    pass
            return {"success": True, "id": node_id, "created": was_new}

        if action == "link":
            try:
                src_id = int(args["src_id"])
                dst_id = int(args["dst_id"])
            except (KeyError, TypeError, ValueError):
                return {"success": False, "error": "src_id and dst_id are required"}
            relation = str(args.get("relation", "")).strip()
            if not relation:
                return {"success": False, "error": "relation is required"}
            ok = await async_link(
                hass,
                src_id,
                dst_id,
                relation,
                weight=float(args.get("weight", 1.0)),
            )
            return {"success": ok}

        if action == "pin":
            try:
                node_id = int(args["id"])
            except (KeyError, TypeError, ValueError):
                return {"success": False, "error": "id is required"}
            pinned = bool(args.get("pinned", True))
            await hass.async_add_executor_job(store.pin, node_id, pinned)
            return {"success": True, "id": node_id, "pinned": pinned}

        if action == "forget":
            try:
                node_id = int(args["id"])
            except (KeyError, TypeError, ValueError):
                return {"success": False, "error": "id is required"}
            await hass.async_add_executor_job(store.forget, node_id)
            return {"success": True, "id": node_id, "forgotten": True}

        if action == "get":
            try:
                node_id = int(args["id"])
            except (KeyError, TypeError, ValueError):
                return {"success": False, "error": "id is required"}
            node = await async_get_node(hass, node_id)
            if node is None:
                return {"success": False, "error": f"node {node_id} not found"}
            return {
                "success": True,
                "node": {
                    "id": node.id,
                    "kind": node.kind,
                    "title": node.title,
                    "body": node.body,
                    "source_doc": node.source_doc,
                    "confidence": node.confidence,
                    "pinned": node.pinned,
                    "access_count": node.access_count,
                    "created_at": node.created_at,
                    "last_accessed_at": node.last_accessed_at,
                },
            }

        if action == "stats":
            stats = await hass.async_add_executor_job(store.stats)
            return {"success": True, **stats}

        if action == "cleanup":
            result = await hass.async_add_executor_job(store.cleanup)
            return {"success": True, **result}

        return {"success": False, "error": f"unknown action: {action}"}


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
        from ..runtime.state import _active_conversation_id
        fire_live_progress(
            hass,
            conversation_id=_active_conversation_id.get(),
            phase="thinking",
            text=thought,
            display_text=tool_progress_line("ThinkContinue", {}, hass.config.language or "en").strip(),
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
    description = "Call up to 8 independent tools in TRUE parallel (asyncio.gather). ALWAYS use this when you have 2+ independent tool calls that don't depend on each other's results. Params: tools=[{name,args}]. Each item: {\"name\":\"ToolName\",\"args\":{...}}. Examples: [{\"name\":\"HAControl\",\"args\":{\"action\":\"shell\",\"params\":{\"command\":\"uptime\"}}},{\"name\":\"HAControl\",\"args\":{\"action\":\"shell\",\"params\":{\"command\":\"df -h\"}}}] or [{\"name\":\"EntityQuery\",\"args\":{\"domain\":\"light\"}},{\"name\":\"EntityQuery\",\"args\":{\"domain\":\"climate\"}}]. Works with ALL tools including HAControl, EntityQuery, HistoryQuery, WebSearch, SmartDiscovery, etc. Do NOT call multiple tools sequentially if they are independent — wrap them in ParallelToolCall instead."
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
            if isinstance(raw_spec, str):
                raw_spec = raw_spec.strip()
                if raw_spec.startswith("{"):
                    try:
                        raw_spec = json.loads(raw_spec)
                    except (json.JSONDecodeError, ValueError):
                        deduped_specs.append(("", {}))
                        continue
                elif ":" in raw_spec:
                    colon_idx = raw_spec.index(":")
                    _name = raw_spec[:colon_idx].strip()
                    _rest = raw_spec[colon_idx + 1:].strip()
                    try:
                        _args = json.loads(_rest) if _rest.startswith("{") else {}
                    except (json.JSONDecodeError, ValueError):
                        _args = {}
                    raw_spec = {"name": _name, "args": _args}
                else:
                    raw_spec = {"name": raw_spec, "args": {}}
            if not isinstance(raw_spec, dict):
                deduped_specs.append(("", {}))
                continue
            tool_name = str(raw_spec.get("name", "")).strip()
            tool_args = raw_spec.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            if tool_name == "SystemControl.set_output_mode":
                tool_name = "SystemControl"
                tool_args = {"action": "set_output_mode", **tool_args}
            if tool_name == "SystemControl.set_global_inject":
                tool_name = "SystemControl"
                tool_args = {"action": "set_global_inject", **tool_args}
            if tool_name == "SystemControl.get_status":
                tool_name = "SystemControl"
                tool_args = {"action": "get_status", **tool_args}
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

        _MAX_PARALLEL = 8
        if len(deduped_specs) > _MAX_PARALLEL:
            deduped_specs = deduped_specs[:_MAX_PARALLEL]

        raw_results = await asyncio.gather(
            *[
                _execute_tool_spec(hass, llm_context, tool_name, tool_args)
                for tool_name, tool_args in deduped_specs
            ],
            return_exceptions=True,
        )
        results = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                results.append({"tool": deduped_specs[i][0], "success": False, "error": str(r)})
            else:
                results.append(_ensure_json_serializable(r))
        success_count = sum(1 for item in results if isinstance(item, dict) and item.get("success"))

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


class CameraCaptureTool(llm.Tool):
    name = "CameraCapture"
    description = (
        "Camera tool: capture snapshots or analyze live camera frames.\n"
        "- camera_entity='list' (or empty) → enumerate all cameras "
        "(bypasses exposure filter).\n"
        "- mode=snapshot (default) → return snapshot_url + `markdown_hint` "
        "(ready-to-paste `![cam](url)`). On the `ha` channel include "
        "`markdown_hint` in your reply so the frontend renders the image.\n"
        "- mode=analyze → return base64 JPEG for vision reasoning; describe "
        "what you see. Only include `markdown_hint` if the user should ALSO "
        "see the image alongside your analysis.\n"
        "Params: camera_entity (entity_id / friendly name / 'list'), "
        "mode (snapshot|analyze, default snapshot), max_dim (default 640), "
        "target_kb (default 40).\n"
        "For uploaded images/videos/GIFs, use MediaAnalyze instead."
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
                "message": f"Found {len(cameras)} camera(s). Call CameraCapture again with camera_entity set to a specific entity_id.",
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
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            import base64

            primary_err: Exception | None = None
            raw_bytes: bytes = b""
            jpeg_bytes: bytes = b""
            final_w = final_h = final_q = 0
            fetch_path = ""

            try:
                image = await async_get_image(hass, target_camera)
                raw_bytes = image.content or b""
                if not raw_bytes:
                    raise ValueError("async_get_image returned empty content")
                jpeg_bytes, final_w, final_h, final_q = await hass.async_add_executor_job(
                    _compress_camera_frame, raw_bytes, max_dim, target_kb
                )
                fetch_path = "internal"
            except Exception as err:
                primary_err = err
                _LOGGER.debug(
                    "CameraCapture internal path failed for %s (%s); trying HTTP proxy fallback",
                    target_camera,
                    err,
                )

            if not jpeg_bytes:
                if not snapshot_url:
                    return {
                        "success": False,
                        "error": (
                            f"Failed to capture camera frame: {primary_err}. "
                            f"No access_token on {target_camera}, cannot fall back to HTTP."
                        ),
                    }
                try:
                    session = async_get_clientsession(hass)
                    async with session.get(snapshot_url) as resp:
                        resp.raise_for_status()
                        raw_bytes = await resp.read()
                    if not raw_bytes:
                        raise ValueError("HTTP camera_proxy returned empty body")
                    jpeg_bytes, final_w, final_h, final_q = await hass.async_add_executor_job(
                        _compress_camera_frame, raw_bytes, max_dim, target_kb
                    )
                    fetch_path = "http_proxy"
                except Exception as fallback_err:
                    _LOGGER.error(
                        "CameraCapture fallback also failed for %s: internal=%s; http=%s",
                        target_camera,
                        primary_err,
                        fallback_err,
                    )
                    return {
                        "success": False,
                        "error": (
                            f"Failed to capture camera frame: internal={primary_err}; "
                            f"http_proxy={fallback_err}"
                        ),
                    }

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
                "fetch_path": fetch_path,
                "message": (
                    f"Captured {target_camera}: {final_w}x{final_h} JPEG q={final_q}, "
                    f"{len(jpeg_bytes)} bytes (via {fetch_path})"
                ),
            }
            if snapshot_url:
                result["snapshot_url"] = snapshot_url
                result["markdown_hint"] = f"![{target_camera}]({snapshot_url})"
            return result
        except Exception as err:
            _LOGGER.error("CameraCaptureTool error: %s", err)
            return {"success": False, "error": f"Failed to capture camera frame: {err}"}


_VIDEO_EXTENSIONS = frozenset({"mp4", "avi", "mov", "mkv", "webm", "flv", "m4v", "ts", "3gp"})
_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "bmp", "webp", "tiff", "tif", "heic", "heif", "avif", "ico", "svg"})
_GIF_EXTENSION = "gif"
_MIN_VIDEO_FRAMES = 3
_MAX_VIDEO_FRAMES = 10


def _calc_frame_count(duration: float) -> int:
    if duration <= 0:
        return 4
    if duration <= 5:
        return _MIN_VIDEO_FRAMES
    if duration <= 30:
        return 4
    if duration <= 60:
        return 6
    if duration <= 180:
        return 8
    return _MAX_VIDEO_FRAMES

_FFPROBE_TIMEOUT = 10
_FFMPEG_FRAME_TIMEOUT = 30
_VIDEO_COMPRESS_THRESHOLD = 5 * 1024 * 1024  # 5MB
_VIDEO_COMPRESS_TIMEOUT = 120


def _compress_video_sync(ffmpeg_bin: str, src: str, dst: str) -> bool:
    import subprocess
    cmd = [
        ffmpeg_bin, "-y",
        "-i", src,
        "-vf", "scale='min(480,iw)':-2",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "35",
        "-an",
        "-movflags", "+faststart",
        dst,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_VIDEO_COMPRESS_TIMEOUT)
        from pathlib import Path as _P
        return proc.returncode == 0 and _P(dst).is_file() and _P(dst).stat().st_size > 0
    except Exception as err:
        _LOGGER.debug("Video compress failed: %s", err)
        return False


def _get_video_duration(ffprobe_bin: str, file_path: str) -> float:
    import subprocess
    try:
        result = subprocess.run(
            [
                ffprobe_bin, "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", file_path,
            ],
            capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except Exception:
        return 0.0


def _extract_frames_sync(
    ffmpeg_bin: str,
    file_path: str,
    out_dir: str,
    num_frames: int,
    duration: float,
) -> list[str]:
    import subprocess
    if duration <= 0:
        timestamps = [0.0, 0.5, 1.0, 2.0, 5.0, 8.0, 12.0, 16.0, 20.0, 25.0][:num_frames]
    else:
        step = duration / num_frames
        timestamps = [step * i for i in range(num_frames)]

    paths: list[str] = []
    for i, ts in enumerate(timestamps):
        out = f"{out_dir}/frame_{i:03d}.jpg"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", file_path,
            "-ss", f"{ts:.2f}",
            "-frames:v", "1",
            "-q:v", "2",
            out,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=_FFMPEG_FRAME_TIMEOUT,
            )
            from pathlib import Path as _P
            if _P(out).is_file() and _P(out).stat().st_size > 0:
                paths.append(out)
            elif proc.returncode != 0:
                _LOGGER.debug(
                    "ffmpeg frame %d failed (rc=%d): %s",
                    i, proc.returncode,
                    (proc.stderr or b"")[-500:].decode("utf-8", errors="replace"),
                )
        except Exception as err:
            _LOGGER.debug("ffmpeg frame %d exception: %s", i, err)
    return paths


def _compose_frame_grid(
    frame_bytes_list: list[bytes],
    max_dim: int = 640,
    target_kb: int = 80,
) -> tuple[bytes, int, int] | None:
    from io import BytesIO
    try:
        from PIL import Image
    except ImportError:
        return None

    if not frame_bytes_list:
        return None

    images = []
    for fb in frame_bytes_list:
        try:
            images.append(Image.open(BytesIO(fb)).convert("RGB"))
        except Exception:
            pass
    if not images:
        return None

    n = len(images)
    if n <= 2:
        cols, rows = n, 1
    elif n <= 4:
        cols, rows = 2, 2
    elif n <= 6:
        cols, rows = 3, 2
    elif n <= 8:
        cols, rows = 4, 2
    else:
        cols, rows = 5, 2

    grid_dim = max_dim if n <= 6 else int(max_dim * 1.4)
    thumb_w = grid_dim // cols
    thumb_h = thumb_w * 3 // 4
    grid_w = cols * thumb_w
    grid_h = rows * thumb_h

    grid = Image.new("RGB", (grid_w, grid_h), (30, 30, 30))
    for idx, img in enumerate(images[:cols * rows]):
        r, c = divmod(idx, cols)
        resized = img.resize((thumb_w, thumb_h), Image.LANCZOS)
        grid.paste(resized, (c * thumb_w, r * thumb_h))

    buf = BytesIO()
    quality = 70
    grid.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    if len(data) > target_kb * 1024 and quality > 40:
        buf = BytesIO()
        grid.save(buf, format="JPEG", quality=50, optimize=True)
        data = buf.getvalue()

    return data, grid_w, grid_h


def _extract_at_timestamps(
    ffmpeg_bin: str,
    file_path: str,
    out_dir: str,
    timestamps: list[float],
) -> list[str]:
    import subprocess
    paths: list[str] = []
    for i, ts in enumerate(timestamps):
        out = f"{out_dir}/frame_{i:03d}.jpg"
        cmd = [
            ffmpeg_bin, "-y",
            "-i", file_path,
            "-ss", f"{ts:.2f}",
            "-frames:v", "1",
            "-q:v", "2",
            out,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_FRAME_TIMEOUT)
            from pathlib import Path as _P
            if _P(out).is_file() and _P(out).stat().st_size > 0:
                paths.append(out)
            elif proc.returncode != 0:
                _LOGGER.debug(
                    "ffmpeg ts=%.2f failed (rc=%d): %s",
                    ts, proc.returncode,
                    (proc.stderr or b"")[-300:].decode("utf-8", errors="replace"),
                )
        except Exception as err:
            _LOGGER.debug("ffmpeg ts=%.2f exception: %s", ts, err)
    return paths


def _extract_gif_frames_pil_raw(
    file_path: str, max_dim: int, max_frames: int = 6,
) -> list[bytes]:
    from io import BytesIO
    try:
        from PIL import Image
        img = Image.open(file_path)
    except Exception:
        return []

    n_frames = getattr(img, "n_frames", 1)
    if n_frames <= 1:
        step = 1
    else:
        step = max(1, n_frames // max_frames)
    indices = list(range(0, n_frames, step))[:max_frames]

    frames: list[bytes] = []
    for idx in indices:
        try:
            img.seek(idx)
            rgb = img.convert("RGB")
            w, h = rgb.size
            if w > max_dim or h > max_dim:
                ratio = max_dim / max(w, h)
                rgb = rgb.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            buf = BytesIO()
            rgb.save(buf, format="JPEG", quality=70, optimize=True)
            frames.append(buf.getvalue())
        except Exception:
            pass
    return frames


def _ffmpeg_diagnose(ffmpeg_bin: str, file_path: str) -> str:
    import subprocess
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-y", "-i", file_path, "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, timeout=15,
        )
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        return "\n".join(lines[-20:]) if lines else f"rc={proc.returncode}, no stderr"
    except Exception as err:
        return f"diagnose failed: {err}"


class MediaAnalyzeTool(llm.Tool):
    name = "MediaAnalyze"
    description = (
        "Analyze uploaded media files (images, GIFs, videos) ONLY.\n"
        "Supported: .jpg .jpeg .png .webp .bmp .gif .mp4 .mov .mkv .webm .avi\n"
        "NOT supported: documents (.docx .pdf .txt .csv .xlsx etc). "
        "Never call this tool for non-media files.\n"
        "Use this when the user sends a picture, photo, GIF, or video "
        "via IM or references a local file path.\n"
        "For images: returns a single base64 JPEG.\n"
        "For videos/GIFs: extracts key frames and returns multiple "
        "base64 JPEGs so you can understand the video content.\n"
        "Workflow for videos:\n"
        "  1) First call without timestamps → auto-extracts overview frames.\n"
        "  2) If you need more detail at specific moments, call again with "
        "timestamps=[1.5, 3.0, 7.2] to extract frames at those exact seconds.\n"
        "IMPORTANT: After seeing the media, respond NATURALLY — react to "
        "its mood, humor, meaning, or intent. Do NOT mechanically list "
        "objects or describe frames. Act like a human reacting to the content.\n"
        "Params: file_path (required), max_dim (default 640), "
        "target_kb (default 40), timestamps (optional list of seconds "
        "for precise frame extraction on follow-up calls)."
    )
    parameters = vol.Schema({
        vol.Required("file_path"): str,
        vol.Optional("max_dim", default=640): int,
        vol.Optional("target_kb", default=40): int,
        vol.Optional("timestamps", default=[]): list,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        import base64
        from pathlib import Path

        file_path = (tool_input.tool_args.get("file_path", "") or "").strip()
        max_dim = max(160, int(tool_input.tool_args.get("max_dim", 640) or 640))
        target_kb = max(10, int(tool_input.tool_args.get("target_kb", 40) or 40))
        timestamps = tool_input.tool_args.get("timestamps") or []

        if not file_path:
            return {"success": False, "error": "file_path is required"}

        p = Path(file_path)
        if not await hass.async_add_executor_job(p.is_file):
            return {"success": False, "error": f"File not found: {file_path}"}

        ext = p.suffix.lstrip(".").lower()

        if ext == _GIF_EXTENSION:
            return await self._analyze_gif(hass, file_path, p, max_dim, target_kb)

        if ext in _VIDEO_EXTENSIONS:
            return await self._analyze_video(hass, file_path, p, ext, max_dim, target_kb, timestamps)

        if ext not in _IMAGE_EXTENSIONS:
            return {
                "success": False,
                "error": (
                    f"Unsupported file type '.{ext}' for MediaAnalyze. "
                    f"This tool only handles images ({', '.join(sorted(_IMAGE_EXTENSIONS))}), "
                    f"GIFs, and videos. For documents, use a different approach."
                ),
            }

        return await self._analyze_image(hass, file_path, p, max_dim, target_kb)

    async def _analyze_image(
        self, hass: HomeAssistant, file_path: str, p, max_dim: int, target_kb: int,
    ) -> JsonObjectType:
        import base64

        try:
            raw_bytes = await hass.async_add_executor_job(p.read_bytes)
        except Exception as err:
            return {"success": False, "error": f"Failed to read file: {err}"}
        if not raw_bytes:
            return {"success": False, "error": "File is empty"}

        try:
            jpeg_bytes, w, h, q = await hass.async_add_executor_job(
                _compress_camera_frame, raw_bytes, max_dim, target_kb
            )
        except Exception as err:
            _LOGGER.warning("MediaAnalyze image compress failed for %s: %s", p.name, err)
            return {"success": False, "error": f"Image processing failed: {err}"}

        return {
            "success": True,
            "media_type": "image",
            "source": file_path,
            "image_base64": base64.b64encode(jpeg_bytes).decode("utf-8"),
            "content_type": "image/jpeg",
            "width": w,
            "height": h,
            "message": f"Image {p.name}: {w}x{h}. React naturally to what you see — do NOT just list objects.",
        }

    async def _analyze_gif(
        self, hass: HomeAssistant, file_path: str, p, max_dim: int, target_kb: int,
    ) -> JsonObjectType:
        import base64

        frames_data = await hass.async_add_executor_job(
            _extract_gif_frames_pil_raw, file_path, max_dim
        )
        if not frames_data:
            return await self._analyze_image(hass, file_path, p, max_dim, target_kb)

        grid_result = await hass.async_add_executor_job(
            _compose_frame_grid, frames_data, max_dim, target_kb * 2
        )
        if not grid_result:
            return await self._analyze_image(hass, file_path, p, max_dim, target_kb)

        grid_bytes, grid_w, grid_h = grid_result
        return {
            "success": True,
            "media_type": "GIF",
            "source": file_path,
            "image_base64": base64.b64encode(grid_bytes).decode("utf-8"),
            "content_type": "image/jpeg",
            "width": grid_w,
            "height": grid_h,
            "frame_count": len(frames_data),
            "message": (
                f"GIF {p.name}: {len(frames_data)} frames as grid."
                " React naturally to the content — humor, emotion, meaning. Don't mechanically describe each frame."
            ),
        }

    async def _analyze_video(
        self, hass: HomeAssistant, file_path: str, p, ext: str,
        max_dim: int, target_kb: int, timestamps: list | None = None,
    ) -> JsonObjectType:
        import base64
        import shutil
        from pathlib import Path

        ffmpeg_bin, ffprobe_bin = await hass.async_add_executor_job(
            lambda: (shutil.which("ffmpeg"), shutil.which("ffprobe"))
        )
        if not ffmpeg_bin:
            return {"success": False, "error": "ffmpeg not found — cannot extract video frames"}

        if not await hass.async_add_executor_job(p.is_file):
            return {"success": False, "error": f"Video file not found: {file_path}"}
        file_size = await hass.async_add_executor_job(lambda: p.stat().st_size)
        if file_size == 0:
            return {"success": False, "error": f"Video file is empty: {file_path}"}

        from ..runtime.data_path import tmp_dir_path
        import uuid
        work_dir = tmp_dir_path(hass) / f"vframes_{uuid.uuid4().hex[:8]}"
        await hass.async_add_executor_job(lambda: work_dir.mkdir(parents=True, exist_ok=True))
        frame_dir = work_dir

        effective_path = file_path
        if file_size > _VIDEO_COMPRESS_THRESHOLD:
            compressed_path = str(work_dir / "compressed.mp4")
            ok = await hass.async_add_executor_job(
                _compress_video_sync, ffmpeg_bin, file_path, compressed_path
            )
            if ok:
                effective_path = compressed_path
                compressed_size = await hass.async_add_executor_job(
                    lambda: Path(compressed_path).stat().st_size
                )
                _LOGGER.debug(
                    "MediaAnalyze compressed video: %d -> %d bytes",
                    file_size, compressed_size,
                )

        duration = 0.0
        if ffprobe_bin:
            duration = await hass.async_add_executor_job(
                _get_video_duration, ffprobe_bin, effective_path
            )

        ai_timestamps = None
        if timestamps:
            ai_timestamps = [max(0.0, float(t)) for t in timestamps[:10]]

        _LOGGER.debug(
            "MediaAnalyze video: path=%s size=%d duration=%.1f timestamps=%s compressed=%s",
            file_path, file_size, duration, ai_timestamps, effective_path != file_path,
        )

        if ai_timestamps:
            frame_paths = await hass.async_add_executor_job(
                _extract_at_timestamps,
                ffmpeg_bin, effective_path, str(frame_dir), ai_timestamps,
            )
            used_timestamps = ai_timestamps
        else:
            num_frames = _calc_frame_count(duration)
            frame_paths = await hass.async_add_executor_job(
                _extract_frames_sync,
                ffmpeg_bin, effective_path, str(frame_dir),
                num_frames, duration,
            )
            if duration <= 0:
                used_timestamps = [0.0, 0.5, 1.0, 2.0, 5.0, 8.0, 12.0, 16.0, 20.0, 25.0][:len(frame_paths)]
            else:
                step = duration / num_frames
                used_timestamps = [step * i for i in range(num_frames)][:len(frame_paths)]

        if not frame_paths:
            diag = await hass.async_add_executor_job(
                _ffmpeg_diagnose, ffmpeg_bin, effective_path
            )
            _LOGGER.warning(
                "MediaAnalyze ffmpeg failed for %s: %s", p.name, diag[:200]
            )
            return {
                "success": False,
                "error": f"Failed to extract frames from {p.name}",
                "diagnostics": diag,
            }

        raw_frames: list[bytes] = []
        ts_labels: list[str] = []
        for idx, fp in enumerate(frame_paths):
            try:
                raw = await hass.async_add_executor_job(Path(fp).read_bytes)
                if raw:
                    raw_frames.append(raw)
                    if idx < len(used_timestamps):
                        ts_labels.append(f"{used_timestamps[idx]:.1f}s")
            except Exception as err:
                _LOGGER.debug("MediaAnalyze frame read skip %s: %s", fp, err)

        if not raw_frames:
            return {"success": False, "error": f"All frames failed to read for {p.name}"}

        grid_result = await hass.async_add_executor_job(
            _compose_frame_grid, raw_frames, max_dim, target_kb * 2
        )
        if not grid_result:
            return {"success": False, "error": f"Failed to compose frame grid for {p.name}"}

        grid_bytes, grid_w, grid_h = grid_result

        import shutil as _shutil
        await hass.async_add_executor_job(lambda: _shutil.rmtree(work_dir, ignore_errors=True))

        mode_hint = "AI-selected timestamps" if ai_timestamps else "auto overview"
        return {
            "success": True,
            "media_type": "video",
            "source": file_path,
            "image_base64": base64.b64encode(grid_bytes).decode("utf-8"),
            "content_type": "image/jpeg",
            "width": grid_w,
            "height": grid_h,
            "frame_count": len(raw_frames),
            "duration_seconds": round(duration, 1) if duration > 0 else None,
            "frame_timestamps": ts_labels,
            "extraction_mode": mode_hint,
            "message": (
                f"Video {p.name}: {len(raw_frames)} frames as grid ({mode_hint})"
                + (f", {duration:.1f}s total" if duration > 0 else "")
                + f". Frames at: {', '.join(ts_labels)}."
                + " React naturally to the video content — don't just list what each frame shows."
                + (" Call again with timestamps=[...] if you need specific moments." if not ai_timestamps else "")
            ),
        }


class GetConversationHistoryTool(llm.Tool):
    name = "GetConversationHistory"
    description = (
        "Inspect/manage conversation history. "
        "action=get (default): current conversation's recent turns. "
        "action=recent: turns from ALL conversations touched within the last N minutes — "
        "use this to recall what was just discussed even after the user closed the window / got a new conversation_id. "
        "action=clear: delete history for current conversation, a specific conversation_id, or all (scope=all). "
        "action=stats: counts and oldest/newest timestamps. "
        "Params: action, max_turns(default 5), include_tools(bool), recent_minutes(default 60), "
        "conversation_id(optional, override target), scope(current|all for clear)."
    )
    parameters = vol.Schema({
        vol.Optional("action", default="get"): vol.In(["get", "recent", "clear", "stats"]),
        vol.Optional("max_turns", default=5): int,
        vol.Optional("include_tools", default=False): bool,
        vol.Optional("recent_minutes", default=60): vol.Any(int, float),
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
        recent_minutes = float(args.get("recent_minutes", 60) or 60)
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


_EXPOSE_PRIVACY_NOTICE = (
    "Hello! I'm the Claw Assistant integration. This project was created to unlock the full potential "
    "of AI in your smart home — giving you powerful voice and chat control over your devices. "
    "To enable AI control of a device, it needs to be 'exposed' to the conversation assistant. "
    "\n\n⚠️ PRIVACY & SECURITY NOTICE:\n"
    "• Your data stays 100% LOCAL within your Home Assistant instance\n"
    "• No device states or commands are sent to external servers\n"
    "• Only exposed entities can be read or controlled by AI\n"
    "• You can unexpose entities at any time to revoke AI access\n"
    "• This integration respects Home Assistant's built-in privacy controls\n\n"
    "By exposing an entity, you're granting the AI assistant permission to:\n"
    "1. Read the device's current state and attributes\n"
    "2. Send control commands (turn on/off, adjust settings, etc.)\n"
    "3. Include the device in automations and routines\n\n"
    "Would you like me to proceed with exposing this entity?"
)


class ExposeEntityTool(llm.Tool):
    name = "ExposeEntity"
    description = (
        "Expose or unexpose entities to the conversation assistant. "
        "⚠️ IMPORTANT: Before exposing any entity, you MUST present the full privacy notice to the user "
        "(available in tool result as 'privacy_notice'). Wait for user acknowledgment before proceeding. "
        "action=list: list unexposed entities (returns privacy_notice). "
        "action=expose: expose/unexpose entity. "
        "Params: action(expose/list), entity_id, expose(bool, default true), domain."
    )

    parameters = vol.Schema(
        {
            vol.Optional("action", default="expose"): vol.In(["expose", "list"]),
            vol.Optional("entity_id"): str,
            vol.Optional("entity_ids"): list,
            vol.Optional("expose", default=True): bool,
            vol.Optional("domain"): str,
            vol.Optional("confirmed", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        from homeassistant.components.homeassistant.exposed_entities import (
            async_expose_entity,
            async_should_expose,
        )

        action = tool_input.tool_args.get("action", "expose")
        entity_id = tool_input.tool_args.get("entity_id")
        entity_ids = tool_input.tool_args.get("entity_ids") or []
        expose = tool_input.tool_args.get("expose", True)
        domain = tool_input.tool_args.get("domain")
        confirmed = tool_input.tool_args.get("confirmed", False)

        if entity_id and entity_id not in entity_ids:
            entity_ids = [entity_id] + list(entity_ids)

        if action == "list":
            unexposed = []
            for state in hass.states.async_all():
                eid = state.entity_id
                if domain and not eid.startswith(f"{domain}."):
                    continue
                if not async_should_expose(hass, "conversation", eid):
                    unexposed.append({
                        "entity_id": eid,
                        "name": state.attributes.get("friendly_name", eid),
                    })
            return {
                "success": True,
                "action": "list",
                "domain": domain,
                "unexposed_count": len(unexposed),
                "unexposed_entities": unexposed[:50],
                "privacy_notice": _EXPOSE_PRIVACY_NOTICE,
                "instruction": "IMPORTANT: Present the privacy_notice to the user BEFORE exposing any entity.",
            }

        if not entity_ids:
            return {"success": False, "error": "entity_id or entity_ids required for expose action"}

        targets = []
        for eid in entity_ids:
            state = hass.states.get(eid)
            if state:
                targets.append({
                    "entity_id": eid,
                    "name": state.attributes.get("friendly_name", eid),
                    "currently_exposed": async_should_expose(hass, "conversation", eid),
                })

        if not targets:
            return {"success": False, "error": "No valid entities found"}

        if not confirmed:
            return {
                "success": False,
                "requires_confirmation": True,
                "action": "expose" if expose else "unexpose",
                "targets": targets,
                "privacy_notice": _EXPOSE_PRIVACY_NOTICE,
                "instruction": (
                    "STOP! You MUST present the privacy_notice to the user and list the entities to be exposed. "
                    "Wait for user to acknowledge. Then call again with confirmed=true."
                ),
            }

        results = []
        for eid in entity_ids:
            state = hass.states.get(eid)
            if not state:
                continue
            was_exposed = async_should_expose(hass, "conversation", eid)
            async_expose_entity(hass, "conversation", eid, expose)
            is_exposed = async_should_expose(hass, "conversation", eid)
            results.append({
                "entity_id": eid,
                "name": state.attributes.get("friendly_name", eid),
                "was_exposed": was_exposed,
                "is_exposed": is_exposed,
            })

        return {
            "success": True,
            "action": "exposed" if expose else "unexposed",
            "count": len(results),
            "results": results,
        }


_PLUGIN_RESULT_SENSITIVE_KEYS = frozenset({
    "plugin_path", "module_path", "hermes_home", "database_path",
    "database_path_source", "plugin_git_commit", "plugin_git_remote",
    "tool_args", "fingerprint", "approval_id",
})


def _filter_plugin_result(result: dict) -> dict:
    """Filter sensitive fields from plugin tool result to prevent leakage."""
    if not isinstance(result, dict):
        return result
    filtered = {}
    for key, value in result.items():
        if key in _PLUGIN_RESULT_SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            value = _filter_plugin_result(value)
        elif isinstance(value, list):
            value = [_filter_plugin_result(v) if isinstance(v, dict) else v for v in value]
        filtered[key] = value
    return filtered


class PluginManagerTool(llm.Tool):
    name = "PluginManager"
    description = (
        "Manage Hermes-compatible plugins in Home Assistant. "
        "Actions: list (all), loaded (active + their tool names), "
        "load/unload/hot_reload (single), reload_all, "
        "validate (check source), guide (install help), "
        "install (clone from GitHub), uninstall (unload + delete from disk), "
        "call_tool (invoke a loaded plugin tool by name + args). "
        "Plugins dir: .storage/claw_assistant/plugins/. "
        "HOT reload - no HA restart needed."
    )
    parameters = vol.Schema({
        vol.Required("action"): vol.In([
            "list", "loaded", "load", "unload", "uninstall", "hot_reload", "reload_all",
            "validate", "guide", "install", "pending", "cancel_approval", "call_tool"
        ]),
        vol.Optional("plugin_name", default=""): str,
        vol.Optional("source_path", default=""): str,
        vol.Optional("git_url", default=""): str,
        vol.Optional("approval_id", default=""): str,
        vol.Optional("tool_name", default=""): str,
        vol.Optional("tool_args", default={}): dict,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..runtime.plugin_store import (
            cancel_plugin_approval,
            get_loaded_plugins,
            get_plugin_install_guide,
            get_plugin_tool_registry,
            hot_load_plugin,
            hot_reload_plugin,
            hot_unload_plugin,
            list_installed_plugins,
            list_pending_plugin_approvals,
            reload_plugins,
            validate_plugin_installation,
        )
        from ..runtime.tool_executor import execute_kernel_tool

        action = tool_input.tool_args.get("action", "list")
        plugin_name = tool_input.tool_args.get("plugin_name", "").strip()
        source_path = tool_input.tool_args.get("source_path", "").strip()
        git_url = tool_input.tool_args.get("git_url", "").strip()
        approval_id = tool_input.tool_args.get("approval_id", "").strip()
        plugin_tool_name = tool_input.tool_args.get("tool_name", "").strip()
        plugin_tool_args_raw = tool_input.tool_args.get("tool_args", {})
        if isinstance(plugin_tool_args_raw, str):
            try:
                plugin_tool_args = json.loads(plugin_tool_args_raw) if plugin_tool_args_raw.strip() else {}
            except Exception:
                plugin_tool_args = {}
        elif isinstance(plugin_tool_args_raw, dict):
            plugin_tool_args = plugin_tool_args_raw
        else:
            plugin_tool_args = {}
        plugins_dir = Path(hass.config.config_dir) / ".storage/claw_assistant/plugins"

        if action == "list":
            plugins = await hass.async_add_executor_job(list_installed_plugins)
            return {
                "success": True,
                "count": len(plugins),
                "plugins": plugins,
                "plugins_dir": str(Path(hass.config.config_dir) / ".storage/claw_assistant/plugins"),
            }

        if action == "loaded":
            loaded = get_loaded_plugins()
            return {"success": True, "count": len(loaded), "plugins": loaded}

        if action == "load":
            if not plugin_name:
                return {"success": False, "error": "plugin_name required"}
            return hot_load_plugin(hass, plugin_name)

        if action == "unload":
            if not plugin_name:
                return {"success": False, "error": "plugin_name required"}
            return hot_unload_plugin(hass, plugin_name)

        if action == "hot_reload":
            if not plugin_name:
                return {"success": False, "error": "plugin_name required"}
            return hot_reload_plugin(hass, plugin_name)

        if action == "reload_all":
            result = await hass.async_add_executor_job(reload_plugins, hass)
            return result

        if action == "validate":
            if not source_path:
                return {"success": False, "error": "source_path required"}
            result = await hass.async_add_executor_job(validate_plugin_installation, source_path)
            return result

        if action == "guide":
            if not plugin_name:
                return {"success": False, "error": "plugin_name required"}
            result = await hass.async_add_executor_job(get_plugin_install_guide, plugin_name)
            return result

        if action == "install":
            if not git_url:
                return {"success": False, "error": "git_url required (GitHub repo URL)"}
            import re
            import subprocess
            match = re.search(r"github\.com[/:]([^/]+)/([^/\s\.]+)", git_url)
            if not match:
                return {"success": False, "error": "Invalid GitHub URL"}
            repo_name = match.group(2).replace(".git", "")
            target_dir = plugins_dir / repo_name
            plugins_dir.mkdir(parents=True, exist_ok=True)
            if target_dir.exists():
                return {
                    "success": False,
                    "error": f"Plugin '{repo_name}' already exists",
                    "hint": f"Use hot_reload to reload, or delete {target_dir} first",
                }
            try:
                result = await hass.async_add_executor_job(
                    subprocess.run,
                    ["git", "clone", "--depth", "1", git_url, str(target_dir)],
                    {"capture_output": True, "text": True, "timeout": 60}
                )
                if result.returncode != 0:
                    return {"success": False, "error": f"Git clone failed: {result.stderr[:500]}"}
                load_result = hot_load_plugin(hass, repo_name)
                return {
                    "success": True,
                    "action": "installed",
                    "plugin": repo_name,
                    "path": str(target_dir),
                    "load_result": load_result,
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        if action == "uninstall":
            if not plugin_name:
                return {"success": False, "error": "plugin_name required"}
            import shutil
            target_dir = plugins_dir / plugin_name
            if not target_dir.exists():
                return {"success": False, "error": f"Plugin '{plugin_name}' not found in {plugins_dir}"}
            unload_result = hot_unload_plugin(hass, plugin_name)
            try:
                await hass.async_add_executor_job(shutil.rmtree, str(target_dir))
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Unloaded from memory but failed to delete: {e}",
                    "unload_result": unload_result,
                }
            return {
                "success": True,
                "action": "uninstalled",
                "plugin": plugin_name,
                "path": str(target_dir),
                "unload_result": unload_result,
            }

        if action == "pending":
            pending = list_pending_plugin_approvals(hass)
            return {"success": True, "count": len(pending), "pending": pending}

        if action == "cancel_approval":
            if not approval_id:
                return {"success": False, "error": "approval_id required"}
            return cancel_plugin_approval(hass, approval_id)

        if action == "call_tool":
            if not plugin_tool_name:
                return {"success": False, "error": "tool_name required. Use action=loaded to see available plugin tools."}
            plugin_registry = get_plugin_tool_registry()
            if plugin_tool_name not in plugin_registry:
                available = list(plugin_registry.keys())[:5]
                hint = f"Available: {', '.join(available)}" if available else "No plugin tools loaded."
                return {"success": False, "error": f"Plugin tool not found: {plugin_tool_name}. {hint}"}
            result = await execute_kernel_tool(
                hass,
                tool_name=plugin_tool_name,
                tool_args=plugin_tool_args,
                agent_id=llm_context.assistant or llm_context.platform or "conversation",
                context=llm_context.context,
                language=llm_context.language,
                device_id=llm_context.device_id,
            )
            filtered = _filter_plugin_result(result)
            filtered["called_via"] = "PluginManager"
            return filtered

        return {"success": False, "error": f"Unknown action: {action}"}
