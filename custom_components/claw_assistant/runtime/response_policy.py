

from __future__ import annotations

from difflib import SequenceMatcher
import re

from .config import DEFAULT_THRESHOLDS


def is_user_done_text(text: str, detect_user_ending_intent) -> bool:

    continue_keywords = [
        "继续",
        "还要",
        "再",
        "另外",
        "还有",
        "接着",
        "然后",
        "下一步",
        "不对",
        "错了",
        "重新",
        "?",
        "？",
    ]
    text_lower = text.lower().strip()
    if any(keyword in text_lower for keyword in continue_keywords):
        return False
    return detect_user_ending_intent(text)


def analyze_response_state(
    response_text: str,
    history: list,
    *,
    expecting_response: bool | None = None,
    conversation_state_reason: str = "",
    last_tool: str = "",
) -> dict:

    if expecting_response is not None:
        if not expecting_response:
            return {
                "state": "final",
                "reason": f"LLM明确表示任务完成: {conversation_state_reason}",
            }
        return {
            "state": "need_user",
            "reason": f"LLM等待用户回复: {conversation_state_reason}",
            "continue": True,
        }

    if last_tool in [
        "WebSearch",
        "SetConversationState",
        "ServiceCall",
        "BatchControl",
        "Notify",
        "ScriptExecute",
    ]:
        return {"state": "final", "reason": f"工具{last_tool}已调用，直接终止"}

    search_result_indicators = [
        "搜索结果",
        "根据搜索",
        "查询结果",
        "以下是",
        "找到了",
        "获取到",
        "新闻",
        "热榜",
        "来源:",
        "链接:",
        "http://",
        "https://",
        "根据以上",
        "综合以上",
        "根据网络",
        "根据查询",
        "天气",
        "温度",
        "气温",
        "湿度",
        "风力",
        "如下：",
    ]
    if any(ind in response_text for ind in search_result_indicators):
        return {"state": "final", "reason": "包含搜索/查询结果，直接终止"}

    final_indicators = [
        "综上所述",
        "希望对您有帮助",
        "以上就是",
        "总结如下",
        "以上是我的回答",
        "已完成",
        "已执行",
        "已帮您",
        "操作成功",
        "已为您",
    ]
    if any(ind in response_text for ind in final_indicators) and len(
        response_text
    ) > DEFAULT_THRESHOLDS.response_complete_indicator_min_length:
        return {"state": "final", "reason": "检测到完成指示词"}

    waiting_user_indicators = [
        "请告诉我",
        "请提供",
        "请问您",
        "您希望",
        "您想要",
        "需要您提供",
        "请指定",
        "请说明",
        "请确认",
        "您可以告诉我",
        "等待您的",
        "请输入",
        "请选择",
        "您需要",
        "请描述",
        "告诉我您的",
        "需要更多信息",
        "请问",
        "您想",
        "您要",
    ]
    if any(ind in response_text for ind in waiting_user_indicators):
        return {
            "state": "need_user",
            "reason": "AI需要用户提供更多信息",
            "continue": True,
        }

    continuation_signals = [
        "让我继续",
        "我需要进一步",
        "让我再看看",
        "我还需要",
        "接下来我会",
        "我来分析",
        "让我检查",
        "我先查一下",
        "让我再想想",
        "我继续分析",
        "Let me continue",
        "I need to",
        "Let me check",
        "Let me think",
    ]
    if any(sig in response_text for sig in continuation_signals) and len(response_text) < DEFAULT_THRESHOLDS.response_auto_final_length:
        return {
            "state": "continue",
            "reason": "AI显式请求继续思考",
            "continuation_eligible": True,
        }

    action_indicators = [
        "正在查询",
        "正在搜索",
        "正在执行",
        "让我",
        "我来",
        "稍等",
        "正在处理",
        "正在获取",
        "我需要先",
    ]
    if any(ind in response_text for ind in action_indicators) and len(response_text) < 200:
        return {
            "state": "need_action",
            "reason": "AI正在执行操作，可能需要继续",
            "continuation_eligible": True,
        }

    if len(history) >= 2:
        last_responses = [
            item.get("content", "")
            for item in history[-3:]
            if item.get("role") == "assistant"
        ]
        if len(last_responses) >= 2:
            prev = last_responses[-2] if len(last_responses) >= 2 else ""
            if prev and SequenceMatcher(
                None, prev[:300], response_text[:300]
            ).ratio() > DEFAULT_THRESHOLDS.response_duplicate_similarity:
                return {"state": "final", "reason": "检测到重复回复，强制终止"}

    if len(response_text) > DEFAULT_THRESHOLDS.response_auto_final_length:
        return {"state": "final", "reason": "回复足够长，视为完成"}

    return {"state": "continue", "reason": "需要继续思考"}


def is_clarification_request_text(response_text: str) -> bool:

    text = response_text.strip()
    if not text:
        return False

    question_mark_patterns = ("？", "?")
    clarification_patterns = [
        r"请.*(指定|提供|告诉我|确认)",
        r"(哪个|哪一个|哪些|哪种|哪类)",
        r"(客厅|卧室|区域).*(还是|或)",
        r"要.*(哪个|哪一个|哪些|哪种)",
        r"是否.*(需要|要)",
        r"比如.*(或|还是)",
    ]

    if any(mark in text for mark in question_mark_patterns):
        return True

    return any(re.search(pattern, text) for pattern in clarification_patterns)
