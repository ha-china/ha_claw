

from __future__ import annotations

from .skill_store import (
    load_homeassistant_priority_skill_block,
    load_master_prompt,
    load_runtime_prompt_doc,
    load_skill_catalog_prompt,
)


_SKILL_INDEX_GUIDANCE = (
    "Skill bodies are not in prompt. Fetch relevant ones with "
    "`GetInstalledSkill(name=\"<slug>\")`; do not assume contents."
)

_PLUGIN_INDEX_GUIDANCE = (
    "For plugin-related requests, use the `PluginManager` bridge first. "
    "Inspect with action=loaded/list, then execute plugin tools with action=call_tool, tool_name, and tool_args. "
    "Do not use `IntentCall` for Claw plugins."
)

_CACHED_MASTER_SECTIONS: tuple[str, ...] | None = None
_CACHED_MASTER_SIGNATURE: tuple[str, ...] | None = None


def _build_capability_overview() -> str:
    try:
        from ..tools.registry import get_full_tool_registry
    except Exception:
        return ""
    registry = get_full_tool_registry()
    if not registry:
        return ""
    categories: dict[str, list[str]] = {}
    for name, info in registry.items():
        cat = info.get("category", "misc")
        categories.setdefault(cat, []).append(name)
    summary_parts = [f"{cat}({len(tools)})" for cat, tools in categories.items()]
    return (
        "## Capabilities\n"
        f"You have {len(registry)} tools across: {', '.join(summary_parts)}.\n"
        "Tool descriptions are in the function schema. Use tools directly; do NOT call tools to discover your own capabilities."
    )


def _build_plugin_catalog() -> str:
    try:
        from .plugin_store import get_loaded_plugins
    except Exception:
        return ""
    plugins = get_loaded_plugins()
    if not plugins:
        return ""
    items = []
    for p in plugins:
        if not p.get("enabled"):
            continue
        name = p.get("name", "")
        tools = p.get("tools", [])
        tools_str = ", ".join(tools) if tools else "no tools"
        items.append(f"- {name}: {tools_str}")
    if not items:
        return ""
    return "\n".join(items)


def invalidate_master_prompt_cache() -> None:
    global _CACHED_MASTER_SECTIONS, _CACHED_MASTER_SIGNATURE
    _CACHED_MASTER_SECTIONS = None
    _CACHED_MASTER_SIGNATURE = None


def build_master_prompt_sections(*, user_text: str = "") -> tuple[str, ...]:
    global _CACHED_MASTER_SECTIONS, _CACHED_MASTER_SIGNATURE
    from .skill_store import _ensure_prompt_store_fresh
    try:
        from .plugin_store import get_plugin_tool_registry
        plugin_sig = tuple(sorted(get_plugin_tool_registry()))
    except Exception:
        plugin_sig = ()
    current_sig = (*_ensure_prompt_store_fresh().signature, *plugin_sig)
    if _CACHED_MASTER_SECTIONS is not None and _CACHED_MASTER_SIGNATURE == current_sig:
        return _CACHED_MASTER_SECTIONS

    sections: list[str] = []

    priority_skill_block = load_homeassistant_priority_skill_block()
    if priority_skill_block:
        sections.append(priority_skill_block)

    master_prompt = load_master_prompt()
    if master_prompt:
        sections.append(master_prompt)

    memory_routing_guidance = load_runtime_prompt_doc("memory_routing")
    if memory_routing_guidance:
        sections.append(memory_routing_guidance)

    skill_catalog = load_skill_catalog_prompt(exclude_homeassistant_priority=True)
    if skill_catalog:
        sections.append(
            f"## Installed Skill Index\n{skill_catalog}\n\n{_SKILL_INDEX_GUIDANCE}"
        )

    plugin_catalog = _build_plugin_catalog()
    if plugin_catalog:
        sections.append(
            f"## Installed Plugin Index\n{plugin_catalog}\n\n{_PLUGIN_INDEX_GUIDANCE}"
        )

    capability_overview = _build_capability_overview()
    if capability_overview:
        sections.insert(0, capability_overview)

    result = tuple(section for section in sections if section.strip())
    _CACHED_MASTER_SECTIONS = result
    _CACHED_MASTER_SIGNATURE = current_sig
    return result


def apply_master_prompt_layers(base_prompt: str, *, user_text: str = "") -> str:

    sections = [base_prompt, *build_master_prompt_sections(user_text=user_text)]
    return "\n\n".join(section for section in sections if section.strip())
