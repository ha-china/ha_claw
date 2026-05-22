from __future__ import annotations

from dataclasses import dataclass
import asyncio
import re
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import intent

from .command_registry import (
    CommandSpec,
    core_command_specs,
    all_command_specs,
    reserved_command_names,
    resolve_core_command,
)
from .conversation_utils import get_conversation_history
from .runtime.state import get_channel_type
from .runtime.i18n import t
from .runtime.skill_store import (
    filter_visible_installed_skills,
    get_installed_skill,
    get_missing_required_environment_variables,
    list_installed_skills,
    match_installed_skills,
    skill_matches_visibility,
)
from .runtime.loop_controller import reset_loop_for_conversation
from .runtime.continuous_conversation import (
    continuous_conversation_enabled,
    start_new_conversation,
)
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


_WRAPPER_PREFIX_RE = re.compile(r"^\s*[\[\<【][^\]\>】]*[\]\>】]\s*")
_HAS_IM_TAG_RE = re.compile(r"\[IM:[^\]]*\]")
_COMMAND_TOKEN_RE = re.compile(r"(?<![\w/])/([a-zA-Z][\w\-]*)(?:\s+(.*))?$", re.DOTALL)


def _strip_wrapper_prefix(text: str) -> str:
    cleaned = text
    while True:
        match = _WRAPPER_PREFIX_RE.match(cleaned)
        if not match:
            break
        cleaned = cleaned[match.end():]
    return cleaned


def _resolve_command_name(name: str) -> tuple[str, str] | None:
    from .runtime.plugin_store import get_plugin_tool_registry
    lowered = name.lower()
    core_spec = resolve_core_command(lowered)
    if core_spec is not None:
        if core_spec.category == "Plugin":
            return "plugin_invoke", lowered
        return core_spec.name, lowered
    if _skill_command_registry().get(lowered) is not None:
        return "skill_invoke", lowered
    plugin_registry = get_plugin_tool_registry()
    if lowered in plugin_registry or name in plugin_registry:
        return "plugin_tool_invoke", name if name in plugin_registry else lowered
    return None


def parse_chat_command(text: str) -> ChatCommand | None:
    if not text:
        return None
    candidates: list[str] = []
    stripped = _strip_wrapper_prefix(text).strip()
    if stripped:
        candidates.append(stripped)
    if _HAS_IM_TAG_RE.search(text):
        candidates.append(text)

    for candidate in candidates:
        if candidate.startswith("/"):
            body = candidate[1:].strip()
            if not body:
                continue
            parts = body.split(None, 1)
            resolved = _resolve_command_name(parts[0])
            if resolved is None:
                continue
            args = parts[1].strip() if len(parts) > 1 else ""
            return ChatCommand(name=resolved[0], args=args, raw_name=resolved[1])

        match = _COMMAND_TOKEN_RE.search(candidate)
        if not match:
            continue
        resolved = _resolve_command_name(match.group(1))
        if resolved is None:
            continue
        args = (match.group(2) or "").strip()
        return ChatCommand(name=resolved[0], args=args, raw_name=resolved[1])

    return None


def _build_result(
    user_input: conversation.ConversationInput,
    message: str,
) -> conversation.ConversationResult:
    response = intent.IntentResponse(language=user_input.language)
    formatted = f"\u200b\n\n{message}" if message and not message.startswith("\u200b") else message
    response.async_set_speech(formatted)
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


def _spec_desc(spec: CommandSpec, lang: str | None) -> str:
    from .runtime.reply_formatter import is_chinese
    if is_chinese(lang) and spec.description_zh:
        return spec.description_zh
    return spec.description


_CATEGORY_KEYS = {
    "Session": "cmd_category_session",
    "Skills": "cmd_category_skills",
    "Config": "cmd_category_config",
    "Info": "cmd_category_info",
    "Plugin": "cmd_category_plugin",
}


def _escape_md_angles(s: str) -> str:
    return s


def _build_command_catalog_message(language: str | None = None) -> str:
    grouped: dict[str, list[CommandSpec]] = {}
    for spec in all_command_specs():
        grouped.setdefault(spec.category, []).append(spec)

    lines: list[str] = []
    for category in ("Session", "Skills", "Config", "Info", "Plugin"):
        specs = grouped.get(category, [])
        if not specs:
            continue
        if lines:
            lines.append("")
        cat_label = t(_CATEGORY_KEYS.get(category, category), language)
        lines.append(f"**{cat_label}{t('cmd_commands_suffix', language)}**")
        lines.append("")
        for spec in specs:
            if spec.subcommands:
                from .runtime.reply_formatter import is_chinese
                zh = is_chinese(language)
                for i, (sub_usage, sub_desc_en, sub_desc_zh) in enumerate(spec.subcommands):
                    sub_usage_esc = _escape_md_angles(sub_usage)
                    sub_desc = sub_desc_zh if zh else sub_desc_en
                    if i == 0:
                        lines.append("")
                    lines.append(f"\u200b`{sub_usage_esc}` — {sub_desc}")
                    lines.append("")
            else:
                usage = _escape_md_angles(spec.usage)
                desc = _spec_desc(spec, language)
                lines.append(f"\u200b`{usage}` — {desc}")
                lines.append("")

    lines.append("")
    lines.append(f"\u200b{t('cmd_help_footer', language)}")
    lines.append("")
    return "\n".join(lines)


def _build_help_message(command_name: str = "", language: str | None = None) -> str:
    lookup = command_name.strip()
    if not lookup:
        return f"\u200b\n\n{_build_command_catalog_message(language)}"

    spec = _find_core_command_spec(lookup)
    if spec is not None:
        usage = _escape_md_angles(spec.usage)
        lines = [
            f"**/{spec.name}**",
            "",
            f"{t('cmd_help_usage', language)}: `{usage}`",
            f"{t('cmd_help_category', language)}: {t(_CATEGORY_KEYS.get(spec.category, spec.category), language)}",
            "",
            _spec_desc(spec, language),
        ]
        if spec.subcommands:
            from .runtime.reply_formatter import is_chinese
            zh = is_chinese(language)
            lines.append("")
            for sub_usage, sub_desc_en, sub_desc_zh in spec.subcommands:
                sub_usage_esc = _escape_md_angles(sub_usage)
                sub_desc = sub_desc_zh if zh else sub_desc_en
                lines.append(f"\u200b`{sub_usage_esc}` — {sub_desc}")
                lines.append("")
        return "\n".join(lines)

    normalized = lookup.lower().lstrip("/")
    skill_meta = _skill_command_registry().get(normalized)
    if skill_meta is not None:
        description = str(skill_meta.get("description", "") or "").strip()
        skill_name = str(skill_meta.get("name", "") or normalized)
        lines = [
            f"**/{normalized}**",
            "",
            f"{t('cmd_help_usage', language)}: `/{normalized} [input]`",
            f"{t('cmd_help_category', language)}: {t('cmd_category_skills', language)}",
            "",
            f"Invoke the installed skill '{skill_name}'.",
        ]
        if description:
            lines.append(description)
        return "\n".join(lines)

    return t("cmd_help_not_found", language).replace("{name}", lookup)


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
    from .runtime.skill_store import _resolve_skill_path
    name = skill["name"]
    slug = skill["slug"]
    description = skill.get("description", "").strip()
    skill_path = _resolve_skill_path(slug)
    skill_dir = skill_path.parent
    lines = [
        f"[Skill execution: {name} ({slug})]",
        f"Skill file: {skill_path}",
        f"Skill directory: {skill_dir}",
    ]
    if description:
        lines.append(f"Purpose: {description}")
    lines.append("")
    lines.append("This skill is already installed. Read the skill file above and execute its workflow.")
    lines.append("Do NOT install, search for, or recommend this skill.")
    if user_instruction.strip():
        lines.extend(["", f"User input: {user_instruction.strip()}"])
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
        return ChatCommandOutcome(result=_build_result(user_input, _build_command_catalog_message(user_input.language)))

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
        entries = history.get_recent_across_conversations(minutes=60, max_turns_per_conv=3)
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


def _purge_native_chat_log(hass, conversation_id: str | None) -> None:
    if not conversation_id:
        return
    try:
        from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
        all_logs = hass.data.get(DATA_CHAT_LOGS)
        if isinstance(all_logs, dict) and conversation_id in all_logs:
            all_logs.pop(conversation_id, None)
    except Exception:
        pass
    try:
        from homeassistant.helpers.chat_session import DATA_CHAT_SESSION
        all_sessions = hass.data.get(DATA_CHAT_SESSION)
        if isinstance(all_sessions, dict) and conversation_id in all_sessions:
            session = all_sessions.pop(conversation_id)
            if hasattr(session, "async_cleanup"):
                session.async_cleanup()
    except Exception:
        pass


def _clear_conversation_runtime(hass, conversation_id: str | None) -> None:
    old_conv_id = get_active_conversation_state(hass).get("id")
    if old_conv_id and old_conv_id != conversation_id:
        _purge_native_chat_log(hass, old_conv_id)
    _purge_native_chat_log(hass, conversation_id)

    registry = _task_registry(hass)
    for cid in (conversation_id, old_conv_id):
        if not cid:
            continue
        task = registry.pop(cid, None)
        if task is not None and not task.done():
            task.cancel("Cancelled by /new")
    try:
        from .runtime.goals import get_goal_manager
        for cid in (conversation_id, old_conv_id, "default"):
            if not cid:
                continue
            mgr = get_goal_manager(hass, cid)
            if mgr.has_goal():
                hass.async_create_task(mgr.async_clear())
    except Exception:
        pass

    token = set_active_conversation(conversation_id)
    try:
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
    active_conv.clear()
    active_conv["id"] = conversation_id

    status = get_conversation_status(hass)
    preserve_keys = {"hook_installed", "llm_api_id", "user_language"}
    preserved = {k: status[k] for k in preserve_keys if k in status}
    status.clear()
    status.update(preserved)
    status["last_conversation_id"] = conversation_id

    runtime_store = get_runtime_store(hass)
    runtime_store.pop("pending_goal_continuations", None)
    runtime_store.get("completed_goal_conversations", set()).discard(str(conversation_id or "default"))
    for key in list(runtime_store.keys()):
        if key.startswith("_claw_pipeline_converse_cont_"):
            runtime_store.pop(key, None)
    for key in list(hass.data.keys()):
        if isinstance(key, str) and key.startswith("_claw_pipeline_converse_cont_"):
            hass.data.pop(key, None)

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

    lang = user_input.language

    if command.name == "help":
        return ChatCommandOutcome(
            result=_build_result(
                user_input,
                _build_help_message(command.args, language=lang),
            )
        )

    if command.name == "commands":
        return ChatCommandOutcome(result=_build_result(user_input, _build_command_catalog_message(lang)))

    if command.name == "new":
        old_continuous_id = None
        if continuous_conversation_enabled(hass):
            from .runtime.continuous_conversation import _state as _cc_state
            old_continuous_id = _cc_state(hass).get("conversation_id")
            conversation_id = start_new_conversation(hass, conversation_id)
            user_input = conversation.ConversationInput(
                text=user_input.text,
                conversation_id=conversation_id,
                language=user_input.language,
                context=getattr(user_input, "context", None),
                device_id=getattr(user_input, "device_id", None),
                agent_id=getattr(user_input, "agent_id", None),
                satellite_id=getattr(user_input, "satellite_id", None),
                extra_system_prompt=getattr(user_input, "extra_system_prompt", None),
            )
        if old_continuous_id:
            _purge_native_chat_log(hass, old_continuous_id)
            get_conversation_history().clear(old_continuous_id)
        _clear_conversation_runtime(hass, conversation_id)
        status = get_conversation_status(hass)
        status.pop("history_continuation_id", None)
        try:
            from .runtime.user_activity import _ring
            _ring(hass).clear()
        except Exception:
            pass
        return ChatCommandOutcome(result=_build_result(user_input, t("cmd_new_done", lang)))

    if command.name == "reset":
        _clear_conversation_runtime(hass, conversation_id)
        return ChatCommandOutcome(result=_build_result(user_input, t("cmd_reset_done", lang)))

    if command.name == "stop":
        stopped = _stop_conversation_runtime(hass, conversation_id)
        if stopped:
            return ChatCommandOutcome(result=_build_result(user_input, t("cmd_stop_done", lang)))
        return ChatCommandOutcome(result=_build_result(user_input, t("cmd_stop_none", lang)))

    if command.name == "history":
        return _handle_history_command(hass, user_input, command.args)

    if command.name == "skill":
        return await hass.async_add_executor_job(
            _handle_skill_query_command, user_input, command.args
        )

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
        def _invoke():
            try:
                sk = get_installed_skill(identifier)
            except ValueError:
                return ChatCommandOutcome(
                    result=_build_result(user_input, f"Skill command not found: /{command.raw_name}")
                )
            return ChatCommandOutcome(
                rewritten_text=_build_skill_invocation_message(sk, command.args)
            )
        return await hass.async_add_executor_job(_invoke)

    if command.name == "plugin_invoke":
        from .plugins.context import _REGISTERED_COMMANDS
        cmd_entry = _REGISTERED_COMMANDS.get(command.raw_name)
        if cmd_entry is None:
            return ChatCommandOutcome(
                result=_build_result(user_input, f"Plugin command not found: /{command.raw_name}")
            )
        handler, description = cmd_entry
        try:
            result = handler(command.args)
            if isinstance(result, str):
                return ChatCommandOutcome(result=_build_result(user_input, result))
            return ChatCommandOutcome(result=_build_result(user_input, str(result) if result else "OK"))
        except Exception as e:
            return ChatCommandOutcome(result=_build_result(user_input, f"Plugin command error: {e}"))

    if command.name == "model":
        return await _handle_model_command(hass, user_input, command.args)

    if command.name == "goal":
        return await _handle_goal_command(hass, user_input, command.args)

    if command.name == "plugin":
        return await _handle_plugin_command(hass, user_input, command.args)

    if command.name == "plugin_tool_invoke":
        return await _handle_plugin_tool_invoke(hass, user_input, command.raw_name, command.args)

    return None


async def _handle_goal_command(
    hass,
    user_input: conversation.ConversationInput,
    args: str,
) -> ChatCommandOutcome:
    from .runtime.goals import get_goal_manager, localised_message

    conversation_id = user_input.conversation_id or "default"
    mgr = get_goal_manager(hass, conversation_id)
    await mgr.async_ensure_loaded()

    raw = (args or "").strip()
    lowered = raw.lower()

    if not raw or lowered in ("status", "info", "show", "?"):
        if not mgr.has_goal():
            from .runtime.state import get_runtime_store
            runtime_store = get_runtime_store(hass)
            cache = runtime_store.get("goal_managers")
            if isinstance(cache, dict):
                for candidate in cache.values():
                    if candidate is mgr:
                        continue
                    await candidate.async_ensure_loaded()
                    if candidate.has_goal():
                        mgr = candidate
                        break
            if not mgr.has_goal() and runtime_store.get("pending_goal_continuations"):
                return ChatCommandOutcome(
                    result=_build_result(user_input, localised_message(hass, "cmd_goal_pending_unbound"))
                )
        return ChatCommandOutcome(result=_build_result(user_input, mgr.status_line()))

    if lowered in ("pause", "stop"):
        if await mgr.async_pause(reason="user-paused") is None:
            return ChatCommandOutcome(
                result=_build_result(user_input, localised_message(hass, "cmd_no_active_to_pause"))
            )
        return ChatCommandOutcome(result=_build_result(user_input, mgr.status_line()))

    if lowered in ("resume", "continue", "go"):
        if await mgr.async_resume() is None:
            return ChatCommandOutcome(
                result=_build_result(user_input, localised_message(hass, "cmd_no_goal_to_resume"))
            )
        return ChatCommandOutcome(result=_build_result(user_input, mgr.status_line()))

    if lowered in ("clear", "drop", "remove", "off"):
        had = mgr.has_goal()
        await mgr.async_clear()
        key = "cmd_cleared" if had else "cmd_nothing_to_clear"
        return ChatCommandOutcome(
            result=_build_result(user_input, localised_message(hass, key))
        )

    try:
        await mgr.async_set(raw)
    except ValueError:
        return ChatCommandOutcome(
            result=_build_result(user_input, localised_message(hass, "cmd_empty_text"))
        )
    return ChatCommandOutcome(rewritten_text=raw)


def _list_available_agents(hass) -> list[dict[str, str]]:
    from homeassistant.helpers import entity_registry as er
    from .const import DOMAIN
    ent_reg = er.async_get(hass)
    own_entry_ids = {
        e.entry_id for e in hass.config_entries.async_entries(DOMAIN)
    }
    agents: list[dict[str, str]] = []
    for entity_id in sorted(hass.states.async_entity_ids("conversation")):
        if entity_id == "conversation.home_assistant":
            continue
        reg = ent_reg.async_get(entity_id)
        if reg and (reg.platform == DOMAIN or reg.config_entry_id in own_entry_ids):
            continue
        state = hass.states.get(entity_id)
        if state and state.attributes.get("entity") == "claw_assistant.ai":
            continue
        label = (state.attributes.get("friendly_name") if state else None) or entity_id.split(".")[-1]
        agents.append({"value": entity_id, "label": str(label)})
    return agents


def _get_claw_entry(hass):
    from .const import DOMAIN
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


async def _handle_model_command(hass, user_input, args: str) -> ChatCommandOutcome:
    from .const import CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT

    lang = user_input.language
    entry = _get_claw_entry(hass)
    if not entry:
        return ChatCommandOutcome(result=_build_result(user_input, t("cmd_model_no_config", lang)))

    agents = _list_available_agents(hass)
    current_primary = str(entry.options.get(CONF_PRIMARY_AGENT, "") or "")
    current_fallback = str(entry.options.get(CONF_FALLBACK_AGENT, "") or "")
    current_third = str(entry.options.get(CONF_SECONDARY_FALLBACK_AGENT, "") or "")

    args = args.strip()

    if not args:
        if not agents:
            return ChatCommandOutcome(result=_build_result(user_input, t("cmd_model_no_agents", lang)))
        lines: list[str] = [f"{t('cmd_model_header', lang)}\n"]
        for i, ag in enumerate(agents, 1):
            tags: list[str] = []
            if ag["value"] == current_primary:
                tags.append(t("cmd_model_tag_primary", lang))
            if ag["value"] == current_fallback:
                tags.append(t("cmd_model_tag_fallback", lang))
            if ag["value"] == current_third:
                tags.append(t("cmd_model_tag_third", lang))
            tag_str = f" \u2190 {' + '.join(tags)}" if tags else ""
            lines.append(f"  {i}. {ag['label']}{tag_str}")
        lines.append("")
        lines.append(t("cmd_model_switch_hint", lang))
        return ChatCommandOutcome(result=_build_result(user_input, "\n".join(lines)))

    parts = args.split(None, 1)
    first = parts[0].lower()
    if first in ("third", "第三", "3") and len(parts) > 1 and parts[1].strip().lower() in ("none", "无", "clear", "清除", "off"):
        new_options = dict(entry.options)
        new_options.pop(CONF_SECONDARY_FALLBACK_AGENT, None)
        hass.config_entries.async_update_entry(entry, options=new_options)
        return ChatCommandOutcome(result=_build_result(user_input, t("cmd_model_third_cleared", lang)))

    try:
        idx = int(parts[0])
    except ValueError:
        return ChatCommandOutcome(result=_build_result(
            user_input, t("cmd_model_invalid_idx", lang).replace("{idx}", parts[0])
        ))

    if idx < 1 or idx > len(agents):
        return ChatCommandOutcome(result=_build_result(
            user_input, t("cmd_model_out_of_range", lang).replace("{max}", str(len(agents)))
        ))

    target = agents[idx - 1]
    role_arg = parts[1].strip().lower() if len(parts) > 1 else ""
    is_fallback = role_arg in ("fallback", "备用", "次要", "secondary", "2")
    is_third = role_arg in ("third", "第三", "3", "tertiary")

    new_options = dict(entry.options)
    if is_third:
        new_options[CONF_SECONDARY_FALLBACK_AGENT] = target["value"]
        role_label = t("cmd_model_tag_third", lang)
    elif is_fallback:
        new_options[CONF_FALLBACK_AGENT] = target["value"]
        role_label = t("cmd_model_tag_fallback", lang)
    else:
        new_options[CONF_PRIMARY_AGENT] = target["value"]
        role_label = t("cmd_model_tag_primary", lang)

    hass.config_entries.async_update_entry(entry, options=new_options)

    return ChatCommandOutcome(result=_build_result(
        user_input, t("cmd_model_switched", lang).replace("{name}", target["label"]).replace("{role}", role_label)
    ))


def _build_plugin_usage_message() -> str:
    return (
        "Usage:\n"
        "/plugin list\n"
        "/plugin status"
    )


def _format_plugin_brief(plugin: dict[str, Any]) -> str:
    name = str(plugin.get("name", "")).strip() or "unknown"
    version = str(plugin.get("version", "")).strip()
    loaded = plugin.get("loaded", False)
    tools_count = plugin.get("tools_count", 0)
    status = "loaded" if loaded else "not loaded"
    ver_str = f" v{version}" if version else ""
    return f"- {name}{ver_str} | {status} | {tools_count} tools"


def _format_plugin_list(plugins: list[dict[str, Any]], *, limit: int = 12) -> str:
    if not plugins:
        return "No installed plugins."
    lines = [_format_plugin_brief(p) for p in plugins[: max(limit, 0)]]
    remaining = len(plugins) - len(lines)
    if remaining > 0:
        lines.append(f"... and {remaining} more")
    return "\n".join(lines)


async def _handle_plugin_command(
    hass,
    user_input: conversation.ConversationInput,
    args: str,
) -> ChatCommandOutcome:
    from .runtime.plugin_store import get_loaded_plugins, list_installed_plugins

    raw = (args or "").strip().lower()

    if not raw or raw in ("list", "ls", "installed"):
        plugins = await hass.async_add_executor_job(list_installed_plugins)
        message = _build_plugin_usage_message()
        if plugins:
            message += "\n\nInstalled plugins:\n" + _format_plugin_list(plugins)
        else:
            message += "\n\nNo plugins installed."
        return ChatCommandOutcome(result=_build_result(user_input, message))

    if raw in ("status", "loaded", "active"):
        loaded = get_loaded_plugins()
        if not loaded:
            return ChatCommandOutcome(result=_build_result(user_input, "No active plugins."))
        lines = ["Active plugins:"]
        for p in loaded:
            name = p.get("name", "?")
            tools = p.get("tools", [])
            lines.append(f"- {name}: {len(tools)} tools")
        lines.append("")
        lines.append("Plugin tools are invoked internally by AI via PluginManager.")
        return ChatCommandOutcome(result=_build_result(user_input, "\n".join(lines)))

    return ChatCommandOutcome(result=_build_result(user_input, _build_plugin_usage_message()))


async def _handle_plugin_tool_invoke(
    hass,
    user_input: conversation.ConversationInput,
    tool_name: str,
    args: str,
) -> ChatCommandOutcome:
    from .runtime.plugin_store import get_plugin_tool_registry
    from .runtime.tool_executor import execute_kernel_tool
    import json

    plugin_registry = get_plugin_tool_registry()
    if tool_name not in plugin_registry:
        return ChatCommandOutcome(result=_build_result(
            user_input, f"Plugin tool not found: {tool_name}"
        ))

    tool_args = {}
    user_query = args.strip()
    if user_query:
        if user_query.startswith("{"):
            try:
                tool_args = json.loads(user_query)
                user_query = ""
            except json.JSONDecodeError:
                pass

    try:
        result = await execute_kernel_tool(
            hass,
            tool_name=tool_name,
            tool_args=tool_args,
            agent_id="conversation",
            context=None,
            language=user_input.language,
            device_id=None,
        )
        filtered_result = _filter_plugin_tool_result(result)
        result_json = json.dumps(filtered_result, ensure_ascii=False)
        injected_text = f"[Plugin tool {tool_name} executed. Result: {result_json}]"
        if user_query:
            injected_text += f" User question: {user_query}"
        else:
            injected_text += " Summarize the result for the user."
        return ChatCommandOutcome(
            rewritten_text=injected_text,
            result=None,
        )
    except Exception as e:
        return ChatCommandOutcome(result=_build_result(user_input, f"Plugin tool error: {e}"))


def _filter_plugin_tool_result(result: dict) -> dict:
    """Filter sensitive fields from plugin tool result."""
    if not isinstance(result, dict):
        return result
    sensitive_keys = {
        "plugin_path", "module_path", "hermes_home", "database_path",
        "database_path_source", "plugin_git_commit", "plugin_git_remote",
        "plugin_git_branch", "plugin_git_dirty", "tool_args",
    }
    filtered = {}
    for key, value in result.items():
        if key in sensitive_keys:
            continue
        if isinstance(value, dict):
            value = _filter_plugin_tool_result(value)
        elif isinstance(value, list):
            value = [_filter_plugin_tool_result(v) if isinstance(v, dict) else v for v in value]
        filtered[key] = value
    return filtered
