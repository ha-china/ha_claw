from __future__ import annotations

import ast
import logging
from pathlib import Path

from .manifest import PluginToolInfo

LOGGER = logging.getLogger(__name__)


def analyze_plugin_tools(plugin_path: Path) -> list[PluginToolInfo]:
    init_path = plugin_path / "__init__.py"
    if not init_path.exists():
        return []
    try:
        source = init_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        LOGGER.warning("Failed to parse %s: %s", init_path, e)
        return []
    tools = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "register":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Call):
                    if isinstance(stmt.func, ast.Attribute) and stmt.func.attr == "register_tool":
                        tool_info = extract_tool_info_from_call(stmt)
                        if tool_info:
                            tools.append(tool_info)
    return tools


def extract_tool_info_from_call(call: ast.Call) -> PluginToolInfo | None:
    try:
        kwargs = {}
        for kw in call.keywords:
            if kw.arg and isinstance(kw.value, ast.Constant):
                kwargs[kw.arg] = kw.value.value
            elif kw.arg and isinstance(kw.value, ast.Dict):
                kwargs[kw.arg] = _ast_dict_to_dict(kw.value)
            elif kw.arg and isinstance(kw.value, ast.Name):
                kwargs[kw.arg] = f"${kw.value.id}"
        for i, arg in enumerate(call.args):
            if i == 0 and isinstance(arg, ast.Constant):
                kwargs.setdefault("name", arg.value)
            elif i == 1 and isinstance(arg, ast.Constant):
                kwargs.setdefault("toolset", arg.value)
        name = kwargs.get("name")
        if not name:
            return None
        handler_name = None
        for kw in call.keywords:
            if kw.arg == "handler" and isinstance(kw.value, ast.Name):
                handler_name = kw.value.id
        if not handler_name:
            handler_name = name
        return PluginToolInfo(
            name=name,
            handler_name=handler_name,
            schema=kwargs.get("schema", {}),
            description=kwargs.get("description", ""),
            toolset=kwargs.get("toolset", "default"),
        )
    except Exception as e:
        LOGGER.debug("Failed to extract tool info: %s", e)
        return None


def _ast_dict_to_dict(node: ast.Dict) -> dict:
    result = {}
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
            result[key.value] = value.value
        elif isinstance(key, ast.Constant) and isinstance(value, ast.Dict):
            result[key.value] = _ast_dict_to_dict(value)
        elif isinstance(key, ast.Constant) and isinstance(value, ast.List):
            result[key.value] = [
                el.value if isinstance(el, ast.Constant) else str(el)
                for el in value.elts
            ]
    return result
