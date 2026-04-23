

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm

from .master_prompt import build_master_prompt_sections
from .skill_store import format_runtime_prompt_doc
from .state import (
    get_active_conversation_state,
    get_conversation_status,
    get_runtime_store,
    get_tool_calls_state,
    get_tool_results_state,
)
from .workspace_store import (
    build_workspace_prompt_sections,
    build_workspace_startup_bundle,
    get_workspace_startup_doc_names,
)

LOGGER = logging.getLogger(__name__)

CUSTOM_API_ID = "ha_crack_enhanced"
_TZ = timezone(timedelta(hours=8))
_PATCH_KEY = "_ha_crack_patched"
_TOOL_TRACK_KEY = "_ha_crack_tool_tracking"
_ASSIST_PROMPT_ORIGINAL_KEY = "_ha_crack_original_assist_api_prompt"
_ASSIST_TOOLS_ORIGINAL_KEY = "_ha_crack_original_assist_api_tools"
_TOOL_TRACK_ORIGINAL_KEY = "_ha_crack_original_tool_tracking"
_CHATLOG_TOOLS_ORIGINAL_KEY = "_ha_crack_original_chatlog_tools"
_API_STATE: dict[str, Callable[[], None] | None] = {"unregister_api": None}
_RUNTIME_PATCH_SNAPSHOT_KEY = "internal_llm_patch_snapshot"
_TOOL_MODE: ContextVar[str] = ContextVar("claw_assistant_tool_mode", default="unset")


_MAX_SYSTEM_PROMPT_CHARS = 16000
_PROMPT_SECTION_SEPARATOR = "\n\n"
_MIN_SECTION_PRESERVE_CHARS = 256

_RESULT_SUMMARY_FIELDS = (
    "success",
    "message",
    "response",
    "state",
    "count",
    "response_type",
    "speech",
    "speech_slots",
    "data",
    "matched_states",
    "intent",
    "news",
    "results",
    "contents",
    "content",
    "help",
    "entries",
    "docs",
    "markdown",
    "entities",
    "tasks",
    "last_checked_at",
    "last_status",
    "stdout",
    "stderr",
    "result",
    "returncode",
    "elapsed",
    "error",
    "traceback",
)
_LONG_RESULT_FIELDS = frozenset(
    {"markdown", "content", "help", "news", "results", "docs", "tasks", "stdout", "result"}
)
_LONG_RESULT_FIELD_LIMIT = 8000
_DEFAULT_RESULT_FIELD_LIMIT = 1200
_PATCH_SNAPSHOT_KEYS = (
    "assist_api_prompt",
    "assist_api_tools",
    "api_instance_call_tool",
    "chatlog_update",
)


def _compact_result_summary(result: dict[str, Any]) -> dict[str, Any]:

    compacted: dict[str, Any] = {}
    for key, value in result.items():
        if key not in _RESULT_SUMMARY_FIELDS:
            continue
        if isinstance(value, str):
            limit = _LONG_RESULT_FIELD_LIMIT if key in _LONG_RESULT_FIELDS else _DEFAULT_RESULT_FIELD_LIMIT
            compacted[key] = value if len(value) <= limit else value[:limit] + "..."
            continue
        if isinstance(value, list) and len(value) > 20:
            compacted[key] = value[:20]
            continue
        compacted[key] = value
    return compacted


def _build_current_datetime() -> str:

    return datetime.now(_TZ).strftime(
        "Today is %Y-%m-%d %A, current time %H:%M:%S (Beijing Time)"
    )


def _build_runtime_prompt_kwargs(user_text: str = "") -> dict[str, str]:

    return {
        "current_datetime": _build_current_datetime(),
        "user_text": user_text,
    }


def _capture_patch_snapshot() -> dict[str, Callable[..., Any]]:

    from homeassistant.components.conversation import chat_log as chat_log_module
    from homeassistant.helpers import llm as llm_module

    return {
        "assist_api_prompt": llm_module.AssistAPI._async_get_api_prompt,
        "assist_api_tools": llm_module.AssistAPI._async_get_tools,
        "api_instance_call_tool": llm_module.APIInstance.async_call_tool,
        "chatlog_update": chat_log_module.ChatLog.async_update_llm_data,
    }


def _is_valid_patch_snapshot(snapshot: object) -> bool:

    return isinstance(snapshot, dict) and all(key in snapshot for key in _PATCH_SNAPSHOT_KEYS)


def _restore_patch_snapshot(snapshot: dict[str, Callable[..., Any]]) -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module
    from homeassistant.helpers import llm as llm_module

    llm_module.AssistAPI._async_get_api_prompt = snapshot["assist_api_prompt"]
    llm_module.AssistAPI._async_get_tools = snapshot["assist_api_tools"]
    llm_module.APIInstance.async_call_tool = snapshot["api_instance_call_tool"]
    chat_log_module.ChatLog.async_update_llm_data = snapshot["chatlog_update"]


def _trim_prompt(text: str, *, max_chars: int = _MAX_SYSTEM_PROMPT_CHARS) -> str:

    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _join_prompt_sections(*sections: str) -> str:

    return _PROMPT_SECTION_SEPARATOR.join(section for section in sections if section.strip())


def _fit_head_section_to_required_suffix(
    head_section: str,
    required_sections: list[str],
    *,
    max_chars: int = _MAX_SYSTEM_PROMPT_CHARS,
) -> str:

    if not head_section.strip():
        return ""

    required_suffix = _join_prompt_sections(*required_sections)
    if not required_suffix:
        return _trim_prompt(head_section, max_chars=max_chars)

    available_chars = max_chars - len(required_suffix) - len(_PROMPT_SECTION_SEPARATOR)
    if available_chars <= 0:
        return ""
    return _trim_prompt(head_section, max_chars=available_chars)


def _fit_sections_to_budget_preserving_suffix(
    sections: list[str],
    *,
    max_chars: int = _MAX_SYSTEM_PROMPT_CHARS,
) -> str:

    kept_sections = [section for section in sections if section.strip()]
    if not kept_sections or max_chars <= 0:
        return ""

    prompt = _join_prompt_sections(*kept_sections)
    if len(prompt) <= max_chars:
        return prompt

    fitted_sections = [_minimum_section_text(section) for section in kept_sections]

    while fitted_sections:
        prompt = _join_prompt_sections(*fitted_sections)
        if len(prompt) <= max_chars:
            break
        fitted_sections.pop(0)
        kept_sections.pop(0)

    if not fitted_sections:
        return ""

    prompt = _join_prompt_sections(*fitted_sections)
    if len(prompt) > max_chars:
        return _trim_prompt(fitted_sections[-1], max_chars=max_chars)

    remaining_chars = max_chars - len(prompt)
    for index in range(len(kept_sections) - 1, -1, -1):
        section = kept_sections[index]
        current = fitted_sections[index]
        if len(current) >= len(section) or remaining_chars <= 0:
            continue
        expanded_len = min(len(section), len(current) + remaining_chars)
        fitted_sections[index] = _trim_prompt(section, max_chars=expanded_len)
        updated_prompt = _join_prompt_sections(*fitted_sections)
        remaining_chars = max_chars - len(updated_prompt)

    return _join_prompt_sections(*fitted_sections)


def _minimum_budget_for_section_headings(sections: list[str]) -> int:

    minimum_sections = [
        _minimum_section_text(section) for section in sections if section.strip()
    ]
    if not minimum_sections:
        return 0
    return sum(len(section) for section in minimum_sections) + (
        len(_PROMPT_SECTION_SEPARATOR) * (len(minimum_sections) - 1)
    )


def _minimum_section_text(section: str) -> str:

    trimmed = section.strip()
    if len(trimmed) <= _MIN_SECTION_PRESERVE_CHARS:
        return trimmed
    return next((line.strip() for line in trimmed.splitlines() if line.strip()), "")


def _build_required_prompt(
    *,
    required_prefix_sections: list[str],
    required_suffix_sections: list[str],
    max_chars: int = _MAX_SYSTEM_PROMPT_CHARS,
) -> str:

    kept_required_prefix = [section for section in required_prefix_sections if section.strip()]
    kept_required_suffix = [section for section in required_suffix_sections if section.strip()]
    kept_required = [*kept_required_prefix, *kept_required_suffix]

    prompt = _join_prompt_sections(*kept_required)
    if len(prompt) <= max_chars:
        return prompt

    prefix_budget = max_chars
    if kept_required_prefix and kept_required_suffix:
        prefix_budget = max(
            max_chars
            - _minimum_budget_for_section_headings(kept_required_suffix)
            - len(_PROMPT_SECTION_SEPARATOR),
            0,
        )

    prefix_prompt = _fit_sections_to_budget_preserving_suffix(
        kept_required_prefix,
        max_chars=prefix_budget,
    )
    remaining_chars = max_chars - len(prefix_prompt)
    if prefix_prompt and kept_required_suffix:
        remaining_chars -= len(_PROMPT_SECTION_SEPARATOR)
    if remaining_chars <= 0:
        return prefix_prompt

    required_suffix_prompt = _fit_sections_to_budget_preserving_suffix(
        kept_required_suffix,
        max_chars=remaining_chars,
    )
    return _join_prompt_sections(prefix_prompt, required_suffix_prompt)


def _build_budgeted_prompt(
    *,
    head_sections: list[str],
    required_prefix_sections: list[str],
    required_suffix_sections: list[str],
    optional_tail_sections: list[str],
    max_chars: int = _MAX_SYSTEM_PROMPT_CHARS,
) -> str:

    kept_heads = [section for section in head_sections if section.strip()]
    kept_required_prefix = [section for section in required_prefix_sections if section.strip()]
    kept_required_suffix = [section for section in required_suffix_sections if section.strip()]
    kept_tail = [section for section in optional_tail_sections if section.strip()]
    kept_required = [*kept_required_prefix, *kept_required_suffix]

    prompt = _join_prompt_sections(*kept_heads, *kept_required, *kept_tail)
    if len(prompt) <= max_chars:
        return prompt

    required_prompt = _build_required_prompt(
        required_prefix_sections=kept_required_prefix,
        required_suffix_sections=kept_required_suffix,
        max_chars=max_chars,
    )
    prompt = _join_prompt_sections(*kept_heads, required_prompt)
    if len(prompt) <= max_chars:
        return prompt

    if not kept_heads:
        return required_prompt

    remaining_chars = max_chars - len(required_prompt)
    if required_prompt:
        remaining_chars -= len(_PROMPT_SECTION_SEPARATOR)
    if remaining_chars <= 0:
        return required_prompt

    head_prompt = _fit_sections_to_budget_preserving_suffix(
        kept_heads,
        max_chars=remaining_chars,
    )
    if not head_prompt and len(kept_heads) == 1:
        head_prompt = _fit_head_section_to_required_suffix(
            kept_heads[0],
            [required_prompt] if required_prompt else [],
            max_chars=max_chars,
        )

    return _join_prompt_sections(head_prompt, required_prompt)


def set_runtime_tool_mode(mode: str) -> object:

    return _TOOL_MODE.set(mode)


def reset_runtime_tool_mode(token: object) -> None:

    _TOOL_MODE.reset(token)


def build_internal_llm_prompt(user_text: str = "") -> str:

    workspace_block = build_workspace_startup_bundle(user_text=user_text)
    loaded_workspace_docs = set(get_workspace_startup_doc_names(user_text=user_text))
    selective_workspace_sections = list(
        build_workspace_prompt_sections(
            user_text=user_text,
            exclude_doc_names=loaded_workspace_docs,
        )
    )
    prompt_kwargs = _build_runtime_prompt_kwargs(user_text)
    runtime_context = format_runtime_prompt_doc("runtime_context", **prompt_kwargs)
    skill_mode = format_runtime_prompt_doc("skill_mode", **prompt_kwargs)
    master_sections = list(build_master_prompt_sections(user_text=user_text))
    optional_tail_sections: list[str] = []
    if master_sections and master_sections[-1].startswith(
        ("## Relevant Installed Skills", "## Installed Skill Index")
    ):
        optional_tail_sections.append(master_sections.pop())

    return _build_budgeted_prompt(
        head_sections=[workspace_block, *selective_workspace_sections],
        required_prefix_sections=[runtime_context, skill_mode],
        required_suffix_sections=master_sections,
        optional_tail_sections=optional_tail_sections,
    )


def build_native_tool_prompt(user_text: str = "") -> str:

    workspace_block = build_workspace_startup_bundle(user_text=user_text)
    loaded_workspace_docs = set(get_workspace_startup_doc_names(user_text=user_text))
    selective_workspace_sections = list(
        build_workspace_prompt_sections(
            user_text=user_text,
            exclude_doc_names=loaded_workspace_docs,
        )
    )
    prompt_kwargs = _build_runtime_prompt_kwargs(user_text)
    runtime_context = format_runtime_prompt_doc("runtime_context", **prompt_kwargs)
    native_mode = format_runtime_prompt_doc("native_mode", **prompt_kwargs)
    master_sections = list(build_master_prompt_sections(user_text=user_text))
    optional_tail_sections: list[str] = []
    if master_sections and master_sections[-1].startswith(
        ("## Relevant Installed Skills", "## Installed Skill Index")
    ):
        optional_tail_sections.append(master_sections.pop())

    return _build_budgeted_prompt(
        head_sections=[workspace_block, *selective_workspace_sections],
        required_prefix_sections=[runtime_context, native_mode],
        required_suffix_sections=master_sections,
        optional_tail_sections=optional_tail_sections,
    )


def use_native_tool_surface(hass: HomeAssistant) -> bool:

    tool_mode = _TOOL_MODE.get()
    if tool_mode == "native":
        return True
    if tool_mode == "kernel":
        return False
    if tool_mode == "minimal":
        return False
    return bool(get_conversation_status(hass).get("is_internal_llm"))


def build_assist_api_prompt(hass: HomeAssistant, original_prompt: str) -> str:

    del hass
    del original_prompt
    return ""


def build_assist_api_tools(
    hass: HomeAssistant,
    original_tools: list[llm.Tool],
) -> list[llm.Tool]:

    if _TOOL_MODE.get() == "kernel":
        return []
    if use_native_tool_surface(hass):
        return merge_tool_lists(original_tools, build_runtime_tool_list())

    return build_runtime_tool_list()


def build_runtime_tool_list() -> list[llm.Tool]:

    from ..tools.registry import build_tool_list
    from ..tools.skill_tools import build_skill_tool_list

    return merge_tool_lists(build_tool_list(), build_skill_tool_list())


def build_minimal_tool_list(
    *,
    live_context_tools: list[llm.Tool] | None = None,
    include_live_context: bool = True,
) -> list[llm.Tool]:

    del live_context_tools
    del include_live_context
    return build_runtime_tool_list()


def merge_tool_lists(*tool_groups: list[llm.Tool]) -> list[llm.Tool]:

    merged_tools: list[llm.Tool] = []
    seen_names: set[str] = set()

    for group in tool_groups:
        for tool in group:
            if tool.name in seen_names:
                continue
            seen_names.add(tool.name)
            merged_tools.append(tool)

    return merged_tools


@dataclass(slots=True, kw_only=True)
class EnhancedAPI(llm.API):


    id: str = CUSTOM_API_ID
    name: str = "HA Crack Enhanced API"

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        tools = build_runtime_tool_list()
        return llm.APIInstance(
            api=self,
            api_prompt=build_internal_llm_prompt(),
            llm_context=llm_context,
            tools=tools,
        )


@callback
def async_register_enhanced_api(hass: HomeAssistant) -> None:

    if _API_STATE["unregister_api"] is not None:
        return

    try:
        api = EnhancedAPI(hass=hass)
        _API_STATE["unregister_api"] = llm.async_register_api(hass, api)
        LOGGER.debug("Registered enhanced LLM API: %s", CUSTOM_API_ID)
    except Exception as err:
        LOGGER.error("Failed to register enhanced LLM API: %s", err)


@callback
def async_unregister_enhanced_api() -> None:

    unregister_api = _API_STATE["unregister_api"]
    if unregister_api is None:
        return
    unregister_api()
    _API_STATE["unregister_api"] = None
    LOGGER.debug("Unregistered enhanced LLM API")


def _patch_assist_api_prompt(hass: HomeAssistant) -> None:

    from homeassistant.helpers import llm as llm_module

    if hasattr(llm_module, _PATCH_KEY):
        return

    original_get_api_prompt = llm_module.AssistAPI._async_get_api_prompt
    original_get_tools = llm_module.AssistAPI._async_get_tools

    @callback
    def patched_get_api_prompt(self, llm_context, exposed_entities):
        del llm_context
        del exposed_entities
        return build_assist_api_prompt(hass, "")

    @callback
    def patched_get_tools(self, llm_context, exposed_entities):
        original_tools = original_get_tools(self, llm_context, exposed_entities)
        final_tools = build_assist_api_tools(hass, original_tools)
        LOGGER.debug(
            "Internal LLM tool mode: %s (%s -> %s)",
            "native" if use_native_tool_surface(hass) else "runtime",
            len(original_tools),
            len(final_tools),
        )
        return final_tools

    llm_module.AssistAPI._async_get_api_prompt = patched_get_api_prompt
    llm_module.AssistAPI._async_get_tools = patched_get_tools
    setattr(llm_module.AssistAPI, _ASSIST_PROMPT_ORIGINAL_KEY, original_get_api_prompt)
    setattr(llm_module.AssistAPI, _ASSIST_TOOLS_ORIGINAL_KEY, original_get_tools)
    setattr(llm_module, _PATCH_KEY, True)
    LOGGER.debug("AssistAPI switched to the centralized internal LLM kernel")


def _unpatch_assist_api_prompt() -> None:

    from homeassistant.helpers import llm as llm_module

    original_get_api_prompt = getattr(
        llm_module.AssistAPI, _ASSIST_PROMPT_ORIGINAL_KEY, None
    )
    original_get_tools = getattr(llm_module.AssistAPI, _ASSIST_TOOLS_ORIGINAL_KEY, None)
    if original_get_api_prompt is None or original_get_tools is None:
        return

    llm_module.AssistAPI._async_get_api_prompt = original_get_api_prompt
    llm_module.AssistAPI._async_get_tools = original_get_tools
    delattr(llm_module.AssistAPI, _ASSIST_PROMPT_ORIGINAL_KEY)
    delattr(llm_module.AssistAPI, _ASSIST_TOOLS_ORIGINAL_KEY)
    if hasattr(llm_module, _PATCH_KEY):
        delattr(llm_module, _PATCH_KEY)
    LOGGER.debug("AssistAPI prompt/tool patch restored")


def _patch_tool_call_tracking(hass: HomeAssistant) -> None:

    from homeassistant.helpers import llm as llm_module

    if hasattr(llm_module.APIInstance, _TOOL_TRACK_KEY):
        return

    original_async_call_tool = llm_module.APIInstance.async_call_tool

    async def tracked_async_call_tool(self, tool_input):
        tool_results = get_tool_results_state(hass)
        tool_calls = get_tool_calls_state(hass)
        conv_id_before = get_active_conversation_state(hass).get("id")
        tool_calls.append(tool_input.tool_name)

        try:
            try:
                result = await original_async_call_tool(self, tool_input)
            except Exception as transient_err:
                LOGGER.info(
                    "Tool call %s raised %s; retrying once for network jitter",
                    tool_input.tool_name,
                    transient_err,
                )
                result = await original_async_call_tool(self, tool_input)

            conv_id_after = get_active_conversation_state(hass).get("id")
            if conv_id_before and conv_id_after and conv_id_before != conv_id_after:
                LOGGER.info(
                    "Conversation switched (%s -> %s) during tool %s, aborting stale turn",
                    conv_id_before, conv_id_after, tool_input.tool_name,
                )
                return {"success": False, "error": "conversation_switched"}
            success = True
            error = None
            result_summary = None

            if isinstance(result, dict):
                if "success" in result:
                    success = result.get("success", True)
                    error = result.get("error")
                elif "response_type" in result:
                    success = result.get("response_type") != "error"
                    if result.get("data", {}).get("failed"):
                        success = False
                        error = f"Failed targets: {result['data']['failed']}"

                result_summary = _compact_result_summary(result)

            tool_results.append(
                {
                    "tool_name": tool_input.tool_name,
                    "tool_args": tool_input.tool_args,
                    "success": success,
                    "error": error,
                    "result": result_summary,
                }
            )
            if not success:
                LOGGER.info("Tool call failed: %s - %s | args=%s", tool_input.tool_name, error or "unknown", tool_input.tool_args)
            else:
                LOGGER.info("Tool call ok: %s", tool_input.tool_name)
            return result
        except Exception as err:
            tool_results.append(
                {
                    "tool_name": tool_input.tool_name,
                    "tool_args": tool_input.tool_args,
                    "success": False,
                    "error": str(err),
                    "result": None,
                }
            )
            raise

    llm_module.APIInstance.async_call_tool = tracked_async_call_tool
    setattr(llm_module.APIInstance, _TOOL_TRACK_ORIGINAL_KEY, original_async_call_tool)
    setattr(llm_module.APIInstance, _TOOL_TRACK_KEY, True)
    LOGGER.debug("Tool-call tracking enabled")


def _unpatch_tool_call_tracking() -> None:

    from homeassistant.helpers import llm as llm_module

    original_async_call_tool = getattr(llm_module.APIInstance, _TOOL_TRACK_ORIGINAL_KEY, None)
    if original_async_call_tool is None:
        return

    llm_module.APIInstance.async_call_tool = original_async_call_tool
    delattr(llm_module.APIInstance, _TOOL_TRACK_ORIGINAL_KEY)
    if hasattr(llm_module.APIInstance, _TOOL_TRACK_KEY):
        delattr(llm_module.APIInstance, _TOOL_TRACK_KEY)
    LOGGER.debug("Tool-call tracking restored")


def patch_chatlog_tools(hass: HomeAssistant) -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    if hasattr(chat_log_module.ChatLog, _PATCH_KEY):
        return

    original_async_update_llm_data = chat_log_module.ChatLog.async_update_llm_data

    async def patched_async_update_llm_data(
        self,
        llm_context,
        user_llm_hass_api,
        user_llm_prompt,
        user_extra_system_prompt=None,
    ):
        await original_async_update_llm_data(
            self,
            llm_context,
            user_llm_hass_api,
            user_llm_prompt,
            user_extra_system_prompt,
        )

        if not self.llm_api or not self.llm_api.tools:
            return

        if get_conversation_status(hass).get("is_internal_llm"):
            LOGGER.debug("Internal LLM mode: keep native tools and do not inject enhanced tools")
            return

        original_count = len(self.llm_api.tools)
        filtered_tools = build_runtime_tool_list()
        self.llm_api = llm.APIInstance(
            api=self.llm_api.api,
            api_prompt=self.llm_api.api_prompt,
            llm_context=self.llm_api.llm_context,
            tools=filtered_tools,
            custom_serializer=self.llm_api.custom_serializer,
        )
        LOGGER.debug(
            "External AI tool list switched to the registry-driven tool surface: %s -> %s",
            original_count,
            len(filtered_tools),
        )

    chat_log_module.ChatLog.async_update_llm_data = patched_async_update_llm_data
    setattr(
        chat_log_module.ChatLog,
        _CHATLOG_TOOLS_ORIGINAL_KEY,
        original_async_update_llm_data,
    )
    setattr(chat_log_module.ChatLog, _PATCH_KEY, True)
    LOGGER.debug("ChatLog switched to the centralized internal LLM tool surface")


def _unpatch_chatlog_tools() -> None:

    from homeassistant.components.conversation import chat_log as chat_log_module

    original_async_update_llm_data = getattr(
        chat_log_module.ChatLog, _CHATLOG_TOOLS_ORIGINAL_KEY, None
    )
    if original_async_update_llm_data is None:
        return

    chat_log_module.ChatLog.async_update_llm_data = original_async_update_llm_data
    delattr(chat_log_module.ChatLog, _CHATLOG_TOOLS_ORIGINAL_KEY)
    if hasattr(chat_log_module.ChatLog, _PATCH_KEY):
        delattr(chat_log_module.ChatLog, _PATCH_KEY)
    LOGGER.debug("ChatLog tool surface restored")


async def async_setup_internal_llm(hass: HomeAssistant) -> None:

    runtime_store = get_runtime_store(hass)
    patch_snapshot = runtime_store.get(_RUNTIME_PATCH_SNAPSHOT_KEY)
    if not _is_valid_patch_snapshot(patch_snapshot):
        patch_snapshot = _capture_patch_snapshot()
    _unpatch_chatlog_tools()
    _unpatch_tool_call_tracking()
    _unpatch_assist_api_prompt()
    async_unregister_enhanced_api()
    _restore_patch_snapshot(patch_snapshot)
    runtime_store[_RUNTIME_PATCH_SNAPSHOT_KEY] = patch_snapshot
    async_register_enhanced_api(hass)
    get_conversation_status(hass)["llm_api_id"] = CUSTOM_API_ID
    _patch_assist_api_prompt(hass)
    _patch_tool_call_tracking(hass)
    patch_chatlog_tools(hass)


def async_unload_internal_llm(hass: HomeAssistant) -> None:

    _unpatch_chatlog_tools()
    _unpatch_tool_call_tracking()
    _unpatch_assist_api_prompt()
    async_unregister_enhanced_api()
    runtime_store = get_runtime_store(hass)
    patch_snapshot = runtime_store.pop(_RUNTIME_PATCH_SNAPSHOT_KEY, None)
    if not _is_valid_patch_snapshot(patch_snapshot):
        return

    _restore_patch_snapshot(patch_snapshot)
