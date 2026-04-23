from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .heartbeat_store import get_due_tasks, async_record_heartbeat_result, HeartbeatTask

LOGGER = logging.getLogger(__name__)
_TICK_INTERVAL = timedelta(seconds=10)
_UNSUB_KEY = "heartbeat_ticker_unsub"

_HEARTBEAT_SYSTEM_PROMPT = (
    "You are a background heartbeat agent running an automated task. "
    "RULES: "
    "1. NEVER call the Notify tool. Your reply IS the notification — it will be auto-delivered to the user. "
    "2. Use other tools (HAControl, SmartDiscovery, etc.) if the task requires device data. "
    "3. Reply with SHORT, user-facing Chinese text only. No markdown, no questions. "
    "4. If you see [AUTO_DELIVER:wechat], your reply goes straight to WeChat — just write the message content."
)


def _build_heartbeat_text(task: HeartbeatTask) -> str:
    parts = [f"[heartbeat:{task.slug}]"]
    if task.objective:
        parts.append(task.objective)
    if task.steps:
        parts.append(f"Steps: {task.steps}")
    if task.notify_channel:
        if task.notify_channel.startswith("wechat:"):
            parts.append("[AUTO_DELIVER:wechat] Your reply text will be sent to WeChat automatically. Do NOT call Notify.")
        else:
            parts.append(f"[AUTO_DELIVER:{task.notify_channel}]")
    return " ".join(parts)


async def _tick(hass: HomeAssistant, _now: Any = None) -> None:
    due_tasks = await hass.async_add_executor_job(get_due_tasks)
    if not due_tasks:
        return

    from .state import get_runtime_store

    from homeassistant.components.conversation import agent_manager

    runtime_store = get_runtime_store(hass)
    if not runtime_store.get("original_async_converse"):
        LOGGER.warning("Heartbeat ticker: runtime hook not ready")
        return

    for task in due_tasks:
        LOGGER.info("Heartbeat due: %s — %s", task.slug, task.objective)
        try:
            text = _build_heartbeat_text(task)
            result = await agent_manager.async_converse(
                hass,
                text,
                f"heartbeat_{task.slug}",
                hass.data.get("claw_assistant_context"),
                None,
                None,
                None,
                None,
                _HEARTBEAT_SYSTEM_PROMPT,
            )
            speech = ""
            if result.response.speech:
                speech = (
                    result.response.speech.get("plain", {}).get("speech", "")
                    if isinstance(result.response.speech, dict)
                    else ""
                )
            status = "success" if speech else "executed"
            await async_record_heartbeat_result(
                hass, slug=task.slug, status=status, note="auto-tick"
            )
            if speech and task.notify_channel:
                await _push_to_channel(hass, task.notify_channel, speech)
        except Exception:
            LOGGER.exception("Heartbeat tick failed for %s", task.slug)
            await async_record_heartbeat_result(
                hass, slug=task.slug, status="error", note="auto-tick failed"
            )


async def _push_to_channel(hass: HomeAssistant, channel: str, message: str) -> None:
    try:
        if not hass.services.has_service("cn_im_hub", "send_message"):
            LOGGER.warning("cn_im_hub.send_message not available yet, skipping push")
            return
        parts = channel.split(":", 2)
        provider = parts[0] if parts else ""
        if provider == "wechat":
            svc_data: dict[str, str] = {
                "channel": "wechat/user_id",
                "message": message,
            }
            if len(parts) >= 2:
                svc_data["wechat_account_id"] = parts[1]
            if len(parts) >= 3:
                svc_data["target"] = parts[2]
            await hass.services.async_call(
                "cn_im_hub", "send_message", svc_data, blocking=True,
            )
        else:
            LOGGER.warning("Unknown notify_channel format: %s", channel)
    except Exception:
        LOGGER.exception("Failed to push heartbeat result to channel: %s", channel)


@callback
def async_setup_heartbeat_ticker(hass: HomeAssistant) -> None:
    if _UNSUB_KEY in hass.data:
        return

    @callback
    def _schedule_tick(now: Any) -> None:
        hass.async_create_task(_tick(hass, now), "heartbeat_tick")

    unsub = async_track_time_interval(hass, _schedule_tick, _TICK_INTERVAL)
    hass.data[_UNSUB_KEY] = unsub

    async def _deferred_initial_tick() -> None:
        from .state import get_runtime_store
        for _ in range(60):
            hook_ready = get_runtime_store(hass).get("original_async_converse")
            svc_ready = hass.services.has_service("cn_im_hub", "send_message")
            if hook_ready and svc_ready:
                break
            await asyncio.sleep(1)
        await _tick(hass)

    hass.async_create_task(_deferred_initial_tick(), "heartbeat_tick_initial")


@callback
def async_unload_heartbeat_ticker(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_UNSUB_KEY, None)
    if unsub:
        unsub()
