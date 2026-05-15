
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from .runtime.response_format import sanitize_response_text

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "claw_assistant_conversation_history"
STORAGE_SAVE_DELAY = 5.0

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
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConversationHistory:


    def __init__(
        self,
        max_turns: int = 30,
        max_age_hours: float = 24.0,
    ):
        self._histories: Dict[str, List[ConversationTurn]] = {}
        self._last_touched: Dict[str, float] = {}
        self.max_turns = max_turns
        self.max_age_seconds = max_age_hours * 3600
        self._store: Optional["Store"] = None

    def add_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_response: str,
        tool_calls: List[Dict[str, Any]] = None,
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
        self._last_touched[conversation_id] = turn.timestamp

        if len(self._histories[conversation_id]) > self.max_turns:
            self._histories[conversation_id] = self._histories[conversation_id][-self.max_turns:]

        self._schedule_save()

    def set_conversation_title(self, conversation_id: str, title: str) -> None:
        title = (title or "").strip()
        if not title or conversation_id not in self._histories:
            return

        metadata = self._histories[conversation_id][0].metadata
        if metadata.get("title"):
            return
        metadata["title"] = title
        self._schedule_save()

    def get_conversation_title(self, conversation_id: str) -> str:
        turns = self._histories.get(conversation_id) or []
        if not turns:
            return ""
        return str((turns[0].metadata or {}).get("title", "") or "").strip()

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
                tool_names = [tc.get("tool_name", str(tc)) if isinstance(tc, dict) else str(tc) for tc in turn.tool_calls]
                lines.append(f"Tool: {', '.join(tool_names)}")
            response = sanitize_response_text(turn.assistant_response)
            if response:
                if len(response) > 500:
                    response = response[:500] + "..."
                lines.append(f"Assistant: {response}")
            lines.append("")

        return "\n".join(lines)

    def clear(self, conversation_id: str = None) -> int:

        if conversation_id:
            removed = len(self._histories.get(conversation_id, []))
            self._histories.pop(conversation_id, None)
            self._last_touched.pop(conversation_id, None)
            self._schedule_save()
            return removed
        removed = sum(len(turns) for turns in self._histories.values())
        self._histories.clear()
        self._last_touched.clear()
        self._schedule_save()
        return removed

    def get_recent_across_conversations(
        self,
        minutes: float = 30.0,
        max_turns_per_conv: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return turns from all conversations whose last_touched is within the last N minutes.

        Useful after a window/conversation_id has been closed to recall what the user
        was just discussing."""
        now = time.time()
        cutoff = now - minutes * 60
        result: List[Dict[str, Any]] = []
        for conv_id, last_ts in self._last_touched.items():
            if last_ts < cutoff:
                continue
            turns = self._histories.get(conv_id, [])
            if not turns:
                continue
            recent_turns = [t for t in turns if t.timestamp >= cutoff][-max_turns_per_conv:]
            if not recent_turns:
                continue
            result.append(
                {
                    "conversation_id": conv_id,
                    "last_touched": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(last_ts)
                    ),
                    "last_touched_seconds_ago": int(now - last_ts),
                    "turn_count": len(recent_turns),
                    "turns": [
                        {
                            "user": t.user_message,
                            "assistant": (
                                t.assistant_response[:500] + "..."
                                if len(t.assistant_response) > 500
                                else t.assistant_response
                            ),
                            "tool_calls": t.tool_calls,
                            "at": time.strftime(
                                "%H:%M:%S", time.localtime(t.timestamp)
                            ),
                        }
                        for t in recent_turns
                    ],
                }
            )
        result.sort(key=lambda x: x["last_touched_seconds_ago"])
        return result

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
            self._last_touched.pop(conv_id, None)

        if removed:
            self._schedule_save()

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


    def attach_store(self, store: "Store") -> None:
        """Attach an HA Store; subsequent mutations will schedule debounced saves."""
        self._store = store

    def _data_to_save(self) -> Dict[str, Any]:
        return {
            "histories": {
                conv_id: [
                    {
                        "user_message": t.user_message,
                        "assistant_response": t.assistant_response,
                        "timestamp": t.timestamp,
                        "tool_calls": list(t.tool_calls or []),
                        "metadata": dict(t.metadata or {}),
                    }
                    for t in turns
                ]
                for conv_id, turns in self._histories.items()
            },
            "last_touched": dict(self._last_touched),
        }

    def _schedule_save(self) -> None:
        if self._store is None:
            return
        try:
            self._store.async_delay_save(self._data_to_save, STORAGE_SAVE_DELAY)
        except Exception as err:  # pragma: no cover
            _LOGGER.debug("Failed to schedule history save: %s", err)

    def load_from_dict(self, data: Dict[str, Any]) -> int:
        """Load persisted data and drop anything older than max_age_seconds."""
        if not data:
            return 0
        histories = data.get("histories") or {}
        last_touched = data.get("last_touched") or {}
        now = time.time()
        cutoff = now - self.max_age_seconds
        loaded = 0
        for conv_id, turns in histories.items():
            kept: List[ConversationTurn] = []
            for t in turns or []:
                ts = float(t.get("timestamp", 0) or 0)
                if ts <= 0 or ts < cutoff:
                    continue
                raw_tool_calls = t.get("tool_calls") or []
                normalized_tool_calls = []
                for tc in raw_tool_calls:
                    if isinstance(tc, dict):
                        normalized_tool_calls.append(tc)
                    elif isinstance(tc, str):
                        normalized_tool_calls.append({"tool_name": tc})
                kept.append(
                    ConversationTurn(
                        user_message=str(t.get("user_message", "")),
                        assistant_response=str(t.get("assistant_response", "")),
                        timestamp=ts,
                        tool_calls=normalized_tool_calls,
                        metadata=dict(t.get("metadata") or {}),
                    )
                )
            if kept:
                self._histories[conv_id] = kept[-self.max_turns:]
                loaded += len(self._histories[conv_id])
        for conv_id, ts in last_touched.items():
            try:
                ts_f = float(ts)
            except (TypeError, ValueError):
                continue
            if ts_f < cutoff:
                continue
            if conv_id in self._histories:
                self._last_touched[conv_id] = ts_f
        return loaded


_conversation_history: Optional[ConversationHistory] = None


async def async_setup_history_store(hass: "HomeAssistant") -> ConversationHistory:
    """Load persisted history from disk and attach the store for future saves.

    Safe to call multiple times; subsequent calls are no-ops if already attached.
    """
    from homeassistant.helpers.storage import Store

    history = get_conversation_history()
    if history._store is not None:
        return history
    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    try:
        data = await store.async_load()
    except Exception as err:
        _LOGGER.warning("Failed to load conversation history from store: %s", err)
        data = None
    if data:
        loaded = history.load_from_dict(data)
        _LOGGER.info(
            "Restored %d conversation turns from storage (24h retention)", loaded
        )
    history.attach_store(store)
    history.cleanup_all()
    return history


async def async_flush_history_store(hass: "HomeAssistant") -> None:
    """Force-flush any pending history save (called on unload)."""
    history = get_conversation_history()
    if history._store is None:
        return
    try:
        await history._store.async_save(history._data_to_save())
    except Exception as err:  # pragma: no cover
        _LOGGER.debug("Failed to flush history store: %s", err)


def get_conversation_history() -> ConversationHistory:

    global _conversation_history
    if _conversation_history is None:
        _conversation_history = ConversationHistory()
    return _conversation_history
