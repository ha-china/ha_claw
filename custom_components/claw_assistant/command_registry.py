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
    subcommands: tuple[tuple[str, str, str], ...] = ()


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
        usage="/history",
        description="Manage or inspect stored conversation history.",
        description_zh="管理或查看对话历史记录。",
        category="Session",
        subcommands=(
            ("/history clear", "Clear all stored history.", "清除所有对话历史。"),
            ("/history stats", "Show history statistics.", "显示历史统计信息。"),
            ("/history recent", "Show recent entries.", "显示最近的记录。"),
        ),
    ),
    CommandSpec(
        name="skill",
        usage="/skill",
        description="Inspect installed skills or invoke one by name.",
        description_zh="查看已安装技能或按名称调用。",
        category="Skills",
        aliases=("skills",),
        subcommands=(
            ("/skill list", "List all installed skills.", "列出所有已安装技能。"),
            ("/skill <name>", "View skill details.", "查看技能详情。"),
            ("/skill <name> [input]", "Invoke a skill with input.", "用输入调用技能。"),
        ),
    ),
    CommandSpec(
        name="model",
        usage="/model",
        description="List available AI agents or switch primary/fallback/third by number.",
        description_zh="列出可用模型或按序号切换主力/备用/第三。",
        category="Config",
        aliases=("models",),
        subcommands=(
            ("/model", "List all available models.", "列出所有可用模型。"),
            ("/model 序号", "Set as primary.", "设为主力。"),
            ("/model 序号 fallback", "Set as fallback.", "设为备用。"),
            ("/model 序号 third", "Set as third (optional).", "设为第三（可选）。"),
            ("/model third none", "Clear third model.", "清除第三模型。"),
        ),
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
        usage="/goal",
        description="Set a standing goal. The agent keeps working until done.",
        description_zh="设置常驻目标，助手自动续跑直到完成。",
        category="Session",
        aliases=("goals",),
        subcommands=(
            ("/goal <text>", "Set a new goal.", "设置新目标。"),
            ("/goal status", "Check current goal progress.", "查看当前目标进度。"),
            ("/goal pause", "Pause the current goal.", "暂停当前目标。"),
            ("/goal resume", "Resume a paused goal.", "恢复已暂停的目标。"),
            ("/goal clear", "Stop and remove the goal.", "停止并清除目标。"),
        ),
    ),
    CommandSpec(
        name="plugin",
        usage="/plugin",
        description="Manage installed plugins. AI handles plugin tools internally.",
        description_zh="管理已安装插件。插件工具由 AI 内部处理。",
        category="System",
        aliases=("plugins",),
        subcommands=(
            ("/plugin list", "List installed plugins.", "列出已安装插件。"),
            ("/plugin status", "Check plugin status.", "检查插件状态。"),
        ),
    ),
)


def core_command_specs() -> tuple[CommandSpec, ...]:
    return CORE_COMMAND_REGISTRY


def get_plugin_command_specs() -> list[CommandSpec]:
    try:
        from .plugins.context import _REGISTERED_COMMANDS
        specs = []
        for name, (_, description) in _REGISTERED_COMMANDS.items():
            specs.append(CommandSpec(
                name=name,
                usage=f"/{name}",
                description=description or f"Plugin command: {name}",
                description_zh=description or f"插件命令: {name}",
                category="Plugin",
            ))
        return specs
    except Exception:
        return []


def all_command_specs() -> list[CommandSpec]:
    specs = list(CORE_COMMAND_REGISTRY)
    specs.extend(get_plugin_command_specs())
    return specs


def build_core_command_map() -> dict[str, CommandSpec]:
    lookup: dict[str, CommandSpec] = {}
    for spec in CORE_COMMAND_REGISTRY:
        lookup[spec.name] = spec
        for alias in spec.aliases:
            lookup[alias] = spec
    return lookup


def build_full_command_map() -> dict[str, CommandSpec]:
    lookup = build_core_command_map()
    for spec in get_plugin_command_specs():
        lookup[spec.name] = spec
        for alias in spec.aliases:
            lookup[alias] = spec
    return lookup


def resolve_core_command(name: str) -> CommandSpec | None:
    lookup = name.strip().lower().lstrip("/")
    if not lookup:
        return None
    return build_full_command_map().get(lookup)


def reserved_command_names() -> frozenset[str]:
    names: set[str] = set()
    for spec in CORE_COMMAND_REGISTRY:
        names.add(spec.name)
        names.update(spec.aliases)
    return frozenset(names)
