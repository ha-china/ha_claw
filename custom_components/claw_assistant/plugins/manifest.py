from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginManifest:
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    kind: str = "standalone"
    pip_dependencies: list[str] = field(default_factory=list)
    requires_env: list[str] = field(default_factory=list)
    provides_tools: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    path: str | None = None
    key: str = ""
    is_valid: bool = True
    validation_errors: list[str] = field(default_factory=list)
    validation_hints: list[str] = field(default_factory=list)


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    module: Any = None
    tools_registered: list[str] = field(default_factory=list)
    enabled: bool = False
    load_error: str | None = None


@dataclass
class PluginToolInfo:
    name: str
    handler_name: str
    schema: dict
    description: str = ""
    toolset: str = "default"
