from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import quote, urlparse

import aiohttp
import voluptuous as vol
import voluptuous_serialize
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.loader import (
    Integration,
    IntegrationNotFound,
    async_get_config_flows,
    async_get_integration_descriptions,
    async_get_integrations,
)
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)


_SHELL_MAX_OUTPUT = 64 * 1024
_SHELL_DEFAULT_TIMEOUT = 30
_SHELL_MAX_TIMEOUT = 600
_HA_TOKEN_RE = re.compile(r"ha_token\s*[:：]\s*`?([A-Za-z0-9._\-]+)`?")


def _read_ha_token_from_tools() -> str | None:
    from ..runtime.workspace_store import get_workspace_doc
    try:
        doc = get_workspace_doc("TOOLS")
        text = doc.get("markdown", "")
        m = _HA_TOKEN_RE.search(text)
        return m.group(1) if m else None
    except Exception:
        return None


async def _rest_fallback(hass: HomeAssistant, endpoint: str, params: dict) -> dict | None:
    token = _read_ha_token_from_tools()
    if not token:
        return None
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    session = async_get_clientsession(hass)
    port = getattr(getattr(hass, "http", None), "server_port", None) or 8123
    url = f"http://127.0.0.1:{port}{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        timeout_s = int(params.get("timeout", 15)) if isinstance(params, dict) else 15
        resp = await session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout_s))
        text = await resp.text()
        if len(text) > _SHELL_MAX_OUTPUT:
            text = text[:_SHELL_MAX_OUTPUT] + f"\n...[truncated]"
        if resp.status >= 400:
            return {"success": False, "error": f"REST {resp.status}: {text[:200]}"}
        return {"success": True, "body": text, "status": resp.status, "via": "rest"}
    except Exception as err:
        _LOGGER.debug("REST fallback failed for %s: %s", endpoint, err)
        return None


async def _run_shell(hass: HomeAssistant, params: dict) -> JsonObjectType:

    command = str(params.get("command", "")).strip()
    if not command:
        return {"success": False, "error": "Missing required parameter: command"}

    raw_timeout = params.get("timeout", _SHELL_DEFAULT_TIMEOUT) or _SHELL_DEFAULT_TIMEOUT
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        timeout = _SHELL_DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, _SHELL_MAX_TIMEOUT))

    cwd = params.get("cwd") or hass.config.config_dir

    started = time.monotonic()
    tmp_script = None
    try:
        if "\n" in command or "<<" in command:
            import tempfile
            tmp_script = tempfile.NamedTemporaryFile(
                mode="w", suffix=".sh", dir=cwd, delete=False
            )
            tmp_script.write(command)
            tmp_script.flush()
            tmp_script.close()
            exec_cmd = f"/bin/sh {tmp_script.name}"
        else:
            exec_cmd = command
        proc = await asyncio.create_subprocess_shell(
            exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except Exception as err:
        if tmp_script:
            import os
            os.unlink(tmp_script.name)
        return {"success": False, "error": f"spawn failed: {err}"}

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "success": False,
            "error": f"command exceeded {timeout}s",
            "timeout": True,
            "elapsed": round(time.monotonic() - started, 3),
        }
    finally:
        if tmp_script:
            import os
            try:
                os.unlink(tmp_script.name)
            except OSError:
                pass

    elapsed = round(time.monotonic() - started, 3)
    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    if len(stdout) > _SHELL_MAX_OUTPUT:
        stdout = stdout[:_SHELL_MAX_OUTPUT] + f"\n...[truncated, {len(stdout)} bytes total]"
    if len(stderr) > _SHELL_MAX_OUTPUT:
        stderr = stderr[:_SHELL_MAX_OUTPUT] + f"\n...[truncated, {len(stderr)} bytes total]"

    rc = proc.returncode if proc.returncode is not None else -1
    return {
        "success": rc == 0,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed": elapsed,
        "cwd": str(cwd),
    }


async def _run_ssh(params: dict) -> JsonObjectType:
    import asyncssh

    host = str(params.get("host", "")).strip()
    if not host:
        return {"success": False, "error": "Missing required parameter: host"}
    command = str(params.get("command", "")).strip()
    if not command:
        return {"success": False, "error": "Missing required parameter: command"}

    username = str(params.get("username", "root")).strip()
    password = params.get("password") or None
    port = int(params.get("port", 22) or 22)
    timeout = max(1, min(int(params.get("timeout", _SHELL_DEFAULT_TIMEOUT) or _SHELL_DEFAULT_TIMEOUT), _SHELL_MAX_TIMEOUT))

    connect_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,
    }
    if password:
        connect_kwargs["password"] = password
    key_file = params.get("key_file") or None
    if key_file:
        connect_kwargs["client_keys"] = [key_file]

    started = time.monotonic()
    try:
        async with asyncssh.connect(**connect_kwargs) as conn:
            result = await asyncio.wait_for(conn.run(command), timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "success": False,
            "error": f"SSH command exceeded {timeout}s",
            "timeout": True,
            "elapsed": round(time.monotonic() - started, 3),
        }
    except Exception as err:
        return {"success": False, "error": f"SSH connection/execution failed: {err}"}

    elapsed = round(time.monotonic() - started, 3)
    stdout = (result.stdout or "")
    stderr = (result.stderr or "")
    if len(stdout) > _SHELL_MAX_OUTPUT:
        stdout = stdout[:_SHELL_MAX_OUTPUT] + f"\n...[truncated, {len(stdout)} bytes total]"
    if len(stderr) > _SHELL_MAX_OUTPUT:
        stderr = stderr[:_SHELL_MAX_OUTPUT] + f"\n...[truncated, {len(stderr)} bytes total]"

    rc = result.exit_status if result.exit_status is not None else -1
    return {
        "success": rc == 0,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed": elapsed,
        "host": host,
    }


def _normalize_repo_source(value: str) -> dict[str, str]:

    raw = value.strip()
    if not raw:
        return {"raw": "", "repository": "", "source_url": "", "host": ""}

    host = ""
    source_url = ""
    repository = ""

    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.lower()
        source_url = raw
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            repository = f"{parts[0]}/{parts[1]}".rstrip("/")
            if repository.endswith(".git"):
                repository = repository[:-4]
    else:
        repository = raw.split("?")[0].split("#")[0].rstrip("/")

    if repository:
        repository = repository.strip("/")
        repository = repository[:-4] if repository.endswith(".git") else repository

    return {
        "raw": raw,
        "repository": repository,
        "source_url": source_url,
        "host": host,
    }


async def _search_github_repositories(query: str) -> list[dict[str, object]]:

    if not query.strip():
        return []

    results: list[dict[str, object]] = []
    async with aiohttp.ClientSession() as session:
        for search_query in [query, f"{query} home assistant", f"{query} hass"]:
            async with session.get(
                f"https://api.github.com/search/repositories?q={quote(search_query)}&sort=stars&per_page=10",
                headers={"Accept": "application/vnd.github.v3+json"},
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for item in data.get("items", []):
                    full_name = item.get("full_name")
                    if full_name and not any(
                        result["full_name"] == full_name for result in results
                    ):
                        results.append(
                            {
                                "name": item.get("name"),
                                "full_name": full_name,
                                "description": (item.get("description") or "")[:300],
                                "stars": item.get("stargazers_count"),
                                "html_url": item.get("html_url", ""),
                            }
                        )
                        if len(results) >= 15:
                            break
            if len(results) >= 15:
                break

    results.sort(key=lambda item: int(item.get("stars", 0) or 0), reverse=True)
    return results[:15]


def _find_repo_by_query(hacs_data, query: str):
    query_lower = query.lower().strip()
    if not query_lower:
        return None
    for repo in hacs_data.repositories.list_all:
        haystacks = [
            str(repo.data.name or "").lower(),
            str(repo.data.full_name or "").lower(),
            str(repo.data.description or "").lower(),
            " ".join(repo.data.topics or []).lower(),
        ]
        if any(query_lower in hay for hay in haystacks):
            return repo
    return None


def _serialize_hacs_repo(repo) -> dict[str, object]:
    latest = repo.data.last_version or repo.data.last_commit
    desc = repo.data.description or ""
    return {
        "id": str(getattr(repo.data, "id", "")),
        "name": repo.data.name,
        "full_name": repo.data.full_name,
        "description": desc[:300] if desc else "",
        "installed": bool(repo.data.installed),
        "installed_version": repo.data.installed_version,
        "latest": latest,
        "update_available": bool(
            repo.data.installed and latest and latest != repo.data.installed_version
        ),
        "domain": getattr(repo.data, "domain", None),
        "category": str(getattr(repo.data, "category", "")),
        "stars": getattr(repo.data, "stargazers_count", 0),
        "topics": list(getattr(repo.data, "topics", []) or []),
        "show_beta": bool(getattr(repo.data, "show_beta", False)),
        "selected_tag": getattr(repo.data, "selected_tag", None),
        "state": getattr(repo, "state", None),
        "default_branch": getattr(repo.data, "default_branch", None),
    }


def _prepare_flow_result_json(
    result: data_entry_flow.FlowResult,
    *,
    include_entry_result: bool = False,
    configure_action: str = "config_entries/flow/configure",
) -> dict[str, object]:
    flow_type = result.get("type")

    if flow_type == data_entry_flow.FlowResultType.CREATE_ENTRY:
        data: dict[str, object] = {
            "type": "create_entry",
            "title": result.get("title", ""),
            "domain": result.get("handler", ""),
            "version": result.get("version"),
        }
        if include_entry_result and "result" in result:
            entry: config_entries.ConfigEntry = result["result"]
            data["entry_id"] = entry.entry_id
            data["entry_title"] = entry.title
            data["entry_domain"] = entry.domain
            data["entry_state"] = str(entry.state)
            data["entry_options"] = dict(entry.options) if entry.options else {}
            data["entry_data_keys"] = list((entry.data or {}).keys())
        data["next_action"] = "done — entry created successfully. To create another, call config_entries/flow/init again with the same handler."
        return data

    if flow_type == data_entry_flow.FlowResultType.ABORT:
        return {
            "type": "abort",
            "reason": result.get("reason", "unknown"),
            "description_placeholders": result.get("description_placeholders"),
            "next_action": "aborted — no further action needed",
        }

    if flow_type == data_entry_flow.FlowResultType.EXTERNAL_STEP:
        flow_id = result.get("flow_id")
        return {
            "type": "external",
            "flow_id": flow_id,
            "step_id": result.get("step_id"),
            "url": result.get("url", ""),
            "example_call": {"action": configure_action, "params": {"flow_id": flow_id}},
            "next_action": f"Tell user to open the URL in browser to complete authentication. Then call ConfigEntries with action='{configure_action}' to check completion.",
        }

    if flow_type == data_entry_flow.FlowResultType.EXTERNAL_STEP_DONE:
        flow_id = result.get("flow_id")
        return {
            "type": "external_done",
            "flow_id": flow_id,
            "example_call": {"action": configure_action, "params": {"flow_id": flow_id}},
            "next_action": f"Call ConfigEntries with action='{configure_action}' to proceed.",
        }

    if flow_type == data_entry_flow.FlowResultType.SHOW_PROGRESS:
        flow_id = result.get("flow_id")
        return {
            "type": "progress",
            "flow_id": flow_id,
            "step_id": result.get("step_id"),
            "progress_action": result.get("progress_action"),
            "example_call": {"action": configure_action, "params": {"flow_id": flow_id}},
            "next_action": f"Wait a few seconds, then call ConfigEntries with action='{configure_action}' to check if progress is done.",
        }

    if flow_type == data_entry_flow.FlowResultType.SHOW_PROGRESS_DONE:
        flow_id = result.get("flow_id")
        return {
            "type": "progress_done",
            "flow_id": flow_id,
            "example_call": {"action": configure_action, "params": {"flow_id": flow_id}},
            "next_action": f"Call ConfigEntries with action='{configure_action}' to proceed to next step.",
        }

    if flow_type == data_entry_flow.FlowResultType.MENU:
        flow_id = result.get("flow_id")
        menu_options = result.get("menu_options", [])
        return {
            "type": "menu",
            "flow_id": flow_id,
            "step_id": result.get("step_id"),
            "menu_options": menu_options,
            "example_call": {
                "action": configure_action,
                "params": {"flow_id": flow_id, "next_step_id": menu_options[0] if menu_options else ""},
            },
            "next_action": f"Call ConfigEntries with action='{configure_action}'. Set next_step_id to one of: {menu_options}",
        }

    flow_id = result.get("flow_id")
    step_id = result.get("step_id")
    schema = result.get("data_schema")
    fields: list[dict[str, object]] = []
    if schema is not None:
        serialized = voluptuous_serialize.convert(
            schema, custom_serializer=cv.custom_serializer
        )
        fields = _extract_serialized_schema_fields(serialized)
    example_params: dict[str, object] = {"flow_id": flow_id}
    for f in fields:
        name = f.get("name", "")
        if not name:
            continue
        if f.get("default") is not None:
            example_params[name] = f["default"]
        elif f.get("options"):
            example_params[name] = f["options"][0] if f["options"] else ""
        elif f.get("selector") == "boolean":
            example_params[name] = False
        elif f.get("type") in ("integer", "positive_int"):
            example_params[name] = f.get("min", 0)
        elif f.get("type") == "float":
            example_params[name] = f.get("min", 0.0)
        else:
            example_params[name] = ""
    return {
        "type": "form",
        "flow_id": flow_id,
        "step_id": step_id,
        "fields": fields,
        "errors": result.get("errors"),
        "example_call": {
            "action": configure_action,
            "params": example_params,
        },
        "next_action": f"Call ConfigEntries with action='{configure_action}' and params shown in example_call. Fill in the field values.",
    }


def _extract_serialized_schema_fields(
    serialized_schema: object,
) -> list[dict[str, object]]:

    if not isinstance(serialized_schema, list):
        return []

    fields: list[dict[str, object]] = []
    for item in serialized_schema:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue

        field: dict[str, object] = {"name": name}
        if "required" in item:
            field["required"] = bool(item["required"])
        if "default" in item:
            field["default"] = item["default"]
        if "type" in item and item["type"] not in (None, ""):
            field["type"] = item["type"]
        if "value" in item:
            field["value"] = item["value"]

        selector = item.get("selector")
        if isinstance(selector, dict) and selector:
            selector_name, selector_config = next(iter(selector.items()))
            field["selector"] = selector_name
            if isinstance(selector_config, dict):
                for key in ("mode", "multiple", "custom_value", "type", "min", "max", "step"):
                    if key in selector_config:
                        field[key] = selector_config[key]
                options = selector_config.get("options")
                if isinstance(options, list):
                    normalized_options: list[object] = []
                    for option in options[:50]:
                        if isinstance(option, dict):
                            normalized_options.append(
                                option.get("value", option.get("label", option))
                            )
                        else:
                            normalized_options.append(option)
                    field["options"] = normalized_options
                    if len(options) > 50:
                        field["options_truncated"] = True

        if "description" in item and item["description"] not in (None, ""):
            field["description"] = item["description"]

        fields.append(field)

    return fields


async def _matching_config_entries_json_fragments(
    hass: HomeAssistant,
    *,
    type_filter: list[str] | None = None,
    domain: str | None = None,
) -> list[object]:

    if domain:
        entries = hass.config_entries.async_entries(domain)
    else:
        entries = hass.config_entries.async_entries()

    if not type_filter:
        return [entry.as_json_fragment for entry in entries]

    integrations: dict[str, Integration] = {}
    domains = {entry.domain for entry in entries}
    for domain_key, integration_or_exc in (
        await async_get_integrations(hass, domains)
    ).items():
        if isinstance(integration_or_exc, Integration):
            integrations[domain_key] = integration_or_exc
        elif not isinstance(integration_or_exc, IntegrationNotFound):
            raise integration_or_exc

    filter_is_not_helper = type_filter != ["helper"]
    filter_set = set(type_filter)
    return [
        entry.as_json_fragment
        for entry in entries
        if (
            (integration := integrations.get(entry.domain))
            and integration.integration_type in filter_set
        )
        or (filter_is_not_helper and entry.domain not in integrations)
    ]


def _normalize_filter_list(value: object) -> list[str] | None:

    if value in (None, "", []):
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return None


_PARAM_KEY_ALIASES: dict[str, str] = {
    "flowid": "flow_id",
    "entryid": "entry_id",
    "userinput": "user_input",
    "nextstepid": "next_step_id",
    "subentrytype": "subentry_type",
    "subentryid": "subentry_id",
    "typefilter": "type_filter",
    "todolistname": "todo_list_name",
    "showadvancedoptions": "show_advanced_options",
    "disabledby": "disabled_by",
    "storagekey": "storage_key",
}


def _coerce_params(raw_params: object) -> dict[str, object]:

    if isinstance(raw_params, str):
        try:
            parsed = json.loads(raw_params)
        except Exception:
            return {}
        raw = parsed if isinstance(parsed, dict) else {}
    else:
        raw = dict(raw_params) if isinstance(raw_params, dict) else {}
    result: dict[str, object] = {}
    for k, v in raw.items():
        norm = k.replace("_", "").replace("-", "").lower()
        result[_PARAM_KEY_ALIASES.get(norm, k)] = v
    return result


class HAControlTool(llm.Tool):
    name = "HAControl"
    description = """Advanced Home Assistant control for system actions, UI-adjacent operations,
integration inspection, and host shell execution.

Available actions:
- shell: Run a shell command via `/bin/sh -c` asynchronously. params: {command, timeout=30, cwd}.
  Returns stdout/stderr/returncode/elapsed. Default cwd is the HA config directory.
  Example: {"command": "curl -sS https://api.github.com/zen"}
  IMPORTANT: Before using shell to modify automations.yaml / configuration.yaml / sensors.yaml,
  politely explain to the user what you want to change and why, then wait for explicit confirmation.
  Prefer the Automation tool for automation changes (safer, uses official API). Reading these files is fine.
- check_config: Validate the current Home Assistant configuration
- list_integrations: List installed integrations
- get_integration: Get details for one integration (params: {domain: "integration_domain"})
- list_entities_by_integration: List entities for one integration (params: {domain: "integration_domain"})
- list_devices: List devices from device registry. Same data as frontend config/device_registry/list.
  params: {domain: "optional_filter", entry_id: "optional_filter"}. Returns id, name, manufacturer, model, area_id, etc.
- reload_integration: Reload one integration (params: {domain: "integration_domain"})
- rename_entry: Rename a config entry (params: {domain: "integration_domain", name: "new_name"})
- navigate: Navigate to a page (path: "/lovelace", "/config", "/developer-tools/service", etc.)
- reload_themes/reload_resources/reload_scripts/reload_automations: Reload related HA resources
- get_system_log: Get recent HA system error/warning log entries from memory (params: {limit: 20})
- get_error_log: Read the HA error log file tail (params: {lines: 50}). Works even if shell is unavailable.
- get_diagnostics: Get diagnostic info for an integration (params: {domain}). Returns entry state, unavailable entities, related errors.
- ssh: Execute a command on a remote host via SSH (pure Python, no sshpass needed).
  params: {host, command, username="root", password, key_file, port=22, timeout=30}.
  Returns stdout/stderr/returncode/elapsed. Supports password or key-based auth."""
    parameters = vol.Schema(
        {
            vol.Required("action"): str,
            vol.Optional("params", default={}): vol.Any(dict, str),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        params = tool_input.tool_args.get("params", {})

        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}

        if action == "shell":
            return await _run_shell(hass, params)

        if action == "ssh":
            return await _run_ssh(params)

        if action == "list_integrations":
            entries = hass.config_entries.async_entries()
            integrations = {}
            for entry in entries:
                domain = entry.domain
                integrations.setdefault(domain, {"count": 0, "entries": []})
                integrations[domain]["count"] += 1
                integrations[domain]["entries"].append(
                    {
                        "title": entry.title,
                        "state": entry.state.value
                        if hasattr(entry.state, "value")
                        else str(entry.state),
                        "entry_id": entry.entry_id[:8],
                    }
                )
            return {"success": True, "integrations": integrations, "total": len(entries)}

        if action == "get_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "Missing required parameter: domain"}
            entries = [entry for entry in hass.config_entries.async_entries() if entry.domain == domain]
            if not entries:
                return {"success": False, "error": f"Integration not found: {domain}"}
            result = []
            for entry in entries:
                result.append(
                    {
                        "title": entry.title,
                        "domain": entry.domain,
                        "state": entry.state.value
                        if hasattr(entry.state, "value")
                        else str(entry.state),
                        "entry_id": entry.entry_id,
                        "data": {
                            key: "***"
                            if "key" in key.lower()
                            or "token" in key.lower()
                            or "password" in key.lower()
                            else value
                            for key, value in entry.data.items()
                        },
                    }
                )
            return {"success": True, "integration": domain, "entries": result}

        if action == "list_entities_by_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "Missing required parameter: domain"}
            from homeassistant.helpers import device_registry as dr, entity_registry as er

            ent_reg = er.async_get(hass)
            dev_reg = dr.async_get(hass)
            entities = []
            for entity in ent_reg.entities.values():
                if entity.platform == domain:
                    state = hass.states.get(entity.entity_id)
                    item: dict[str, object] = {
                        "entity_id": entity.entity_id,
                        "name": entity.name or entity.original_name,
                        "state": state.state if state else "unknown",
                        "device_class": entity.device_class
                        or entity.original_device_class,
                    }
                    if entity.device_id:
                        dev = dev_reg.async_get(entity.device_id)
                        if dev:
                            item["device_id"] = dev.id
                            item["device_name"] = dev.name
                            item["device_model"] = dev.model
                            item["manufacturer"] = dev.manufacturer
                    entities.append(item)
            return {
                "success": True,
                "integration": domain,
                "entities": entities,
                "count": len(entities),
            }

        if action == "list_devices":
            from homeassistant.helpers import device_registry as dr

            dev_reg = dr.async_get(hass)
            filter_domain = str(params.get("domain", "") or "").strip()
            filter_entry_id = str(params.get("entry_id", "") or "").strip()
            devices = []
            for dev in dev_reg.devices.values():
                if filter_entry_id and filter_entry_id not in dev.config_entries:
                    continue
                if filter_domain:
                    entry_domains = {
                        e.domain for eid in dev.config_entries
                        if (e := hass.config_entries.async_get_entry(eid)) is not None
                    }
                    if filter_domain not in entry_domains:
                        continue
                devices.append({
                    "id": dev.id,
                    "name": dev.name_by_user or dev.name,
                    "manufacturer": dev.manufacturer,
                    "model": dev.model,
                    "model_id": dev.model_id,
                    "area_id": dev.area_id,
                    "hw_version": dev.hw_version,
                    "sw_version": dev.sw_version,
                    "config_entries": list(dev.config_entries),
                    "identifiers": [list(i) for i in dev.identifiers],
                    "via_device_id": dev.via_device_id,
                    "disabled_by": str(dev.disabled_by) if dev.disabled_by else None,
                    "entry_type": str(dev.entry_type) if dev.entry_type else None,
                })
            return {
                "success": True,
                "devices": devices,
                "count": len(devices),
            }

        if action == "navigate":
            return {
                "success": False,
                "error": "The frontend bridge has been removed; HAControl no longer supports navigation actions",
            }

        if action in ["reload_themes", "reload_resources", "reload_scripts", "reload_automations"]:
            service_map = {
                "reload_themes": ("frontend", "reload_themes"),
                "reload_resources": ("lovelace", "reload_resources"),
                "reload_scripts": ("script", "reload"),
                "reload_automations": ("automation", "reload"),
            }
            domain, service = service_map[action]
            await hass.services.async_call(domain, service, {}, blocking=True)
            return {"success": True, "message": f"Reloaded {action}"}

        if action == "check_config":
            await hass.services.async_call(
                "homeassistant",
                "check_config",
                {},
                blocking=True,
            )
            return {
                "success": True,
                "message": "Configuration check completed",
            }

        if action == "reload_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "You must specify an integration domain (domain)"}
            entries = hass.config_entries.async_entries(domain)
            if not entries:
                return {"success": False, "error": f"Integration not found: {domain}"}

            failed_entries = []
            for entry in entries:
                if not await hass.config_entries.async_reload(entry.entry_id):
                    failed_entries.append(entry.entry_id)

            if failed_entries:
                return {
                    "success": False,
                    "error": f"Failed to reload integration {domain}",
                    "failed_entries": failed_entries,
                }

            return {
                "success": True,
                "message": f"Reloaded integration {domain}",
                "reloaded_entries": len(entries),
            }

        if action == "rename_entry":
            domain = params.get("domain", "")
            new_name = params.get("name", "")
            if not domain or not new_name:
                return {
                    "success": False,
                    "error": "You must specify an integration domain (domain) and a new name (name)",
                }
            entries = hass.config_entries.async_entries(domain)
            if entries:
                for entry in entries:
                    hass.config_entries.async_update_entry(entry, title=new_name)
                return {"success": True, "message": f"Renamed {domain} to {new_name}"}
            return {"success": False, "error": f"Integration not found: {domain}"}

        if action in {"show_toast", "show_dialog"}:
            return {
                "success": False,
                "error": "The frontend bridge has been removed; HAControl no longer supports frontend popup actions",
            }

        if action == "get_system_log":
            handler = hass.data.get("system_log")
            if handler:
                limit = int(params.get("limit", 20))
                entries = handler.records.to_list()[:limit]
                return {"success": True, "entries": entries, "count": len(entries)}
            result = await _rest_fallback(hass, "/api/error/all", params)
            if result:
                return result
            return {"success": False, "error": "system_log not loaded. Please add ha_token to TOOLS.md for REST fallback."}

        if action == "get_error_log":
            tail = int(params.get("lines", 50))
            from homeassistant.const import KEY_DATA_LOGGING as _LOG_KEY
            log_path = hass.data.get(_LOG_KEY)
            if log_path:
                try:
                    import pathlib
                    _p = pathlib.Path(log_path)
                    log_text = await hass.async_add_executor_job(
                        lambda: _p.read_text(encoding="utf-8", errors="replace")
                    )
                    all_lines = log_text.splitlines(keepends=True)
                    lines = all_lines[-tail:]
                    return {"success": True, "path": str(log_path), "lines": "".join(lines), "total_lines": len(all_lines)}
                except Exception:
                    pass
            result = await _rest_fallback(hass, "/api/error_log", params)
            if result:
                body = result.get("body", "")
                all_lines = body.splitlines(keepends=True)
                return {"success": True, "lines": "".join(all_lines[-tail:]), "total_lines": len(all_lines), "via": "rest"}
            return {"success": False, "error": "Cannot read error log. Please add ha_token to TOOLS.md for REST fallback."}

        if action == "get_diagnostics":
            domain = str(params.get("domain", "")).strip()
            if not domain:
                return {"success": False, "error": "Missing required parameter: domain"}
            info: dict = {"domain": domain}
            entries = hass.config_entries.async_entries(domain)
            if entries:
                entry = entries[0]
                info["entry_id"] = entry.entry_id
                info["state"] = entry.state.value if hasattr(entry.state, "value") else str(entry.state)
                info["version"] = entry.version
            try:
                from homeassistant.loader import async_get_integration as _get_int
                integration = await _get_int(hass, domain)
                info["version_str"] = str(getattr(integration, "version", "unknown"))
            except Exception:
                pass
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(hass)
            unavailable = [
                e.entity_id for e in registry.entities.values()
                if e.platform == domain and hass.states.get(e.entity_id) and hass.states.get(e.entity_id).state == "unavailable"
            ]
            info["unavailable_entities"] = unavailable[:20]
            handler = hass.data.get("system_log")
            if handler:
                info["related_errors"] = [
                    e for e in handler.records.to_list()
                    if domain in str(e.get("name", "")) or domain in str(e.get("message", ""))
                ][:10]
            return {"success": True, "diagnostics": info}

        return {"success": False, "error": f"Unknown action: {action}"}


class ConfigEntriesTool(llm.Tool):
    name = "ConfigEntries"
    description = """Home Assistant integration and config-entry management tool.

WORKFLOWS (follow these exact steps, do NOT explore randomly):

To INSTALL a new integration:
  1. config_entries/flow/init — params: {handler: "domain_name"} → returns flow_id + data_schema_fields
  2. If step has a form: config_entries/flow/configure — params: {flow_id: "...", field1: value1, field2: value2} → done or next step
  That's it. 2 calls max. Do NOT call list/get/flow_handlers/descriptions/help first.
  Put form field values directly in params alongside flow_id (no user_input wrapper needed).

To CHECK existing entries: config_entries/get — params: {domain: "xxx"} (optional)
To CHANGE options: config_entries/options/init — params: {entry_id: "..."} → config_entries/options/configure
  NOTE: If options/init returns "Invalid handler", the integration uses SUBENTRIES instead — see below.
To DELETE: config_entries/delete — params: {entry_id: "..."}
To RELOAD: config_entries/reload — params: {entry_id: "..."}
To RECONFIGURE: config_entries/flow/init — params: {handler: "domain", entry_id: "..."}

SUBENTRIES (many modern integrations use subentries instead of options for per-model/per-agent config):
  To CHECK if subentries are supported: config_entries/get_supported_subentry_types — params: {entry_id: "..."}
  To LIST existing subentries: config_entries/subentries/list — params: {entry_id: "..."}
  To ADD new subentry: config_entries/subentries/flow/init — params: {entry_id: "...", subentry_type: "..."} → returns flow_id + form
  To MODIFY existing subentry: config_entries/subentries/flow/init — params: {entry_id: "...", subentry_type: "...", subentry_id: "..."} → reconfigure flow
  To fill/submit form: config_entries/subentries/flow/configure — params: {flow_id: "...", field1: value1, ...}
  To DELETE subentry: config_entries/subentries/delete — params: {entry_id: "...", subentry_id: "..."}

When response contains next_action, follow that instruction exactly."""

    _VALID_ACTIONS = [
        "integration/descriptions",
        "config_entries/get", "config_entries/get_single",
        "config_entries/get_supported_subentry_types",
        "config_entries/update", "config_entries/disable",
        "config_entries/delete", "config_entries/reload",
        "config_entries/flow_handlers", "config_entries/flow/progress",
        "config_entries/flow/init", "config_entries/flow/get",
        "config_entries/flow/configure", "config_entries/flow/abort",
        "config_entries/ignore_flow",
        "config_entries/options/init", "config_entries/options/get",
        "config_entries/options/configure", "config_entries/options/abort",
        "config_entries/subentries/list", "config_entries/subentries/update",
        "config_entries/subentries/delete",
        "config_entries/subentries/flow/init", "config_entries/subentries/flow/get",
        "config_entries/subentries/flow/configure", "config_entries/subentries/flow/abort",
    ]
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(_VALID_ACTIONS),
            vol.Optional("params", default={}): vol.Any(dict, str),
        }
    )

    @staticmethod
    async def _detect_config_method(
        hass: HomeAssistant, entry: config_entries.ConfigEntry
    ) -> dict[str, object]:
        """Probe whether an entry supports options, subentries, or both."""
        info: dict[str, object] = {}
        try:
            handler = await config_entries._async_get_flow_handler(
                hass, entry.domain, {}
            )
            supported_sub = sorted(
                handler.async_get_supported_subentry_types(entry).keys()
            )
            if supported_sub:
                info["supports_subentries"] = True
                info["subentry_types"] = supported_sub
                info["subentry_count"] = len(entry.subentries)
        except Exception:
            pass
        if "supports_subentries" not in info:
            info["supports_subentries"] = False
        has_options = entry.supports_options
        info["supports_options"] = has_options
        if has_options and info["supports_subentries"]:
            info["config_method"] = "both"
        elif info["supports_subentries"]:
            info["config_method"] = "subentries"
        elif has_options:
            info["config_method"] = "options"
        else:
            info["config_method"] = "none"
        return info

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        del llm_context
        action = str(tool_input.tool_args.get("action", "") or "").strip()
        if action not in self._VALID_ACTIONS:
            norm = action.replace("_", "").replace("-", "").replace(" ", "").lower()
            for valid in self._VALID_ACTIONS:
                if valid.replace("_", "").replace("-", "").replace(" ", "").lower() == norm:
                    action = valid
                    break
        params = _coerce_params(tool_input.tool_args.get("params", {}))

        try:
            if action == "integration/descriptions":
                descriptions = await async_get_integration_descriptions(hass)
                return {
                    "success": True,
                    "message": "Loaded integration descriptions",
                    "descriptions": descriptions,
                }

            if action == "config_entries/get":
                type_filter = _normalize_filter_list(params.get("type_filter"))
                domain = str(params.get("domain", "") or "").strip() or None
                if domain:
                    entries = hass.config_entries.async_entries(domain)
                else:
                    entries = hass.config_entries.async_entries()
                entry_list = []
                config_method_cache: dict[str, dict[str, object]] = {}
                for e in entries:
                    item: dict[str, object] = {
                        "entry_id": e.entry_id,
                        "domain": e.domain,
                        "title": e.title,
                        "state": str(e.state),
                        "disabled_by": str(e.disabled_by) if e.disabled_by else None,
                    }
                    if domain:
                        if e.domain not in config_method_cache:
                            config_method_cache[e.domain] = await self._detect_config_method(hass, e)
                        cm = config_method_cache[e.domain]
                        item["config_method"] = cm.get("config_method")
                        if cm.get("supports_subentries"):
                            item["subentry_types"] = cm.get("subentry_types")
                            item["subentry_count"] = len(e.subentries)
                    entry_list.append(item)
                resp: dict[str, object] = {
                    "success": True,
                    "message": f"Found {len(entry_list)} config entries",
                    "entries": entry_list,
                    "count": len(entry_list),
                }
                if domain and entry_list:
                    cm = config_method_cache.get(domain, {})
                    method = cm.get("config_method", "unknown")
                    if method == "subentries":
                        resp["hint"] = "This integration uses SUBENTRIES for config. Use config_entries/subentries/list to see them, config_entries/subentries/flow/init to add/modify."
                    elif method == "options":
                        resp["hint"] = "Use config_entries/options/init with entry_id to change options."
                    elif method == "both":
                        resp["hint"] = "This integration supports both options and subentries."
                    else:
                        resp["hint"] = "Use entry_id with config_entries/delete, config_entries/reload, etc."
                else:
                    resp["hint"] = "Use entry_id with config_entries/delete, config_entries/reload, config_entries/options/init, etc."
                return resp

            if action == "config_entries/get_single":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}
                cm = await self._detect_config_method(hass, entry)
                resp = {
                    "success": True,
                    "message": f"Loaded config entry {entry.title or entry.domain}",
                    "config_entry": entry.as_json_fragment,
                    **cm,
                }
                return resp

            if action == "config_entries/get_supported_subentry_types":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}
                handler = await config_entries._async_get_flow_handler(
                    hass, entry.domain, {}
                )
                supported = sorted(
                    handler.async_get_supported_subentry_types(entry).keys()
                )
                return {
                    "success": True,
                    "message": f"Loaded supported subentry types for {entry.title or entry.domain}",
                    "entry_id": entry_id,
                    "subentry_types": supported,
                    "count": len(supported),
                }

            if action == "config_entries/update":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}

                changes = {
                    key: params[key]
                    for key in ("title", "pref_disable_new_entities", "pref_disable_polling")
                    if key in params
                }
                if not changes:
                    return {"success": False, "error": "No supported update fields provided"}

                old_disable_polling = entry.pref_disable_polling
                hass.config_entries.async_update_entry(entry, **changes)
                result: dict[str, object] = {
                    "success": True,
                    "message": f"Updated config entry {entry.title or entry.domain}",
                    "config_entry": entry.as_json_fragment,
                    "require_restart": False,
                }
                initial_state = entry.state
                if (
                    old_disable_polling != entry.pref_disable_polling
                    and initial_state is config_entries.ConfigEntryState.LOADED
                ):
                    if not await hass.config_entries.async_reload(entry.entry_id):
                        result["require_restart"] = (
                            entry.state is config_entries.ConfigEntryState.FAILED_UNLOAD
                        )
                return result

            if action == "config_entries/disable":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                disabled_by_param = params.get("disabled_by")
                disabled_by = None
                if disabled_by_param is not None:
                    disabled_by = config_entries.ConfigEntryDisabler(str(disabled_by_param))
                try:
                    success = await hass.config_entries.async_set_disabled_by(
                        entry_id, disabled_by
                    )
                except config_entries.OperationNotAllowed:
                    success = False
                except config_entries.UnknownEntry:
                    return {"success": False, "error": "Config entry not found"}
                return {
                    "success": True,
                    "message": (
                        f"Disabled config entry {entry_id}"
                        if disabled_by is not None
                        else f"Enabled config entry {entry_id}"
                    ),
                    "require_restart": not success,
                }

            if action == "config_entries/delete":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                entry_title = entry.title if entry else entry_id
                entry_domain = entry.domain if entry else "unknown"
                try:
                    result = await hass.config_entries.async_remove(entry_id)
                except config_entries.UnknownEntry:
                    return {"success": False, "error": "Invalid entry specified"}
                return {
                    "success": True,
                    "message": f"Deleted integration entry: {entry_title} ({entry_domain})",
                    "entry_id": entry_id,
                    "require_restart": result.get("require_restart", False),
                }

            if action == "config_entries/reload":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Invalid entry specified"}
                try:
                    await hass.config_entries.async_reload(entry_id)
                except config_entries.OperationNotAllowed:
                    return {"success": False, "error": "Entry cannot be reloaded"}
                return {
                    "success": True,
                    "message": f"Reloaded config entry {entry.title or entry.domain}",
                    "require_restart": not entry.state.recoverable,
                }

            if action == "config_entries/flow_handlers":
                type_filter = str(params.get("type_filter", "") or "").strip() or None
                handlers = sorted(await async_get_config_flows(hass, type_filter=type_filter))
                return {
                    "success": True,
                    "message": f"Loaded {len(handlers)} flow handlers",
                    "handlers": handlers,
                    "count": len(handlers),
                }

            if action == "config_entries/flow/progress":
                flows = [
                    flow
                    for flow in hass.config_entries.flow.async_progress()
                    if flow["context"]["source"]
                    not in (config_entries.SOURCE_RECONFIGURE, config_entries.SOURCE_USER)
                ]
                return {
                    "success": True,
                    "message": f"Found {len(flows)} in-progress discovered flows",
                    "flows": flows,
                    "count": len(flows),
                }

            if action == "config_entries/flow/init":
                handler = params.get("handler")
                if handler in (None, ""):
                    return {"success": False, "error": "Missing required parameter: handler"}
                handler = str(handler).strip()
                context: dict[str, object] = {
                    "show_advanced_options": bool(
                        params.get("show_advanced_options", False)
                    )
                }
                if entry_id := str(params.get("entry_id", "") or "").strip():
                    context["source"] = config_entries.SOURCE_RECONFIGURE
                    context["entry_id"] = entry_id
                else:
                    context["source"] = config_entries.SOURCE_USER
                try:
                    result = await hass.config_entries.flow.async_init(
                        handler, context=context
                    )
                except data_entry_flow.UnknownHandler:
                    all_handlers = sorted(await async_get_config_flows(hass))
                    h_norm = handler.replace("_", "").replace("-", "").replace(" ", "").lower()
                    fuzzy_match = next(
                        (h for h in all_handlers if h.replace("_", "").replace("-", "").lower() == h_norm),
                        None,
                    )
                    if fuzzy_match:
                        try:
                            result = await hass.config_entries.flow.async_init(
                                fuzzy_match, context=context
                            )
                        except (data_entry_flow.UnknownHandler, data_entry_flow.UnknownStep) as err:
                            return {"success": False, "error": str(err)}
                        prepared = _prepare_flow_result_json(result, include_entry_result=True)
                        init_msg = f"Config flow started for {fuzzy_match} (corrected from '{handler}')"
                        if result.get("type") == data_entry_flow.FlowResultType.CREATE_ENTRY:
                            init_msg = f"Integration {fuzzy_match} installed directly (no form needed)"
                        resp = {
                            "success": True,
                            "message": init_msg,
                            **prepared,
                        }
                        if result.get("type") == data_entry_flow.FlowResultType.FORM:
                            resp["flow_id"] = result.get("flow_id")
                        return resp
                    suggestions = [
                        h for h in all_handlers
                        if h_norm in h.replace("_", "").replace("-", "").lower()
                        or h.replace("_", "").replace("-", "").lower() in h_norm
                    ][:5]
                    return {
                        "success": False,
                        "error": f"Unknown handler: '{handler}'.",
                        "suggestions": suggestions if suggestions else None,
                    }
                except data_entry_flow.UnknownStep as err:
                    return {"success": False, "error": str(err)}
                prepared = _prepare_flow_result_json(result, include_entry_result=True)
                init_msg = f"Config flow started for {handler} — fill in fields and call configure"
                if result.get("type") == data_entry_flow.FlowResultType.CREATE_ENTRY:
                    init_msg = f"Integration {handler} installed directly (no form needed)"
                resp = {
                    "success": True,
                    "message": init_msg,
                    **prepared,
                }
                if result.get("type") == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action in {"config_entries/flow/get", "config_entries/flow/configure"}:
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                user_input = params.get("user_input")
                if action == "config_entries/flow/get":
                    user_input = None
                elif user_input is None:
                    remaining = {k: v for k, v in params.items() if k not in ("flow_id", "user_input")}
                    user_input = remaining if remaining else {}
                if isinstance(user_input, str):
                    try:
                        user_input = json.loads(user_input)
                    except Exception:
                        user_input = {}
                if not isinstance(user_input, dict):
                    return {"success": False, "error": "user_input must be an object"}
                try:
                    result = await hass.config_entries.flow.async_configure(
                        flow_id, user_input
                    )
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                except data_entry_flow.InvalidData as err:
                    return {
                        "success": False,
                        "error": "Invalid data provided",
                        "schema_errors": err.schema_errors,
                        "submitted_input": user_input,
                    }
                prepared = _prepare_flow_result_json(result, include_entry_result=True)
                flow_errors = result.get("errors")
                if flow_errors:
                    prepared["form_errors"] = flow_errors
                    prepared["submitted_input"] = user_input
                result_type = result.get("type")
                if result_type == data_entry_flow.FlowResultType.CREATE_ENTRY:
                    msg = f"Integration entry created: {result.get('title', '')} ({result.get('handler', '')})"
                elif result_type == data_entry_flow.FlowResultType.FORM:
                    msg = f"Form returned — fill in required fields and call configure again"
                elif result_type == data_entry_flow.FlowResultType.ABORT:
                    msg = f"Flow aborted: {result.get('reason', 'unknown')}"
                else:
                    msg = f"Flow step: {result_type}"
                resp = {
                    "success": True,
                    "message": msg,
                    **prepared,
                }
                if result_type == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action == "config_entries/flow/abort":
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                try:
                    hass.config_entries.flow.async_abort(flow_id)
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                return {"success": True, "message": "Flow aborted"}

            if action == "config_entries/ignore_flow":
                flow_id = str(params.get("flow_id", "") or "").strip()
                title = str(params.get("title", "") or "").strip()
                if not flow_id or not title:
                    return {
                        "success": False,
                        "error": "Missing required parameters: flow_id and title",
                    }
                flow = next(
                    (
                        flw
                        for flw in hass.config_entries.flow.async_progress()
                        if flw["flow_id"] == flow_id
                    ),
                    None,
                )
                if flow is None:
                    return {"success": False, "error": "Config flow not found"}
                if "unique_id" not in flow["context"]:
                    return {
                        "success": False,
                        "error": "Specified flow has no unique ID.",
                    }
                context = config_entries.ConfigFlowContext(
                    source=config_entries.SOURCE_IGNORE
                )
                if "discovery_key" in flow["context"]:
                    context["discovery_key"] = flow["context"]["discovery_key"]
                await hass.config_entries.flow.async_init(
                    flow["handler"],
                    context=context,
                    data={
                        "unique_id": flow["context"]["unique_id"],
                        "title": title,
                    },
                )
                return {"success": True, "message": f"Ignored flow {flow_id}"}

            if action == "config_entries/options/init":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                try:
                    result = await hass.config_entries.options.async_init(entry_id)
                except data_entry_flow.UnknownHandler:
                    entry = hass.config_entries.async_get_entry(entry_id)
                    if entry is not None:
                        cm = await self._detect_config_method(hass, entry)
                        if cm.get("supports_subentries"):
                            return {
                                "success": False,
                                "error": "This integration does not support options flow. It uses SUBENTRIES instead.",
                                **cm,
                                "existing_subentries": [
                                    {"subentry_id": s.subentry_id, "subentry_type": s.subentry_type, "title": s.title}
                                    for s in entry.subentries.values()
                                ],
                                "next_action": f"To modify an existing subentry, call config_entries/subentries/flow/init with entry_id='{entry_id}', subentry_type, and subentry_id. To add a new one, omit subentry_id.",
                            }
                    return {"success": False, "error": "This integration does not have an options flow or subentries."}
                except data_entry_flow.UnknownStep as err:
                    return {"success": False, "error": str(err)}
                prepared = _prepare_flow_result_json(result, configure_action="config_entries/options/configure")
                resp = {
                    "success": True,
                    "message": f"Options flow started for {entry_id} — fill in fields and call options/configure",
                    **prepared,
                }
                if result.get("type") == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action in {
                "config_entries/options/get",
                "config_entries/options/configure",
            }:
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                user_input = params.get("user_input")
                if action == "config_entries/options/get":
                    user_input = None
                elif user_input is None:
                    remaining = {k: v for k, v in params.items() if k not in ("flow_id", "user_input")}
                    user_input = remaining if remaining else {}
                if isinstance(user_input, str):
                    try:
                        user_input = json.loads(user_input)
                    except Exception:
                        user_input = {}
                if not isinstance(user_input, dict):
                    return {"success": False, "error": "user_input must be an object"}
                try:
                    result = await hass.config_entries.options.async_configure(
                        flow_id, user_input
                    )
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                except data_entry_flow.InvalidData as err:
                    return {
                        "success": False,
                        "error": "Invalid data provided for options",
                        "schema_errors": err.schema_errors,
                        "submitted_input": user_input,
                    }
                prepared = _prepare_flow_result_json(result, configure_action="config_entries/options/configure")
                flow_errors = result.get("errors")
                if flow_errors:
                    prepared["form_errors"] = flow_errors
                    prepared["submitted_input"] = user_input
                result_type = result.get("type")
                if result_type == data_entry_flow.FlowResultType.CREATE_ENTRY:
                    opt_msg = f"Options saved for {flow_id}"
                elif result_type == data_entry_flow.FlowResultType.FORM:
                    opt_msg = "Options form returned — fill in fields and call options/configure again"
                elif result_type == data_entry_flow.FlowResultType.ABORT:
                    opt_msg = f"Options flow aborted: {result.get('reason', 'unknown')}"
                else:
                    opt_msg = f"Options flow step: {result_type}"
                resp = {
                    "success": True,
                    "message": opt_msg,
                    **prepared,
                }
                if result_type == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action == "config_entries/options/abort":
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                try:
                    hass.config_entries.options.async_abort(flow_id)
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                return {"success": True, "message": "Options flow aborted"}

            if action == "config_entries/subentries/list":
                entry_id = str(params.get("entry_id", "") or "").strip()
                if not entry_id:
                    return {"success": False, "error": "Missing required parameter: entry_id"}
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}
                result = [
                    {
                        "subentry_id": subentry.subentry_id,
                        "subentry_type": subentry.subentry_type,
                        "title": subentry.title,
                        "unique_id": subentry.unique_id,
                        "data": dict(subentry.data),
                    }
                    for subentry in entry.subentries.values()
                ]
                return {
                    "success": True,
                    "message": f"Listed {len(result)} subentries for {entry.title or entry.domain}",
                    "subentries": result,
                    "count": len(result),
                }

            if action == "config_entries/subentries/update":
                entry_id = str(params.get("entry_id", "") or "").strip()
                subentry_id = str(params.get("subentry_id", "") or "").strip()
                if not entry_id or not subentry_id:
                    return {
                        "success": False,
                        "error": "Missing required parameters: entry_id and subentry_id",
                    }
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}
                subentry = entry.subentries.get(subentry_id)
                if subentry is None:
                    return {"success": False, "error": "Config subentry not found"}
                changes = {
                    key: params[key]
                    for key in ("title",)
                    if key in params
                }
                if not changes:
                    return {"success": False, "error": "No supported update fields provided"}
                hass.config_entries.async_update_subentry(entry, subentry, **changes)
                return {
                    "success": True,
                    "message": f"Updated subentry {subentry.title}",
                }

            if action == "config_entries/subentries/delete":
                entry_id = str(params.get("entry_id", "") or "").strip()
                subentry_id = str(params.get("subentry_id", "") or "").strip()
                if not entry_id or not subentry_id:
                    return {
                        "success": False,
                        "error": "Missing required parameters: entry_id and subentry_id",
                    }
                entry = hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return {"success": False, "error": "Config entry not found"}
                try:
                    hass.config_entries.async_remove_subentry(entry, subentry_id)
                except config_entries.UnknownSubEntry:
                    return {"success": False, "error": "Config subentry not found"}
                return {
                    "success": True,
                    "message": f"Deleted subentry {subentry_id}",
                }

            if action == "config_entries/subentries/flow/init":
                entry_id = str(params.get("entry_id", "") or "").strip()
                subentry_type = str(params.get("subentry_type", "") or "").strip()
                if not entry_id or not subentry_type:
                    return {
                        "success": False,
                        "error": "Missing required parameters: entry_id and subentry_type",
                    }
                context: dict[str, object] = {
                    "show_advanced_options": bool(
                        params.get("show_advanced_options", False)
                    ),
                    "source": config_entries.SOURCE_USER,
                }
                if subentry_id := str(params.get("subentry_id", "") or "").strip():
                    context["source"] = config_entries.SOURCE_RECONFIGURE
                    context["subentry_id"] = subentry_id
                try:
                    result = await hass.config_entries.subentries.async_init(
                        (entry_id, subentry_type), context=context
                    )
                except data_entry_flow.UnknownHandler as err:
                    return {"success": False, "error": str(err)}
                except data_entry_flow.UnknownStep as err:
                    return {"success": False, "error": str(err)}
                prepared = _prepare_flow_result_json(result, configure_action="config_entries/subentries/flow/configure")
                resp = {
                    "success": True,
                    "message": f"Subentry flow started for {entry_id}:{subentry_type} — fill in fields and call subentries/flow/configure",
                    **prepared,
                }
                if result.get("type") == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action in {
                "config_entries/subentries/flow/get",
                "config_entries/subentries/flow/configure",
            }:
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                user_input = params.get("user_input")
                if action == "config_entries/subentries/flow/get":
                    user_input = None
                elif user_input is None:
                    remaining = {k: v for k, v in params.items() if k not in ("flow_id", "user_input")}
                    user_input = remaining if remaining else {}
                if isinstance(user_input, str):
                    try:
                        user_input = json.loads(user_input)
                    except Exception:
                        user_input = {}
                if not isinstance(user_input, dict):
                    return {"success": False, "error": "user_input must be an object"}
                try:
                    result = await hass.config_entries.subentries.async_configure(
                        flow_id, user_input
                    )
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                except data_entry_flow.InvalidData as err:
                    return {
                        "success": False,
                        "error": "Invalid data provided for subentry",
                        "schema_errors": err.schema_errors,
                        "submitted_input": user_input,
                    }
                prepared = _prepare_flow_result_json(result, configure_action="config_entries/subentries/flow/configure")
                flow_errors = result.get("errors")
                if flow_errors:
                    prepared["form_errors"] = flow_errors
                    prepared["submitted_input"] = user_input
                result_type = result.get("type")
                if result_type == data_entry_flow.FlowResultType.CREATE_ENTRY:
                    sub_msg = f"Subentry created for {flow_id}"
                elif result_type == data_entry_flow.FlowResultType.FORM:
                    sub_msg = "Subentry form returned — fill in fields and call subentries/flow/configure again"
                elif result_type == data_entry_flow.FlowResultType.ABORT:
                    sub_msg = f"Subentry flow aborted: {result.get('reason', 'unknown')}"
                else:
                    sub_msg = f"Subentry flow step: {result_type}"
                resp = {
                    "success": True,
                    "message": sub_msg,
                    **prepared,
                }
                if result_type == data_entry_flow.FlowResultType.FORM:
                    resp["flow_id"] = result.get("flow_id")
                return resp

            if action == "config_entries/subentries/flow/abort":
                flow_id = str(params.get("flow_id", "") or "").strip()
                if not flow_id:
                    return {"success": False, "error": "Missing required parameter: flow_id"}
                try:
                    hass.config_entries.subentries.async_abort(flow_id)
                except data_entry_flow.UnknownFlow:
                    return {"success": False, "error": "Invalid flow specified"}
                return {"success": True, "message": "Subentry flow aborted"}

            return {
                "success": False,
                "error": f"Unknown action: '{action}'",
                "valid_actions": self._VALID_ACTIONS,
                "hint": "To install an integration: use action='config_entries/flow/init' with params={handler:'domain_name'}",
            }
        except Exception as err:
            return {"success": False, "error": str(err)}


class HACSTool(llm.Tool):
    name = "HACS"
    description = """HACS store tool.

Available actions:
- action=list: List installed HACS repos. page/page_size for pagination
- action=search: Search local HACS cache. page/page_size for pagination
- action=github_search: Search GitHub remotely for discovery
- action=info: Fetch repository details and README
- action=install / update: Install or update a repository using repository/source/query
- action=uninstall: Uninstall a repository
- action=remove: Remove a repository from the HACS registry
- action=manage / edit: View or update repository settings (version/show_beta/state)
- action=open_add_integration: Open the HA add-integration flow and search

Supported params:
- repository: owner/repo or URL
- source: any repository source URL
- query: search term or repository keyword
- category: integration/lovelace/plugin/theme/appdaemon/python_script/template
- params: management params such as version/show_beta/state
- page: page number (default 1)
- page_size: items per page (default 15)"""
    parameters = vol.Schema(
        {
            vol.Required("action"): str,
            vol.Optional("repository", default=""): str,
            vol.Optional("source", default=""): str,
            vol.Optional("query", default=""): str,
            vol.Optional("category", default="integration"): str,
            vol.Optional("params", default={}): vol.Any(dict, str),
            vol.Optional("page", default=1): int,
            vol.Optional("page_size", default=15): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        repository = tool_input.tool_args.get("repository", "")
        source = tool_input.tool_args.get("source", "")
        query = tool_input.tool_args.get("query", "")
        category = tool_input.tool_args.get("category", "integration")
        params = tool_input.tool_args.get("params", {})
        page = max(1, tool_input.tool_args.get("page", 1))
        page_size = max(1, min(30, tool_input.tool_args.get("page_size", 15)))

        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}

        normalized_source = _normalize_repo_source(source or repository)
        repository = normalized_source["repository"]

        try:
            hacs_data = hass.data.get("hacs")
            if not hacs_data:
                return {"success": False, "error": "HACS is not installed"}

            if action == "list":
                all_repos = []
                for repo in hacs_data.repositories.list_all:
                    if not repo.data.installed:
                        continue
                    latest = repo.data.last_version or repo.data.last_commit
                    desc = repo.data.description or ""
                    all_repos.append({
                        "name": repo.data.name,
                        "full_name": repo.data.full_name,
                        "description": desc[:300] if desc else "",
                        "installed_version": repo.data.installed_version,
                        "latest": latest,
                        "update_available": bool(
                            latest and latest != repo.data.installed_version
                        ),
                        "category": str(getattr(repo.data, "category", "")),
                    })
                total = len(all_repos)
                start = (page - 1) * page_size
                paged = all_repos[start:start + page_size]
                return {"success": True, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size, "repositories": paged}

            if action == "search":
                if not query:
                    return {"success": False, "error": "A search query is required (query)"}
                results = []
                query_lower = query.lower()
                for repo in hacs_data.repositories.list_all:
                    if (
                        query_lower in repo.data.name.lower()
                        or query_lower in (repo.data.description or "").lower()
                        or query_lower in " ".join(repo.data.topics or []).lower()
                    ):
                        desc = repo.data.description or ""
                        results.append({
                            "name": repo.data.name,
                            "full_name": repo.data.full_name,
                            "description": desc[:300] if desc else "",
                            "installed": repo.data.installed,
                            "stars": repo.data.stargazers_count,
                            "category": str(getattr(repo.data, "category", "")),
                        })
                total = len(results)
                start = (page - 1) * page_size
                paged = results[start:start + page_size]
                return {"success": True, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size, "results": paged}

            if action == "github_search":
                if not query:
                    return {"success": False, "error": "A search query is required (query)"}
                return {"success": True, "results": await _search_github_repositories(query)}

            if action == "info":
                repo = hacs_data.repositories.get_by_full_name(repository) if repository else None
                if repo is not None:
                    info = _serialize_hacs_repo(repo)
                    readme = ""
                    try:
                        readme = await repo.get_documentation()
                    except Exception:
                        readme = ""
                    info["readme"] = readme[:1000] if readme else ""
                    return {"success": True, **info}

                if not repository or "/" not in repository:
                    return {
                        "success": False,
                        "error": "Missing a recognizable repository/source; unable to fetch remote details",
                        "normalized_source": normalized_source,
                    }

                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://api.github.com/repos/{repository}") as resp:
                        if resp.status != 200:
                            return {"success": False, "error": f"GitHub API error: {resp.status}"}
                        repo_data = await resp.json()

                    async with session.get(
                        f"https://api.github.com/repos/{repository}/readme",
                        headers={"Accept": "application/vnd.github.raw"},
                    ) as resp:
                        readme = ""
                        if resp.status == 200:
                            readme_raw = await resp.text()
                            readme = readme_raw[:1000] if len(readme_raw) > 1000 else readme_raw

                return {
                    "success": True,
                    "name": repo_data.get("name"),
                    "full_name": repo_data.get("full_name"),
                    "description": (repo_data.get("description") or "")[:300],
                    "stars": repo_data.get("stargazers_count"),
                    "readme": readme,
                }

            if action in {"install", "update"}:
                from custom_components.hacs.enums import HacsCategory

                category_map = {
                    "integration": HacsCategory.INTEGRATION,
                    "lovelace": HacsCategory.LOVELACE,
                    "plugin": HacsCategory.PLUGIN,
                    "theme": HacsCategory.THEME,
                    "appdaemon": HacsCategory.APPDAEMON,
                    "python_script": HacsCategory.PYTHON_SCRIPT,
                    "template": HacsCategory.TEMPLATE,
                }
                hacs_category = category_map.get(category, HacsCategory.INTEGRATION)

                repo = hacs_data.repositories.get_by_full_name(repository) if repository else None
                target_repository = repository

                if not target_repository and query:
                    repo = _find_repo_by_query(hacs_data, query)
                    if repo is not None:
                        target_repository = repo.data.full_name
                    else:
                        remote_results = await _search_github_repositories(query)
                        if remote_results:
                            target_repository = str(remote_results[0]["full_name"])

                if not target_repository or "/" not in target_repository:
                    return {
                        "success": False,
                        "error": "Unable to resolve an installable repository from repository/source/query",
                        "normalized_source": normalized_source,
                    }

                existing = repo or hacs_data.repositories.get_by_full_name(target_repository)
                if existing and existing.data.installed:
                    await existing.async_download_repository(ref=params.get("version"))
                    return {
                        "success": True,
                        "message": f"Updated {existing.data.full_name}",
                        "repository": _serialize_hacs_repo(existing),
                    }

                if existing is None:
                    await hacs_data.async_register_repository(target_repository, hacs_category)
                repo = hacs_data.repositories.get_by_full_name(target_repository)
                if repo:
                    await repo.async_download_repository(ref=params.get("version"))
                    domain = (
                        repo.data.domain
                        or repo.data.name.replace("-", "_").replace(" ", "_").lower()
                    )
                    return {
                        "success": True,
                        "message": f"Installed {target_repository}",
                        "domain": domain,
                        "repository": _serialize_hacs_repo(repo),
                        "next_action": f"You can now search for '{domain}' in the Home Assistant integrations page to finish setup.",
                    }
                return {"success": False, "error": f"Registration failed: {target_repository}"}

            if action == "uninstall":
                repo = hacs_data.repositories.get_by_full_name(repository) if repository else None
                if repo is None and query:
                    repo = _find_repo_by_query(hacs_data, query)
                if repo is None:
                    return {"success": False, "error": "Could not find a repository to uninstall"}
                await repo.uninstall()
                return {
                    "success": True,
                    "message": f"Uninstalled {repo.data.full_name}",
                    "repository": _serialize_hacs_repo(repo),
                }

            if action == "remove":
                repo = hacs_data.repositories.get_by_full_name(repository) if repository else None
                if repo is None and query:
                    repo = _find_repo_by_query(hacs_data, query)
                if repo is None:
                    return {"success": False, "error": "Could not find a repository to remove"}
                repo.remove()
                data_store = getattr(hacs_data, "data", None)
                if data_store is not None and hasattr(data_store, "async_write"):
                    await data_store.async_write()
                return {"success": True, "message": f"Removed {repo.data.full_name} from the HACS registry"}

            if action in {"manage", "edit"}:
                repo = hacs_data.repositories.get_by_full_name(repository) if repository else None
                if repo is None and query:
                    repo = _find_repo_by_query(hacs_data, query)
                if repo is None:
                    return {"success": False, "error": "Could not find a repository to manage"}

                updated_fields: dict[str, object] = {}
                if "state" in params:
                    repo.state = params["state"]
                    updated_fields["state"] = repo.state
                if "show_beta" in params:
                    repo.data.show_beta = bool(params["show_beta"])
                    updated_fields["show_beta"] = repo.data.show_beta
                if "version" in params:
                    requested_version = str(params["version"])
                    if requested_version == str(getattr(repo.data, "default_branch", "")):
                        repo.data.selected_tag = None
                    else:
                        repo.data.selected_tag = requested_version
                    updated_fields["selected_tag"] = repo.data.selected_tag
                if updated_fields:
                    await repo.update_repository(force=True)
                    repo.state = None
                return {
                    "success": True,
                    "message": "Repository state updated" if updated_fields else "Repository details",
                    "updated_fields": updated_fields,
                    "repository": _serialize_hacs_repo(repo),
                }

            if action == "open_add_integration":
                search_query = query or ""
                return {
                    "success": True,
                    "message": "Open Home Assistant's integrations page, choose Add Integration, and search as needed.",
                    "query": search_query,
                }

            return {"success": False, "error": f"Unknown action: {action}"}
        except Exception as err:
            return {"success": False, "error": str(err)}


__all__ = [
    "ConfigEntriesTool",
    "HAControlTool",
    "HACSTool",
]
