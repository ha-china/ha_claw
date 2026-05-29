from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_REGISTERED = False


def register_delegation_system(hass: HomeAssistant) -> None:

    global _REGISTERED
    if _REGISTERED:
        return
    
    _register_tool(hass)
    _register_events(hass)
    
    _REGISTERED = True
    _LOGGER.info("Delegation system registered (tool: DelegateTask, DelegateBatch)")


def _register_command(hass: HomeAssistant) -> None:
    """Register /ooo command into the plugin command registry."""
    from ..plugins.context import _REGISTERED_COMMANDS
    from .command import handle_delegate_command
    
    async def ooo_handler(args: str, **kwargs) -> dict[str, Any]:
        conversation_id = kwargs.get("conversation_id")
        agent_id = kwargs.get("agent_id")
        return await handle_delegate_command(
            hass,
            args=args,
            conversation_id=conversation_id,
            agent_id=agent_id,
        )
    
    _REGISTERED_COMMANDS["ooo"] = (
        ooo_handler,
        "子代理任务 - 启动/查看/停止子代理",
    )
    _LOGGER.debug("Registered /ooo command")


def _register_tool(hass: HomeAssistant) -> None:
    from ..runtime.storage.plugin_store import _PLUGIN_TOOLS
    from ..runtime.llm.internal_llm import invalidate_runtime_tool_cache
    from .tool import DelegateTaskTool, DelegateBatchTool
    
    delegate_tool = DelegateTaskTool()
    batch_tool = DelegateBatchTool()
    
    _PLUGIN_TOOLS["_delegation"] = [delegate_tool, batch_tool]
    
    invalidate_runtime_tool_cache()
    
    _LOGGER.debug("Registered DelegateTask and DelegateBatch tools")


def _register_events(hass: HomeAssistant) -> None:
    pass


def unregister_delegation_system() -> None:
    global _REGISTERED
    
    from ..runtime.storage.plugin_store import _PLUGIN_TOOLS
    from ..runtime.llm.internal_llm import invalidate_runtime_tool_cache
    
    _PLUGIN_TOOLS.pop("_delegation", None)
    invalidate_runtime_tool_cache()
    
    _REGISTERED = False
    _LOGGER.info("Delegation system unregistered")


def is_delegation_registered() -> bool:
    return _REGISTERED
