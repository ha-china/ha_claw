
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)

_SANDBOX_DIRNAME = "claw_assistant_sandbox"
_VENV_SUBDIR = "venv"
_INSTALLED_MARKER = ".installed_requirements.json"


def _sandbox_root(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(_SANDBOX_DIRNAME))


def _venv_path(hass: HomeAssistant) -> Path:
    return _sandbox_root(hass) / _VENV_SUBDIR


def _venv_python(hass: HomeAssistant) -> Path:
    venv_dir = _venv_path(hass)
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _installed_marker_path(hass: HomeAssistant) -> Path:
    return _sandbox_root(hass) / _INSTALLED_MARKER


_REQ_NAME_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._\-]*)")


def _normalize_requirement(req: str) -> str:
    text = req.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(("git+", "http://", "https://", "file://", "-e ", "--")):
        return lowered
    match = _REQ_NAME_RE.match(text)
    if not match:
        return lowered
    return match.group(1).lower().replace("_", "-")


def _load_installed(hass: HomeAssistant) -> set[str]:
    marker = _installed_marker_path(hass)
    if not marker.exists():
        return set()
    try:
        return set(json.loads(marker.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_installed(hass: HomeAssistant, installed: set[str]) -> None:
    marker = _installed_marker_path(hass)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(sorted(installed)), encoding="utf-8")


def _ensure_venv_sync(hass: HomeAssistant) -> Path:

    python_bin = _venv_python(hass)
    if python_bin.exists():
        return python_bin

    venv_dir = _venv_path(hass)
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info(
        "Creating sandbox venv at %s (first-time setup, may take ~30s)",
        venv_dir,
    )
    builder = venv.EnvBuilder(
        system_site_packages=False,
        with_pip=True,
        clear=False,
        upgrade=False,
        symlinks=(os.name != "nt"),
    )
    builder.create(str(venv_dir))
    return python_bin


async def _run_subprocess(
    args: list[str],
    timeout: int,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stdin: str | None = None,
) -> tuple[int, str, str]:
    base_env = {**os.environ, **(env or {})}
    base_env.setdefault("PYTHONIOENCODING", "utf-8")
    base_env.setdefault("PYTHONUTF8", "1")
    base_env.setdefault("LC_ALL", "C.UTF-8")
    base_env.setdefault("LANG", "C.UTF-8")
    proc_env = base_env

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        env=proc_env,
        cwd=cwd,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(stdin.encode("utf-8") if stdin is not None else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        (stdout_b or b"").decode("utf-8", errors="replace"),
        (stderr_b or b"").decode("utf-8", errors="replace"),
    )


async def ensure_sandbox_ready(
    hass: HomeAssistant,
    requirements: list[str] | None = None,
    pip_timeout: int = 120,
    *,
    pip_index_url: str | None = None,
) -> Path:

    python_bin = await hass.async_add_executor_job(_ensure_venv_sync, hass)

    cleaned = [req.strip() for req in (requirements or []) if req.strip()]
    if not cleaned:
        return python_bin

    installed = await hass.async_add_executor_job(_load_installed, hass)
    todo: list[str] = []
    todo_keys: list[str] = []
    for req in cleaned:
        key = _normalize_requirement(req)
        if key and key not in installed:
            todo.append(req)
            todo_keys.append(key)
    if not todo:
        return python_bin

    pip_args: list[str] = [
        str(python_bin), "-m", "pip", "install", "--disable-pip-version-check",
    ]
    if pip_index_url:
        pip_args += ["--index-url", pip_index_url]
    pip_args += todo

    LOGGER.info("Sandbox pip install: %s (index=%s)", todo, pip_index_url or "default")
    rc, stdout, stderr = await _run_subprocess(pip_args, timeout=pip_timeout)
    if rc != 0:
        raise RuntimeError(
            f"pip install failed (rc={rc}): {stderr.strip() or stdout.strip()}"
        )

    installed.update(todo_keys)
    await hass.async_add_executor_job(_save_installed, hass, installed)
    return python_bin


_RUNNER_PROLOGUE = """\
import ast as __ast
import json as __json
import sys as __sys
import traceback as __traceback

def __emit(payload):
    __sys.stdout.write('\\x00KADERMGR_RESULT\\x00' + __json.dumps(payload, default=str))
    __sys.stdout.flush()

__code = __USER_CODE__
__auto_key = '__auto_result__'
__local = {}
try:
    __tree = __ast.parse(__code, mode='exec')
    if __tree.body and isinstance(__tree.body[-1], __ast.Expr):
        __last = __tree.body[-1]
        __assign = __ast.Assign(
            targets=[__ast.Name(id=__auto_key, ctx=__ast.Store())],
            value=__last.value,
        )
        __ast.copy_location(__assign, __last)
        __tree.body[-1] = __assign
        __ast.fix_missing_locations(__tree)
    __compiled = compile(__tree, '<sandbox>', 'exec')
    exec(__compiled, __local, __local)
    if 'result' in __local:
        __result = __local['result']
    elif __auto_key in __local:
        __result = __local[__auto_key]
    else:
        __result = None
    __emit({'ok': True, 'result': __result})
except Exception as __err:
    __emit({'ok': False, 'error': repr(__err), 'traceback': __traceback.format_exc()})
"""


def _build_runner_script(user_code: str) -> str:

    return _RUNNER_PROLOGUE.replace("__USER_CODE__", repr(user_code))


async def run_in_sandbox(
    hass: HomeAssistant,
    code: str,
    *,
    requirements: list[str] | None = None,
    timeout: int = 60,
    pip_timeout: int = 120,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    stdin: str | None = None,
    pip_index_url: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    import time as _time

    started = _time.perf_counter()

    python_bin = await ensure_sandbox_ready(
        hass, requirements, pip_timeout, pip_index_url=pip_index_url
    )

    if dry_run:
        # Compile-only smoke test to catch syntax errors cheaply.
        try:
            compile(code, "<sandbox>", "exec")
        except SyntaxError as err:
            return {
                "success": False,
                "dry_run": True,
                "error": f"SyntaxError: {err.msg} (line {err.lineno})",
                "duration_ms": int((_time.perf_counter() - started) * 1000),
            }
        return {
            "success": True,
            "dry_run": True,
            "message": "Compile OK; not executed",
            "duration_ms": int((_time.perf_counter() - started) * 1000),
        }

    script = _build_runner_script(code)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(script)
        tmp_path = fh.name

    try:
        rc, stdout, stderr = await _run_subprocess(
            [str(python_bin), tmp_path],
            timeout=timeout,
            env=env,
            cwd=cwd,
            stdin=stdin,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    duration_ms = int((_time.perf_counter() - started) * 1000)

    marker = "\x00KADERMGR_RESULT\x00"
    payload = None
    user_stdout = stdout
    if marker in stdout:
        before, _, after = stdout.rpartition(marker)
        user_stdout = before
        try:
            payload = json.loads(after)
        except json.JSONDecodeError:
            payload = None

    if payload is None:
        return {
            "success": rc == 0,
            "stdout": user_stdout,
            "stderr": stderr,
            "returncode": rc,
            "duration_ms": duration_ms,
            "error": stderr.strip().splitlines()[-1] if rc != 0 and stderr.strip() else None,
        }

    if payload.get("ok"):
        result = payload.get("result")
        return {
            "success": True,
            "result": result,
            "stdout": user_stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
        }
    return {
        "success": False,
        "error": payload.get("error"),
        "traceback": payload.get("traceback"),
        "stdout": user_stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
    }


def sandbox_info(hass: HomeAssistant) -> dict[str, Any]:
    return {
        "venv_path": str(_venv_path(hass)),
        "python_bin": str(_venv_python(hass)),
        "exists": _venv_python(hass).exists(),
        "installed": sorted(_load_installed(hass)),
    }
