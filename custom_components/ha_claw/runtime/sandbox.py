
from __future__ import annotations

import asyncio
import json
import logging
import os
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
    LOGGER.info("Creating sandbox venv at %s", venv_dir)
    builder = venv.EnvBuilder(
        system_site_packages=False,
        with_pip=True,
        clear=False,
        upgrade=False,
        symlinks=(os.name != "nt"),
    )
    builder.create(str(venv_dir))
    return python_bin


async def _run_subprocess(args: list[str], timeout: int) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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
    hass: HomeAssistant, requirements: list[str] | None = None, pip_timeout: int = 120
) -> Path:

    python_bin = await hass.async_add_executor_job(_ensure_venv_sync, hass)

    requirements = [req.strip() for req in (requirements or []) if req.strip()]
    if not requirements:
        return python_bin

    installed = await hass.async_add_executor_job(_load_installed, hass)
    todo = [req for req in requirements if req not in installed]
    if not todo:
        return python_bin

    LOGGER.info("Sandbox pip install: %s", todo)
    rc, stdout, stderr = await _run_subprocess(
        [str(python_bin), "-m", "pip", "install", "--disable-pip-version-check", *todo],
        timeout=pip_timeout,
    )
    if rc != 0:
        raise RuntimeError(
            f"pip install failed (rc={rc}): {stderr.strip() or stdout.strip()}"
        )

    installed.update(todo)
    await hass.async_add_executor_job(_save_installed, hass, installed)
    return python_bin


_RUNNER_PROLOGUE = """\
import json, sys, traceback
def __emit(payload):
    sys.stdout.write('\\x00KADERMGR_RESULT\\x00' + json.dumps(payload, default=str))
try:
    __local = {}
    __code = __USER_CODE__
    exec(__code, __local, __local)
    __result = __local.get('result')
    __emit({'ok': True, 'result': __result})
except Exception as __err:
    __emit({'ok': False, 'error': repr(__err), 'traceback': traceback.format_exc()})
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
) -> dict[str, Any]:

    python_bin = await ensure_sandbox_ready(hass, requirements, pip_timeout)
    script = _build_runner_script(code)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(script)
        tmp_path = fh.name

    try:
        rc, stdout, stderr = await _run_subprocess(
            [str(python_bin), tmp_path], timeout=timeout
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

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
            "error": stderr.strip().splitlines()[-1] if rc != 0 and stderr.strip() else None,
        }

    if payload.get("ok"):
        result = payload.get("result")
        return {
            "success": True,
            "result": result,
            "stdout": user_stdout,
            "stderr": stderr,
        }
    return {
        "success": False,
        "error": payload.get("error"),
        "traceback": payload.get("traceback"),
        "stdout": user_stdout,
        "stderr": stderr,
    }


def sandbox_info(hass: HomeAssistant) -> dict[str, Any]:
    return {
        "venv_path": str(_venv_path(hass)),
        "python_bin": str(_venv_python(hass)),
        "exists": _venv_python(hass).exists(),
        "installed": sorted(_load_installed(hass)),
    }
