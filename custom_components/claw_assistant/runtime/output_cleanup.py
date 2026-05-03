"""Cleanup for browser-reachable media artefacts in OUTPUT_DIR.

Media files under ``<config>/www/claw_assistant/`` are treated as disposable
preview assets and are deleted hourly once they are older than
``OUTPUT_MEDIA_RETENTION_HOURS``.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .data_path import (
    OUTPUT_MEDIA_RETENTION_HOURS,
    get_output_dir,
    is_output_media_file,
)

LOGGER = logging.getLogger(__name__)

_CLEAN_INTERVAL = timedelta(hours=1)
_DATA_KEY = "claw_assistant_output_cleanup_unsub"


def _sweep_output_media(
    output_dir: Path,
    retention_seconds: float | None,
) -> tuple[int, int]:
    if not output_dir.is_dir():
        return 0, 0

    cutoff = time.time() - retention_seconds if retention_seconds is not None else None
    removed_files = 0
    removed_bytes = 0

    for path in sorted(output_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                if not is_output_media_file(path):
                    continue
                stat = path.stat()
                if cutoff is None or stat.st_mtime < cutoff:
                    removed_bytes += stat.st_size
                    path.unlink()
                    removed_files += 1
            elif path.is_dir():
                try:
                    next(path.iterdir())
                except StopIteration:
                    if path != output_dir:
                        path.rmdir()
        except OSError as err:
            LOGGER.debug("output cleanup skipped %s: %s", path, err)

    return removed_files, removed_bytes

async def async_setup_output_cleanup(hass: HomeAssistant) -> None:
    if _DATA_KEY in hass.data:
        return

    output_dir = get_output_dir(hass)
    retention_seconds = float(OUTPUT_MEDIA_RETENTION_HOURS) * 3600.0

    async def _tick(_now) -> None:
        files, total_bytes = await hass.async_add_executor_job(
            _sweep_output_media,
            output_dir,
            retention_seconds,
        )
        if files:
            LOGGER.info(
                "Pruned %d output media file(s) (%d bytes) older than %dh from %s",
                files,
                total_bytes,
                OUTPUT_MEDIA_RETENTION_HOURS,
                output_dir,
            )

    await hass.async_add_executor_job(
        _sweep_output_media,
        output_dir,
        retention_seconds,
    )

    unsub = async_track_time_interval(hass, _tick, _CLEAN_INTERVAL)
    hass.data[_DATA_KEY] = unsub
    LOGGER.debug(
        "output_cleanup armed: dir=%s retention=%dh interval=%s",
        output_dir,
        OUTPUT_MEDIA_RETENTION_HOURS,
        _CLEAN_INTERVAL,
    )


async def async_unload_output_cleanup(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_DATA_KEY, None)
    if unsub is not None:
        try:
            unsub()
        except Exception as err:
            LOGGER.debug("output_cleanup unload error: %s", err)
