"""Bridge: push claw tool events to Blueprint Studio frontend."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _is_blueprint_studio_loaded(hass: HomeAssistant) -> bool:
    return "blueprint_studio" in hass.config.components


def notify_blueprint_studio(
    hass: HomeAssistant,
    *,
    action: str,
    path: str,
    old_content: str = "",
    new_content: str = "",
) -> None:
    if not _is_blueprint_studio_loaded(hass):
        return

    payload: dict[str, Any] = {
        "action": action,
        "path": path,
        "timestamp": time.time(),
    }
    if action == "ai_edit":
        payload["old_content"] = old_content
        payload["new_content"] = new_content

    hass.bus.async_fire("blueprint_studio_update", payload)
    _LOGGER.debug("blueprint_bridge: fired event action=%s path=%s", action, path)

