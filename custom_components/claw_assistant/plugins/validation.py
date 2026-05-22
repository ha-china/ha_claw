from __future__ import annotations

from pathlib import Path
from typing import Any

from .discovery import plugins_dir, parse_plugin_manifest

try:
    import yaml
except ImportError:
    yaml = None


def get_plugin_install_guide(plugin_name: str) -> dict[str, Any]:
    pdir = plugins_dir()
    plugin_path = pdir / plugin_name
    if not plugin_path.exists():
        return {
            "success": False,
            "error": f"Plugin '{plugin_name}' not found",
            "hint": f"Install plugin to: {pdir}/{plugin_name}/",
            "required_files": ["plugin.yaml", "__init__.py"],
            "example_manifest": {
                "name": plugin_name,
                "version": "1.0.0",
                "description": "Plugin description",
                "pip_dependencies": [],
                "requires_env": [],
            },
        }
    manifest = parse_plugin_manifest(plugin_path)
    if not manifest:
        return {
            "success": False,
            "error": "Invalid plugin structure",
            "hint": "Plugin must have plugin.yaml manifest",
            "path": str(plugin_path),
        }
    result = {
        "success": manifest.is_valid,
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "kind": manifest.kind,
        "path": manifest.path,
    }
    if manifest.validation_errors:
        result["errors"] = manifest.validation_errors
    if manifest.validation_hints:
        result["configuration_hints"] = manifest.validation_hints
    if manifest.pip_dependencies:
        result["pip_dependencies"] = manifest.pip_dependencies
    if manifest.requires_env:
        result["required_env_vars"] = manifest.requires_env
    if manifest.provides_tools:
        result["provides_tools"] = manifest.provides_tools
    return result


def validate_plugin_installation(source_path: str) -> dict[str, Any]:
    source = Path(source_path)
    if not source.exists():
        return {"valid": False, "error": f"Source path does not exist: {source_path}"}
    if not source.is_dir():
        return {"valid": False, "error": "Source must be a directory"}
    manifest_path = source / "plugin.yaml"
    init_path = source / "__init__.py"
    issues = []
    hints = []
    if not manifest_path.exists():
        issues.append("Missing plugin.yaml")
        hints.append("Create plugin.yaml with name, version, description")
    if not init_path.exists():
        issues.append("Missing __init__.py")
        hints.append("Create __init__.py with: def register(ctx): ...")
    else:
        try:
            content = init_path.read_text(encoding="utf-8")
            if "def register" not in content:
                issues.append("__init__.py missing register(ctx) function")
        except Exception as e:
            issues.append(f"Cannot read __init__.py: {e}")
    manifest = None
    if manifest_path.exists() and yaml:
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f)
        except Exception:
            issues.append("Invalid YAML in plugin.yaml")
    target_dir = plugins_dir() / source.name
    return {
        "valid": len(issues) == 0,
        "source": str(source),
        "target": str(target_dir),
        "issues": issues,
        "hints": hints,
        "manifest": manifest,
        "next_steps": [
            f"Copy plugin to {target_dir}",
            "Install pip dependencies if any",
            "Set required environment variables",
            "Restart Home Assistant to load plugin",
        ] if len(issues) == 0 else ["Fix the issues listed above"],
    }
