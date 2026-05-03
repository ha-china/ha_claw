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
        "zh": "这个助手当前没有可用的模型通道（{model}），请在它的设置里补上渠道后再试。",
        "en": "This assistant has no available channel for model {model}. Please configure a channel for it and try again.",
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
