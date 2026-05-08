from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .data_path import get_data_dir
from .reply_formatter import is_chinese
from .state import get_conversation_status, get_runtime_store

LOGGER = logging.getLogger(__name__)


DEFAULT_MAX_TURNS = 0
DEFAULT_JUDGE_TIMEOUT = 30.0
_JUDGE_RESPONSE_SNIPPET_CHARS = 4000

DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3

CONTINUATION_MARKER = "[Continuing toward your standing goal]"

CONTINUATION_PROMPT_TEMPLATE = (
    f"{CONTINUATION_MARKER}\n"
    "Goal: {goal}\n\n"
    "Keep working toward this goal. Take the next concrete step NOW — "
    "run tools, read files, produce output. "
    "Do not delegate this continuation to another agent unless the goal explicitly requires delegation. "
    "Continue directly in the current assistant voice. "
    "If the goal asks for multiple rounds of dialogue, produce the next complete dialogue content yourself; "
    "do not ask the user or the judge to reply before continuing. "
    "If the goal is ambiguous, pick the most likely interpretation and "
    "proceed; do NOT stop to ask the user for clarification. "
    "Only stop when the deliverable is actually produced and verifiable. "
    "If you genuinely finished, state the concrete deliverable explicitly."
)

JUDGE_SYSTEM_PROMPT = (
    "You are the 判官 (Judge) — the gatekeeper of this autonomous AI system. "
    "Your identity IS the judge; when asked who you are, state that you are "
    "the 判官. You are a STRICT autonomy judge for an AI assistant running in a "
    "Ralph loop. You receive the user's goal and the assistant's most "
    "recent response. Decide whether the goal is truly satisfied.\n\n"
    "A response counts as DONE **only** when it contains a concrete, "
    "verifiable deliverable — produced content, executed action, reported "
    "result, or a definitive answer. Examples: listed the 5 light "
    "entities, wrote the file, ran the diagnostic and returned findings, "
    "answered the factual question with specifics.\n\n"
    "A response is NOT done (→ CONTINUE) whenever it:\n"
    "- Asks the user a clarifying / choosing / confirming question;\n"
    "- Offers options and waits for the user to pick;\n"
    "- Says the goal is vague, unclear, or needs more information;\n"
    "- Says it is blocked or needs user input to proceed;\n"
    "- Only describes a plan without executing it;\n"
    "- Returns only tool calls with no synthesis / conclusion.\n\n"
    "An ambiguous goal is NOT a reason to stop. The assistant must pick "
    "the most plausible interpretation and drive forward — judging stops "
    "only when a deliverable actually exists. Reflexively asking the user "
    "'what do you mean?' is an autonomy failure and must be CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line. Do not call any "
    "tools. Do not wrap it in markdown. Do not add prose:\n"
    '{"done": <true|false>, "reason": "<one-sentence rationale>"}'
)

JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Assistant's most recent response:\n{response}\n\n"
    "Is the goal satisfied?"
)

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_CLEARED = "cleared"


_MESSAGES: dict[str, dict[str, str]] = {
    "achieved": {
        "en": "🟢 Goal achieved: {reason}",
        "zh": "🟢 目标达成：{reason}",
    },
    "continuing": {
        "en": "🟡 Continuing toward goal ({progress}): {reason}",
        "zh": "🟡 继续推进目标（{progress} 轮）：{reason}",
    },
    "paused_parse": {
        "en": (
            "🔴 Goal paused — the judge model failed to return JSON "
            "{count} turns in a row. Switch the fallback agent to a "
            "model that follows the JSON contract, then /goal resume."
        ),
        "zh": (
            "🔴 目标已暂停 —— 判官模型连续 {count} 轮没有返回合法 JSON。"
            "请把后备 AI 切换为更稳的模型后，使用 /goal resume 继续。"
        ),
    },
    "paused_budget": {
        "en": (
            "🔴 Goal paused — {used}/{budget} turns used. "
            "/goal resume to keep going, /goal clear to stop."
        ),
        "zh": (
            "🔴 目标已暂停 —— 已用 {used}/{budget} 轮预算。"
            "/goal resume 继续，/goal clear 终止。"
        ),
    },
    "status_active": {
        "en": "🟢 Goal (active, {turns}): {goal}",
        "zh": "🟢 目标进行中（{turns}）：{goal}",
    },
    "status_paused": {
        "en": "🔴 Goal (paused, {turns}{extra}): {goal}",
        "zh": "🔴 目标已暂停（{turns}{extra}）：{goal}",
    },
    "status_done": {
        "en": "🟢 Goal done ({turns}): {goal}",
        "zh": "🟢 目标已完成（{turns}）：{goal}",
    },
    "status_other": {
        "en": "Goal ({status}, {turns}): {goal}",
        "zh": "目标（{status}，{turns}）：{goal}",
    },
    "status_none": {
        "en": "No active goal. Set one with /goal <text>.",
        "zh": "暂无目标。使用 /goal <内容> 设置一个。",
    },
    "turns_with_budget": {
        "en": "{used}/{budget} turns",
        "zh": "{used}/{budget} 轮",
    },
    "turns_unbounded": {
        "en": "{used} turns",
        "zh": "{used} 轮",
    },
    "cmd_no_active_to_pause": {
        "en": "No active goal to pause.",
        "zh": "暂无可暂停的目标。",
    },
    "cmd_no_goal_to_resume": {
        "en": "No goal to resume. Set one with /goal <text>.",
        "zh": "没有可恢复的目标。使用 /goal <内容> 设置一个。",
    },
    "cmd_cleared": {
        "en": "Goal cleared.",
        "zh": "目标已清除。",
    },
    "cmd_nothing_to_clear": {
        "en": "No goal to clear.",
        "zh": "暂无可清除的目标。",
    },
    "cmd_empty_text": {
        "en": "Goal text is empty. Try /goal <text>.",
        "zh": "目标内容为空。请使用 /goal <内容>。",
    },
}


def localised_message(hass: HomeAssistant, key: str, **fmt: Any) -> str:
    return _msg(key, _resolve_language(hass), **fmt)


def _resolve_language(hass: HomeAssistant) -> str:
    try:
        lang = get_conversation_status(hass).get("user_language")
    except Exception:
        lang = None
    if not lang:
        lang = getattr(hass.config, "language", None) or "en"
    return "zh" if is_chinese(lang) else "en"


def _msg(key: str, lang: str, **fmt: Any) -> str:
    bundle = _MESSAGES.get(key) or {}
    template = bundle.get(lang) or bundle.get("en") or ""
    if not template:
        return ""
    try:
        return template.format(**fmt)
    except Exception:
        return template


@dataclass
class GoalState:
    goal: str
    status: str = STATUS_ACTIVE
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: str | None = None
    last_reason: str | None = None
    paused_reason: str | None = None
    consecutive_parse_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalState":
        return cls(
            goal=data.get("goal", ""),
            status=data.get("status", STATUS_ACTIVE),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or 0),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(
                data.get("consecutive_parse_failures", 0) or 0
            ),
        )


def _store_path() -> Path:
    return get_data_dir() / "goals.json"


def _read_all() -> dict[str, dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError) as err:
        LOGGER.debug("goals: failed to read store: %s", err)
    return {}


def _write_all(data: dict[str, dict[str, Any]]) -> None:
    import os
    import tempfile

    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".goals_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as err:
        LOGGER.debug("goals: failed to write store: %s", err, exc_info=True)


def load_goal(conversation_id: str) -> GoalState | None:
    if not conversation_id:
        return None
    data = _read_all()
    raw = data.get(conversation_id)
    if not raw:
        return None
    try:
        return GoalState.from_dict(raw)
    except Exception as err:
        LOGGER.debug("goals: parse failed for %s: %s", conversation_id, err)
        return None


async def async_load_goal(
    hass: HomeAssistant, conversation_id: str
) -> GoalState | None:
    if not conversation_id:
        return None
    return await hass.async_add_executor_job(load_goal, conversation_id)


async def async_save_goal(
    hass: HomeAssistant, conversation_id: str, state: GoalState
) -> None:
    if not conversation_id:
        return
    await hass.async_add_executor_job(save_goal, conversation_id, state)


def save_goal(conversation_id: str, state: GoalState) -> None:
    if not conversation_id:
        return
    data = _read_all()
    data[conversation_id] = state.to_dict()
    _write_all(data)


def delete_goal(conversation_id: str) -> None:
    if not conversation_id:
        return
    data = _read_all()
    if conversation_id in data:
        data.pop(conversation_id, None)
        _write_all(data)


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "… [truncated]"


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse_judge_response(raw: str) -> tuple[bool, str, bool]:
    if not raw:
        return False, "judge returned empty response", True

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]

    data: dict[str, Any] | None = None
    try:
        data = json.loads(text)
    except Exception:
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = None

    if not isinstance(data, dict):
        return False, f"judge reply was not JSON: {_truncate(raw, 200)!r}", True

    done_val = data.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in ("true", "yes", "1", "done")
    else:
        done = bool(done_val)
    reason = str(data.get("reason") or "").strip() or "no reason provided"
    return done, reason, False


def _resolve_agent_label(hass: HomeAssistant, agent_id: str) -> str:
    if not agent_id:
        return ""
    try:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        ent = ent_reg.async_get(agent_id)
        if ent:
            name = ent.name or ent.original_name
            if name:
                return name
        state = hass.states.get(agent_id)
        if state:
            friendly = state.attributes.get("friendly_name")
            if friendly:
                return friendly
        if "." in agent_id:
            return agent_id.split(".", 1)[1]
    except Exception:
        pass
    return agent_id


def _resolve_judge_chain(hass: HomeAssistant) -> list[str]:
    try:
        from ..const import (
            CONF_FALLBACK_AGENT,
            CONF_SECONDARY_FALLBACK_AGENT,
            DOMAIN,
        )
    except Exception:
        return []

    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return []
    options = entries[0].options
    chain: list[str] = []
    for key in (CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT):
        agent_id = str(options.get(key, "") or "").strip()
        if agent_id:
            chain.append(agent_id)
    return chain


REVIEW_JUDGE_SYSTEM_PROMPT = (
    "You are the 复审判官 (Review Judge) — the senior auditor of the judge chain. "
    "Your identity IS the review judge; when asked who you are, state that you are "
    "the 复审判官. You are a STRICT review judge auditing a primary judge's verdict on "
    "whether an autonomous assistant (running in a Ralph loop) achieved "
    "the user's goal.\n\n"
    "You receive: the goal, the assistant's most recent response, and the "
    "primary judge's verdict + reason. Your job is to rule whether the "
    "goal is truly done right now.\n\n"
    "Apply the same strict autonomy rule as the primary judge:\n"
    "DONE requires a concrete, verifiable deliverable in the response "
    "(produced content, executed action, reported result, definitive "
    "answer). The response is NOT done if it asks the user a clarifying "
    "question, offers options and waits, says the goal is vague / "
    "unclear / needs more info, says it is blocked, only plans without "
    "executing, or returns raw tool output without synthesis.\n\n"
    "Ambiguity in the goal is NEVER a reason to stop — the assistant must "
    "pick the most plausible interpretation and drive forward. If the "
    "primary judge approved an 'asked the user to clarify' response as "
    "DONE, OVERRIDE it to CONTINUE. Autonomy failures always lose.\n\n"
    "Reply ONLY with a single JSON object on one line. Do not call any "
    "tools. Do not wrap it in markdown. Do not add prose:\n"
    '{"done": <true|false>, "reason": "<one-sentence audit>"}'
)


REVIEW_JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Assistant's most recent response:\n{response}\n\n"
    "Primary judge's verdict: {prev_verdict}\n"
    "Primary judge's reason: {prev_reason}\n\n"
    "Audit the primary judge."
)


async def _call_judge(
    hass: HomeAssistant,
    *,
    agent_id: str,
    system_prompt: str,
    user_prompt: str,
    conversation_id: str,
    timeout: float,
) -> tuple[str, str, bool]:
    original_async_converse = get_runtime_store(hass).get("original_async_converse")
    if not callable(original_async_converse):
        return "continue", "judge channel unavailable", False

    judge_conversation_id = (
        f"goal-judge:{conversation_id}:{agent_id}:{int(time.time() * 1000)}"
    )

    try:
        result = await asyncio.wait_for(
            original_async_converse(
                hass,
                user_prompt,
                judge_conversation_id,
                None,
                None,
                agent_id,
                None,
                None,
                system_prompt,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        LOGGER.info("goal judge %s: timeout after %.1fs", agent_id, timeout)
        return "continue", "judge timeout", False
    except Exception as err:
        LOGGER.info("goal judge %s: call failed (%s) — fail-open", agent_id, err)
        return "continue", f"judge error: {type(err).__name__}", False

    raw = ""
    try:
        speech = result.response.speech if result and result.response else None
        if isinstance(speech, dict):
            plain = speech.get("plain", {})
            if isinstance(plain, dict):
                raw = plain.get("original_speech") or plain.get("speech") or ""
    except Exception:
        raw = ""

    done, reason, parse_failed = _parse_judge_response(raw)
    verdict = "done" if done else "continue"
    LOGGER.info(
        "goal judge %s: verdict=%s reason=%s",
        agent_id,
        verdict,
        _truncate(reason, 120),
    )
    return verdict, reason, parse_failed


async def judge_goal(
    hass: HomeAssistant,
    *,
    goal: str,
    last_response: str,
    conversation_id: str,
    timeout: float = DEFAULT_JUDGE_TIMEOUT,
) -> tuple[str, str, bool, list[str]]:
    if not goal.strip():
        return "skipped", "empty goal", False, []
    if not last_response.strip():
        return "continue", "empty response (nothing to evaluate)", False, []

    chain = _resolve_judge_chain(hass)
    if not chain:
        return "skipped", "no fallback agent configured", False, []

    truncated_goal = _truncate(goal, 2000)
    truncated_response = _truncate(last_response, _JUDGE_RESPONSE_SNIPPET_CHARS)

    labels: list[str] = []
    verdict = "continue"
    reason = ""
    parse_failed = False

    for idx, agent_id in enumerate(chain):
        labels.append(_resolve_agent_label(hass, agent_id))
        if idx == 0:
            system_prompt = JUDGE_SYSTEM_PROMPT
            user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
                goal=truncated_goal,
                response=truncated_response,
            )
        else:
            system_prompt = REVIEW_JUDGE_SYSTEM_PROMPT
            user_prompt = REVIEW_JUDGE_USER_PROMPT_TEMPLATE.format(
                goal=truncated_goal,
                response=truncated_response,
                prev_verdict=verdict,
                prev_reason=_truncate(reason, 600),
            )
        verdict, reason, parse_failed = await _call_judge(
            hass,
            agent_id=agent_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            conversation_id=conversation_id,
            timeout=timeout,
        )
    return verdict, reason, parse_failed, labels


class GoalManager:
    def __init__(
        self,
        hass: HomeAssistant,
        conversation_id: str,
        *,
        default_max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self.hass = hass
        self.conversation_id = conversation_id
        self.default_max_turns = int(default_max_turns or 0)
        self._state: GoalState | None = None
        self._loaded: bool = False

    async def async_ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._state = await async_load_goal(self.hass, self.conversation_id)
        self._loaded = True

    @property
    def state(self) -> GoalState | None:
        return self._state

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in (
            STATUS_ACTIVE,
            STATUS_PAUSED,
        )

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == STATUS_ACTIVE

    def status_line(self) -> str:
        lang = _resolve_language(self.hass)
        s = self._state
        if s is None or s.status == STATUS_CLEARED:
            return _msg("status_none", lang)
        if s.max_turns and s.max_turns > 0:
            turns = _msg("turns_with_budget", lang, used=s.turns_used, budget=s.max_turns)
        else:
            turns = _msg("turns_unbounded", lang, used=s.turns_used)
        if s.status == STATUS_ACTIVE:
            return _msg("status_active", lang, turns=turns, goal=s.goal)
        if s.status == STATUS_PAUSED:
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return _msg("status_paused", lang, turns=turns, extra=extra, goal=s.goal)
        if s.status == STATUS_DONE:
            return _msg("status_done", lang, turns=turns, goal=s.goal)
        return _msg("status_other", lang, status=s.status, turns=turns, goal=s.goal)

    async def async_set(self, goal: str, *, max_turns: int | None = None) -> GoalState:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        state = GoalState(
            goal=goal,
            status=STATUS_ACTIVE,
            turns_used=0,
            max_turns=int(max_turns) if max_turns is not None else self.default_max_turns,
            created_at=time.time(),
            last_turn_at=0.0,
        )
        self._state = state
        self._loaded = True
        await async_save_goal(self.hass, self.conversation_id, state)
        return state

    async def async_pause(self, reason: str = "user-paused") -> GoalState | None:
        if not self._state:
            return None
        self._state.status = STATUS_PAUSED
        self._state.paused_reason = reason
        await async_save_goal(self.hass, self.conversation_id, self._state)
        return self._state

    async def async_resume(self, *, reset_turns: bool = True) -> GoalState | None:
        if not self._state:
            return None
        self._state.status = STATUS_ACTIVE
        self._state.paused_reason = None
        self._state.consecutive_parse_failures = 0
        if reset_turns:
            self._state.turns_used = 0
        await async_save_goal(self.hass, self.conversation_id, self._state)
        return self._state

    async def async_clear(self) -> None:
        if self._state is None:
            return
        await self.hass.async_add_executor_job(delete_goal, self.conversation_id)
        self._state = None

    async def async_mark_done(self, reason: str) -> None:
        if not self._state:
            return
        self._state.status = STATUS_DONE
        self._state.last_verdict = "done"
        self._state.last_reason = reason
        await async_save_goal(self.hass, self.conversation_id, self._state)

    async def async_evaluate_after_turn(self, last_response: str) -> dict[str, Any]:
        await self.async_ensure_loaded()
        state = self._state
        if state is None or state.status != STATUS_ACTIVE:
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        state.turns_used += 1
        state.last_turn_at = time.time()

        verdict, reason, parse_failed, labels = await judge_goal(
            self.hass,
            goal=state.goal,
            last_response=last_response,
            conversation_id=self.conversation_id,
        )
        state.last_verdict = verdict
        state.last_reason = reason

        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0

        lang = _resolve_language(self.hass)
        label_suffix = "".join(f"[{lbl}]" for lbl in labels if lbl)

        def _stamp(text: str) -> str:
            if not text or not label_suffix:
                return text
            return f"{text} {label_suffix}"

        if verdict == "done":
            state.status = STATUS_DONE
            await async_save_goal(self.hass, self.conversation_id, state)
            return {
                "status": STATUS_DONE,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": _stamp(_msg("achieved", lang, reason=reason)),
            }

        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = STATUS_PAUSED
            state.paused_reason = (
                f"judge model returned unparseable output "
                f"{state.consecutive_parse_failures} turns in a row"
            )
            await async_save_goal(self.hass, self.conversation_id, state)
            return {
                "status": STATUS_PAUSED,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": _stamp(
                    _msg("paused_parse", lang, count=state.consecutive_parse_failures)
                ),
            }

        if state.max_turns and state.turns_used >= state.max_turns:
            state.status = STATUS_PAUSED
            state.paused_reason = (
                f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            )
            await async_save_goal(self.hass, self.conversation_id, state)
            return {
                "status": STATUS_PAUSED,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": _stamp(
                    _msg(
                        "paused_budget",
                        lang,
                        used=state.turns_used,
                        budget=state.max_turns,
                    )
                ),
            }

        await async_save_goal(self.hass, self.conversation_id, state)
        if state.max_turns and state.max_turns > 0:
            progress = f"{state.turns_used}/{state.max_turns}"
        else:
            progress = str(state.turns_used)
        return {
            "status": STATUS_ACTIVE,
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": _stamp(
                _msg("continuing", lang, progress=progress, reason=reason)
            ),
        }

    def next_continuation_prompt(self) -> str | None:
        if not self._state or self._state.status != STATUS_ACTIVE:
            return None
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)


_MANAGER_CACHE_KEY = "goal_managers"


def get_goal_manager(hass: HomeAssistant, conversation_id: str | None) -> GoalManager:
    key = conversation_id or "default"
    runtime_store = get_runtime_store(hass)
    cache = runtime_store.get(_MANAGER_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        runtime_store[_MANAGER_CACHE_KEY] = cache
    mgr = cache.get(key)
    if isinstance(mgr, GoalManager):
        return mgr
    mgr = GoalManager(hass, key)
    cache[key] = mgr
    return mgr


def is_continuation_prompt(text: str | None) -> bool:
    if not text:
        return False
    return CONTINUATION_MARKER in text[:200]


__all__ = ["get_goal_manager", "is_continuation_prompt", "localised_message"]
