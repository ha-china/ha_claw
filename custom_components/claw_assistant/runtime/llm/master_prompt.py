

from __future__ import annotations

from ..storage.skill_store import (
    load_homeassistant_priority_skill_block,
    load_master_prompt,
    load_runtime_prompt_doc,
    load_skill_catalog_prompt,
)


_CONCEPT_TAXONOMY = (
    "## Tool Routing (read FIRST, apply on EVERY request)\n"
    "1. **Service** — execute any HA domain.action on entities. "
    "Tool: `ServiceCall`. Discovery: `ListServices`. "
    "DEFAULT path when user wants to control, query, or change anything in HA.\n"
    "2. **Intent** — native HA voice commands only (HassTurnOn, HassGetState…). "
    "Tool: `IntentCall`. NEVER for plugins, skills, or custom tools.\n"
    "3. **Integration** — HA config entries (add/remove/configure components). "
    "Tool: `ConfigEntries`.\n"
    "4. **Skill** — read-only markdown docs in skills/ dir. Not code, not callable. "
    "Tool: `GetInstalledSkill` / `ListInstalledSkills`.\n"
    "5. **Plugin** — code extensions in plugins/ dir with their own registered tools. "
    "Tool: `PluginManager` (action=loaded to list, action=call_tool to execute).\n"
    "6. **Delegation** — spawn subagents for background/parallel tasks. "
    "Tool: `DelegateTask` (single task) / `DelegateBatch` (multiple parallel). "
    "When user sends `/ooo <task>`, you MUST call `DelegateTask` with that task as goal. "
    "Subagent runs in isolated context, returns summary only. "
    "Use for: reasoning-heavy work, research, tasks that flood context. "
    "Do NOT use for: simple queries, single tool calls, tasks needing user interaction.\n"
    "ONE path per concept. Never cross-route "
    "(e.g. ListServices cannot find plugins; IntentCall cannot run skills).\n"
    "BEFORE calling any tool, verify: "
    "What concept does this request belong to? "
    "Am I using the tool assigned to THAT concept? "
    "If the answer is no, stop and re-route."
)

_SKILL_INDEX_GUIDANCE = (
    "Skill bodies are not in prompt. Fetch relevant ones with "
    "`GetInstalledSkill(name=\"<slug>\")`; do not assume contents."
)

_PLUGIN_INDEX_GUIDANCE = (
    "Use PluginManager action=loaded to see available plugin tools, "
    "then action=call_tool to execute them."
)

_CACHED_MASTER_SECTIONS: tuple[str, ...] | None = None
_CACHED_MASTER_SIGNATURE: tuple[str, ...] | None = None


def _build_capability_overview() -> str:
    try:
        from ...tools.registry import get_full_tool_registry
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
        from ..storage.plugin_store import get_loaded_plugins, list_installed_plugins
    except Exception:
        return ""
    loaded = get_loaded_plugins()
    items = []
    for p in loaded:
        if not p.get("enabled"):
            continue
        name = p.get("name", "")
        tool_count = len(p.get("tools", []))
        items.append(f"- {name} [loaded, {tool_count} tools]")
    if not items:
        try:
            installed = list_installed_plugins()
        except Exception:
            installed = []
        for p in installed:
            name = p.get("name", "")
            error = p.get("load_error") or ""
            status = f"NOT loaded: {error}" if error else "NOT loaded"
            items.append(f"- {name} [{status}]")
    if not items:
        return ""
    return "\n".join(items)


def invalidate_master_prompt_cache() -> None:
    global _CACHED_MASTER_SECTIONS, _CACHED_MASTER_SIGNATURE
    _CACHED_MASTER_SECTIONS = None
    _CACHED_MASTER_SIGNATURE = None


def build_master_prompt_sections(*, user_text: str = "") -> tuple[str, ...]:
    global _CACHED_MASTER_SECTIONS, _CACHED_MASTER_SIGNATURE
    from ..storage.skill_store import _ensure_prompt_store_fresh
    try:
        from ..storage.plugin_store import get_plugin_tool_registry
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

    sections.insert(0, _CONCEPT_TAXONOMY)

    result = tuple(section for section in sections if section.strip())
    _CACHED_MASTER_SECTIONS = result
    _CACHED_MASTER_SIGNATURE = current_sig
    return result


def apply_master_prompt_layers(base_prompt: str, *, user_text: str = "") -> str:

    sections = [base_prompt, *build_master_prompt_sections(user_text=user_text)]
    return "\n\n".join(section for section in sections if section.strip())
