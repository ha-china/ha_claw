
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from .runtime.response_format import sanitize_response_text

_LOGGER = logging.getLogger(__name__)

DEFAULT_END_WORDS = [
    "好", "行", "可以", "没了", "没有了", "就这样", "算了", "不用了", "谢谢",
    "好的", "行了", "可以了", "够了", "结束", "停", "退出", "再见", "拜拜",
    "不需要", "不要了", "完了", "完成", "搞定", "ok", "OK",
    "stop", "done", "bye", "exit", "quit", "thanks", "ok", "okay", "no", "nope",
    "nevermind", "cancel", "end", "finish", "goodbye", "later",
    "thank you", "no thanks", "that's all", "that's it", "never mind",
    "no more", "all done", "good bye", "see you", "不用了谢谢", "就这些",
    "没有其他", "没别的了", "就这样吧"
]


def detect_user_ending_intent(text: str, end_words: List[str] = None, agent_name: str = "") -> bool:

    if not text:
        return False

    if end_words is None:
        end_words = DEFAULT_END_WORDS

    multi_word_phrases = [phrase.lower() for phrase in end_words if ' ' in phrase or len(phrase) > 2]
    single_words = [word.lower() for word in end_words if ' ' not in word and len(word) <= 2]
    single_words.extend([word.lower() for word in end_words if word.isascii() and ' ' not in word])

    text_lower = text.lower().strip()

    has_stop_word = False
    remaining_text = text_lower

    for phrase in multi_word_phrases:
        if phrase in remaining_text:
            has_stop_word = True
            remaining_text = remaining_text.replace(phrase, ' ')

    import re
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', remaining_text)

    if agent_name:
        agent_name_lower = agent_name.lower()
        words = [w for w in words if w != agent_name_lower]

    for word in words:
        if word in single_words:
            has_stop_word = True

    if not has_stop_word:
        return False

    all_stop_words = set(w.lower() for w in end_words)
    non_stop_words = [w for w in words if w.lower() not in all_stop_words and w.strip()]

    return len(non_stop_words) <= 1


@dataclass
class ConversationTurn:

    user_message: str
    assistant_response: str
    timestamp: float = field(default_factory=time.time)
    tool_calls: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConversationHistory:


    def __init__(
        self,
        max_turns: int = 10,
        max_age_hours: float = 24.0,
    ):
        self._histories: Dict[str, List[ConversationTurn]] = {}
        self.max_turns = max_turns
        self.max_age_seconds = max_age_hours * 3600

    def add_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_response: str,
        tool_calls: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> None:

        if conversation_id not in self._histories:
            self._histories[conversation_id] = []

        turn = ConversationTurn(
            user_message=user_message,
            assistant_response=sanitize_response_text(assistant_response),
            tool_calls=tool_calls or [],
            metadata=metadata or {},
        )

        self._histories[conversation_id].append(turn)

        if len(self._histories[conversation_id]) > self.max_turns:
            self._histories[conversation_id] = self._histories[conversation_id][-self.max_turns:]

    def get_history(self, conversation_id: str) -> List[ConversationTurn]:

        self._cleanup_old_turns(conversation_id)
        return self._histories.get(conversation_id, [])

    def get_recent_context(
        self,
        conversation_id: str,
        max_turns: int = 5,
        include_tools: bool = False,
    ) -> str:

        history = self.get_history(conversation_id)
        if not history:
            return ""

        recent = history[-max_turns:]
        lines = []

        for i, turn in enumerate(recent, 1):
            if not turn.user_message and not turn.assistant_response and not turn.tool_calls:
                continue
            lines.append(f"[Turn {i}]")
            lines.append(f"User: {turn.user_message}")
            if include_tools and turn.tool_calls:
                lines.append(f"Tool: {', '.join(turn.tool_calls)}")
            response = sanitize_response_text(turn.assistant_response)
            if response:
                if len(response) > 500:
                    response = response[:500] + "..."
                lines.append(f"Assistant: {response}")
            lines.append("")

        return "\n".join(lines)

    def clear(self, conversation_id: str = None) -> None:

        if conversation_id:
            self._histories.pop(conversation_id, None)
        else:
            self._histories.clear()

    def _cleanup_old_turns(self, conversation_id: str) -> None:

        if conversation_id not in self._histories:
            return

        now = time.time()
        cutoff = now - self.max_age_seconds

        self._histories[conversation_id] = [
            turn for turn in self._histories[conversation_id]
            if turn.timestamp > cutoff
        ]

    def cleanup_all(self) -> int:

        now = time.time()
        cutoff = now - self.max_age_seconds
        removed = 0

        empty_conversations = []
        for conv_id, turns in self._histories.items():
            original_len = len(turns)
            self._histories[conv_id] = [t for t in turns if t.timestamp > cutoff]
            removed += original_len - len(self._histories[conv_id])
            if not self._histories[conv_id]:
                empty_conversations.append(conv_id)

        for conv_id in empty_conversations:
            del self._histories[conv_id]

        return removed

    def get_stats(self) -> Dict[str, Any]:

        total_conversations = len(self._histories)
        total_turns = sum(len(turns) for turns in self._histories.values())
        avg_turns = total_turns / total_conversations if total_conversations > 0 else 0

        all_timestamps = []
        for turns in self._histories.values():
            all_timestamps.extend(t.timestamp for t in turns)

        oldest = min(all_timestamps) if all_timestamps else None
        newest = max(all_timestamps) if all_timestamps else None

        return {
            "total_conversations": total_conversations,
            "total_turns": total_turns,
            "average_turns": round(avg_turns, 1),
            "oldest_turn": time.strftime("%Y-%m-%d %H:%M", time.localtime(oldest)) if oldest else None,
            "newest_turn": time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else None,
        }


_conversation_history: Optional[ConversationHistory] = None


def get_conversation_history() -> ConversationHistory:

    global _conversation_history
    if _conversation_history is None:
        _conversation_history = ConversationHistory()
    return _conversation_history
