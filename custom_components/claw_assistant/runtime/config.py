

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeThresholds:


    max_task_iterations: int = 50
    coordinator_min_iteration: int = 2
    response_duplicate_similarity: float = 0.85
    response_auto_final_length: int = 300
    response_complete_indicator_min_length: int = 100
    max_continuations_per_turn: int = 5
    context_ttl_seconds: int = 1800
    context_cleanup_interval_seconds: int = 300
    context_cleanup_error_retry_seconds: int = 60


DEFAULT_THRESHOLDS = RuntimeThresholds()

DEFAULT_FALLBACK_AGENT_ID = "conversation.home_assistant"
