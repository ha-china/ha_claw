from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

PLATFORM_AVA_SATELLITE = "ava_satellite"

_AVA_MANUFACTURERS = frozenset({"ava", "voice assistant"})
_AVA_MODEL_MARKERS = ("ava.voice_satellite", "ava_android")
_AVA_ENTITY_SUFFIXES = frozenset(
    {"voice_command", "assistant_response", "mute_microphone"}
)

_AVA_CONTROL_SUFFIXES = frozenset({
    "screen_toggle",
    "mute_microphone",
    "lock_screen",
    "restart_service",
    "media_player",
    "microphone_volume",
    "play_wake_sound",
    "stop_alarm",
})

_AVA_VOICE_INTENTS: tuple[tuple[str, str], ...] = (
    (
        "亮屏 / 开屏幕 / 打开屏幕 / 点亮屏幕 / wake screen",
        "switch.turn_on → screen_toggle on THIS device",
    ),
    (
        "关屏 / 熄屏 / 关闭屏幕 / screen off",
        "switch.turn_off → screen_toggle on THIS device",
    ),
    (
        "锁屏 / 锁定屏幕",
        "lock.lock → lock_screen on THIS device",
    ),
    (
        "解锁 / 解除锁屏",
        "lock.unlock → lock_screen on THIS device",
    ),
    (
        "静音 / 关闭麦克风 / mute",
        "switch.turn_on → mute_microphone on THIS device",
    ),
    (
        "取消静音 / 打开麦克风 / unmute",
        "switch.turn_off → mute_microphone on THIS device",
    ),
    (
        "重启语音服务 / 重启 Ava 服务",
        "button.press → restart_service on THIS device",
    ),
    (
        "停止闹钟",
        "button.press → stop_alarm on THIS device",
    ),
    (
        "停止对话 / 别说了 / stop",
        "adb broadcast com.example.ava.ACTION_STOP (or end voice session)",
    ),
    (
        "唤醒设备 / 点亮",
        "switch.turn_on screen_toggle, or adb com.example.ava.ACTION_WAKE",
    ),
    (
        "调大/调小麦克风音量",
        "number.set_value → microphone_volume on THIS device",
    ),
    (
        "播放 / 暂停 / 下一首 / 音量",
        "media_player.* → media_player on THIS device",
    ),
)

_ADB_CONTROL_ACTIONS: tuple[tuple[str, str], ...] = (
    ("ACTION_WAKE", "Wake screen / 亮屏 fallback"),
    ("ACTION_STOP", "Stop current voice session"),
    ("ACTION_MUTE_MIC", "Mute microphone"),
    ("ACTION_UNMUTE_MIC", "Unmute microphone"),
    ("ACTION_TOGGLE_MIC", "Toggle microphone mute"),
    ("ACTION_START_SERVICE", "Start Ava voice satellite service"),
    ("ACTION_STOP_SERVICE", "Stop Ava voice satellite service"),
)

_ADB_GRANT_ACTIONS: tuple[tuple[str, str], ...] = (
    ("ACTION_GRANT_RECORD_AUDIO", "RECORD_AUDIO"),
    ("ACTION_GRANT_CAMERA", "CAMERA"),
    ("ACTION_GRANT_LOCATION", "Location permissions"),
    ("ACTION_GRANT_BLUETOOTH", "BT_SCAN / CONNECT / ADVERTISE + location"),
    ("ACTION_GRANT_NOTIFICATIONS", "POST_NOTIFICATIONS"),
    ("ACTION_GRANT_OVERLAY", "SYSTEM_ALERT_WINDOW via appops"),
    ("ACTION_GRANT_WRITE_SETTINGS", "WRITE_SETTINGS via appops"),
    ("ACTION_GRANT_SECURE_SETTINGS", "WRITE_SECURE_SETTINGS"),
    ("ACTION_GRANT_INSTALL_PACKAGES", "REQUEST_INSTALL_PACKAGES via appops"),
    ("ACTION_ACTIVATE_DEVICE_ADMIN", "dpm set-active-admin"),
)


def detect_ava_identity(
    hass: HomeAssistant,
    *,
    satellite_id: str | None = None,
    device_id: str | None = None,
) -> dict[str, Any] | None:
    resolved_device_id = device_id
    resolved_satellite_id = satellite_id

    ent_reg = er.async_get(hass)
    if satellite_id:
        entry = ent_reg.async_get(satellite_id)
        if entry is None:
            return None
        if entry.domain != "assist_satellite":
            return None
        if entry.device_id:
            resolved_device_id = entry.device_id
        resolved_satellite_id = satellite_id

    if not resolved_device_id:
        return None

    device = dr.async_get(hass).async_get(resolved_device_id)
    if device is None:
        return None

    manufacturer = (device.manufacturer or "").strip().lower()
    model = (device.model or "").strip().lower()
    verified = _matches_ava_registry(manufacturer, model) or _device_has_ava_fingerprint(
        hass, resolved_device_id
    )
    if not verified:
        return None

    context = _resolve_device_context(hass, device)
    control_entities = _collect_control_entities(hass, resolved_device_id)
    return {
        "verified": True,
        "platform": PLATFORM_AVA_SATELLITE,
        "device_id": resolved_device_id,
        "satellite_id": resolved_satellite_id,
        "name": device.name or "",
        "manufacturer": device.manufacturer or "",
        "model": device.model or "",
        "sw_version": device.sw_version or "",
        "area": context.get("area") or "",
        "esphome_host": context.get("esphome_host") or "",
        "control_entities": control_entities,
    }


def apply_ava_identity(conv_status: dict[str, Any], identity: dict[str, Any]) -> None:
    conv_status["detected_platform"] = PLATFORM_AVA_SATELLITE
    conv_status["_ava_identity"] = identity
    conv_status["is_voice_pipeline"] = True


def build_ava_voice_system_prompt(identity: dict[str, Any]) -> str:
    name = identity.get("name") or "Ava voice satellite"
    model = identity.get("model") or "Android"
    sw_version = identity.get("sw_version") or ""
    area = identity.get("area") or ""
    satellite_id = identity.get("satellite_id") or ""
    esphome_host = identity.get("esphome_host") or ""
    control_entities = identity.get("control_entities") or {}

    device_facts = [f"Device name in HA: {name}", "Platform: Android Ava voice satellite"]
    if model:
        device_facts.append(f"Registry model: {model}")
    if sw_version:
        device_facts.append(f"Registry firmware: {sw_version}")
    if area:
        device_facts.append(f"Area: {area}")
    if satellite_id:
        device_facts.append(f"Satellite entity: {satellite_id}")
    if esphome_host:
        device_facts.append(f"ESPHome host (LAN IP hint): {esphome_host}")
    facts_block = "\n".join(f"- {line}" for line in device_facts)

    entities_block = _format_control_entities_block(control_entities)
    intents_block = _format_voice_intents_block(control_entities)
    adb_control_block = _format_adb_control_block()

    host_hint = esphome_host or "<device LAN IP>"
    adb_connect = (
        f"Wireless: adb connect {host_hint}:5555. "
        "USB: developer options + USB debugging, then adb devices."
    )

    grant_block = "\n".join(
        f"- adb shell am broadcast -a com.example.ava.{action}  ({permission})"
        for action, permission in _ADB_GRANT_ACTIONS
    )

    host_for_code = esphome_host or "REPLACE_WITH_DEVICE_IP"
    adb_python_template = (
        "import subprocess, shlex\n"
        f"HOST = \"{host_for_code}\"   # this Ava device's LAN host/IP\n"
        "def adb(args, timeout=8):\n"
        "    cmd = [\"adb\", \"-s\", f\"{HOST}:5555\"] + shlex.split(args) if isinstance(args, str) else [\"adb\", \"-s\", f\"{HOST}:5555\"] + args\n"
        "    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)\n"
        "    return {\"rc\": p.returncode, \"out\": p.stdout.strip(), \"err\": p.stderr.strip()}\n"
        "subprocess.run([\"adb\", \"connect\", f\"{HOST}:5555\"], capture_output=True, text=True, timeout=5)\n"
        "# 亮屏: adb shell input keyevent 224 (KEYCODE_WAKEUP)\n"
        "# 熄屏: adb shell input keyevent 223\n"
        "# Ava 广播:\n"
        "# adb(\"shell am broadcast -a com.example.ava.ACTION_WAKE\")\n"
        "# adb(\"shell am broadcast -a com.example.ava.ACTION_MUTE_MIC\")\n"
        "# 状态探测:\n"
        "# adb(\"shell dumpsys power | grep -E 'mWakefulness|Display Power'\")\n"
        "# adb(\"shell dumpsys battery\")\n"
        "# adb(\"shell ip -4 addr show wlan0\")\n"
        "print(adb(\"shell am broadcast -a com.example.ava.ACTION_WAKE\"))"
    )

    return (
        "## Self-Identity (你是谁)\n"
        "你就是这台 Ava Android 设备上的语音 AI。这一轮对话不是泛化助手 —— "
        "用户的声音由这台设备的麦克风采集，你的回复会从这台设备的扬声器播放。"
        "「我」「这台设备」「本机」在本轮指同一个东西：下面这台机器。\n"
        f"{facts_block}\n"
        "## Self-Capability (你能做什么)\n"
        "你拥有真实的执行能力，不是只能说话。你可以：\n"
        "- 调 Home Assistant 服务（ServiceCall / IntentCall 等工具）控制本机的实体。\n"
        "- 用 ExecutePython 工具跑 Python，subprocess 调 `adb` 真正连这台设备并下发命令。\n"
        "  ExecutePython 在 HA 主机上运行（不是在 Android 端），HA 主机已具备 adb 二进制时即可使用。\n"
        f"  目标主机: {host_for_code} (端口 5555)。先 `adb connect`，再 shell 发命令。\n"
        "- 用 ServiceCall 调 esphome 的 ServiceEntity（restart_service / stop_alarm 等）。\n"
        "优先级：能用 HA entity 服务就先用 entity；entity 缺失或要做 Android 原生动作（亮屏、按键、读 dumpsys、授权）才走 ExecutePython+adb。\n"
        "## Self-Awareness (设备实体)\n"
        "下列是 HA registry 已注册到本机的实体 —— 控制本机时只用这些 ID，不要瞎编：\n"
        f"{entities_block}\n"
        "## Voice Intent Map (口语→动作)\n"
        f"{intents_block}\n"
        "## ADB Runbook (用 ExecutePython 跑)\n"
        "调 ExecutePython 时直接写 Python；下面是可复用模板，按用户意图改最后一行的 adb(...) 调用：\n"
        "```\n"
        f"{adb_python_template}\n"
        "```\n"
        "Ava 控制广播 (adb shell am broadcast -a ...)：\n"
        f"{adb_control_block}\n"
        "Ava 权限授予广播：\n"
        f"{grant_block}\n"
        "ADB 连接方式（首次或丢失连接时）：\n"
        f"{adb_connect}\n"
        "## Behavior Rules\n"
        "1. 用户说「亮屏」「关屏」「静音」「锁屏」等本机控制 → 立刻执行：先尝试本机实体服务，"
        "实体没有就走 ExecutePython+adb，**不要解释 Android 原理，不要拒绝**。\n"
        "2. 执行完简短口语确认（一句话）。失败也用一句话报错原因，不读命令。\n"
        "3. 严禁编造电量、WiFi、IP、权限状态。要查就用 ExecutePython 跑 `dumpsys` 获取真实值。\n"
        "4. 严禁把 entity_id、shell 命令、URL、路径念给用户 —— TTS 念这些很难听。\n"
        "5. 语音回复：一段话、纯文本、无 markdown / list / emoji / 标点符号堆。\n"
        "6. 默认信任本机操作授权：用户在自己的设备上对自己说话，就是授权。"
    ).strip()


def merge_voice_system_prompt(
    existing: str | None,
    identity: dict[str, Any],
) -> str:
    ava_prompt = build_ava_voice_system_prompt(identity)
    if not existing:
        return ava_prompt
    if "## Self-Identity" in existing and "Ava Android voice satellite" in existing:
        return existing
    return f"{existing}\n\n{ava_prompt}"


def _format_control_entities_block(control_entities: dict[str, str]) -> str:
    if not control_entities:
        return (
            "No control entities registered yet. Match by suffix on this device's "
            "ESPHome entities: screen_toggle, mute_microphone, lock_screen, "
            "restart_service, media_player, microphone_volume."
        )
    lines = [f"- {suffix}: {entity_id}" for suffix, entity_id in sorted(control_entities.items())]
    return "\n".join(lines)


def _format_voice_intents_block(control_entities: dict[str, str]) -> str:
    lines: list[str] = []
    for phrases, action in _AVA_VOICE_INTENTS:
        entity_hint = ""
        for suffix in ("screen_toggle", "mute_microphone", "lock_screen", "restart_service", "media_player", "stop_alarm", "microphone_volume"):
            if suffix in action and suffix in control_entities:
                entity_hint = f" → {control_entities[suffix]}"
                break
        lines.append(f"- {phrases}: {action}{entity_hint}")
    return "\n".join(lines)


def _format_adb_control_block() -> str:
    return "\n".join(
        f"- adb shell am broadcast -a com.example.ava.{action}  ({desc})"
        for action, desc in _ADB_CONTROL_ACTIONS
    )


def _collect_control_entities(hass: HomeAssistant, device_id: str) -> dict[str, str]:
    ent_reg = er.async_get(hass)
    controls: dict[str, str] = {}
    for entry in ent_reg.entities.get_entities_for_device_id(device_id):
        if entry.platform != "esphome" or not entry.entity_id:
            continue
        suffix = entry.entity_id.rsplit(".", 1)[-1]
        if suffix in _AVA_CONTROL_SUFFIXES:
            controls[suffix] = entry.entity_id
    return controls


def _resolve_device_context(hass: HomeAssistant, device: dr.DeviceEntry) -> dict[str, str]:
    context: dict[str, str] = {}
    if device.area_id:
        area = ar.async_get(hass).async_get_area(device.area_id)
        if area and area.name:
            context["area"] = area.name
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != "esphome":
            continue
        host = entry.data.get("host")
        if host:
            context["esphome_host"] = str(host)
            break
    return context


def _matches_ava_registry(manufacturer: str, model: str) -> bool:
    if manufacturer == "ava":
        return True
    if "ava.voice_satellite" in model:
        return True
    if manufacturer in _AVA_MANUFACTURERS and any(
        marker in model for marker in _AVA_MODEL_MARKERS
    ):
        return True
    return False


def _device_has_ava_fingerprint(hass: HomeAssistant, device_id: str) -> bool:
    ent_reg = er.async_get(hass)
    suffixes: set[str] = set()
    for entry in ent_reg.entities.get_entities_for_device_id(device_id):
        if entry.platform != "esphome":
            continue
        entity_id = entry.entity_id or ""
        suffix = entity_id.rsplit(".", 1)[-1]
        if suffix in _AVA_ENTITY_SUFFIXES:
            suffixes.add(suffix)
        unique_id = (entry.unique_id or "").lower()
        for marker in _AVA_ENTITY_SUFFIXES:
            if unique_id.endswith(marker):
                suffixes.add(marker)
    return "voice_command" in suffixes and (
        "assistant_response" in suffixes or "mute_microphone" in suffixes
    )
