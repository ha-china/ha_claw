

from __future__ import annotations

from typing import Any

from homeassistant.components.conversation.chat_log import (
    AssistantContent,
    ToolResultContent,
)

from .native_chatlog_bridge import is_step_agent_id

NON_USER_FACING_TOOLS = frozenset(
    {"ThinkContinue", "GetLiveContext", "SetConversationState"}
)
FALLBACK_USER_FACING_TOOLS = frozenset({"GetLiveContext"})


def extract_structured_result_text(result: dict[str, Any]) -> str:

    speech = result.get("speech")
    if isinstance(speech, dict):
        plain = speech.get("plain")
        if isinstance(plain, dict) and plain.get("speech"):
            return str(plain["speech"])

    matched_states = result.get("matched_states")
    if isinstance(matched_states, list) and matched_states:
        lines: list[str] = []
        for item in matched_states[:5]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("entity_id")
            state = item.get("state")
            if name and state not in (None, ""):
                lines.append(f"{name} is currently {state}")
        if lines:
            return "; ".join(lines)

    response_data = result.get("data")
    if isinstance(response_data, dict):
        success_items = response_data.get("success")
        if isinstance(success_items, list) and success_items:
            names: list[str] = []
            for item in success_items[:5]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("id")
                    if name:
                        names.append(str(name))
            if names:
                return f"Processed: {', '.join(names)}"

    entities = result.get("entities")
    if isinstance(entities, dict) and entities:
        lines: list[str] = []
        for entity_id, item in list(entities.items())[:8]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or entity_id
            state = item.get("state")
            if state in (None, ""):
                continue
            lines.append(f"{name}: {state}")
        if lines:
            count = result.get("count")
            prefix = f"Current states ({count} total)" if count not in (None, "") else "Current states"
            return prefix + ": " + "; ".join(lines)

    if isinstance(result.get("entries"), list) and result["entries"]:
        return "\n".join(
            f"{entry.get('key', '')}: {entry.get('value', '')}".strip(": ")
            for entry in result["entries"][:5]
            if isinstance(entry, dict)
        )

    for list_field in ("docs", "results", "runtime_topics", "skills", "keys", "tasks"):
        value = result.get(list_field)
        if isinstance(value, list) and value:
            lines: list[str] = []
            for item in value[:10]:
                if isinstance(item, dict):
                    label = (
                        item.get("title")
                        or item.get("name")
                        or item.get("file")
                        or item.get("path")
                        or item.get("id")
                        or item.get("entity_id")
                    )
                    extra = (
                        item.get("snippet")
                        or item.get("description")
                        or item.get("objective")
                        or item.get("status")
                        or item.get("schedule")
                        or item.get("collection")
                        or item.get("chars")
                        or (
                            "success"
                            if item.get("success") is True
                            else "failed"
                            if item.get("success") is False
                            else None
                        )
                        or item.get("error")
                    )
                    if label and extra:
                        lines.append(f"{label}: {extra}")
                    elif label:
                        lines.append(str(label))
                else:
                    lines.append(str(item))
            if lines:
                return "\n".join(lines)

        if isinstance(value, str) and value:
            return value

    query = result.get("query")
    count = result.get("count")
    if query not in (None, "") and count == 0:
        return f'No results found for "{query}".'

    if isinstance(result.get("collections"), dict):
        return "\n".join(
            f"{key}: {value}" for key, value in result["collections"].items()
        )

    return ""


def extract_successful_tool_response(tool_results: list[dict[str, Any]]) -> str:

    successful = [
        item
        for item in tool_results
        if item.get("success", False)
        and item.get("tool_name") not in NON_USER_FACING_TOOLS
    ]
    if not successful:
        successful = [
            item
            for item in tool_results
            if item.get("success", False)
            and item.get("tool_name") in FALLBACK_USER_FACING_TOOLS
        ]
    if not successful:
        return ""

    preferred_fields = ("response", "message", "result", "state", "count")
    parts: list[str] = []
    for item in successful[:3]:
        result = item.get("result")
        if isinstance(result, dict):
            for rich_field in ("news", "content", "markdown", "help"):
                value = result.get(rich_field)
                if value not in (None, "", [], {}):
                    parts.append(str(value))
                    break
            else:
                generic_text = extract_structured_result_text(result)
                if generic_text:
                    parts.append(generic_text)
                    continue
            for field in preferred_fields:
                value = result.get(field)
                if value not in (None, "", [], {}):
                    parts.append(str(value))
                    break
        elif result not in (None, "", [], {}):
            parts.append(str(result))

    if parts:
        return "\n".join(parts[:3])

    return ""


def extract_failed_tool_response(tool_results: list[dict[str, Any]]) -> str:

    failed = [item for item in tool_results if not item.get("success", True)]
    if not failed:
        return ""

    parts: list[str] = []
    for item in failed[:3]:
        tool_name = str(item.get("tool_name", "tool"))
        error = item.get("error")
        result = item.get("result")
        if not error and isinstance(result, dict):
            error = result.get("error") or result.get("error_text")
        if not error:
            error = "Tool call failed"
        detail = f"{tool_name} failed: {error}"
        if isinstance(result, dict) and result.get("missing_target"):
            available_entities = result.get("available_entities")
            suggestions: list[str] = []
            if isinstance(available_entities, list) and available_entities:
                candidates = []
                for item in available_entities[:5]:
                    if not isinstance(item, dict):
                        continue
                    entity_id = item.get("entity_id")
                    names = item.get("names")
                    if entity_id and names:
                        candidates.append(f"{entity_id} ({names})")
                    elif entity_id:
                        candidates.append(str(entity_id))
                if candidates:
                    suggestions.append("Candidates: " + ", ".join(candidates))
            recovery_hint = result.get("recovery_hint")
            if recovery_hint:
                suggestions.append(str(recovery_hint))
            if suggestions:
                detail += ". " + " ".join(suggestions)
        parts.append(detail)

    return "\n".join(parts)


def _normalize_chat_log_tool_results(
    tool_results: list[ToolResultContent],
) -> list[dict[str, Any]]:

    normalized: list[dict[str, Any]] = []
    for item in tool_results:
        raw_result = item.tool_result
        success = True
        normalized_result = raw_result
        if isinstance(raw_result, dict) and "success" in raw_result:
            success = bool(raw_result.get("success", True))
            if "result" in raw_result and raw_result.get("result") not in (None, ""):
                normalized_result = raw_result["result"]
        normalized.append(
            {
                "tool_name": item.tool_name,
                "success": success,
                "error": raw_result.get("error") if isinstance(raw_result, dict) else None,
                "result": normalized_result,
            }
        )
    return normalized


def _find_turn_start(content: list[Any]) -> int:

    for index in range(len(content) - 1, -1, -1):
        if getattr(content[index], "role", None) == "user":
            return index + 1
    return 0





def _collect_trailing_tool_results(content: list[Any]) -> list[Any]:

    start = _find_turn_start(content)
    return [item for item in content[start:] if isinstance(item, ToolResultContent)]


def _synthesize_from_tool_results(
    content: list[Any], fallback_agent_id: str
) -> AssistantContent | None:
    tool_results = _collect_trailing_tool_results(content)
    if not tool_results:
        return None
    normalized = _normalize_chat_log_tool_results(tool_results)
    response_text = extract_successful_tool_response(normalized).strip()
    if not response_text:
        response_text = extract_failed_tool_response(normalized).strip()
    if not response_text:
        return None
    return AssistantContent(
        agent_id=tool_results[-1].agent_id or fallback_agent_id,
        content=response_text,
    )


def build_synthesized_assistant_from_chat_log(chat_log: Any) -> AssistantContent | None:

    content = getattr(chat_log, "content", None)
    if not content:
        return None

    last = content[-1]
    agent_id = getattr(last, "agent_id", "") or ""


    if isinstance(last, ToolResultContent):
        return _synthesize_from_tool_results(content, agent_id)


    if isinstance(last, AssistantContent) and not last.content:
        return _synthesize_from_tool_results(content, agent_id)

    return None
