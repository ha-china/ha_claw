from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap


def build_plugin_runner_script(plugin_path: str, handler_name: str, tool_args: dict) -> str:
    args_json = json.dumps(tool_args)
    return textwrap.dedent(f'''
import sys
import json
import types
from pathlib import Path

def _setup_mocks():
    hermes_cli = types.ModuleType("hermes_cli")
    sys.modules["hermes_cli"] = hermes_cli
    hermes_cli_auth = types.ModuleType("hermes_cli.auth")
    hermes_cli_auth.get_auth_status = lambda provider: {{"logged_in": False}}
    sys.modules["hermes_cli.auth"] = hermes_cli_auth
    tools_mod = types.ModuleType("tools")
    sys.modules["tools"] = tools_mod
    tools_registry = types.ModuleType("tools.registry")
    tools_registry.tool_result = lambda data: {{"success": True, "result": data}}
    tools_registry.tool_error = lambda msg, **kw: {{"success": False, "error": msg, **kw}}
    tools_registry.register = lambda **kw: None
    sys.modules["tools.registry"] = tools_registry
    hermes_constants = types.ModuleType("hermes_constants")
    hermes_constants.get_hermes_home = lambda: Path.home() / ".hermes"
    sys.modules["hermes_constants"] = hermes_constants

def main():
    _setup_mocks()
    plugin_path = {repr(plugin_path)}
    handler_name = {repr(handler_name)}
    tool_args = {args_json}
    sys.path.insert(0, plugin_path)
    parent = str(Path(plugin_path).parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("plugin_module", Path(plugin_path) / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, handler_name, None)
        if handler is None:
            print(json.dumps({{"success": False, "error": f"Handler {{handler_name}} not found"}}))
            return
        result = handler(tool_args)
        if isinstance(result, dict):
            output = result
        elif result is None:
            output = {{"success": True}}
        else:
            output = {{"success": True, "result": str(result)}}
        print(json.dumps(output))
    except Exception as e:
        print(json.dumps({{"success": False, "error": str(e)}}))

if __name__ == "__main__":
    main()
''')


def run_plugin_subprocess(script: str, timeout: int) -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {"success": False, "error": f"Plugin crashed: {stderr[:500]}"}
        stdout = result.stdout.strip()
        if not stdout:
            return {"success": False, "error": "Plugin returned no output"}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return {"success": True, "result": stdout[:2000]}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Plugin timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": f"Subprocess error: {e}"}
