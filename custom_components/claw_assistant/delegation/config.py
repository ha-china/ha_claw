from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant


@dataclass
class DelegationConfig:
    
    max_concurrent_children: int = 3
    child_timeout_seconds: float = 1200.0
    max_spawn_depth: int = 2
    orchestrator_enabled: bool = True
    default_toolsets: list[str] = field(default_factory=lambda: ["terminal", "file", "web"])
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DelegationConfig":
        return cls(
            max_concurrent_children=int(data.get("max_concurrent_children", 3)),
            child_timeout_seconds=float(data.get("child_timeout_seconds", 1200.0)),
            max_spawn_depth=int(data.get("max_spawn_depth", 2)),
            orchestrator_enabled=bool(data.get("orchestrator_enabled", True)),
            default_toolsets=list(data.get("default_toolsets", ["terminal", "file", "web"])),
        )


def load_delegation_config(hass: HomeAssistant) -> DelegationConfig:
    from ..const import DOMAIN
    
    data = hass.data.get(DOMAIN, {})
    delegation_data = data.get("delegation_config", {})
    
    if isinstance(delegation_data, DelegationConfig):
        return delegation_data
    
    if isinstance(delegation_data, dict):
        return DelegationConfig.from_dict(delegation_data)
    
    return DelegationConfig()
