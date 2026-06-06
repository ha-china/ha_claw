DOMAIN = "claw_assistant"
VERSION = "9.1.0"

IM_CHANNEL_NAMES: dict[str, str] = {
    "wechat:": "WeChat",
    "feishu:": "Feishu",
    "dingtalk:": "DingTalk",
    "qq:": "QQ",
    "wecom:": "WeCom",
    "xiaoyi:": "XiaoYi",
}

CONF_PRIMARY_AGENT = "primary_agent"
CONF_FALLBACK_AGENT = "fallback_agent"
CONF_SECONDARY_FALLBACK_AGENT = "secondary_fallback_agent"
CONF_CONVERSATION_MODE = "conversation_mode"
CONF_ERROR_RESPONSES = "error_responses"
CONF_ENABLE_AI_SUMMARY = "enable_ai_summary"
CONF_ENABLE_WEB_SEARCH = "enable_web_search"
CONF_ENABLE_STREAMING_EFFECT = "enable_streaming_effect"
CONF_CONTINUOUS_CONVERSATION = "continuous_conversation"
CONF_ENABLE_SOUND_NOTIFICATIONS = "enable_sound_notifications"
CONF_ENABLE_CONTEXT_STATUS_BAR = "enable_context_status_bar"
CONF_ENABLE_FILE_UPLOAD = "enable_file_upload"
CONF_ENABLE_RICH_MARKDOWN = "enable_rich_markdown"
CONF_ENABLE_ACTIVITY_TRACKING = "enable_activity_tracking"
CONF_ENABLE_SIDEBAR_DOCK = "enable_sidebar_dock"
CONF_ENABLE_TOOL_DETAILS = "enable_tool_details"
CONF_ENABLE_TOOL_PROGRESS = "enable_tool_progress"
CONF_MAX_TOOL_REPEAT = "max_tool_repeat"
CONF_IDENTICAL_CALL_WARN = "identical_call_warn"
CONF_IDENTICAL_CALL_STOP = "identical_call_stop"
CONF_PIPELINE_TIMEOUT = "pipeline_timeout"

CONVERSATION_MODE_NO_NAME = "no_name"
CONVERSATION_MODE_ADD_NAME = "add_name"
CONVERSATION_MODE_DETAILED = "detailed"

DEFAULT_CONVERSATION_MODE = CONVERSATION_MODE_ADD_NAME
DEFAULT_PRIMARY_AGENT = ""
DEFAULT_FALLBACK_AGENT = ""
DEFAULT_SECONDARY_FALLBACK_AGENT = None
DEFAULT_MAX_TOOL_REPEAT = 15
DEFAULT_IDENTICAL_CALL_WARN = 10
DEFAULT_IDENTICAL_CALL_STOP = 20
DEFAULT_PIPELINE_TIMEOUT = 900
