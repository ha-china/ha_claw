from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .state import get_config_approval_state

_UNSUB_KEY = "im_approval_bridge_unsubs"
_MAX_HISTORY = 10
_QQ_APPROVAL_EVENT = "cn_im_hub_qq_approval_resolved"
_QQ_INTERACTION_EVENT = "cn_im_hub_qq_interaction"
_QQ_GROUP_PROACTIVE_EVENT = "cn_im_hub_qq_group_proactive_status"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _push_history(items: list[dict[str, Any]], value: dict[str, Any]) -> None:
    items.insert(0, value)
    del items[_MAX_HISTORY:]


@callback
def async_setup_im_approval_bridge(hass: HomeAssistant) -> None:
    if _UNSUB_KEY in hass.data:
        return

    state = get_config_approval_state(hass)
    state.setdefault("qq_approval_history", [])
    state.setdefault("qq_group_proactive_history", [])
    state.setdefault("qq_last_interaction", {})

    @callback
    def _handle_approval(event) -> None:
        payload = dict(event.data or {})
        entry = {
            "provider": payload.get("provider", "qq"),
            "approval_id": str(payload.get("approval_id") or ""),
            "decision": str(payload.get("decision") or ""),
            "user_id": str(payload.get("user_id") or ""),
            "group_id": str(payload.get("group_id") or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "resolved_at": _now_iso(),
        }
        _push_history(state["qq_approval_history"], entry)
        state["qq_last_resolution"] = entry

    @callback
    def _handle_interaction(event) -> None:
        payload = dict(event.data or {})
        state["qq_last_interaction"] = {
            "provider": payload.get("provider", "qq"),
            "button_data": str(payload.get("button_data") or ""),
            "user_id": str(payload.get("user_id") or ""),
            "group_id": str(payload.get("group_id") or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "seen_at": _now_iso(),
        }

    @callback
    def _handle_group_status(event) -> None:
        payload = dict(event.data or {})
        entry = {
            "provider": payload.get("provider", "qq"),
            "group_id": str(payload.get("group_id") or ""),
            "status": str(payload.get("status") or ""),
            "operator_id": str(payload.get("operator_id") or ""),
            "updated_at": _now_iso(),
        }
        _push_history(state["qq_group_proactive_history"], entry)
        state["qq_last_group_proactive_status"] = entry

    hass.data[_UNSUB_KEY] = [
        hass.bus.async_listen(_QQ_APPROVAL_EVENT, _handle_approval),
        hass.bus.async_listen(_QQ_INTERACTION_EVENT, _handle_interaction),
        hass.bus.async_listen(_QQ_GROUP_PROACTIVE_EVENT, _handle_group_status),
    ]


@callback
def async_unload_im_approval_bridge(hass: HomeAssistant) -> None:
    unsubs = hass.data.pop(_UNSUB_KEY, [])
    for unsub in unsubs:
        unsub()


def build_im_approval_prompt_block(hass: HomeAssistant) -> str:
    state = get_config_approval_state(hass)
    last_resolution = state.get("qq_last_resolution") or {}
    last_group_status = state.get("qq_last_group_proactive_status") or {}
    if not last_resolution and not last_group_status:
        return ""

    lines = ["## IM Approval And Delivery State"]
    if last_resolution:
        lines.append(
            "Last QQ approval: "
            f"approval_id={last_resolution.get('approval_id')} "
            f"decision={last_resolution.get('decision')} "
            f"user={last_resolution.get('user_id') or '-'} "
            f"group={last_resolution.get('group_id') or '-'}"
        )
    if last_group_status:
        lines.append(
            "Last QQ proactive group status: "
            f"group={last_group_status.get('group_id')} "
            f"status={last_group_status.get('status')} "
            f"operator={last_group_status.get('operator_id') or '-'}"
        )
    lines.append(
        "Use this as delivery context only. Do not restate it to the user unless they ask."
    )
    return "\n".join(lines)
