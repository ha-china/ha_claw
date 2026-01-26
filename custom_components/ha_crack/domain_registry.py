"""Domain Registry - Home Assistant 域和服务注册表
借鉴 mcp-assist 的设计，提供服务参数验证和智能提示
"""
from __future__ import annotations
import logging
from typing import Dict, List, Set, Any, Optional
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)

TYPE_CONTROLLABLE = "controllable"
TYPE_READ_ONLY = "read_only"
TYPE_SERVICE_ONLY = "service_only"

PRIORITY_ESSENTIAL = 1
PRIORITY_COMMON = 2
PRIORITY_STANDARD = 3
PRIORITY_EXTENDED = 4
PRIORITY_SPECIALIZED = 5


@dataclass
class ServiceParam:

    name: str
    description: str
    required: bool = False
    param_type: str = "string"
    default: Any = None
    enum: List[Any] = None
    min_value: float = None
    max_value: float = None


@dataclass
class ServiceDef:

    name: str
    description: str
    params: List[ServiceParam] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)


@dataclass
class DomainDef:

    domain: str
    domain_type: str
    priority: int
    description: str
    services: List[ServiceDef] = field(default_factory=list)
    device_classes: List[str] = field(default_factory=list)


DOMAIN_REGISTRY: Dict[str, DomainDef] = {}


def _register_domains():

    global DOMAIN_REGISTRY
    
    
    DOMAIN_REGISTRY["light"] = DomainDef(
        domain="light",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_ESSENTIAL,
        description="灯光控制",
        services=[
            ServiceDef(
                name="turn_on",
                description="开灯",
                aliases=["on"],
                params=[
                    ServiceParam("brightness", "亮度 (0-255)", param_type="number", min_value=0, max_value=255),
                    ServiceParam("brightness_pct", "亮度百分比 (0-100)", param_type="number", min_value=0, max_value=100),
                    ServiceParam("color_temp", "色温 (Kelvin)", param_type="number"),
                    ServiceParam("color_temp_kelvin", "色温 (Kelvin)", param_type="number", min_value=2000, max_value=6500),
                    ServiceParam("rgb_color", "RGB颜色 [r,g,b]", param_type="array"),
                    ServiceParam("hs_color", "HS颜色 [hue, saturation]", param_type="array"),
                    ServiceParam("transition", "过渡时间(秒)", param_type="number", min_value=0),
                    ServiceParam("effect", "灯光效果", param_type="string"),
                ]
            ),
            ServiceDef(name="turn_off", description="关灯", aliases=["off"]),
            ServiceDef(name="toggle", description="切换开关状态"),
        ],
        device_classes=["light"]
    )
    
    DOMAIN_REGISTRY["switch"] = DomainDef(
        domain="switch",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_ESSENTIAL,
        description="开关控制",
        services=[
            ServiceDef(name="turn_on", description="打开", aliases=["on"]),
            ServiceDef(name="turn_off", description="关闭", aliases=["off"]),
            ServiceDef(name="toggle", description="切换"),
        ],
        device_classes=["outlet", "switch"]
    )
    
    DOMAIN_REGISTRY["climate"] = DomainDef(
        domain="climate",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_ESSENTIAL,
        description="空调/温控",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(
                name="set_temperature",
                description="设置温度",
                params=[
                    ServiceParam("temperature", "目标温度", required=True, param_type="number"),
                    ServiceParam("target_temp_high", "最高温度", param_type="number"),
                    ServiceParam("target_temp_low", "最低温度", param_type="number"),
                    ServiceParam("hvac_mode", "模式", param_type="string", 
                               enum=["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]),
                ]
            ),
            ServiceDef(
                name="set_hvac_mode",
                description="设置模式",
                params=[
                    ServiceParam("hvac_mode", "模式", required=True, param_type="string",
                               enum=["off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"]),
                ]
            ),
            ServiceDef(
                name="set_fan_mode",
                description="设置风速",
                params=[
                    ServiceParam("fan_mode", "风速", required=True, param_type="string",
                               enum=["auto", "low", "medium", "high"]),
                ]
            ),
        ]
    )
    
    DOMAIN_REGISTRY["cover"] = DomainDef(
        domain="cover",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_ESSENTIAL,
        description="窗帘/卷帘",
        services=[
            ServiceDef(name="open_cover", description="打开", aliases=["open"]),
            ServiceDef(name="close_cover", description="关闭", aliases=["close"]),
            ServiceDef(name="stop_cover", description="停止", aliases=["stop"]),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(
                name="set_cover_position",
                description="设置位置",
                params=[
                    ServiceParam("position", "位置 (0-100)", required=True, param_type="number", min_value=0, max_value=100),
                ]
            ),
            ServiceDef(
                name="set_cover_tilt_position",
                description="设置倾斜角度",
                params=[
                    ServiceParam("tilt_position", "倾斜 (0-100)", required=True, param_type="number", min_value=0, max_value=100),
                ]
            ),
        ],
        device_classes=["awning", "blind", "curtain", "damper", "door", "garage", "gate", "shade", "shutter", "window"]
    )
    
    
    DOMAIN_REGISTRY["fan"] = DomainDef(
        domain="fan",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_COMMON,
        description="风扇",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(
                name="set_percentage",
                description="设置风速百分比",
                params=[
                    ServiceParam("percentage", "风速 (0-100)", required=True, param_type="number", min_value=0, max_value=100),
                ]
            ),
            ServiceDef(name="oscillate", description="摆头", params=[
                ServiceParam("oscillating", "是否摆头", required=True, param_type="boolean"),
            ]),
            ServiceDef(
                name="set_direction",
                description="设置方向",
                params=[
                    ServiceParam("direction", "方向", required=True, param_type="string", enum=["forward", "reverse"]),
                ]
            ),
        ]
    )
    
    DOMAIN_REGISTRY["media_player"] = DomainDef(
        domain="media_player",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_COMMON,
        description="媒体播放器",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(name="media_play", description="播放", aliases=["play"]),
            ServiceDef(name="media_pause", description="暂停", aliases=["pause"]),
            ServiceDef(name="media_stop", description="停止", aliases=["stop"]),
            ServiceDef(name="media_next_track", description="下一曲", aliases=["next"]),
            ServiceDef(name="media_previous_track", description="上一曲", aliases=["previous"]),
            ServiceDef(
                name="volume_set",
                description="设置音量",
                params=[
                    ServiceParam("volume_level", "音量 (0-1)", required=True, param_type="number", min_value=0, max_value=1),
                ]
            ),
            ServiceDef(name="volume_up", description="音量+"),
            ServiceDef(name="volume_down", description="音量-"),
            ServiceDef(name="volume_mute", description="静音", params=[
                ServiceParam("is_volume_muted", "是否静音", required=True, param_type="boolean"),
            ]),
            ServiceDef(
                name="play_media",
                description="播放媒体",
                params=[
                    ServiceParam("media_content_id", "媒体ID/URL", required=True, param_type="string"),
                    ServiceParam("media_content_type", "媒体类型", required=True, param_type="string",
                               enum=["music", "video", "image", "playlist", "channel"]),
                ]
            ),
        ],
        device_classes=["tv", "speaker", "receiver"]
    )
    
    DOMAIN_REGISTRY["lock"] = DomainDef(
        domain="lock",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_COMMON,
        description="门锁",
        services=[
            ServiceDef(name="lock", description="上锁"),
            ServiceDef(name="unlock", description="解锁"),
            ServiceDef(name="open", description="打开（支持的锁）"),
        ]
    )
    
    DOMAIN_REGISTRY["vacuum"] = DomainDef(
        domain="vacuum",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_COMMON,
        description="扫地机器人",
        services=[
            ServiceDef(name="start", description="开始清扫"),
            ServiceDef(name="stop", description="停止"),
            ServiceDef(name="pause", description="暂停"),
            ServiceDef(name="return_to_base", description="返回充电座", aliases=["return_home", "dock"]),
            ServiceDef(name="locate", description="定位（发出声音）"),
            ServiceDef(
                name="set_fan_speed",
                description="设置吸力",
                params=[
                    ServiceParam("fan_speed", "吸力档位", required=True, param_type="string"),
                ]
            ),
            ServiceDef(
                name="send_command",
                description="发送命令",
                params=[
                    ServiceParam("command", "命令", required=True, param_type="string"),
                    ServiceParam("params", "参数", param_type="object"),
                ]
            ),
        ]
    )
    
    
    DOMAIN_REGISTRY["sensor"] = DomainDef(
        domain="sensor",
        domain_type=TYPE_READ_ONLY,
        priority=PRIORITY_STANDARD,
        description="传感器（只读）",
        services=[],
        device_classes=[
            "apparent_power", "aqi", "atmospheric_pressure", "battery", "carbon_dioxide",
            "carbon_monoxide", "current", "data_rate", "data_size", "date", "distance",
            "duration", "energy", "enum", "frequency", "gas", "humidity", "illuminance",
            "irradiance", "moisture", "monetary", "nitrogen_dioxide", "nitrogen_monoxide",
            "nitrous_oxide", "ozone", "pm1", "pm10", "pm25", "power", "power_factor",
            "precipitation", "precipitation_intensity", "pressure", "reactive_power",
            "signal_strength", "sound_pressure", "speed", "sulphur_dioxide", "temperature",
            "timestamp", "volatile_organic_compounds", "voltage", "volume", "water", "weight", "wind_speed"
        ]
    )
    
    DOMAIN_REGISTRY["binary_sensor"] = DomainDef(
        domain="binary_sensor",
        domain_type=TYPE_READ_ONLY,
        priority=PRIORITY_STANDARD,
        description="二元传感器（只读）",
        services=[],
        device_classes=[
            "battery", "battery_charging", "carbon_monoxide", "cold", "connectivity",
            "door", "garage_door", "gas", "heat", "light", "lock", "moisture", "motion",
            "moving", "occupancy", "opening", "plug", "power", "presence", "problem",
            "running", "safety", "smoke", "sound", "tamper", "update", "vibration", "window"
        ]
    )
    
    
    DOMAIN_REGISTRY["script"] = DomainDef(
        domain="script",
        domain_type=TYPE_SERVICE_ONLY,
        priority=PRIORITY_EXTENDED,
        description="脚本",
        services=[
            ServiceDef(name="turn_on", description="执行脚本"),
            ServiceDef(name="turn_off", description="停止脚本"),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(name="reload", description="重新加载脚本"),
        ]
    )
    
    DOMAIN_REGISTRY["automation"] = DomainDef(
        domain="automation",
        domain_type=TYPE_SERVICE_ONLY,
        priority=PRIORITY_EXTENDED,
        description="自动化",
        services=[
            ServiceDef(name="turn_on", description="启用自动化"),
            ServiceDef(name="turn_off", description="禁用自动化"),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(name="trigger", description="触发自动化", params=[
                ServiceParam("skip_condition", "跳过条件检查", param_type="boolean", default=False),
            ]),
            ServiceDef(name="reload", description="重新加载自动化"),
        ]
    )
    
    DOMAIN_REGISTRY["scene"] = DomainDef(
        domain="scene",
        domain_type=TYPE_SERVICE_ONLY,
        priority=PRIORITY_EXTENDED,
        description="场景",
        services=[
            ServiceDef(name="turn_on", description="激活场景", aliases=["activate"]),
        ]
    )
    
    DOMAIN_REGISTRY["notify"] = DomainDef(
        domain="notify",
        domain_type=TYPE_SERVICE_ONLY,
        priority=PRIORITY_EXTENDED,
        description="通知服务",
        services=[
            ServiceDef(
                name="send_message",
                description="发送通知",
                params=[
                    ServiceParam("message", "消息内容", required=True, param_type="string"),
                    ServiceParam("title", "标题", param_type="string"),
                    ServiceParam("target", "目标", param_type="array"),
                    ServiceParam("data", "附加数据", param_type="object"),
                ]
            ),
        ]
    )
    
    DOMAIN_REGISTRY["tts"] = DomainDef(
        domain="tts",
        domain_type=TYPE_SERVICE_ONLY,
        priority=PRIORITY_EXTENDED,
        description="文字转语音",
        services=[
            ServiceDef(
                name="speak",
                description="朗读文字",
                params=[
                    ServiceParam("media_player_entity_id", "播放设备", required=True, param_type="string"),
                    ServiceParam("message", "文字内容", required=True, param_type="string"),
                    ServiceParam("language", "语言", param_type="string"),
                    ServiceParam("cache", "缓存", param_type="boolean", default=True),
                ]
            ),
        ]
    )
    
    DOMAIN_REGISTRY["input_boolean"] = DomainDef(
        domain="input_boolean",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="输入布尔值",
        services=[
            ServiceDef(name="turn_on", description="设为开"),
            ServiceDef(name="turn_off", description="设为关"),
            ServiceDef(name="toggle", description="切换"),
        ]
    )
    
    DOMAIN_REGISTRY["input_number"] = DomainDef(
        domain="input_number",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="输入数值",
        services=[
            ServiceDef(
                name="set_value",
                description="设置值",
                params=[
                    ServiceParam("value", "数值", required=True, param_type="number"),
                ]
            ),
            ServiceDef(name="increment", description="增加"),
            ServiceDef(name="decrement", description="减少"),
        ]
    )
    
    DOMAIN_REGISTRY["input_select"] = DomainDef(
        domain="input_select",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="输入选择",
        services=[
            ServiceDef(
                name="select_option",
                description="选择选项",
                params=[
                    ServiceParam("option", "选项", required=True, param_type="string"),
                ]
            ),
            ServiceDef(name="select_first", description="选择第一个"),
            ServiceDef(name="select_last", description="选择最后一个"),
            ServiceDef(name="select_next", description="选择下一个"),
            ServiceDef(name="select_previous", description="选择上一个"),
        ]
    )
    
    
    DOMAIN_REGISTRY["alarm_control_panel"] = DomainDef(
        domain="alarm_control_panel",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_SPECIALIZED,
        description="报警控制面板",
        services=[
            ServiceDef(name="alarm_disarm", description="撤防", params=[
                ServiceParam("code", "密码", param_type="string"),
            ]),
            ServiceDef(name="alarm_arm_home", description="在家布防", params=[
                ServiceParam("code", "密码", param_type="string"),
            ]),
            ServiceDef(name="alarm_arm_away", description="离家布防", params=[
                ServiceParam("code", "密码", param_type="string"),
            ]),
            ServiceDef(name="alarm_arm_night", description="夜间布防", params=[
                ServiceParam("code", "密码", param_type="string"),
            ]),
            ServiceDef(name="alarm_trigger", description="触发报警"),
        ]
    )
    
    DOMAIN_REGISTRY["camera"] = DomainDef(
        domain="camera",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_SPECIALIZED,
        description="摄像头",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(name="enable_motion_detection", description="启用移动侦测"),
            ServiceDef(name="disable_motion_detection", description="禁用移动侦测"),
            ServiceDef(name="snapshot", description="拍照", params=[
                ServiceParam("filename", "文件名", param_type="string"),
            ]),
            ServiceDef(name="record", description="录像", params=[
                ServiceParam("filename", "文件名", param_type="string"),
                ServiceParam("duration", "时长(秒)", param_type="number"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["humidifier"] = DomainDef(
        domain="humidifier",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_SPECIALIZED,
        description="加湿器",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(name="toggle", description="切换"),
            ServiceDef(name="set_humidity", description="设置湿度", params=[
                ServiceParam("humidity", "目标湿度 (0-100)", required=True, param_type="number", min_value=0, max_value=100),
            ]),
            ServiceDef(name="set_mode", description="设置模式", params=[
                ServiceParam("mode", "模式", required=True, param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["water_heater"] = DomainDef(
        domain="water_heater",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_SPECIALIZED,
        description="热水器",
        services=[
            ServiceDef(name="turn_on", description="开启"),
            ServiceDef(name="turn_off", description="关闭"),
            ServiceDef(name="set_temperature", description="设置温度", params=[
                ServiceParam("temperature", "目标温度", required=True, param_type="number"),
            ]),
            ServiceDef(name="set_operation_mode", description="设置模式", params=[
                ServiceParam("operation_mode", "模式", required=True, param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["input_text"] = DomainDef(
        domain="input_text",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="输入文本",
        services=[
            ServiceDef(name="set_value", description="设置文本", params=[
                ServiceParam("value", "文本内容", required=True, param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["input_datetime"] = DomainDef(
        domain="input_datetime",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="输入日期时间",
        services=[
            ServiceDef(name="set_datetime", description="设置日期时间", params=[
                ServiceParam("date", "日期 (YYYY-MM-DD)", param_type="string"),
                ServiceParam("time", "时间 (HH:MM:SS)", param_type="string"),
                ServiceParam("datetime", "日期时间", param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["button"] = DomainDef(
        domain="button",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="按钮",
        services=[
            ServiceDef(name="press", description="按下"),
        ]
    )
    
    DOMAIN_REGISTRY["number"] = DomainDef(
        domain="number",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="数值实体",
        services=[
            ServiceDef(name="set_value", description="设置值", params=[
                ServiceParam("value", "数值", required=True, param_type="number"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["select"] = DomainDef(
        domain="select",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="选择实体",
        services=[
            ServiceDef(name="select_option", description="选择选项", params=[
                ServiceParam("option", "选项", required=True, param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["timer"] = DomainDef(
        domain="timer",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="计时器",
        services=[
            ServiceDef(name="start", description="开始", params=[
                ServiceParam("duration", "时长 (HH:MM:SS)", param_type="string"),
            ]),
            ServiceDef(name="pause", description="暂停"),
            ServiceDef(name="cancel", description="取消"),
            ServiceDef(name="finish", description="完成"),
            ServiceDef(name="change", description="修改时长", params=[
                ServiceParam("duration", "新时长", required=True, param_type="string"),
            ]),
        ]
    )
    
    DOMAIN_REGISTRY["counter"] = DomainDef(
        domain="counter",
        domain_type=TYPE_CONTROLLABLE,
        priority=PRIORITY_EXTENDED,
        description="计数器",
        services=[
            ServiceDef(name="increment", description="增加"),
            ServiceDef(name="decrement", description="减少"),
            ServiceDef(name="reset", description="重置"),
            ServiceDef(name="set_value", description="设置值", params=[
                ServiceParam("value", "数值", required=True, param_type="number"),
            ]),
        ]
    )


_register_domains()



def get_domain(domain: str) -> Optional[DomainDef]:

    return DOMAIN_REGISTRY.get(domain)


def get_supported_domains() -> List[str]:

    return list(DOMAIN_REGISTRY.keys())


def get_domains_by_type(domain_type: str) -> List[str]:

    return [d.domain for d in DOMAIN_REGISTRY.values() if d.domain_type == domain_type]


def get_domains_by_priority(max_priority: int) -> List[str]:

    return [d.domain for d in DOMAIN_REGISTRY.values() if d.priority <= max_priority]


def get_service(domain: str, service: str) -> Optional[ServiceDef]:

    domain_def = DOMAIN_REGISTRY.get(domain)
    if not domain_def:
        return None
    
    for svc in domain_def.services:
        if svc.name == service or service in svc.aliases:
            return svc
    return None


def validate_service_call(domain: str, service: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """验证服务调用参数
    
    返回:
    {
        "valid": bool,
        "errors": List[str],
        "warnings": List[str],
        "normalized_service": str,
        "suggestions": Dict[str, Any],
    }
    """
    result = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "normalized_service": service,
        "suggestions": {},
    }
    
    domain_def = DOMAIN_REGISTRY.get(domain)
    if not domain_def:
        result["warnings"].append(f"域 '{domain}' 未在注册表中，无法验证")
        return result
    
    service_def = get_service(domain, service)
    if not service_def:
        available = [s.name for s in domain_def.services]
        result["errors"].append(f"域 '{domain}' 不支持服务 '{service}'，可用: {available}")
        result["valid"] = False
        return result
    
    result["normalized_service"] = service_def.name
    
    if not data:
        data = {}
    
    for param in service_def.params:
        if param.required and param.name not in data:
            result["errors"].append(f"缺少必需参数: {param.name} ({param.description})")
            result["valid"] = False
    
    for param in service_def.params:
        if param.name not in data:
            continue
        
        value = data[param.name]
        
        if param.enum and value not in param.enum:
            result["errors"].append(f"参数 '{param.name}' 值 '{value}' 无效，可选: {param.enum}")
            result["valid"] = False
        
        if param.param_type == "number" and isinstance(value, (int, float)):
            if param.min_value is not None and value < param.min_value:
                result["errors"].append(f"参数 '{param.name}' 值 {value} 小于最小值 {param.min_value}")
                result["valid"] = False
            if param.max_value is not None and value > param.max_value:
                result["errors"].append(f"参数 '{param.name}' 值 {value} 大于最大值 {param.max_value}")
                result["valid"] = False
    
    for param in service_def.params:
        if param.name not in data:
            suggestion = {"description": param.description, "type": param.param_type}
            if param.default is not None:
                suggestion["default"] = param.default
            if param.enum:
                suggestion["options"] = param.enum
            if param.min_value is not None:
                suggestion["min"] = param.min_value
            if param.max_value is not None:
                suggestion["max"] = param.max_value
            result["suggestions"][param.name] = suggestion
    
    return result


def get_service_help(domain: str, service: str = None) -> str:

    domain_def = DOMAIN_REGISTRY.get(domain)
    if not domain_def:
        return f"未知域: {domain}"
    
    lines = [f"## {domain} ({domain_def.description})"]
    lines.append(f"类型: {domain_def.domain_type}")
    
    if service:
        service_def = get_service(domain, service)
        if not service_def:
            return f"未知服务: {domain}.{service}"
        
        lines.append(f"\n### {service_def.name}")
        lines.append(f"描述: {service_def.description}")
        if service_def.aliases:
            lines.append(f"别名: {', '.join(service_def.aliases)}")
        if service_def.params:
            lines.append("\n参数:")
            for p in service_def.params:
                req = "必需" if p.required else "可选"
                line = f"  - {p.name} ({p.param_type}, {req}): {p.description}"
                if p.enum:
                    line += f" [可选值: {', '.join(str(e) for e in p.enum)}]"
                if p.min_value is not None or p.max_value is not None:
                    line += f" [范围: {p.min_value}-{p.max_value}]"
                lines.append(line)
    else:
        lines.append("\n可用服务:")
        for svc in domain_def.services:
            aliases = f" (别名: {', '.join(svc.aliases)})" if svc.aliases else ""
            lines.append(f"  - {svc.name}: {svc.description}{aliases}")
    
    return "\n".join(lines)
