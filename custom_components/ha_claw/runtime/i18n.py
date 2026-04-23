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
        "zh": "运行时钩子未初始化",
        "en": "claw_assistant runtime hook is not initialized",
    },
}


def t(key: str, language: str | None = None) -> str:
    entry = _MESSAGES.get(key)
    if not entry:
        return key
    lang = "zh" if language and language.startswith("zh") else "en"
    return entry.get(lang, entry.get("en", key))
