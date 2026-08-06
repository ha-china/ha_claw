"""Passive habit detector (G3) — observe repeated user patterns above signal_capture.

Sits on top of ``signal_capture``: every captured passive signal (reminder /
schedule) is recorded as a lightweight observation carrying a behavior key and
an hour-of-day bucket. When the same key appears in the same hour bucket at
least ``min_frequency`` times within a rolling window, a *routine candidate* is
produced with ``confidence = frequency / window_size``.

Design notes
------------
- Keep the core pure: ``detect_candidates_from_observations`` is a plain
  function over ``HabitObservation`` values, so it is trivially unit-testable
  without Home Assistant.
- File-backed observation store lives at
  ``<data_dir>/workspace/memory/habit-observations.json`` (absent file == no
  observations).
- Pluggable: callers may supply their own observations source; the default
  ``record_observation`` / ``detect_candidates`` pair is used by the
  ``signal_capture`` hook.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

_OBSERVATIONS_RELATIVE_PATH = Path("workspace") / "memory" / "habit-observations.json"

# Defaults for the lightweight frequency heuristic.
DEFAULT_MIN_FREQUENCY = 3
DEFAULT_WINDOW_DAYS = 14
DEFAULT_MIN_CONFIDENCE = 0.3
_CATEGORY_ROUTINE = "routine"


def _observations_path() -> Path:
    """Return the absolute path of the observation store."""
    return get_data_dir() / _OBSERVATIONS_RELATIVE_PATH


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True, frozen=True)
class HabitObservation:
    """A single observation of a repeated behavior.

    ``key`` is the normalized behavior key (entity id or the signal title /
    objective). ``hour`` is the hour-of-day bucket (0-23). ``occurred_at`` is
    the ISO timestamp of the capture.
    """

    key: str
    hour: int
    occurred_at: str = ""
    source: str = ""
    text: str = ""


@dataclass(slots=True, frozen=True)
class HabitCandidate:
    """A routine candidate detected from repeated observations."""

    key: str
    hour: int
    frequency: int
    window_size: int
    confidence: float
    pattern: str
    source: str = ""
    category: str = _CATEGORY_ROUTINE


def build_pattern(key: str, hour: int) -> str:
    """Build a human-readable Chinese routine pattern."""
    key_text = str(key or "").strip() or "该行为"
    return f"你常在 {hour:02d} 点做「{key_text}」"


def load_observations() -> list[dict[str, Any]]:
    """Load all recorded observations (absent/corrupt file -> empty list)."""
    path = _observations_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Failed to load habit observations: %s", exc)
    return []


def save_observations(observations: list[dict[str, Any]]) -> Path:
    """Persist observations to disk, creating parent directories as needed."""
    path = _observations_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(observations, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("Failed to write habit observations: %s", exc)
    return path


def record_observation(observation: HabitObservation) -> Path:
    """Append a single observation to the store and return the file path."""
    observations = load_observations()
    observations.append(
        {
            "key": str(observation.key or "").strip()[:128],
            "hour": int(observation.hour) % 24,
            "occurred_at": observation.occurred_at or _now_iso(),
            "source": observation.source,
            "text": observation.text,
        }
    )
    # Keep the store bounded (last 1000 observations).
    if len(observations) > 1000:
        observations = observations[-1000:]
    return save_observations(observations)


def detect_candidates_from_observations(
    observations: list[dict[str, Any]],
    *,
    min_frequency: int = DEFAULT_MIN_FREQUENCY,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[HabitCandidate]:
    """Detect routine candidates from raw observation dicts.

    Groups observations by (key, hour). For each group the frequency is the
    number of observations; the window size is the number of distinct calendar
    days spanned by *all* observations (capped at ``window_days``); confidence
    is ``frequency / window_size`` capped at 1.0. A candidate is returned when
    ``frequency >= min_frequency`` and ``confidence >= min_confidence``.
    """
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    days: set[str] = set()
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        key = str(obs.get("key") or "").strip()
        if not key:
            continue
        try:
            hour = int(obs.get("hour", 0)) % 24
        except (TypeError, ValueError):
            hour = 0
        groups.setdefault((key, hour), []).append(obs)
        occurred = str(obs.get("occurred_at") or "")
        if occurred:
            days.add(occurred[:10])

    window_size = max(1, min(int(window_days), max(1, len(days))))
    candidates: list[HabitCandidate] = []
    for (key, hour), items in groups.items():
        frequency = len(items)
        if frequency < max(1, int(min_frequency)):
            continue
        confidence = min(1.0, frequency / window_size)
        if confidence < min_confidence:
            continue
        source = ""
        for item in items:
            if item.get("source"):
                source = str(item["source"])
                break
        candidates.append(
            HabitCandidate(
                key=key,
                hour=hour,
                frequency=frequency,
                window_size=window_size,
                confidence=round(confidence, 4),
                pattern=build_pattern(key, hour),
                source=source,
                category=_CATEGORY_ROUTINE,
            )
        )
    # Most confident first.
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def detect_candidates(
    *,
    min_frequency: int = DEFAULT_MIN_FREQUENCY,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[HabitCandidate]:
    """Detect routine candidates from the on-disk observation store."""
    return detect_candidates_from_observations(
        load_observations(),
        min_frequency=min_frequency,
        window_days=window_days,
        min_confidence=min_confidence,
    )


def clear_observations() -> Path:
    """Remove all recorded observations (used by tests / maintenance)."""
    return save_observations([])


def observation_to_dict(observation: HabitObservation) -> dict[str, Any]:
    """Serialize a HabitObservation (handy for callers / tests)."""
    return asdict(observation)
