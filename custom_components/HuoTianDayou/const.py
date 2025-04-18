DOMAIN = "kadermanager"

CONF_PRIMARY_AGENT = "primary_agent"
CONF_FALLBACK_AGENT = "fallback_agent"
CONF_SECONDARY_FALLBACK_AGENT = "secondary_fallback_agent"
CONF_CONVERSATION_MODE = "conversation_mode"
CONF_ERROR_RESPONSES = "error_responses"
CONF_ENABLE_AI_SUMMARY = "enable_ai_summary"
CONF_ENABLE_WEB_SEARCH = "enable_web_search"
CONF_WEB_SEARCH_ENGINE = "web_search_engine"

CONVERSATION_MODE_NO_NAME = "no_name"
CONVERSATION_MODE_ADD_NAME = "add_name"
CONVERSATION_MODE_DETAILED = "detailed"

CONF_SPEAKER_ENTITY = "speaker_entity"
CONF_ENABLE_SPEAKER = "enable_speaker"
CONF_SPEAKER_TYPE = "speaker_type"
CONF_TTS_SERVICE = "tts_service"

SPEAKER_TYPE_DISABLED = "disabled"
SPEAKER_TYPE_XIAOMI = "xiaomi"
SPEAKER_TYPE_OTHER = "other"

SEARCH_ENGINE_GOOGLE = "google"
SEARCH_ENGINE_BAIDU = "baidu"
SEARCH_ENGINE_BING = "bing"

DEFAULT_NAME = "火天大有（聚合AI）"
DEFAULT_CONVERSATION_MODE = CONVERSATION_MODE_ADD_NAME
DEFAULT_PRIMARY_AGENT = ""
DEFAULT_FALLBACK_AGENT = ""
DEFAULT_SECONDARY_FALLBACK_AGENT = None
DEFAULT_SEARCH_ENGINE = SEARCH_ENGINE_BAIDU

DEFAULT_ERROR_RESPONSES = """很抱歉，我无法理解你的问题。
对不起，我没有找到相关的答案。
抱歉，我不明白你的意思。
抱歉，暂不支持该操作。如果问题持续，可能需要调整指令。
抱歉，我目前暂不支持控制智能家居设备。如需查询设备状态，我可以为您服务。
"""
