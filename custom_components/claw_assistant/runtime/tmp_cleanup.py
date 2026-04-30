"""Hourly cleanup of the ExecutePython tmp artefact directory.

Files (or empty sub-directories) older than :data:`TMP_RETENTION_HOURS`
inside ``<config>/.storage/claw_assistant/tmp/`` are removed. The cleanup
runs in an executor job so file-system I/O never blocks the event loop.

Lifecycle hooks:
    * :func:`async_setup_tmp_cleanup` — register interval + run once.
    * :func:`async_unload_tmp_cleanup` — cancel the registered listener.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .data_path import TMP_RETENTION_HOURS, get_tmp_dir

LOGGER = logging.getLogger(__name__)

_CLEAN_INTERVAL = timedelta(hours=1)
_DATA_KEY = "claw_assistant_tmp_cleanup_unsub"


def _sweep(tmp_dir: Path, retention_seconds: float) -> tuple[int, int]:
    """Delete files older than ``retention_seconds``. Returns (files, bytes)."""

    if not tmp_dir.is_dir():
        return 0, 0

    cutoff = time.time() - retention_seconds
    removed_files = 0
    removed_bytes = 0

    for path in sorted(tmp_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                stat = path.stat()
                if stat.st_mtime < cutoff:
                    removed_bytes += stat.st_size
                    path.unlink()
                    removed_files += 1
            elif path.is_dir():
                try:
                    next(path.iterdir())
                except StopIteration:
                    if path != tmp_dir:
                        path.rmdir()
        except OSError as err:
            LOGGER.debug("tmp cleanup skipped %s: %s", path, err)

    return removed_files, removed_bytes


async def async_setup_tmp_cleanup(hass: HomeAssistant) -> None:
    """Register the hourly sweeper and run an initial pass."""

    if _DATA_KEY in hass.data:
        return

    tmp_dir = get_tmp_dir(hass)
    retention_seconds = float(TMP_RETENTION_HOURS) * 3600.0

    async def _tick(_now) -> None:
        files, total_bytes = await hass.async_add_executor_job(
            _sweep, tmp_dir, retention_seconds
        )
        if files:
            LOGGER.info(
                "Pruned %d tmp file(s) (%d bytes) older than %dh from %s",
                files,
                total_bytes,
                TMP_RETENTION_HOURS,
                tmp_dir,
            )

    await hass.async_add_executor_job(_sweep, tmp_dir, retention_seconds)

    unsub = async_track_time_interval(hass, _tick, _CLEAN_INTERVAL)
    hass.data[_DATA_KEY] = unsub
    LOGGER.debug(
        "tmp_cleanup armed: dir=%s retention=%dh interval=%s",
        tmp_dir,
        TMP_RETENTION_HOURS,
        _CLEAN_INTERVAL,
    )


async def async_unload_tmp_cleanup(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_DATA_KEY, None)
    if unsub is not None:
        try:
            unsub()
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("tmp_cleanup unload error: %s", err)
