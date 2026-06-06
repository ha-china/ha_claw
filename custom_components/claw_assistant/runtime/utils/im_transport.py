from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

_IM_DOMAIN = "cn_im_hub"
_IM_SEND_SERVICE = "send_message"


def has_im_transport(hass: HomeAssistant) -> bool:
    return hass.services.has_service(_IM_DOMAIN, _IM_SEND_SERVICE)


def channel_provider(channel: str) -> str:
    return channel.split(":", 1)[0].strip() if channel else ""


def build_im_service_data(channel: str, message: str = "") -> dict[str, str]:
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
        return svc_data
    if provider == "qq":
        if len(parts) < 3 or parts[1] not in ("user", "group", "channel"):
            raise ValueError("QQ target must be qq:user:openid, qq:group:group_openid, or qq:channel:channel_id")
        return {
            "channel": f"qq/{parts[1]}",
            "message": message,
            "target": parts[2],
        }
    raise ValueError(f"Unknown notify channel format: {channel}")


async def async_send_im_payload(
    hass: HomeAssistant,
    channel: str,
    *,
    message: str = "",
    camera_entity: str = "",
    media_type: str = "",
    tts_text: str = "",
) -> None:
    if not has_im_transport(hass):
        raise RuntimeError("cn_im_hub.send_message not available")

    payload: dict[str, Any] = build_im_service_data(channel, message)
    if camera_entity:
        payload["camera_entity"] = camera_entity
    if media_type:
        payload["media_type"] = media_type
    if tts_text:
        payload["tts_text"] = tts_text

    await hass.services.async_call(
        _IM_DOMAIN,
        _IM_SEND_SERVICE,
        payload,
        blocking=True,
    )
