from __future__ import annotations

from dataclasses import dataclass
import asyncio
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import intent

from .command_registry import (
    CommandSpec,
    core_command_specs,
    reserved_command_names,
    resolve_core_command,
)
from .conversation_utils import get_conversation_history
from .runtime.state import get_channel_type
from .runtime.skill_store import (
    filter_visible_installed_skills,
    get_installed_skill,
    get_missing_required_environment_variables,
    list_installed_skills,
    match_installed_skills,
    skill_matches_visibility,
)
from .runtime.loop_controller import reset_loop_for_conversation
from .runtime.state import (
    get_active_conversation_state,
    get_conversation_status,
    get_should_end_flag,
    get_task_loop_state,
    get_tool_calls_state,
    get_tool_results_state,
    reset_active_conversation,
    set_active_conversation,
)
from .runtime import get_runtime_store
from .tools.registry import TOOL_REGISTRY


@dataclass(slots=True, frozen=True)
class ChatCommand:
    name: str
    args: str = ""
    raw_name: str = ""


@dataclass(slots=True, frozen=True)
class ChatCommandOutcome:
    result: conversation.ConversationResult | None = None
    rewritten_text: str | None = None


_TASKS_KEY = "chat_command_tasks"
_STOP_REQUESTS_KEY = "chat_command_stop_requests"
_RESERVED_COMMANDS = reserved_command_names()


def parse_chat_command(text: str) -> ChatCommand | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    body = stripped[1:].strip()
    if not body:
        return None

    parts = body.split(None, 1)
    name = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    if not name:
        return None
    core_spec = resolve_core_command(name)
    if core_spec is not None:
        return ChatCommand(name=core_spec.name, args=args, raw_name=name)

    skill_entry = _skill_command_registry().get(name)
    if skill_entry is not None:
        return ChatCommand(name="skill_invoke", args=args, raw_name=name)
    return None


def _build_result(
    user_input: conversation.ConversationInput,
    message: str,
) -> conversation.ConversationResult:
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(message)
    return conversation.ConversationResult(
        conversation_id=user_input.conversation_id,
        response=response,
    )


def _build_skill_usage_message() -> str:
    return (
        "Usage:\n"
        "/skill list\n"
        "/skill search <keyword>\n"
        "/skill show <name>\n"
        "/skill <name> [input]"
    )


def _available_skill_tool_names() -> set[str]:
    return set(TOOL_REGISTRY)


def _available_skill_toolsets() -> set[str]:
    toolsets: set[str] = set()
    for meta in TOOL_REGISTRY.values():
        category = str(meta.get("category", "")).strip().lower()
        if category:
            toolsets.add(category)
    return toolsets


def _build_history_usage_message() -> str:
    return (
        "Usage:\n"
        "/history clear\n"
        "/history stats\n"
        "/history recent"
    )


def _core_command_map() -> dict[str, CommandSpec]:
    return {spec.name: spec for spec in core_command_specs()}


def _find_core_command_spec(name: str) -> CommandSpec | None:
    return resolve_core_command(name)


def _slugify_skill_command(value: str) -> str:
    lowered = value.strip().lower().replace(" ", "-").replace("_", "-")
    cleaned = "".join(ch for ch in lowered if ch.isalnum() or ch == "-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def _skill_command_registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for skill in filter_visible_installed_skills(
        channel_type="ha",
        tool_names=_available_skill_tool_names(),
    ):
        slug = _slugify_skill_command(str(skill.get("slug", "") or skill.get("name", "")))
        if not slug or slug in _RESERVED_COMMANDS or slug in registry:
            continue
        registry[slug] = skill
    return registry


def _conflicting_skill_commands() -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    seen_generated: set[str] = set()
    for skill in list_installed_skills():
        slug = _slugify_skill_command(str(skill.get("slug", "") or skill.get("name", "")))
        if not slug:
            conflicts.append({"skill": skill, "reason": "empty_slug"})
            continue
        if slug in _RESERVED_COMMANDS:
            conflicts.append({"skill": skill, "reason": f"reserved:{slug}"})
            continue
        if slug in seen_generated:
            conflicts.append({"skill": skill, "reason": f"duplicate:{slug}"})
            continue
        seen_generated.add(slug)
    return conflicts


def _build_command_catalog_message() -> str:
    grouped: dict[str, list[CommandSpec]] = {}
    for spec in core_command_specs():
        grouped.setdefault(spec.category, []).append(spec)

    lines: list[str] = []
    for category in ("Session", "Skills", "Info"):
        specs = grouped.get(category, [])
        if not specs:
            continue
        if lines:
            lines.append("")
        lines.append(f"{category} commands:")
        for spec in specs:
            lines.append(f"- {spec.usage} - {spec.description}")

    skill_registry = _skill_command_registry()
    if skill_registry:
        lines.extend(["", "Skill commands:"])
        for command_name, skill in sorted(skill_registry.items()):
            description = str(skill.get("description", "") or "").strip()
            suffix = f" - {description}" if description else ""
            lines.append(f"- /{command_name}{suffix}")

    conflicts = _conflicting_skill_commands()
    if conflicts:
        lines.extend(["", "Hidden conflicting skill commands:"])
        for entry in conflicts[:8]:
            skill = entry["skill"]
            skill_name = str(skill.get("name", "") or skill.get("slug", ""))
            lines.append(f"- {skill_name} ({entry['reason']})")
        remaining = len(conflicts) - min(len(conflicts), 8)
        if remaining > 0:
            lines.append(f"... and {remaining} more")

    return "\n".join(lines)


def _build_help_message(command_name: str = "") -> str:
    lookup = command_name.strip()
    if not lookup:
        return (
            "Available commands:\n"
            f"{_build_command_catalog_message()}\n\n"
            "Use /help <command> for details."
        )

    spec = _find_core_command_spec(lookup)
    if spec is not None:
        return (
            f"/{spec.name}\n"
            f"Usage: {spec.usage}\n"
            f"Category: {spec.category}\n"
            f"{spec.description}"
        )

    normalized = lookup.lower().lstrip("/")
    skill_meta = _skill_command_registry().get(normalized)
    if skill_meta is not None:
        description = str(skill_meta.get("description", "") or "").strip()
        skill_name = str(skill_meta.get("name", "") or normalized)
        lines = [
            f"/{normalized}",
            f"Usage: /{normalized} [input]",
            "Category: Skills",
            f"Invoke the installed skill '{skill_name}'.",
        ]
        if description:
            lines.append(description)
        return "\n".join(lines)

    return (
        f"Unknown command: {lookup}\n\n"
        "Use /commands to list all available commands."
    )


def _skill_identifier_variants(skill: dict[str, Any]) -> list[str]:
    variants: list[str] = []
    for key in ("slug", "name", "file"):
        value = str(skill.get(key, "") or "").strip()
        if not value:
            continue
        variants.append(value)
        if key == "file" and value.lower().endswith(".md"):
            variants.append(value[:-3])
    deduped: list[str] = []
    seen: set[str] = set()
    for value in variants:
        normalized = value.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value)
    return deduped


def _format_skill_brief(skill: dict[str, Any]) -> str:
    name = str(skill.get("name", "") or skill.get("slug", ""))
    slug = str(skill.get("slug", "") or "")
    description = str(skill.get("description", "") or "").strip()
    file_name = str(skill.get("file", "") or "").strip()
    parts = [name]
    if slug:
        parts.append(f"slug: {slug}")
    if file_name:
        parts.append(f"file: {file_name}")
    category = str(skill.get("category", "") or "").strip()
    if category:
        parts.append(f"category: {category}")
    header = " | ".join(parts)
    detail_lines: list[str] = []
    if description:
        detail_lines.append(f"  {description}")
    tags = [str(tag).strip() for tag in skill.get("tags", []) if str(tag).strip()]
    if tags:
        detail_lines.append(f"  tags: {', '.join(tags[:6])}")
    platforms = [str(platform).strip() for platform in skill.get("platforms", []) if str(platform).strip()]
    if platforms:
        detail_lines.append(f"  platforms: {', '.join(platforms)}")
    requires_toolsets = [str(name).strip() for name in skill.get("requires_toolsets", []) if str(name).strip()]
    if requires_toolsets:
        detail_lines.append(f"  requires_toolsets: {', '.join(requires_toolsets)}")
    fallback_for_toolsets = [str(name).strip() for name in skill.get("fallback_for_toolsets", []) if str(name).strip()]
    if fallback_for_toolsets:
        detail_lines.append(f"  fallback_for_toolsets: {', '.join(fallback_for_toolsets)}")
    requires_tools = [str(name).strip() for name in skill.get("requires_tools", []) if str(name).strip()]
    if requires_tools:
        detail_lines.append(f"  requires_tools: {', '.join(requires_tools)}")
    fallback_for_tools = [str(name).strip() for name in skill.get("fallback_for_tools", []) if str(name).strip()]
    if fallback_for_tools:
        detail_lines.append(f"  fallback_for_tools: {', '.join(fallback_for_tools)}")
    required_env = [
        str(name).strip()
        for name in skill.get("required_environment_variables", [])
        if str(name).strip()
    ]
    if required_env:
        detail_lines.append(f"  required_env: {', '.join(required_env)}")
    config_keys = [
        str(name).strip()
        for name in skill.get("config_keys", [])
        if str(name).strip()
    ]
    if config_keys:
        detail_lines.append(f"  config_keys: {', '.join(config_keys)}")
    if detail_lines:
        return "- " + header + "\n" + "\n".join(detail_lines)
    return f"- {header}"


def _format_skill_list(skills: list[dict[str, Any]], *, limit: int = 12) -> str:
    if not skills:
        return "No installed skills."
    lines = [_format_skill_brief(skill) for skill in skills[: max(limit, 0)]]
    remaining = len(skills) - len(lines)
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines)


def _resolve_skill_invocation(argument_text: str) -> tuple[dict[str, str], str] | None:
    raw = argument_text.strip()
    if not raw:
        return None

    try:
        exact = get_installed_skill(raw)
    except ValueError:
        exact = None
    if exact is not None:
        return exact, ""

    best_match: tuple[int, dict[str, Any], str] | None = None
    lowered = raw.casefold()
    for skill in list_installed_skills():
        for variant in _skill_identifier_variants(skill):
            variant_lower = variant.casefold()
            if lowered == variant_lower:
                try:
                    return get_installed_skill(variant), ""
                except ValueError:
                    continue
            prefix = variant_lower + " "
            if lowered.startswith(prefix):
                score = len(variant_lower)
                if best_match is None or score > best_match[0]:
                    best_match = (score, skill, raw[len(variant) :].strip())

    if best_match is None:
        return None

    _, skill_meta, remaining = best_match
    identifier = str(skill_meta.get("slug") or skill_meta.get("name") or skill_meta.get("file") or "")
    return get_installed_skill(identifier), remaining


def _build_skill_invocation_message(skill: dict[str, str], user_instruction: str) -> str:
    name = skill["name"]
    slug = skill["slug"]
    description = skill.get("description", "").strip()
    markdown = skill["markdown"].strip()
    lines = [
        f"[Skill activated: {name} ({slug})]",
    ]
    if description:
        lines.extend(["", f"Description: {description}"])
    lines.extend(
        [
            "",
            markdown,
            "",
            "Follow this skill before answering normally.",
        ]
    )
    if user_instruction.strip():
        lines.extend(["", f"User instruction: {user_instruction.strip()}"])
    return "\n".join(lines).strip()


def _handle_skill_query_command(
    user_input: conversation.ConversationInput,
    args: str,
) -> ChatCommandOutcome:
    normalized = args.strip()
    channel_type = get_channel_type(user_input.conversation_id)
    available_tools = _available_skill_tool_names()
    available_toolsets = _available_skill_toolsets()
    skills = filter_visible_installed_skills(
        channel_type=channel_type,
        tool_names=available_tools,
        toolsets=available_toolsets,
    )

    if not normalized:
        message = _build_skill_usage_message()
        if skills:
            message += "\n\nInstalled skills:\n" + _format_skill_list(skills)
        return ChatCommandOutcome(result=_build_result(user_input, message))

    parts = normalized.split(None, 1)
    subcommand = parts[0].casefold()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "list":
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                "Installed skills:\n" + _format_skill_list(skills),
            )
        )

    if subcommand == "search":
        if not remainder:
            return ChatCommandOutcome(
                result=_build_result(
                    user_input,
                    "Missing required parameter: keyword\n\n" + _build_skill_usage_message(),
                )
            )
        matches = [
            match
            for match in match_installed_skills(remainder, limit=8)
            if skill_matches_visibility(
                match,
                channel_type=channel_type,
                tool_names=available_tools,
                toolsets=available_toolsets,
            )
        ]
        if not matches:
            return ChatCommandOutcome(
                result=_build_result(user_input, f"No skills matched: {remainder}")
            )
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                f"Skill search results for '{remainder}':\n" + _format_skill_list(matches, limit=8),
            )
        )

    if subcommand in {"show", "get", "view"}:
        if not remainder:
            return ChatCommandOutcome(
                result=_build_result(
                    user_input,
                    "Missing required parameter: name\n\n" + _build_skill_usage_message(),
                )
            )
        try:
            skill = get_installed_skill(remainder)
        except ValueError:
            suggestions = [
                match
                for match in match_installed_skills(remainder, limit=5)
                if skill_matches_visibility(
                    match,
                    channel_type=channel_type,
                    tool_names=available_tools,
                    toolsets=available_toolsets,
                )
            ]
            message = f"Skill not found: {remainder}"
            if suggestions:
                message += "\n\nClosest matches:\n" + _format_skill_list(suggestions, limit=5)
            return ChatCommandOutcome(result=_build_result(user_input, message))
        if not skill_matches_visibility(
            skill,
            channel_type=channel_type,
            tool_names=available_tools,
            toolsets=available_toolsets,
        ):
            return ChatCommandOutcome(
                result=_build_result(
                    user_input,
                    f"Skill is installed but not available on this surface right now: {skill['name']}",
                )
            )
        details = [
            f"Name: {skill['name']}",
            f"Slug: {skill['slug']}",
            f"File: {skill['file']}",
        ]
        description = skill.get("description", "").strip()
        if description:
            details.append(f"Description: {description}")
        category = str(skill.get("category", "") or "").strip()
        if category:
            details.append(f"Category: {category}")
        tags = [str(tag).strip() for tag in skill.get("tags", []) if str(tag).strip()]
        if tags:
            details.append(f"Tags: {', '.join(tags)}")
        platforms = [str(platform).strip() for platform in skill.get("platforms", []) if str(platform).strip()]
        if platforms:
            details.append(f"Platforms: {', '.join(platforms)}")
        requires_toolsets = [str(name).strip() for name in skill.get("requires_toolsets", []) if str(name).strip()]
        if requires_toolsets:
            details.append(f"Requires toolsets: {', '.join(requires_toolsets)}")
        fallback_for_toolsets = [str(name).strip() for name in skill.get("fallback_for_toolsets", []) if str(name).strip()]
        if fallback_for_toolsets:
            details.append(f"Fallback for toolsets: {', '.join(fallback_for_toolsets)}")
        requires_tools = [str(name).strip() for name in skill.get("requires_tools", []) if str(name).strip()]
        if requires_tools:
            details.append(f"Requires tools: {', '.join(requires_tools)}")
        fallback_for_tools = [str(name).strip() for name in skill.get("fallback_for_tools", []) if str(name).strip()]
        if fallback_for_tools:
            details.append(f"Fallback for tools: {', '.join(fallback_for_tools)}")
        required_env = [
            str(name).strip()
            for name in skill.get("required_environment_variables", [])
            if str(name).strip()
        ]
        if required_env:
            details.append(
                f"Required environment variables: {', '.join(required_env)}"
            )
        config_keys = [
            str(name).strip()
            for name in skill.get("config_keys", [])
            if str(name).strip()
        ]
        if config_keys:
            details.append(f"Config keys: {', '.join(config_keys)}")
        details.extend(["", skill["markdown"]])
        return ChatCommandOutcome(
            result=_build_result(user_input, "\n".join(details).strip())
        )

    if subcommand in {"help", "commands"}:
        return ChatCommandOutcome(result=_build_result(user_input, _build_command_catalog_message()))

    resolved = _resolve_skill_invocation(normalized)
    if resolved is None:
        suggestions = [
            match
            for match in match_installed_skills(normalized, limit=5)
            if skill_matches_visibility(
                match,
                channel_type=channel_type,
                tool_names=available_tools,
                toolsets=available_toolsets,
            )
        ]
        message = f"Skill not found: {normalized}"
        if suggestions:
            message += "\n\nClosest matches:\n" + _format_skill_list(suggestions, limit=5)
        else:
            message += "\n\n" + _build_skill_usage_message()
        return ChatCommandOutcome(result=_build_result(user_input, message))

    skill, user_instruction = resolved
    if not skill_matches_visibility(
        skill,
        channel_type=channel_type,
        tool_names=available_tools,
        toolsets=available_toolsets,
    ):
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                f"Skill is installed but not available on this surface right now: {skill['name']}",
            )
        )
    missing_env = get_missing_required_environment_variables(skill)
    if missing_env:
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                "Skill cannot run yet because required environment variables are missing: "
                + ", ".join(missing_env),
            )
        )
    return ChatCommandOutcome(
        rewritten_text=_build_skill_invocation_message(skill, user_instruction)
    )


def _handle_history_command(
    hass,
    user_input: conversation.ConversationInput,
    args: str,
) -> ChatCommandOutcome:
    normalized = args.strip()
    if not normalized:
        return ChatCommandOutcome(
            result=_build_result(user_input, _build_history_usage_message())
        )

    parts = normalized.split(None, 1)
    subcommand = parts[0].strip().lower()
    history = get_conversation_history()

    if subcommand == "clear":
        removed = history.clear(user_input.conversation_id)
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                f"Cleared history for current conversation. Removed turns: {removed}",
            )
        )

    if subcommand == "stats":
        stats = history.get_stats()
        lines = [
            "History stats:",
            f"- total_conversations: {stats.get('total_conversations', 0)}",
            f"- total_turns: {stats.get('total_turns', 0)}",
            f"- average_turns: {stats.get('average_turns', 0)}",
            f"- oldest_turn: {stats.get('oldest_turn') or 'n/a'}",
            f"- newest_turn: {stats.get('newest_turn') or 'n/a'}",
        ]
        return ChatCommandOutcome(result=_build_result(user_input, "\n".join(lines)))

    if subcommand == "recent":
        entries = history.get_recent_across_conversations(minutes=30, max_turns_per_conv=3)
        if not entries:
            return ChatCommandOutcome(
                result=_build_result(user_input, "No recent conversations found.")
            )
        lines = ["Recent conversations:"]
        for entry in entries[:8]:
            lines.append(
                f"- {entry['conversation_id']} | {entry['last_touched']} | turns: {entry['turn_count']}"
            )
        remaining = len(entries) - min(len(entries), 8)
        if remaining > 0:
            lines.append(f"... and {remaining} more")
        return ChatCommandOutcome(result=_build_result(user_input, "\n".join(lines)))

    return ChatCommandOutcome(
        result=_build_result(user_input, _build_history_usage_message())
    )


def _task_registry(hass) -> dict[str, asyncio.Task]:
    runtime_store = get_runtime_store(hass)
    registry = runtime_store.get(_TASKS_KEY)
    if not isinstance(registry, dict):
        registry = {}
        runtime_store[_TASKS_KEY] = registry
    return registry


def _stop_requests(hass) -> set[str]:
    runtime_store = get_runtime_store(hass)
    requests = runtime_store.get(_STOP_REQUESTS_KEY)
    if not isinstance(requests, set):
        requests = set()
        runtime_store[_STOP_REQUESTS_KEY] = requests
    return requests


def register_running_task(hass, conversation_id: str | None, task: asyncio.Task) -> None:
    if not conversation_id:
        return
    _task_registry(hass)[conversation_id] = task


def unregister_running_task(hass, conversation_id: str | None, task: asyncio.Task | None) -> None:
    if not conversation_id:
        return
    registry = _task_registry(hass)
    current = registry.get(conversation_id)
    if current is task:
        registry.pop(conversation_id, None)


def consume_stop_request(hass, conversation_id: str | None) -> bool:
    if not conversation_id:
        return False
    requests = _stop_requests(hass)
    if conversation_id in requests:
        requests.remove(conversation_id)
        return True
    return False


def _clear_conversation_runtime(hass, conversation_id: str | None) -> None:
    token = set_active_conversation(conversation_id)
    try:
        get_conversation_history().clear(conversation_id)
        task_loop = get_task_loop_state(hass)
        max_iterations = int(task_loop.get("max_iterations", 50) or 50)
        reset_loop_for_conversation(
            hass,
            conversation_id=conversation_id,
            max_iterations=max_iterations,
        )
        get_tool_calls_state(hass).clear()
        get_tool_results_state(hass).clear()
    finally:
        reset_active_conversation(token)

    active_conv = get_active_conversation_state(hass)
    active_conv["id"] = conversation_id

    status = get_conversation_status(hass)
    status["last_conversation_id"] = conversation_id
    status.pop("last_tool", None)
    status.pop("tool_called", None)

    get_should_end_flag(hass)["value"] = False


def _stop_conversation_runtime(hass, conversation_id: str | None) -> bool:
    registry = _task_registry(hass)
    task = registry.get(conversation_id or "")
    if task is not None and not task.done():
        _stop_requests(hass).add(conversation_id or "")
        task.cancel("Stopped by /stop command")

    token = set_active_conversation(conversation_id)
    try:
        task_loop = get_task_loop_state(hass)
        was_active = bool(task_loop.get("active", False))
        task_loop["active"] = False
        task_loop["phase"] = "stopped"
        task_loop["stop_reason"] = "Stopped by /stop command"
        task_loop["waiting_choice"] = False
        task_loop["pending_feedback"] = None
        task_loop["last_choice"] = None
    finally:
        reset_active_conversation(token)

    get_should_end_flag(hass)["value"] = True
    return was_active or (task is not None and not task.done())


async def async_handle_chat_command(
    hass,
    user_input: conversation.ConversationInput,
) -> ChatCommandOutcome | None:
    command = parse_chat_command(user_input.text)
    if command is None:
        return None

    conversation_id = user_input.conversation_id

    if command.name == "help":
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                _build_help_message(command.args),
            )
        )

    if command.name == "commands":
        return ChatCommandOutcome(result=_build_result(user_input, _build_command_catalog_message()))

    if command.name == "new":
        _clear_conversation_runtime(hass, conversation_id)
        return ChatCommandOutcome(result=_build_result(user_input, "Started a new conversation."))

    if command.name == "reset":
        _clear_conversation_runtime(hass, conversation_id)
        return ChatCommandOutcome(result=_build_result(user_input, "Reset the current conversation."))

    if command.name == "stop":
        stopped = _stop_conversation_runtime(hass, conversation_id)
        if stopped:
            return ChatCommandOutcome(result=_build_result(user_input, "Stopped the current run."))
        return ChatCommandOutcome(result=_build_result(user_input, "No active run to stop."))

    if command.name == "history":
        return _handle_history_command(hass, user_input, command.args)

    if command.name == "skill":
        return _handle_skill_query_command(user_input, command.args)

    if command.name == "skill_invoke":
        skill_meta = _skill_command_registry().get(command.raw_name)
        if skill_meta is None:
            return ChatCommandOutcome(
                result=_build_result(user_input, f"Skill command not found: /{command.raw_name}")
            )
        identifier = str(
            skill_meta.get("slug")
            or skill_meta.get("name")
            or skill_meta.get("file")
            or command.raw_name
        )
        try:
            skill = get_installed_skill(identifier)
        except ValueError:
            return ChatCommandOutcome(
                result=_build_result(user_input, f"Skill command not found: /{command.raw_name}")
            )
        return ChatCommandOutcome(
            rewritten_text=_build_skill_invocation_message(skill, command.args)
        )

    return None
