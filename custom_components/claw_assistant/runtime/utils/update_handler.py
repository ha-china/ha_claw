from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from aiohttp import ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

LOGGER = logging.getLogger(__name__)

REPO_FULL_NAME = "ha-china/ha_claw"

GH_PROXY = "https://gh-proxy.org/"
GITHUB_DOWNLOAD_HOSTS = (
    "https://github.com/",
    "https://raw.githubusercontent.com/",
    "https://codeload.github.com/",
    "https://objects.githubusercontent.com/",
)

_API_RELEASES = f"https://api.github.com/repos/{REPO_FULL_NAME}/releases/latest"
_API_COMMITS = f"https://api.github.com/repos/{REPO_FULL_NAME}/commits/master"
_API_RELEASES_PROXIED = f"{GH_PROXY}{_API_RELEASES}"
_API_COMMITS_PROXIED = f"{GH_PROXY}{_API_COMMITS}"

CHECK_INTERVAL = timedelta(hours=2)
_UNSUB_KEY = "claw_assistant_update_handler_unsub"
_PROXY_PATCHED_KEY = "_claw_hacs_proxy_patched"
_PROXY_ORIGINAL_KEY = "_claw_hacs_original_download"


def _get_hacs(hass: HomeAssistant) -> Any | None:
    return hass.data.get("hacs")


def _find_our_repo(hacs: Any) -> Any | None:
    if not hacs or not hasattr(hacs, "repositories"):
        return None
    try:
        repo = hacs.repositories.get_by_full_name(REPO_FULL_NAME)
        if repo is not None:
            return repo
    except Exception:
        pass
    try:
        for r in hacs.repositories.list_downloaded:
            fn = getattr(getattr(r, "data", None), "full_name", "")
            dm = getattr(getattr(r, "data", None), "domain", "")
            if fn == REPO_FULL_NAME or dm == "claw_assistant":
                return r
    except Exception:
        pass
    return None


async def _api_get_json(session, url: str, timeout: int = 20) -> dict | None:
    try:
        async with session.get(
            url,
            timeout=ClientTimeout(total=timeout),
            headers={"Accept": "application/vnd.github.v3+json"},
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            LOGGER.debug("API %s => %s", url, resp.status)
    except Exception as exc:
        LOGGER.debug("API %s failed: %s", url, exc)
    return None


async def _fetch_latest_version(session) -> tuple[str | None, str | None]:
    tag = None
    sha = None

    data = await _api_get_json(session, _API_RELEASES_PROXIED)
    if data is None:
        data = await _api_get_json(session, _API_RELEASES)
    if data:
        tag = data.get("tag_name")

    data = await _api_get_json(session, _API_COMMITS_PROXIED)
    if data is None:
        data = await _api_get_json(session, _API_COMMITS)
    if data:
        sha = data.get("sha")

    return tag, sha


def _inject_proxy(hacs: Any) -> bool:
    if getattr(hacs, _PROXY_PATCHED_KEY, False):
        return True
    original = getattr(hacs, "async_download_file", None)
    if not callable(original):
        LOGGER.debug("HACS has no async_download_file, skip proxy injection")
        return False

    async def proxied_download(url: str, **kwargs: Any) -> bytes | None:
        if url and any(url.startswith(h) for h in GITHUB_DOWNLOAD_HOSTS):
            proxied_url = GH_PROXY + url
            LOGGER.debug("HACS download via proxy: %s", proxied_url)
            try:
                result = await original(proxied_url, **kwargs)
                if result is not None:
                    return result
            except Exception:
                pass
            LOGGER.debug("Proxy failed, fallback direct: %s", url)
        return await original(url, **kwargs)

    proxied_download.__wrapped__ = original
    hacs.async_download_file = proxied_download
    setattr(hacs, _PROXY_PATCHED_KEY, True)
    setattr(hacs, _PROXY_ORIGINAL_KEY, original)
    LOGGER.info("Injected GitHub proxy into HACS download pipeline")
    return True


def _notify_hacs_update(hacs: Any, repo: Any, upstream_version: str) -> None:
    installed = None
    try:
        installed = repo.display_installed_version
    except Exception:
        installed = getattr(getattr(repo, "data", None), "installed_version", None)

    if installed and str(installed) == str(upstream_version):
        LOGGER.debug("Already up to date: %s", installed)
        return

    try:
        repo.data.last_version = upstream_version
        tags = getattr(repo.data, "published_tags", None) or []
        repo.data.published_tags = [upstream_version] + [
            t for t in tags if t != upstream_version
        ]
    except Exception:
        LOGGER.debug("Failed to set version on repo data", exc_info=True)
        return

    LOGGER.info(
        "HACS notified: %s update %s -> %s", REPO_FULL_NAME, installed, upstream_version,
    )

    if hasattr(hacs, "async_dispatch"):
        try:
            hacs.async_dispatch("hacs_dispatch_repository", {})
        except Exception:
            pass

    coordinators = getattr(hacs, "coordinators", None)
    if isinstance(coordinators, dict):
        for coordinator in coordinators.values():
            try:
                coordinator.async_update_listeners()
            except Exception:
                pass


def _clear_skipped_version(hass: HomeAssistant) -> None:
    entity_id = None
    for eid in hass.states.async_entity_ids("update"):
        state = hass.states.get(eid)
        if not state:
            continue
        name = (state.attributes.get("friendly_name") or "").lower()
        if "claw" in name or REPO_FULL_NAME.lower() in name:
            entity_id = eid
            break
    if not entity_id:
        return
    state = hass.states.get(entity_id)
    if not state:
        return
    skipped = state.attributes.get("skipped_version")
    if not skipped:
        return
    LOGGER.debug("Clearing skipped_version=%s on %s", skipped, entity_id)
    try:
        from homeassistant.helpers import entity_component
        component = hass.data.get("entity_components", {}).get("update")
        if component is None:
            component = hass.data.get("update")
        if component and hasattr(component, "async_get_entity"):
            entity = component.async_get_entity(entity_id)
        else:
            entity = None
        if entity is not None and hasattr(entity, "async_clear_skipped"):
            hass.async_create_task(entity.async_clear_skipped())
            LOGGER.info("Cleared skipped_version on %s", entity_id)
            return
    except Exception:
        pass
    try:
        hass.async_create_task(
            hass.services.async_call(
                "update", "clear_skipped", {"entity_id": entity_id}
            )
        )
        LOGGER.info("Cleared skipped_version via service call on %s", entity_id)
    except Exception:
        LOGGER.debug("Failed to clear skipped_version on %s", entity_id, exc_info=True)


_NOTIFICATION_ID = "claw_assistant_update"


def _post_update_notification(hass: HomeAssistant, installed: str, upstream: str) -> None:
    from homeassistant.components.persistent_notification import async_create
    async_create(
        hass,
        title="🔔 Claw Assistant 有新版本",
        message=(
            f"当前版本: **{installed}**\n\n"
            f"最新版本: **{upstream}**\n\n"
            f"请前往 **HACS → Claw Assistant → 更新** 安装新版本，"
            f"更新后需重启 Home Assistant。"
        ),
        notification_id=_NOTIFICATION_ID,
    )


def _dismiss_update_notification(hass: HomeAssistant) -> None:
    try:
        from homeassistant.components.persistent_notification import async_dismiss
        async_dismiss(hass, _NOTIFICATION_ID)
    except Exception:
        pass


async def async_check_update(hass: HomeAssistant, _=None) -> None:
    hacs = _get_hacs(hass)
    if hacs is None:
        LOGGER.debug("HACS not loaded, skip")
        return

    _inject_proxy(hacs)

    repo = _find_our_repo(hacs)
    if repo is None:
        LOGGER.debug("claw_assistant repo not found in HACS")
        return

    session = getattr(hacs, "session", None) or async_get_clientsession(hass)

    tag, sha = await _fetch_latest_version(session)
    upstream = tag or sha
    if not upstream:
        LOGGER.debug("Could not determine upstream version")
        return

    installed = getattr(repo, "display_installed_version", None)
    if installed is None:
        installed = getattr(getattr(repo, "data", None), "installed_version", "?")

    LOGGER.debug(
        "Upstream: tag=%s sha=%s installed=%s",
        tag, sha[:8] if sha else None, installed,
    )

    if str(installed) == str(upstream):
        _dismiss_update_notification(hass)
        return

    _notify_hacs_update(hacs, repo, upstream)
    _clear_skipped_version(hass)
    _post_update_notification(hass, str(installed), upstream)


def async_setup_update_handler(hass: HomeAssistant) -> None:
    if hass.data.get(_UNSUB_KEY):
        return

    async def _delayed_first_check() -> None:
        for attempt in range(10):
            await asyncio.sleep(60 + attempt * 30)
            hacs = _get_hacs(hass)
            if hacs is not None and hasattr(hacs, "repositories"):
                break
        await async_check_update(hass)

    hass.async_create_background_task(_delayed_first_check(), "claw_update_first_check")
    unsub = async_track_time_interval(hass, async_check_update, CHECK_INTERVAL)
    hass.data[_UNSUB_KEY] = unsub
    LOGGER.info("Update handler started (interval=%s)", CHECK_INTERVAL)


def async_unload_update_handler(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_UNSUB_KEY, None)
    if callable(unsub):
        unsub()
    hacs = _get_hacs(hass)
    if hacs and getattr(hacs, _PROXY_PATCHED_KEY, False):
        original = getattr(hacs, _PROXY_ORIGINAL_KEY, None)
        if callable(original):
            hacs.async_download_file = original
        for key in (_PROXY_PATCHED_KEY, _PROXY_ORIGINAL_KEY):
            try:
                delattr(hacs, key)
            except Exception:
                pass
    LOGGER.info("Update handler unloaded")
