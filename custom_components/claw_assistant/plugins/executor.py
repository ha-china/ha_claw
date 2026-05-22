from __future__ import annotations

import concurrent.futures

_PLUGIN_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def get_plugin_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _PLUGIN_EXECUTOR
    if _PLUGIN_EXECUTOR is None:
        _PLUGIN_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="claw_plugin_"
        )
    return _PLUGIN_EXECUTOR


def shutdown_plugin_executor() -> None:
    global _PLUGIN_EXECUTOR
    if _PLUGIN_EXECUTOR is not None:
        _PLUGIN_EXECUTOR.shutdown(wait=False)
        _PLUGIN_EXECUTOR = None
