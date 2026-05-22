from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

_REGISTERED_TOOLS: dict[str, dict] = {}
_REGISTERED_CONTEXT_ENGINES: dict[str, Any] = {}
_REGISTERED_COMMANDS: dict[str, tuple[Callable, str]] = {}
_REGISTERED_HOOKS: dict[str, list[tuple[str, Callable]]] = {}
_REGISTERED_SKILLS: dict[str, str] = {}


def get_all_plugin_tools() -> dict[str, dict]:
    return _REGISTERED_TOOLS.copy()


def get_context_engine(name: str) -> Any:
    return _REGISTERED_CONTEXT_ENGINES.get(name)


def get_all_context_engines() -> dict[str, Any]:
    return _REGISTERED_CONTEXT_ENGINES.copy()


def get_hooks(event: str) -> list[tuple[str, Callable]]:
    return _REGISTERED_HOOKS.get(event, [])


def fire_hook(event: str, *args, **kwargs) -> list[Any]:
    results = []
    for plugin_name, callback in get_hooks(event):
        try:
            result = callback(*args, **kwargs)
            if result is not None:
                results.append(result)
        except Exception as e:
            LOGGER.warning("Hook %s from %s failed: %s", event, plugin_name, e)
    return results


def clear_plugin_registrations(plugin_name: str) -> None:
    keys_to_remove = [k for k in _REGISTERED_TOOLS if _REGISTERED_TOOLS[k].get("_plugin") == plugin_name]
    for k in keys_to_remove:
        del _REGISTERED_TOOLS[k]
    if plugin_name in _REGISTERED_CONTEXT_ENGINES:
        del _REGISTERED_CONTEXT_ENGINES[plugin_name]
    for event in _REGISTERED_HOOKS:
        _REGISTERED_HOOKS[event] = [(p, cb) for p, cb in _REGISTERED_HOOKS[event] if p != plugin_name]
    keys_to_remove = [k for k in _REGISTERED_SKILLS if _REGISTERED_SKILLS[k] == plugin_name]
    for k in keys_to_remove:
        del _REGISTERED_SKILLS[k]


class PluginContext:
    def __init__(self, hass: Any, plugin_name: str):
        self._hass = hass
        self._plugin_name = plugin_name

    def register_tool(
        self,
        name: str,
        toolset: str = "default",
        schema: dict | None = None,
        handler: Callable | None = None,
        description: str = "",
        **kwargs,
    ) -> None:
        _REGISTERED_TOOLS[name] = {
            "name": name,
            "toolset": toolset,
            "schema": schema or {},
            "handler": handler,
            "description": description,
            "_plugin": self._plugin_name,
            **kwargs,
        }
        LOGGER.info("Plugin %s registered tool: %s", self._plugin_name, name)

    def register_hook(self, event: str, callback: Callable) -> None:
        if event not in _REGISTERED_HOOKS:
            _REGISTERED_HOOKS[event] = []
        _REGISTERED_HOOKS[event].append((self._plugin_name, callback))
        LOGGER.info("Plugin %s registered hook: %s", self._plugin_name, event)

    def register_command(self, name: str, handler: Callable, description: str = "") -> None:
        _REGISTERED_COMMANDS[name] = (handler, description)
        LOGGER.info("Plugin %s registered command: /%s", self._plugin_name, name)

    def register_context_engine(self, engine: Any) -> None:
        _REGISTERED_CONTEXT_ENGINES[self._plugin_name] = engine
        LOGGER.info("Plugin %s registered context engine", self._plugin_name)

    def register_skill(self, name: str, path: str) -> None:
        skill_key = f"{self._plugin_name}:{name}"
        _REGISTERED_SKILLS[skill_key] = path
        LOGGER.info("Plugin %s registered skill: %s", self._plugin_name, skill_key)

    def dispatch_tool(self, name: str, args: dict) -> Any:
        tool = _REGISTERED_TOOLS.get(name)
        if not tool:
            return {"success": False, "error": f"Tool {name} not found"}
        handler = tool.get("handler")
        if not handler:
            return {"success": False, "error": f"Tool {name} has no handler"}
        try:
            result = handler(args)
            if isinstance(result, str):
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return {"success": True, "result": result}
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def inject_message(self, content: str, role: str = "user") -> None:
        LOGGER.info("Plugin %s injected message (role=%s): %s...", self._plugin_name, role, content[:50])

    async def call_service(self, domain: str, service: str, data: dict | None = None, **kwargs) -> None:
        await self._hass.services.async_call(domain, service, data or {}, **kwargs)

    async def fire_event(self, event_type: str, event_data: dict | None = None) -> None:
        self._hass.bus.async_fire(event_type, event_data or {})

    async def get_state(self, entity_id: str) -> dict | None:
        state = self._hass.states.get(entity_id)
        if not state:
            return None
        return {
            "entity_id": state.entity_id,
            "state": state.state,
            "attributes": dict(state.attributes),
            "last_changed": state.last_changed.isoformat() if state.last_changed else None,
            "last_updated": state.last_updated.isoformat() if state.last_updated else None,
        }

    async def set_state(self, entity_id: str, new_state: str, attributes: dict | None = None) -> None:
        self._hass.states.async_set(entity_id, new_state, attributes or {})

    async def get_all_states(self, domain: str | None = None) -> list[dict]:
        states = []
        for state in self._hass.states.async_all():
            if domain and state.domain != domain:
                continue
            states.append({
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
            })
        return states

    async def listen_event(self, event_type: str, callback) -> callable:
        return self._hass.bus.async_listen(event_type, callback)

    async def get_areas(self) -> list[dict]:
        from homeassistant.helpers import area_registry as ar
        registry = ar.async_get(self._hass)
        return [{"id": a.id, "name": a.name} for a in registry.async_list_areas()]

    async def get_devices(self, area_id: str | None = None) -> list[dict]:
        from homeassistant.helpers import device_registry as dr
        registry = dr.async_get(self._hass)
        devices = []
        for d in registry.devices.values():
            if area_id and d.area_id != area_id:
                continue
            devices.append({
                "id": d.id,
                "name": d.name,
                "manufacturer": d.manufacturer,
                "model": d.model,
                "area_id": d.area_id,
            })
        return devices

    async def get_entities(self, domain: str | None = None, area_id: str | None = None) -> list[dict]:
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self._hass)
        entities = []
        for e in registry.entities.values():
            if domain and e.domain != domain:
                continue
            if area_id and e.area_id != area_id:
                continue
            entities.append({
                "entity_id": e.entity_id,
                "name": e.name or e.original_name,
                "platform": e.platform,
                "area_id": e.area_id,
                "device_id": e.device_id,
            })
        return entities

    async def get_services(self, domain: str | None = None) -> dict:
        services = self._hass.services.async_services()
        if domain:
            return {domain: services.get(domain, {})}
        return services

    async def schedule_task(self, coro, delay_seconds: float = 0) -> None:
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        self._hass.async_create_task(coro)

    @property
    def config_dir(self) -> str:
        return self._hass.config.config_dir

    @property
    def hass(self) -> Any:
        return self._hass

    @property
    def plugin_name(self) -> str:
        return self._plugin_name
