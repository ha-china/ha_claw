from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .manifest import PluginManifest

LOGGER = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None

_data_dir_fn: Callable[[], Path] | None = None


def set_data_dir_fn(fn: Callable[[], Path]) -> None:
    global _data_dir_fn
    _data_dir_fn = fn


def plugins_dir() -> Path:
    if _data_dir_fn:
        return _data_dir_fn() / "plugins"
    return Path.home() / ".claw_plugins"


def ensure_plugin_store() -> None:
    plugins_dir().mkdir(parents=True, exist_ok=True)


def parse_plugin_manifest(plugin_path: Path) -> PluginManifest | None:
    manifest_path = plugin_path / "plugin.yaml"
    init_path = plugin_path / "__init__.py"
    if not manifest_path.exists():
        return None
    if yaml is None:
        LOGGER.warning("yaml module not available")
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        LOGGER.error("Failed to parse %s: %s", manifest_path, e)
        return None
    if not isinstance(data, dict):
        return None
    manifest = PluginManifest(
        name=data.get("name", plugin_path.name),
        version=data.get("version", ""),
        description=data.get("description", ""),
        author=data.get("author", ""),
        kind=data.get("kind", "standalone"),
        pip_dependencies=data.get("pip_dependencies", []),
        requires_env=data.get("requires_env", []),
        provides_tools=data.get("provides_tools", []),
        provides_hooks=data.get("provides_hooks", []),
        path=str(plugin_path),
        key=plugin_path.name,
    )
    errors = []
    hints = []
    if not init_path.exists():
        errors.append(f"Missing __init__.py in {plugin_path.name}")
        hints.append("Plugin must have __init__.py with register(ctx) function")
    if manifest.pip_dependencies:
        hints.append(f"Plugin requires pip packages: {', '.join(manifest.pip_dependencies)}")
    if manifest.requires_env:
        hints.append(f"Plugin requires env vars: {', '.join(manifest.requires_env)}")
    manifest.is_valid = len(errors) == 0
    manifest.validation_errors = errors
    manifest.validation_hints = hints
    return manifest


def discover_plugins() -> list[PluginManifest]:
    ensure_plugin_store()
    pdir = plugins_dir()
    manifests = []
    for plugin_path in pdir.iterdir():
        if not plugin_path.is_dir():
            continue
        if plugin_path.name.startswith(".") or plugin_path.name.startswith("_"):
            continue
        manifest = parse_plugin_manifest(plugin_path)
        if manifest:
            manifests.append(manifest)
    return manifests
