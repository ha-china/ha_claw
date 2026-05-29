"""Delegation command handler for /ooo.

/ooo is a hint to the main AI to use DelegateTask tool for background execution.
When user types /ooo <task>, the command is rewritten and passed to the AI,
which then decides whether to use DelegateTask or handle it directly.

Usage:
  /ooo <task>           - Tell AI to handle this task (may use DelegateTask)
  /ooo list             - List active subagents
  /ooo stop <task_id>   - Stop a running subagent
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from ..runtime.utils.i18n import t

_LOGGER = logging.getLogger(__name__)


async def handle_delegate_command(
    hass: HomeAssistant,
    *,
    args: str,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Handle the /ooo command.
    
    Returns a dict with:
      - response: Text response to show user (if handled directly)
      - handled: True if command was fully handled here
      - rewrite: Rewritten text to pass to AI (if not handled here)
    """
    args = (args or "").strip()
    lang = language or hass.config.language
    
    if not args or args.lower() == "help":
        return _help_response(lang)
    
    if args.lower() == "list":
        return await _list_subagents(lang)
    
    if args.lower().startswith("stop "):
        task_id = args[5:].strip()
        return await _stop_subagent(task_id, lang)
    
    return _rewrite_for_ai(args, lang)


def _help_response(language: str | None) -> dict[str, Any]:
    """Return help text."""
    title = t("delegation_help_title", language)
    usage = t("delegation_help_usage", language)
    start_desc = t("delegation_help_start", language)
    list_desc = t("delegation_help_list", language)
    stop_desc = t("delegation_help_stop", language)
    examples = t("delegation_help_examples", language)
    description = t("delegation_help_description", language)
    
    help_text = f"""**{title}**

**{usage}:**
- `/ooo <task>` - {start_desc}
- `/ooo list` - {list_desc}
- `/ooo stop <task_id>` - {stop_desc}

**{examples}:**
```
/ooo Search for the latest Python 3.12 features and summarize
/ooo Analyze the structure of config/configuration.yaml
/ooo list
/ooo stop sa-abc123
```

**{description}**"""
    
    return {
        "response": help_text,
        "handled": True,
        "continue_conversation": False,
    }


async def _list_subagents(language: str | None) -> dict[str, Any]:
    """List active subagents."""
    from .executor import list_active_subagents
    
    subagents = await list_active_subagents()
    
    if not subagents:
        return {
            "response": t("delegation_no_active", language),
            "handled": True,
            "continue_conversation": False,
        }
    
    tool_calls_label = t("delegation_tool_calls", language)
    lines = [f"**{t('delegation_active_header', language)}**\n"]
    for sa in subagents:
        status_icon = {
            "pending": "⚪️",
            "running": "⚫️",
            "interrupting": "🟤",
        }.get(sa["status"], "⚪️")
        
        goal_preview = sa["goal"][:50] + "..." if len(sa["goal"]) > 50 else sa["goal"]
        lines.append(
            f"- {status_icon} `{sa['task_id']}` - {goal_preview} "
            f"({tool_calls_label}: {sa['tool_count']})"
        )
    
    return {
        "response": "\n".join(lines),
        "handled": True,
        "continue_conversation": False,
    }


async def _stop_subagent(task_id: str, language: str | None) -> dict[str, Any]:
    """Stop a running subagent."""
    from .executor import interrupt_subagent
    
    if not task_id:
        return {
            "response": t("delegation_stop_no_id", language),
            "handled": True,
            "continue_conversation": False,
        }
    
    success = await interrupt_subagent(task_id)
    
    if success:
        msg = t("delegation_stop_requested", language).format(task_id=task_id)
        return {
            "response": msg,
            "handled": True,
            "continue_conversation": False,
        }
    else:
        msg = t("delegation_stop_not_found", language).format(task_id=task_id)
        return {
            "response": msg,
            "handled": True,
            "continue_conversation": False,
        }


def _rewrite_for_ai(task: str, language: str | None) -> dict[str, Any]:
    """Rewrite /ooo command as instruction for AI to use DelegateTask tool.
    
    The AI receives this as a user message and should call DelegateTask tool
    to spawn a subagent. The subagent runs independently and returns results.
    """
    instruction = t("delegation_ooo_instruction", language)
    rewritten = f"{instruction}\n\n{task}"
    
    return {
        "response": None,
        "handled": False,
        "rewrite": rewritten,
    }
