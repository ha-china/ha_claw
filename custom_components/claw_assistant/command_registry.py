from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    category: str
    aliases: tuple[str, ...] = ()


CORE_COMMAND_REGISTRY: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="new",
        usage="/new",
        description="Start a fresh conversation in the current chat surface.",
        category="Session",
    ),
    CommandSpec(
        name="reset",
        usage="/reset",
        description="Clear history and runtime state for the current conversation.",
        category="Session",
    ),
    CommandSpec(
        name="stop",
        usage="/stop",
        description="Cancel the currently running turn for this conversation.",
        category="Session",
    ),
    CommandSpec(
        name="history",
        usage="/history <clear|stats|recent>",
        description="Manage or inspect stored conversation history for this assistant.",
        category="Session",
    ),
    CommandSpec(
        name="skill",
        usage="/skill <name> [input]",
        description="Inspect installed skills or invoke one by name.",
        category="Skills",
        aliases=("skills",),
    ),
    CommandSpec(
        name="help",
        usage="/help [command]",
        description="Show command help or inspect a single command.",
        category="Info",
        aliases=("h",),
    ),
    CommandSpec(
        name="commands",
        usage="/commands",
        description="List all core commands and generated skill commands.",
        category="Info",
        aliases=("cmds",),
    ),
)


def core_command_specs() -> tuple[CommandSpec, ...]:
    return CORE_COMMAND_REGISTRY


def build_core_command_map() -> dict[str, CommandSpec]:
    lookup: dict[str, CommandSpec] = {}
    for spec in CORE_COMMAND_REGISTRY:
        lookup[spec.name] = spec
        for alias in spec.aliases:
            lookup[alias] = spec
    return lookup


def resolve_core_command(name: str) -> CommandSpec | None:
    lookup = name.strip().lower().lstrip("/")
    if not lookup:
        return None
    return build_core_command_map().get(lookup)


def reserved_command_names() -> frozenset[str]:
    names: set[str] = set()
    for spec in CORE_COMMAND_REGISTRY:
        names.add(spec.name)
        names.update(spec.aliases)
    return frozenset(names)
