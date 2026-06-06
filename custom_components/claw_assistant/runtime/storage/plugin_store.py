from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.llm import JsonObjectType

from ..utils.data_path import get_data_dir
from ..core.state import get_config_approval_state

from ...plugins import (
    PluginContext,
    PluginManifest,
    PluginToolInfo,
    LoadedPlugin,
    get_plugin_executor,
    shutdown_plugin_executor,
    build_plugin_runner_script,
    run_plugin_subprocess,
    analyze_plugin_tools,
    discover_plugins,
    parse_plugin_manifest,
    plugins_dir,
    ensure_plugin_store,
    stage_plugin_call,
    execute_with_approval,
    cancel_approval,
    list_pending,
    build_approval_prompt,
    validate_plugin_installation,
    get_plugin_install_guide,
    set_data_dir_fn,
    set_approval_state_fn,
    get_all_context_engines,
)

LOGGER = logging.getLogger(__name__)
PLUGIN_CALL_TIMEOUT = 30

set_data_dir_fn(get_data_dir)
set_approval_state_fn(get_config_approval_state)

__all__ = [
    "PluginContext",
    "PluginManifest",
    "PluginToolInfo",
    "LoadedPlugin",
    "IsolatedPluginTool",
    "PrivilegedPluginTool",
    "get_plugin_executor",
    "shutdown_plugin_executor",
    "build_plugin_runner_script",
    "run_plugin_subprocess",
    "analyze_plugin_tools",
    "discover_plugins",
    "parse_plugin_manifest",
    "plugins_dir",
    "ensure_plugin_store",
    "stage_plugin_call",
    "execute_with_approval",
    "cancel_approval",
    "list_pending",
    "build_approval_prompt",
    "validate_plugin_installation",
    "get_plugin_install_guide",
    "analyze_plugin",
    "load_all_plugins",
    "get_plugin_tools",
    "get_plugin_tool_registry",
    "list_installed_plugins",
    "reload_plugins",
    "hot_load_plugin",
    "hot_unload_plugin",
    "hot_reload_plugin",
    "enable_plugin",
    "disable_plugin",
    "get_loaded_plugins",
    "cancel_plugin_approval",
    "list_pending_plugin_approvals",
    "build_plugin_approval_prompt",
]

_PLUGIN_STORE: dict[str, LoadedPlugin] = {}
_PLUGIN_TOOLS: dict[str, list[llm.Tool]] = {}


def _convert_schema_to_vol(schema: dict) -> vol.Schema:
    if not schema:
        return vol.Schema({})
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    vol_schema = {}
    for key, prop in props.items():
        prop_type = prop.get("type", "string")
        if prop_type == "string":
            validator = str
        elif prop_type == "integer":
            validator = int
        elif prop_type == "number":
            validator = vol.Coerce(float)
        elif prop_type == "boolean":
            validator = bool
        elif prop_type == "array":
            validator = list
        elif prop_type == "object":
            validator = dict
        else:
            validator = str
        if key in required:
            vol_schema[vol.Required(key)] = validator
        else:
            default = prop.get("default")
            vol_schema[vol.Optional(key, default=default)] = validator
    return vol.Schema(vol_schema)


def _extract_description(schema: dict | None, fallback: str = "") -> str:
    if not schema or not isinstance(schema, dict):
        return fallback or ""
    
    candidates = [
        schema.get("description"),
        schema.get("desc"),
        schema.get("summary"),
        schema.get("help"),
        schema.get("info"),
    ]
    
    if "parameters" in schema and isinstance(schema["parameters"], dict):
        params = schema["parameters"]
        candidates.extend([
            params.get("description"),
            params.get("desc"),
            params.get("summary"),
        ])
    
    if "function" in schema and isinstance(schema["function"], dict):
        func = schema["function"]
        candidates.extend([
            func.get("description"),
            func.get("desc"),
        ])
    
    if "tool" in schema and isinstance(schema["tool"], dict):
        tool = schema["tool"]
        candidates.extend([
            tool.get("description"),
            tool.get("desc"),
        ])
    
    if "metadata" in schema and isinstance(schema["metadata"], dict):
        meta = schema["metadata"]
        candidates.extend([
            meta.get("description"),
            meta.get("desc"),
        ])
    
    for c in candidates:
        if c and isinstance(c, str) and c.strip():
            return c.strip()
    
    return fallback or ""


class IsolatedPluginTool(llm.Tool):
    def __init__(
        self,
        tool_name: str,
        plugin_path: str,
        handler_name: str,
        schema: dict,
        description: str,
        plugin_name: str,
        requires_approval: bool = True,
        timeout: int = PLUGIN_CALL_TIMEOUT,
    ):
        self.name = tool_name
        self.description = f"[Plugin: {plugin_name}] {description}" if description else f"[Plugin: {plugin_name}]"
        self._plugin_path = plugin_path
        self._handler_name = handler_name
        self._plugin_name = plugin_name
        self._requires_approval = requires_approval
        self._timeout = timeout
        self._schema = schema
        base_schema = _convert_schema_to_vol(schema)
        if requires_approval:
            self.parameters = base_schema.extend({
                vol.Optional("approval_id", default=""): str,
                vol.Optional("user_consent", default=False): bool,
                vol.Optional("consent_quote", default=""): str,
            })
        else:
            self.parameters = base_schema

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        args = dict(tool_input.tool_args)
        approval_id = args.pop("approval_id", "")
        user_consent = args.pop("user_consent", False)
        consent_quote = args.pop("consent_quote", "")
        if self._requires_approval:
            if not approval_id:
                return stage_plugin_call(
                    hass, self._plugin_name, self.name, self._handler_name,
                    self._plugin_path, args, privileged=False
                )
            ok, pending, err = execute_with_approval(hass, approval_id, user_consent)
            if not ok:
                return {"success": False, "error": err}
            args = pending["args"]
            result = await self._execute_directly(hass, args)
            result["consent_quote"] = consent_quote
            result["approval_id"] = approval_id
            return result
        return await self._execute_directly(hass, args)

    async def _execute_directly(self, hass: HomeAssistant, args: dict) -> JsonObjectType:
        script = build_plugin_runner_script(self._plugin_path, self._handler_name, args)
        loop = asyncio.get_running_loop()
        executor = get_plugin_executor()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(executor, run_plugin_subprocess, script, self._timeout),
                timeout=self._timeout + 5,
            )
            return result
        except asyncio.TimeoutError:
            LOGGER.error("Plugin tool %s timed out", self.name)
            return {"success": False, "error": f"Timed out after {self._timeout}s"}
        except Exception as e:
            LOGGER.exception("Plugin tool %s failed", self.name)
            return {"success": False, "error": str(e)}


class PrivilegedPluginTool(llm.Tool):
    def __init__(
        self,
        tool_name: str,
        plugin_path: str,
        handler_name: str,
        schema: dict,
        description: str,
        plugin_name: str,
        timeout: int = PLUGIN_CALL_TIMEOUT,
    ):
        self.name = tool_name
        self.description = f"[Plugin: {plugin_name}] [PRIVILEGED] {description}"
        self._plugin_path = plugin_path
        self._handler_name = handler_name
        self._plugin_name = plugin_name
        self._timeout = timeout
        self._schema = schema
        base_schema = _convert_schema_to_vol(schema)
        self.parameters = base_schema.extend({
            vol.Optional("approval_id", default=""): str,
            vol.Optional("user_consent", default=False): bool,
            vol.Optional("consent_quote", default=""): str,
        })

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        args = dict(tool_input.tool_args)
        approval_id = args.pop("approval_id", "")
        user_consent = args.pop("user_consent", False)
        consent_quote = args.pop("consent_quote", "")
        if not approval_id:
            return stage_plugin_call(
                hass, self._plugin_name, self.name, self._handler_name,
                self._plugin_path, args, privileged=True
            )
        ok, pending, err = execute_with_approval(hass, approval_id, user_consent)
        if not ok:
            return {"success": False, "error": err}
        args = pending["args"]
        try:
            result = await self._execute_privileged(hass, args)
            result["consent_quote"] = consent_quote
            result["approval_id"] = approval_id
            return result
        except Exception as e:
            LOGGER.exception("Privileged plugin tool %s failed", self.name)
            return {"success": False, "error": str(e)}

    async def _execute_privileged(self, hass: HomeAssistant, args: dict) -> JsonObjectType:
        plugin_path = Path(self._plugin_path)
        init_path = plugin_path / "__init__.py"
        if not init_path.exists():
            return {"success": False, "error": "Plugin __init__.py not found"}
        ctx = PluginContext(hass, self._plugin_name)
        loop = asyncio.get_running_loop()
        executor = get_plugin_executor()

        def _load_module():
            module_name = f"claw_priv_{self._plugin_name}_{uuid.uuid4().hex[:8]}"
            spec = importlib.util.spec_from_file_location(module_name, init_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("Failed to load plugin module")
            module = importlib.util.module_from_spec(spec)
            plugin_parent = str(plugin_path.parent)
            plugin_str = str(plugin_path)
            if plugin_parent not in sys.path:
                sys.path.insert(0, plugin_parent)
            if plugin_str not in sys.path:
                sys.path.insert(0, plugin_str)
            spec.loader.exec_module(module)
            handler = getattr(module, self._handler_name, None)
            if handler is None:
                raise RuntimeError(f"Handler {self._handler_name} not found")
            return handler

        try:
            handler = await loop.run_in_executor(executor, _load_module)
            if asyncio.iscoroutinefunction(handler):
                result = await asyncio.wait_for(handler(ctx, args), timeout=self._timeout)
            else:
                result = await asyncio.wait_for(
                    loop.run_in_executor(executor, lambda: handler(ctx, args)),
                    timeout=self._timeout
                )
            if isinstance(result, dict):
                return result
            return {"success": True, "result": str(result) if result else None}
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Timed out after {self._timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}


def analyze_plugin(manifest: PluginManifest, hass: HomeAssistant | None = None) -> LoadedPlugin:
    from ...plugins import get_all_plugin_tools, clear_plugin_registrations
    plugin_path = Path(manifest.path)
    loaded = LoadedPlugin(manifest=manifest)
    if not (plugin_path / "__init__.py").exists():
        loaded.load_error = "Missing __init__.py"
        return loaded
    is_privileged = manifest.kind == "privileged"
    try:
        init_content = (plugin_path / "__init__.py").read_text(encoding="utf-8")
        if "(hass" in init_content or "(ctx:" in init_content or "(ctx," in init_content:
            is_privileged = True
        if "register_context_engine" in init_content or "register_hook" in init_content:
            is_privileged = True
    except Exception:
        pass
    clear_plugin_registrations(manifest.name)
    if is_privileged and hass:
        try:
            ctx = PluginContext(hass, manifest.name)
            module = _load_plugin_module(plugin_path, manifest.name)
            register_fn = getattr(module, "register", None)
            if callable(register_fn):
                register_fn(ctx)
                LOGGER.info("Plugin %s: called register(ctx)", manifest.name)
            loaded.module = module
        except Exception as e:
            LOGGER.warning("Plugin %s register() failed: %s", manifest.name, e)
            loaded.load_error = f"register() failed: {e}"
            _PLUGIN_STORE[manifest.key] = loaded
            return loaded
    registered_tools = get_all_plugin_tools()
    plugin_tools = {k: v for k, v in registered_tools.items() if v.get("_plugin") == manifest.name}
    try:
        tool_infos = analyze_plugin_tools(plugin_path)
        if manifest.provides_tools and not tool_infos:
            for tool_name in manifest.provides_tools:
                if tool_name not in plugin_tools:
                    tool_infos.append(PluginToolInfo(
                        name=tool_name,
                        handler_name=tool_name,
                        schema={},
                        description=f"Tool from {manifest.name}",
                    ))
        tools = []
        for info in tool_infos:
            if info.name in plugin_tools:
                continue
            info_schema = info.schema if isinstance(info.schema, dict) else {}
            info_desc = _extract_description(info_schema, info.description)
            if is_privileged:
                tool = PrivilegedPluginTool(
                    tool_name=info.name,
                    plugin_path=str(plugin_path),
                    handler_name=info.handler_name,
                    schema=info_schema,
                    description=info_desc,
                    plugin_name=manifest.name,
                )
            else:
                tool = IsolatedPluginTool(
                    tool_name=info.name,
                    plugin_path=str(plugin_path),
                    handler_name=info.handler_name,
                    schema=info_schema,
                    description=info_desc,
                    plugin_name=manifest.name,
                )
            tools.append(tool)
        for name, tool_def in plugin_tools.items():
            handler = tool_def.get("handler")
            raw_schema = tool_def.get("schema", {})
            description = _extract_description(raw_schema, tool_def.get("description", ""))
            schema = raw_schema
            if isinstance(schema, dict) and "parameters" in schema:
                schema = schema.get("parameters", {})
            tools.append(_create_registered_tool(
                name=name,
                handler=handler,
                schema=schema,
                description=description,
                plugin_name=manifest.name,
            ))
        _PLUGIN_TOOLS[manifest.key] = tools
        loaded.tools_registered = [t.name for t in tools]
        loaded.enabled = True
        mode = "privileged" if is_privileged else "isolated"
        LOGGER.info("Plugin %s: %d tools (%s)", manifest.name, len(tools), mode)
        context_engines = get_all_context_engines()
        if manifest.name in context_engines:
            try:
                from ...conversation_utils import get_conversation_history
                engine = context_engines[manifest.name]
                get_conversation_history().set_context_engine(engine)
                LOGGER.info("Plugin %s: context engine activated", manifest.name)
                if hasattr(engine, "get_tool_schemas") and hasattr(engine, "handle_tool_call"):
                    engine_tools = engine.get_tool_schemas()
                    engine_tool_names = {s.get("name", "") for s in engine_tools}
                    tools = [t for t in tools if t.name not in engine_tool_names]
                    for schema in engine_tools:
                        tool_name = schema.get("name", "")
                        if not tool_name:
                            continue
                        tools.append(_create_context_engine_tool(
                            name=tool_name,
                            engine=engine,
                            schema=schema.get("parameters", {}),
                            description=_extract_description(schema),
                            plugin_name=manifest.name,
                        ))
                    _PLUGIN_TOOLS[manifest.key] = tools
                    loaded.tools_registered = [t.name for t in tools]
                    LOGGER.info("Plugin %s: registered %d tools from context engine", manifest.name, len(engine_tools))
            except Exception as e:
                LOGGER.warning("Failed to set context engine from %s: %s", manifest.name, e)
        from ..llm.internal_llm import invalidate_runtime_tool_cache
        invalidate_runtime_tool_cache()
    except Exception as e:
        LOGGER.exception("Failed to analyze plugin %s", manifest.name)
        loaded.load_error = str(e)
    _PLUGIN_STORE[manifest.key] = loaded
    return loaded


def _load_plugin_module(plugin_path: Path, plugin_name: str):
    from ...plugins.hermes_compat import install_hermes_shims
    install_hermes_shims()
    init_path = plugin_path / "__init__.py"
    module_name = f"claw_plugin_{plugin_name}_{uuid.uuid4().hex[:8]}"
    plugin_parent = str(plugin_path.parent)
    plugin_str = str(plugin_path)
    if plugin_parent not in sys.path:
        sys.path.insert(0, plugin_parent)
    if plugin_str not in sys.path:
        sys.path.insert(0, plugin_str)
    pkg_name = plugin_path.name.replace("-", "_")
    if pkg_name not in sys.modules:
        pkg_module = type(sys)(pkg_name)
        pkg_module.__path__ = [plugin_str]
        pkg_module.__package__ = pkg_name
        sys.modules[pkg_name] = pkg_module
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.__init__",
        init_path,
        submodule_search_locations=[plugin_str],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load plugin module")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[f"{pkg_name}.__init__"] = module
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _create_context_engine_tool(name: str, engine: Any, schema: dict, description: str, plugin_name: str) -> llm.Tool:
    class ContextEngineTool(llm.Tool):
        def __init__(self):
            self.name = name
            self._tool_name = name
            self.description = f"[Plugin: {plugin_name}] {description}"
            self._engine = engine
            self._plugin_name = plugin_name
            self.parameters = _convert_schema_to_vol(schema)

        async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
            args = dict(tool_input.tool_args)
            tool_name = self._tool_name
            try:
                loop = asyncio.get_running_loop()
                executor = get_plugin_executor()
                result = await loop.run_in_executor(
                    executor, lambda: self._engine.handle_tool_call(tool_name, args)
                )
                if isinstance(result, str):
                    try:
                        return json.loads(result)
                    except json.JSONDecodeError:
                        return {"success": True, "result": result}
                if isinstance(result, dict):
                    return result
                return {"success": True, "result": str(result) if result else None}
            except Exception as e:
                LOGGER.exception("Context engine tool %s failed", self._tool_name)
                return {"success": False, "error": str(e)}
    return ContextEngineTool()


def _create_registered_tool(name: str, handler, schema: dict, description: str, plugin_name: str) -> llm.Tool:
    class RegisteredPluginTool(llm.Tool):
        def __init__(self):
            self.name = name
            self.description = f"[Plugin: {plugin_name}] {description}"
            self._handler = handler
            self._plugin_name = plugin_name
            self.parameters = _convert_schema_to_vol(schema)

        async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
            args = dict(tool_input.tool_args)
            try:
                if asyncio.iscoroutinefunction(self._handler):
                    result = await self._handler(args)
                else:
                    loop = asyncio.get_running_loop()
                    executor = get_plugin_executor()
                    result = await loop.run_in_executor(executor, lambda: self._handler(args))
                if isinstance(result, str):
                    try:
                        return json.loads(result)
                    except json.JSONDecodeError:
                        return {"success": True, "result": result}
                if isinstance(result, dict):
                    return result
                return {"success": True, "result": str(result) if result else None}
            except Exception as e:
                LOGGER.exception("Plugin tool %s failed", name)
                return {"success": False, "error": str(e)}
    return RegisteredPluginTool()


def load_all_plugins(hass: HomeAssistant) -> list[LoadedPlugin]:
    ensure_plugin_store()
    manifests = discover_plugins()
    results = []
    for manifest in manifests:
        if not manifest.is_valid:
            LOGGER.warning("Skipping invalid plugin: %s", manifest.name)
            continue
        if manifest.key in _PLUGIN_STORE and _PLUGIN_STORE[manifest.key].enabled:
            results.append(_PLUGIN_STORE[manifest.key])
            continue
        loaded = analyze_plugin(manifest, hass)
        results.append(loaded)
    return results


def get_plugin_tools() -> list[llm.Tool]:
    tools = []
    for plugin_tools in _PLUGIN_TOOLS.values():
        tools.extend(plugin_tools)
    return tools


def get_plugin_tool_registry() -> dict[str, dict[str, Any]]:
    registry = {}
    for key, tools in _PLUGIN_TOOLS.items():
        plugin = _PLUGIN_STORE.get(key)
        plugin_name = plugin.manifest.name if plugin else key
        for tool in tools:
            registry[tool.name] = {
                "category": "plugin",
                "desc": tool.description,
                "priority": 3,
                "plugin": plugin_name,
            }
    return registry


def list_installed_plugins() -> list[dict[str, Any]]:
    manifests = discover_plugins()
    result = []
    for m in manifests:
        loaded = _PLUGIN_STORE.get(m.key)
        install_time = None
        if m.path:
            try:
                install_time = Path(m.path).stat().st_mtime
            except Exception:
                pass
        tools_with_desc = []
        if loaded and loaded.enabled:
            for tool in _PLUGIN_TOOLS.get(m.key, []):
                tools_with_desc.append({
                    "name": tool.name,
                    "description": tool.description or "",
                })
        result.append({
            "name": m.name,
            "key": m.key,
            "version": m.version,
            "description": m.description,
            "author": m.author,
            "kind": m.kind,
            "path": m.path,
            "valid": m.is_valid,
            "loaded": loaded.enabled if loaded else False,
            "load_error": loaded.load_error if loaded else None,
            "tools_count": len(loaded.tools_registered) if loaded else 0,
            "tools_with_desc": tools_with_desc,
            "errors": m.validation_errors,
            "hints": m.validation_hints,
            "pip_dependencies": m.pip_dependencies,
            "requires_env": m.requires_env,
            "provides_tools": m.provides_tools,
            "install_time": install_time,
        })
    return result


def reload_plugins(hass: HomeAssistant) -> dict[str, Any]:
    global _PLUGIN_STORE, _PLUGIN_TOOLS
    _PLUGIN_STORE.clear()
    _PLUGIN_TOOLS.clear()
    loaded = load_all_plugins(hass)
    return {
        "success": True,
        "loaded": len([p for p in loaded if p.enabled]),
        "failed": len([p for p in loaded if not p.enabled]),
        "plugins": [
            {"name": p.manifest.name, "enabled": p.enabled, "error": p.load_error, "tools": p.tools_registered}
            for p in loaded
        ],
    }


def hot_load_plugin(hass: HomeAssistant, plugin_name: str) -> dict[str, Any]:
    pdir = plugins_dir()
    plugin_path = pdir / plugin_name
    if not plugin_path.exists():
        return {"success": False, "error": f"Plugin '{plugin_name}' not found"}
    manifest = parse_plugin_manifest(plugin_path)
    if not manifest:
        return {"success": False, "error": "Invalid plugin manifest"}
    if not manifest.is_valid:
        return {"success": False, "error": "Plugin validation failed", "errors": manifest.validation_errors}
    if manifest.key in _PLUGIN_STORE:
        hot_unload_plugin(hass, plugin_name)
    loaded = analyze_plugin(manifest, hass)
    if not loaded.enabled:
        return {"success": False, "error": loaded.load_error}
    return {
        "success": True,
        "plugin": manifest.name,
        "tools": loaded.tools_registered,
        "mode": "privileged" if manifest.kind == "privileged" else "isolated",
    }


def hot_unload_plugin(hass: HomeAssistant, plugin_name: str) -> dict[str, Any]:
    from ...plugins import clear_plugin_registrations
    from ..llm.internal_llm import invalidate_runtime_tool_cache
    key = plugin_name
    if key not in _PLUGIN_STORE:
        for k, p in _PLUGIN_STORE.items():
            if p.manifest.name == plugin_name:
                key = k
                break
    if key not in _PLUGIN_STORE:
        return {"success": False, "error": f"Plugin '{plugin_name}' not loaded"}
    loaded = _PLUGIN_STORE.pop(key)
    tools = _PLUGIN_TOOLS.pop(key, [])
    clear_plugin_registrations(loaded.manifest.name)
    invalidate_runtime_tool_cache()
    module = loaded.module
    if module:
        module_name = getattr(module, "__name__", None)
        if module_name and module_name in sys.modules:
            del sys.modules[module_name]
    return {
        "success": True,
        "plugin": loaded.manifest.name,
        "unloaded_tools": [t.name for t in tools],
    }


def hot_reload_plugin(hass: HomeAssistant, plugin_name: str) -> dict[str, Any]:
    unload_result = hot_unload_plugin(hass, plugin_name)
    if not unload_result["success"] and "not loaded" not in unload_result.get("error", ""):
        return unload_result
    return hot_load_plugin(hass, plugin_name)


def enable_plugin(hass: HomeAssistant, plugin_name: str) -> dict[str, Any]:
    return hot_load_plugin(hass, plugin_name)


def disable_plugin(hass: HomeAssistant, plugin_name: str) -> dict[str, Any]:
    return hot_unload_plugin(hass, plugin_name)


def get_loaded_plugins() -> list[dict[str, Any]]:
    return [
        {
            "name": p.manifest.name,
            "key": k,
            "enabled": p.enabled,
            "tools": p.tools_registered,
            "kind": p.manifest.kind,
        }
        for k, p in _PLUGIN_STORE.items()
    ]


def cancel_plugin_approval(hass: HomeAssistant, approval_id: str) -> dict[str, Any]:
    return cancel_approval(hass, approval_id)


def list_pending_plugin_approvals(hass: HomeAssistant) -> list[dict[str, Any]]:
    return list_pending(hass)


def build_plugin_approval_prompt(hass: HomeAssistant) -> str:
    return build_approval_prompt(hass)
