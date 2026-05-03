from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    "agents_unavailable": {
        "zh": "所有 AI 助手当前不可用，请稍后再试",
        "en": "All AI agents are currently unavailable, please try again later",
    },
    "no_valid_input": {
        "zh": "未收到有效输入，请重试",
        "en": "No valid input was received. Please try again.",
    },
    "budget_exhausted": {
        "zh": "迭代预算已耗尽，请开始新对话",
        "en": "Iteration budget exhausted. Please start a new conversation.",
    },
    "hook_not_ready": {
        "zh": "运行时钩子未初始化，请重新启动系统生效",
        "en": "claw_assistant runtime hook is not initialized",
    },
    "attachment_only_input": {
        "zh": "用户发送了一张图片，请描述其内容。",
        "en": "The user sent an image, please describe its content.",
    },
    "err_service_unavailable": {
        "zh": "AI 服务暂时不可用，稍后会自动重试，您也可以过一会儿再问一次。",
        "en": "The AI service is temporarily unavailable. It will retry automatically; please try again shortly.",
    },
    "err_model_not_found": {
        "zh": "助手当前没有可用的模型通道（{model}），请在设置里配好渠道后再试。",
        "en": "No available channel for model {model}. Please configure one and try again.",
    },
    "err_rate_limited": {
        "zh": "请求太频繁，AI 服务限流中，请稍等片刻再试。",
        "en": "Rate limited by the AI service. Please wait a moment and try again.",
    },
    "err_auth_failed": {
        "zh": "AI 服务认证失败，请检查 API 密钥是否正确。",
        "en": "AI service authentication failed. Please check your API key.",
    },
    "err_quota_exceeded": {
        "zh": "AI 服务额度已用完，请检查账户余额或升级套餐。",
        "en": "AI service quota exceeded. Please check your account balance or upgrade your plan.",
    },
    "err_context_too_long": {
        "zh": "对话内容过长，AI 无法处理。请开始新对话或缩短消息。",
        "en": "Conversation too long for the AI model. Please start a new conversation or shorten your message.",
    },
    "err_content_filtered": {
        "zh": "内容被 AI 安全过滤器拦截，请换一种方式描述。",
        "en": "Content was blocked by the AI safety filter. Please rephrase your message.",
    },
    "err_timeout": {
        "zh": "AI 服务响应超时，请稍后再试。",
        "en": "AI service timed out. Please try again shortly.",
    },
    "err_connection": {
        "zh": "无法连接到 AI 服务，请检查网络或稍后再试。",
        "en": "Cannot connect to the AI service. Please check your network or try again later.",
    },
    "err_generic_api": {
        "zh": "AI 服务返回错误：{detail}",
        "en": "The AI service returned an error: {detail}",
    },
    "err_model_placeholder": {
        "zh": "目标模型",
        "en": "the target model",
    },
}


def t(key: str, language: str | None = None) -> str:
    entry = _MESSAGES.get(key)
    if not entry:
        return key
    lang = "zh" if language and language.startswith("zh") else "en"
    return entry.get(lang, entry.get("en", key))
