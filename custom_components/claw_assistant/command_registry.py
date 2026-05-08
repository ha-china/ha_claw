from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    category: str
    description_zh: str = ""
    aliases: tuple[str, ...] = ()


CORE_COMMAND_REGISTRY: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="new",
        usage="/new",
        description="Start a fresh conversation in the current chat surface.",
        description_zh="在当前聊天窗口开始新对话。",
        category="Session",
    ),
    CommandSpec(
        name="reset",
        usage="/reset",
        description="Clear history and runtime state for the current conversation.",
        description_zh="清除当前对话的历史和运行时状态。",
        category="Session",
    ),
    CommandSpec(
        name="stop",
        usage="/stop",
        description="Cancel the currently running turn for this conversation.",
        description_zh="取消当前正在运行的对话轮次。",
        category="Session",
    ),
    CommandSpec(
        name="history",
        usage="/history <clear|stats|recent>",
        description="Manage or inspect stored conversation history for this assistant.",
        description_zh="管理或查看助手的对话历史记录。",
        category="Session",
    ),
    CommandSpec(
        name="skill",
        usage="/skill <name> [input]",
        description="Inspect installed skills or invoke one by name.",
        description_zh="查看已安装技能或按名称调用。",
        category="Skills",
        aliases=("skills",),
    ),
    CommandSpec(
        name="model",
        usage="/model [number]",
        description="List available AI agents or switch primary/fallback by number.",
        description_zh="列出可用模型或按序号切换主力/备用。",
        category="Config",
        aliases=("models",),
    ),
    CommandSpec(
        name="help",
        usage="/help [command]",
        description="Show command help or inspect a single command.",
        description_zh="显示命令帮助或查看单个命令详情。",
        category="Info",
        aliases=("h",),
    ),
    CommandSpec(
        name="commands",
        usage="/commands",
        description="List all core commands and generated skill commands.",
        description_zh="列出所有核心命令和技能命令。",
        category="Info",
        aliases=("cmds",),
    ),
    CommandSpec(
        name="goal",
        usage="/goal [<text>|status|pause|resume|clear]",
        description=(
            "Set a standing goal. The fallback (备用) agent judges each turn; "
            "claw_assistant keeps working until the goal is done or you "
            "/goal pause | /goal clear."
        ),
        description_zh=(
            "设置常驻目标。每轮由\u201c后备 AI\u201d判定是否完成；未完成则 "
            "自动续跑，直到 /goal pause 或 /goal clear 才停。"
        ),
        category="Session",
        aliases=("goals",),
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
