from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers import service as service_helper
from homeassistant.util.json import JsonObjectType

from ..runtime import (
    set_conversation_state,
)
from ..runtime.config_file_store import (
    async_apply_staged_operation,
    async_list_config_entries,
    async_read_config_file,
    cancel_staged_operation,
    list_pending_operations,
    stage_config_operation,
)
from ..runtime.text_patch import PatchError, apply_patches
from ..runtime.data_path import output_dir_path, tmp_dir_path
from ..runtime.im_transport import async_send_im_payload
from ..entity_privacy import entity_is_exposed, privacy_blocked_response, domain_unexposed_response

_LOGGER = logging.getLogger(__name__)


def _read_text_file(path: Path, offset: int, max_chars: int) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    size = len(text)
    start = max(0, offset)
    end = size if max_chars > 0 else size
    if max_chars > 0:
        end = min(size, start + max_chars)
    return {
        "content": text[start:end],
        "offset": start,
        "returned_chars": end - start,
        "total_chars": size,
        "truncated": end < size,
        "next_offset": end if end < size else None,
    }


def _search_text_file(path: Path, query: str, fuzzy: bool, context_chars: int) -> dict[str, Any]:
    import re
    text = path.read_text(encoding="utf-8", errors="replace")
    size = len(text)
    matches = []

    if fuzzy:
        keywords = [k.strip() for k in query.split() if k.strip()]
        if not keywords:
            return {"total_chars": size, "matches": [], "match_count": 0}
        pattern = ".*?".join(re.escape(k) for k in keywords)
        for m in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
            start = max(0, m.start() - context_chars)
            end = min(size, m.end() + context_chars)
            matches.append({
                "offset": m.start(),
                "matched": m.group()[:200],
                "context": text[start:end],
            })
            if len(matches) >= 30:
                break
    else:
        search_lower = query.lower()
        text_lower = text.lower()
        pos = 0
        while True:
            idx = text_lower.find(search_lower, pos)
            if idx < 0:
                break
            start = max(0, idx - context_chars)
            end = min(size, idx + len(query) + context_chars)
            matches.append({
                "offset": idx,
                "matched": text[idx:idx + len(query)],
                "context": text[start:end],
            })
            pos = idx + 1
            if len(matches) >= 50:
                break

    return {
        "total_chars": size,
        "matches": matches,
        "match_count": len(matches),
    }


async def _async_normalize_camera_service_paths(hass: HomeAssistant, domain: str, service: str, data: dict[str, object]) -> dict[str, object]:
    if domain != "camera" or service not in {"record", "snapshot"}:
        return data

    filename = data.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        return data

    candidate = filename.strip()
    resolved: str | None = None
    output_dir = output_dir_path(hass)
    output_target = output_dir / (Path(candidate).name or ("camera.mp4" if service == "record" else "camera.jpg"))

    def _resolve_candidate() -> str | None:
        try:
            output_dir_resolved = output_dir.resolve(strict=False)
            candidate_path = Path(candidate)
            if candidate_path.is_absolute():
                candidate_resolved = candidate_path.resolve(strict=False)
                try:
                    candidate_resolved.relative_to(output_dir_resolved)
                except ValueError:
                    return None
                return str(candidate_resolved)
        except OSError:
            return None
        return None

    resolved = await hass.async_add_executor_job(_resolve_candidate)

    if resolved is None and candidate.startswith(
        ("/config/local/", "/config/www/", "/config/media/", "/local/", "/media/local/", "/media/")
    ):
        resolved = str(output_target)
    elif resolved is None and candidate.startswith("media-source://media_source/local/"):
        resolved = str(output_target)

    if resolved is None:
        return data

    updated = dict(data)
    updated["filename"] = resolved
    return updated


async def _verify_generated_service_file(
    hass: HomeAssistant,
    path: str,
    *,
    timeout_seconds: int,
) -> dict[str, object]:
    def _probe() -> dict[str, object]:
        file_path = Path(path)
        if not file_path.exists():
            return {"exists": False, "verified": False}
        try:
            stat = file_path.stat()
        except OSError as err:
            return {"exists": False, "verified": False, "error": str(err)}
        return {
            "exists": True,
            "verified": stat.st_size > 0,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        }

    deadline = time.monotonic() + max(1, timeout_seconds)
    last = {"exists": False, "verified": False}
    while time.monotonic() < deadline:
        last = await hass.async_add_executor_job(_probe)
        if bool(last.get("verified")):
            return last
        await asyncio.sleep(0.5)
    return last
_TARGET_PARAMS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")


def _as_entity_id_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item]
    return []


def _extract_entity_ids(service_data: dict, target: dict | None = None) -> list[str]:
    entity_ids = _as_entity_id_list(service_data.get("entity_id"))
    if target:
        entity_ids.extend(_as_entity_id_list(target.get("entity_id")))
    return list(dict.fromkeys(entity_ids))


_COVER_FEATURE_OPEN = 1
_COVER_FEATURE_CLOSE = 2
_COVER_FEATURE_SET_POSITION = 4
_COVER_FEATURE_STOP = 8
_COVER_FEATURE_OPEN_TILT = 16
_COVER_FEATURE_CLOSE_TILT = 32
_COVER_FEATURE_STOP_TILT = 64
_COVER_FEATURE_SET_TILT_POSITION = 128


def _cover_supported_features(hass: HomeAssistant, entity_id: str) -> int:
    state = hass.states.get(entity_id)
    if state is None:
        return 0
    try:
        return int(state.attributes.get("supported_features", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_cover_service_for_features(
    hass: HomeAssistant,
    service: str,
    service_data: dict,
    target: dict | None = None,
) -> tuple[str, dict]:
    entity_ids = _extract_entity_ids(service_data, target)
    if len(entity_ids) != 1:
        return service, service_data
    features = _cover_supported_features(hass, entity_ids[0])
    if not features:
        return service, service_data
    if service == "open_cover" and not features & _COVER_FEATURE_OPEN and features & _COVER_FEATURE_OPEN_TILT:
        return "open_cover_tilt", service_data
    if service == "close_cover" and not features & _COVER_FEATURE_CLOSE and features & _COVER_FEATURE_CLOSE_TILT:
        return "close_cover_tilt", service_data
    if service == "stop_cover" and not features & _COVER_FEATURE_STOP and features & _COVER_FEATURE_STOP_TILT:
        return "stop_cover_tilt", service_data
    if service == "set_cover_position" and not features & _COVER_FEATURE_SET_POSITION and features & _COVER_FEATURE_SET_TILT_POSITION:
        updated = dict(service_data)
        if "position" in updated and "tilt_position" not in updated:
            updated["tilt_position"] = updated.pop("position")
        return "set_cover_tilt_position", updated
    if service == "toggle" and not features & (_COVER_FEATURE_OPEN | _COVER_FEATURE_CLOSE) and features & (_COVER_FEATURE_OPEN_TILT | _COVER_FEATURE_CLOSE_TILT):
        return "toggle_tilt", service_data
    return service, service_data


def _expected_state_for_service(domain: str, service: str, before: str | None) -> str | None:
    from ..domain_registry import get_expected_state
    return get_expected_state(domain, service, before)


async def _verify_entity_control_state(
    hass: HomeAssistant,
    entity_ids: list[str],
    service: str | dict[str, str],
    before: dict[str, str | None],
) -> dict[str, object]:
    if not entity_ids:
        return {"verified": True, "skipped": True}

    deadline = time.monotonic() + 2.0
    details: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        details = []
        all_verified = True
        for entity_id in entity_ids:
            domain = entity_id.split(".", 1)[0]
            state_obj = hass.states.get(entity_id)
            after = state_obj.state if state_obj else None
            entity_service = service.get(entity_id, "") if isinstance(service, dict) else service
            expected = _expected_state_for_service(domain, entity_service, before.get(entity_id))
            verified = True if expected is None else after == expected
            if not verified:
                all_verified = False
            details.append(
                {
                    "entity_id": entity_id,
                    "before": before.get(entity_id),
                    "after": after,
                    "expected": expected,
                    "verified": verified,
                }
            )
        if all_verified:
            return {"verified": True, "entities": details}
        await asyncio.sleep(0.2)
    return {"verified": False, "entities": details}


def _extract_discovered_entity_id(results: list[object]) -> str | None:

    for item in results:
        if isinstance(item, dict):
            entity_id = item.get("entity_id")
        else:
            entity_id = getattr(item, "entity_id", None)
        if entity_id and entity_id != "_summary":
            return str(entity_id)
    return None


def _extract_service_target(data: dict) -> tuple[dict, dict]:

    if not isinstance(data, dict):
        return {}, {}

    service_data = dict(data)
    raw_target = service_data.pop("target", None)
    target = dict(raw_target) if isinstance(raw_target, dict) else {}
    return service_data, target


def _has_explicit_target(service_data: dict, target: dict) -> bool:

    return any(param in service_data or param in target for param in _TARGET_PARAMS)


async def _service_requires_explicit_target(
    hass: HomeAssistant, domain: str, service: str
) -> bool:

    description = service_helper.async_get_cached_service_description(
        hass, domain, service
    )
    if description is None:
        descriptions = await service_helper.async_get_all_descriptions(hass)
        description = descriptions.get(domain, {}).get(service)

    if not isinstance(description, dict):
        return True

    return "target" in description


def _build_missing_target_response(
    *,
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    domain: str,
    service: str,
    data: dict,
    tool: "ServiceCallTool",
) -> JsonObjectType:

    from homeassistant.helpers.llm import _get_exposed_entities

    exposed_entities = (
        _get_exposed_entities(hass, llm_context.assistant)
        if llm_context.assistant
        else {}
    )
    available_entities = tool._get_exposed_entities_list(domain, exposed_entities)[:10]
    from ..domain_registry import get_service as _get_svc
    svc_def = _get_svc(domain, service)
    param_hints = (
        [{"name": p.name, "desc": p.description, "type": p.param_type} for p in svc_def.params]
        if svc_def and svc_def.params else []
    )
    resp: dict[str, object] = {
        "success": False,
        "error": f"Service call requires at least one of: {', '.join(_TARGET_PARAMS)}",
        "retryable": True,
        "missing_target": True,
        "domain": domain,
        "service": service,
        "data": data,
        "available_entities": available_entities,
        "recovery_hint": (
            "Resolve a concrete target entity first, then call the same service again. "
            "Include ALL user-mentioned attributes in data (natural language accepted)."
        ),
    }
    if param_hints:
        resp["available_params"] = param_hints
    return resp


class GetSystemIndexTool(llm.Tool):
    name = "GetSystemIndex"
    description = """Get the cached system structure index. Returns a lightweight overview including:
- areas: area list with entity/device counts
- domains: domains with entity counts
- device_classes: device classes grouped by domain
- people: people entities and states
- automations: automation list
- scripts: script list
- base_url: resolved public URL of this Home Assistant instance (empty string when not configured)

The index is cached for 5 minutes and refreshes automatically when state changes."""
    parameters = vol.Schema({vol.Optional("force_refresh", default=False): bool})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..index_manager import get_index_manager
        from ..runtime.data_path import get_ha_base_url

        force_refresh = tool_input.tool_args.get("force_refresh", False)
        manager = await get_index_manager(hass, llm_context.assistant)
        if force_refresh:
            await manager._async_refresh_index()
        index = await manager.get_index()
        return {
            "success": True,
            "cached": not force_refresh,
            "base_url": get_ha_base_url(hass) or "",
            **index,
        }


class SetConversationStateTool(llm.Tool):
    name = "SetConversationState"
    description = """Set the conversation state ONLY for complex multi-turn interactions.

- expecting_response=true: you asked the user a question and need their reply before proceeding
- expecting_response=false: a long-running multi-step task is fully done

DO NOT call this for simple tasks like device control, queries, or single-action requests. The system auto-detects task completion from your reply text. Only use when the conversation lifecycle genuinely needs explicit signaling."""
    parameters = vol.Schema(
        {
            vol.Required("expecting_response"): bool,
            vol.Optional("reason", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        expecting = tool_input.tool_args.get("expecting_response", False)
        reason = tool_input.tool_args.get("reason", "")
        set_conversation_state(hass, expecting_response=expecting, reason=reason)
        _LOGGER.debug("SetConversationState: expecting=%s, reason=%s", expecting, reason)
        return {
            "success": True,
            "expecting_response": expecting,
            "message": "Conversation state updated" if expecting else "Task completed; conversation may end",
        }


def _get_current_agent_id(hass: HomeAssistant) -> str:
    from ..runtime.state import get_conversation_status
    return str(get_conversation_status(hass).get("current_agent_id", "") or "")


def _resolve_peer_agents(hass: HomeAssistant) -> list[dict[str, str]]:
    """Get configured conversation agents with names and is_you flag."""
    from ..runtime.state import get_runtime_store
    from homeassistant.helpers import entity_registry as er

    runtime_store = get_runtime_store(hass)
    entry = runtime_store.get("config_entry")
    if entry is None:
        return []
    from ..const import CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT
    _ROLE_LABELS = {
        CONF_PRIMARY_AGENT: "primary",
        CONF_FALLBACK_AGENT: "secondary",
        CONF_SECONDARY_FALLBACK_AGENT: "tertiary",
    }
    current_aid = _get_current_agent_id(hass)
    options = entry.options
    ent_reg = er.async_get(hass)
    agents: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in (CONF_PRIMARY_AGENT, CONF_FALLBACK_AGENT, CONF_SECONDARY_FALLBACK_AGENT):
        aid = str(options.get(key, "") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        ent = ent_reg.async_get(aid)
        name = (ent.name or ent.original_name) if ent else aid.split(".")[-1]
        is_you = (aid == current_aid)
        agents.append({"agent_id": aid, "agent_name": name, "role": _ROLE_LABELS.get(key, "peer"), "is_you": is_you})
    return agents


_DONE_SIGNALS = (
    "[DONE]", "[END]", "[RESOLVED]", "[CONCLUDED]",
    "没有更多问题", "问题已解决", "讨论结束",
    "no further questions", "issue resolved", "discussion complete",
)


def _extract_reply(result) -> str:
    if result and result.response and result.response.speech:
        plain = result.response.speech.get("plain", {})
        if isinstance(plain, dict):
            return plain.get("speech", "")
    return ""


def _is_conversation_done(text: str) -> bool:
    low = text.lower().strip()
    return any(sig.lower() in low for sig in _DONE_SIGNALS)


async def _consult_agent(
    hass: HomeAssistant,
    agent_id: str,
    question: str,
    context: str = "",
    max_rounds: int = 30,
    timeout: int = 120,
) -> dict[str, str]:
    """Call another conversation agent for one or more dialogue rounds.

    When max_rounds > 1 the two AIs converse: the peer's reply is sent back
    as the next user message with a [Peer-AI] prefix, continuing until the
    peer signals done, gives a short conclusive answer, or max_rounds is hit.
    """
    from homeassistant.components.conversation import agent_manager
    from homeassistant.components import conversation
    from homeassistant.helpers import entity_registry as er
    from homeassistant.util import ulid

    ent_reg = er.async_get(hass)
    ent = ent_reg.async_get(agent_id)
    agent_name = (ent.name or ent.original_name) if ent else agent_id.split(".")[-1]

    try:
        agent = agent_manager.async_get_agent(hass, agent_id)
        if agent is None:
            return {"success": False, "error": f"Agent {agent_id} not found"}
    except Exception as exc:
        return {"success": False, "error": f"Cannot get agent {agent_id}: {exc}"}

    conv_id = ulid.ulid()
    prompt = f"[PEER-CONSULT]\n{question}"
    if context:
        prompt = f"[PEER-CONSULT]\n[Context from calling AI]\n{context}\n\n[Question]\n{question}"

    dialogue: list[dict[str, str]] = []
    max_rounds = max(1, min(max_rounds, 50))

    for round_num in range(max_rounds):
        user_input = conversation.ConversationInput(
            text=prompt,
            conversation_id=conv_id,
            language=hass.config.language,
            context=None,
            device_id=None,
            agent_id=agent_id,
            satellite_id=None,
        )
        result = None
        last_error = None
        for attempt in range(3):
            try:
                import asyncio
                result = await asyncio.wait_for(agent.async_process(user_input), timeout=timeout)
                break
            except asyncio.TimeoutError as exc:
                last_error = exc
                _LOGGER.warning("ConsultAgent %s round %d attempt %d timeout", agent_id, round_num, attempt + 1)
                await asyncio.sleep(0.5 * (2 ** attempt))
            except Exception as exc:
                err_str = str(exc).lower()
                is_transient = any(k in err_str for k in ("cannot connect", "server disconnected", "ssl", "timeout", "connection", "payload", "transfer", "encoding", "client"))
                if is_transient and attempt < 2:
                    _LOGGER.warning("ConsultAgent %s round %d attempt %d transient error: %s", agent_id, round_num, attempt + 1, exc)
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    last_error = exc
                    continue
                last_error = exc
                break
        if result is None:
            dialogue.append({"round": round_num + 1, "role": "error", "text": str(last_error) if last_error else "unknown error"})
            break

        reply = _extract_reply(result)
        if not reply:
            dialogue.append({"round": round_num + 1, "role": agent_name, "text": "(no reply)"})
            break
        low_reply = reply.lower()
        if any(k in low_reply for k in ("error calling llm", "server disconnected", "clientpayloaderror", "transferencodingerror", "not enough data")):
            dialogue.append({"round": round_num + 1, "role": "error", "text": reply})
            break
        dialogue.append({"round": round_num + 1, "role": agent_name, "text": reply})
        if _is_conversation_done(reply) or round_num + 1 >= max_rounds:
            break

        prompt = f"[PEER-CONSULT]\n[Peer-AI round {round_num + 2}] Based on your previous answer, here is follow-up:\n{reply}\n\nPlease continue or conclude with [DONE] if resolved."

    final_reply = dialogue[-1]["text"] if dialogue else "(no reply)"
    last_is_error = dialogue and dialogue[-1].get("role") == "error"
    return {
        "success": not last_is_error,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "rounds": len(dialogue),
        "reply": final_reply,
        "dialogue": dialogue,
    }


class AgentHandoffTool(llm.Tool):
    name = "AgentHandoff"
    description = """Consult another AI agent. You keep control. Supports multi-turn dialogue.

DISCOVERY: Call with question="" to see all available peer AI agents (names, IDs, roles).

DIALOGUE:
- Single round (default): ask a question, get one reply.
- Multi-round (max_rounds > 1): the two AIs discuss back and forth until the peer signals [DONE] or max_rounds is reached.

You always keep control. The full dialogue + available_agents list is returned.

Use cases:
- Get a second opinion from a different AI model.
- Collaborate: let two AIs work through a problem together.
- Delegate a subtask, get the result back.
- Cross-check / peer-review your answer.

Params:
- agent_id: target agent entity_id. Leave empty to auto-select the first peer.
- question: what you want to discuss. Empty = list available agents only.
- context: your current work / analysis so the other AI has full context.
- intent: "consult" (opinion), "request" (action), "review" (check my work).
- max_rounds: safety cap on dialogue rounds (default 30, max 50). You do NOT need to set this. Just talk naturally and end with [DONE] when finished.

The response always includes available_agents so you know who you can talk to."""
    parameters = vol.Schema(
        {
            vol.Optional("agent_id", default=""): str,
            vol.Required("question"): str,
            vol.Optional("context", default=""): str,
            vol.Optional("intent", default="consult"): vol.In(["consult", "request", "review"]),
            vol.Optional("max_rounds", default=30): vol.All(int, vol.Range(min=1, max=50)),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        agent_id = str(tool_input.tool_args.get("agent_id", "") or "").strip()
        question = str(tool_input.tool_args.get("question", "") or "").strip()
        context = str(tool_input.tool_args.get("context", "") or "").strip()
        max_rounds = int(tool_input.tool_args.get("max_rounds", 30) or 30)

        peers = _resolve_peer_agents(hass)
        others = [p for p in peers if not p.get("is_you")]

        if not question:
            return {
                "success": True,
                "mode": "discovery",
                "you": next((p for p in peers if p.get("is_you")), None),
                "available_agents": others,
                "message": "No question provided. Here are the AI agents you can consult (is_you=true is yourself).",
            }

        if not agent_id:
            if not others:
                return {"success": False, "error": "No other peer agents configured", "available_agents": peers}
            agent_id = others[0]["agent_id"]
        else:
            known_ids = {p["agent_id"] for p in peers}
            if agent_id not in known_ids:
                return {
                    "success": False,
                    "error": f"Agent {agent_id} not found in configured peers",
                    "available_agents": others,
                }

        try:
            _LOGGER.info("AgentHandoff: consulting %s, max_rounds=%d, question=%s...", agent_id, max_rounds, question[:80])
            result = await _consult_agent(hass, agent_id, question, context, max_rounds)
            result["you"] = next((p for p in peers if p.get("is_you")), None)
            result["available_agents"] = others
            return result
        except Exception as exc:
            _LOGGER.warning("AgentHandoff failed for %s: %s", agent_id, exc)
            return {"success": False, "error": str(exc), "agent_id": agent_id, "available_agents": others}


class NextAgentHandoffTool(llm.Tool):
    name = "NextAgentHandoff"
    description = """Consult the next configured AI agent. Supports multi-turn dialogue.

Shortcut for AgentHandoff — auto-selects the first available peer agent.
You keep control. Talk naturally with the peer AI; it ends with [DONE] when finished.
The response includes available_agents so you always know who else is available."""
    parameters = vol.Schema(
        {
            vol.Optional("question", default=""): str,
            vol.Optional("context", default=""): str,
            vol.Optional("max_rounds", default=30): vol.All(int, vol.Range(min=1, max=50)),
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        question = str(tool_input.tool_args.get("question", "") or "").strip()
        context = str(tool_input.tool_args.get("context", "") or "").strip()
        max_rounds = int(tool_input.tool_args.get("max_rounds", 30) or 30)

        peers = _resolve_peer_agents(hass)
        others = [p for p in peers if not p.get("is_you")]
        if not others:
            return {"success": False, "error": "No other peer agents configured", "available_agents": peers}

        if not question:
            return {
                "success": True,
                "mode": "discovery",
                "you": next((p for p in peers if p.get("is_you")), None),
                "available_agents": others,
                "message": "No question provided. Here are the AI agents you can consult.",
            }

        agent_id = others[0]["agent_id"]
        try:
            _LOGGER.info("NextAgentHandoff: consulting %s, max_rounds=%d, question=%s...", agent_id, max_rounds, question[:80])
            result = await _consult_agent(hass, agent_id, question, context, max_rounds)
            result["you"] = next((p for p in peers if p.get("is_you")), None)
            result["available_agents"] = others
            return result
        except Exception as exc:
            _LOGGER.warning("NextAgentHandoff failed for %s: %s", agent_id, exc)
            return {"success": False, "error": str(exc), "agent_id": agent_id, "available_agents": others}


class ReadFileTool(llm.Tool):
    name = "ReadFile"
    description = """Read or search a text file created by Claw Assistant.

Only files under Claw Assistant temp/output directories are allowed.

Modes (controlled by action param):
- action=read (default): Read file content with pagination.
  Default reads first 1500 chars. Use offset + max_chars to paginate.
  Response includes total_chars, returned_chars, truncated, next_offset
  so you know how much remains and where to continue.
- action=search: Exact case-insensitive search. Returns offset + context for each match.
- action=search_fuzzy: Fuzzy multi-keyword match (space-separated keywords,
  matched in order with gaps). Returns offset + context for each match.
- action=info: File metadata only (total_chars, line_count, no content).

Strategy for large files:
1. First call with action=read (gets first 1500 chars + file stats)
2. If truncated=true, use action=search/search_fuzzy to locate specific content
3. Use offset+max_chars to read specific sections as needed
4. Never read the entire file at once if total_chars > 5000

Params: path(required), action(read/search/search_fuzzy/info, default read),
offset(default 0), max_chars(default 1500, set 0 to read all — use sparingly),
query(required for search/search_fuzzy), context_chars(default 200)."""
    parameters = vol.Schema(
        {
            vol.Required("path"): str,
            vol.Optional("action", default="read"): vol.In(["read", "search", "search_fuzzy", "info"]),
            vol.Optional("offset", default=0): int,
            vol.Optional("max_chars", default=1500): int,
            vol.Optional("query", default=""): str,
            vol.Optional("context_chars", default=200): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        raw_path = str(tool_input.tool_args.get("path", "")).strip()
        action = str(tool_input.tool_args.get("action", "read")).strip()
        offset = int(tool_input.tool_args.get("offset", 0) or 0)
        max_chars = int(tool_input.tool_args.get("max_chars", 1500) or 1500)
        query = str(tool_input.tool_args.get("query", "")).strip()
        context_chars = int(tool_input.tool_args.get("context_chars", 200) or 200)

        if not raw_path:
            return {"success": False, "error": "'path' is required"}

        target = Path(raw_path).expanduser().resolve()
        allowed_roots = [
            tmp_dir_path(hass).resolve(),
            output_dir_path(hass).resolve(),
        ]
        if not any(target == root or root in target.parents for root in allowed_roots):
            return {"success": False, "error": "ReadFile can only access Claw Assistant temp/output files"}
        if not target.is_file():
            return {"success": False, "error": f"File not found: {target}"}

        if action == "info":
            def _get_info(p):
                text = p.read_text(encoding="utf-8", errors="replace")
                return {"total_chars": len(text), "line_count": text.count("\n") + 1}
            info = await hass.async_add_executor_job(_get_info, target)
            return {"success": True, "path": str(target), **info}

        if action in ("search", "search_fuzzy"):
            if not query:
                return {"success": False, "error": "'query' is required for search"}
            fuzzy = action == "search_fuzzy"
            result = await hass.async_add_executor_job(
                _search_text_file, target, query, fuzzy, context_chars,
            )
            return {"success": True, "path": str(target), "action": action, "query": query, **result}

        result = await hass.async_add_executor_job(_read_text_file, target, offset, max_chars)
        resp: dict[str, Any] = {"success": True, "path": str(target), **result}
        if result.get("truncated") and result.get("next_offset") is not None:
            remaining = result["total_chars"] - result["next_offset"]
            resp["_hint"] = (
                f"Content truncated. {remaining} chars remaining. "
                f"You MUST call ReadFile again with offset={result['next_offset']} "
                f"to continue reading the next page."
            )
        return resp


class ConfigFileTool(llm.Tool):
    name = "ConfigFile"
    description = """Access the Home Assistant config directory.

Available actions:
- action=list: list a directory
- action=read: read a file
- action=stage_write / stage_append / stage_mkdir: stage a non-destructive change
- action=stage_patch: surgical anchor-based edit of an existing file (PREFERRED over stage_write for modifications)
- action=stage_delete: stage a file or directory deletion (destructive)
- action=apply: apply a staged operation
- action=cancel: cancel a staged operation
- action=list_pending: list pending operations

PATCH-FIRST RULE (MANDATORY):
1. ALWAYS call action=read FIRST to see the current file content and locate exact anchors.
2. For ANY modification, YOU MUST use action=stage_patch with surgical anchor ops.
3. Only fall back to action=stage_write when the change covers >50% of the file content.
4. NEVER stage_write a file you haven't read first — you WILL corrupt it.
stage_patch params: patches=[{"op":"replace","anchor":"old","new_text":"new"}, ...], dry_run=true/false.
Each patch must be an object, not a JSON-encoded string. Do not pass patches=["{\"op\":\"replace\",...}"].
Ops: replace | insert_before | insert_after | delete | prepend | append | create.
Anchors match against the current file text. Copy exact strings from the read result as anchors.

Policy (text-driven, NO UI buttons exist; YOU judge the user's intent):
- write/append/mkdir auto-apply on `apply` (reversible; no consent needed).
  You may still stage first if you want to show the user the diff.
- delete is destructive. The pipeline is:
    1. stage_delete the path → get an approval_id.
    2. In your chat reply, describe exactly what will be deleted and why.
    3. Wait for the user's next message and JUDGE IT YOURSELF — there is no
       hardcoded keyword list. If you understand them as agreeing, call
       `apply` with user_consent=true and consent_quote="<their literal words>".
       If you understand them as declining or unsure, call `cancel` (or ask
       a clarifying question first).
    4. Without user_consent=true, `apply` will refuse the delete.
- Only paths inside the Home Assistant config directory are allowed.

IMPORTANT: For core config files (automations.yaml / configuration.yaml / sensors.yaml),
still politely explain the change to the user and prefer the Automation tool for
automation edits. Reading these files is always fine."""
    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In(
                [
                    "list",
                    "read",
                    "stage_write",
                    "stage_append",
                    "stage_mkdir",
                    "stage_patch",
                    "stage_delete",
                    "apply",
                    "cancel",
                    "list_pending",
                ]
            ),
            vol.Optional("path", default=""): str,
            vol.Optional("content", default=""): str,
            vol.Optional("approval_id", default=""): str,
            vol.Optional("create_dirs", default=False): bool,
            vol.Optional("include_hidden", default=False): bool,
            vol.Optional("user_consent", default=False): bool,
            vol.Optional("consent_quote", default=""): str,
            vol.Optional("patches", default=[]): list,
            vol.Optional("dry_run", default=False): bool,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        path = tool_input.tool_args.get("path", "")
        content = tool_input.tool_args.get("content", "")
        approval_id = tool_input.tool_args.get("approval_id", "")
        create_dirs = bool(tool_input.tool_args.get("create_dirs", False))
        include_hidden = bool(tool_input.tool_args.get("include_hidden", False))
        user_consent = bool(tool_input.tool_args.get("user_consent", False))
        consent_quote = str(tool_input.tool_args.get("consent_quote", ""))

        try:
            if action == "list":
                result = await async_list_config_entries(hass, path, include_hidden)
                if path:
                    from ..runtime.blueprint_bridge import notify_blueprint_studio
                    notify_blueprint_studio(hass, action="navigate", path=path)
                return {"success": True, **result}

            if action == "read":
                result = await async_read_config_file(hass, path)
                from ..runtime.blueprint_bridge import notify_blueprint_studio
                notify_blueprint_studio(hass, action="navigate", path=path)
                return {"success": True, **result}

            if action == "list_pending":
                pending = list_pending_operations(hass)
                return {
                    "success": True,
                    "count": len(pending),
                    "pending": pending,
                }

            if action == "stage_patch":
                return await self._stage_patch(hass, path, tool_input.tool_args)

            if action in {"stage_write", "stage_append", "stage_mkdir", "stage_delete"}:
                stage_action = action.removeprefix("stage_")
                operation = stage_config_operation(
                    hass,
                    action=stage_action,
                    relative_path=path,
                    content=content,
                    create_dirs=create_dirs,
                )
                if stage_action == "delete":
                    message = (
                        "Staged a destructive delete. Tell the user IN CHAT what "
                        "will be deleted and why, wait for their reply, JUDGE "
                        "yourself whether they agreed, then call action='apply' "
                        "with user_consent=true and consent_quote=\"<their words>\". "
                        "If you decide they declined, call action='cancel'."
                    )
                else:
                    message = (
                        "Staged. Call action='apply' with this approval_id to "
                        "execute (no confirmation required for non-delete)."
                    )
                return {
                    "success": True,
                    "message": message,
                    **operation,
                }

            if action == "apply":
                result = await async_apply_staged_operation(
                    hass,
                    approval_id,
                    user_consent=user_consent,
                    consent_quote=consent_quote,
                )
                return {"success": True, **result}

            if action == "cancel":
                result = cancel_staged_operation(hass, approval_id)
                return {"success": True, **result}

            return {"success": False, "error": f"Unknown action: {action}"}
        except PermissionError as err:
            return {"success": False, "error": str(err), "requires_confirmation": True}
        except Exception as err:
            return {"success": False, "error": str(err)}

    async def _stage_patch(self, hass: HomeAssistant, path: str, args: dict) -> JsonObjectType:
        patches = self._normalize_patches(args.get("patches", []))
        if isinstance(patches, str):
            return {"success": False, "error": patches}
        dry_run = bool(args.get("dry_run", False))
        if not isinstance(patches, list) or not patches:
            return {"success": False, "error": "'patches' must be a non-empty list"}
        if not path:
            return {"success": False, "error": "'path' is required for stage_patch"}

        read_result = await async_read_config_file(hass, path)
        if "error" in read_result:
            return {"success": False, "error": read_result["error"]}

        original = read_result.get("content", "")
        label = f"config/{path}"

        try:
            report = apply_patches(original, patches, label=label)
        except PatchError as err:
            return {"success": False, "error": str(err), **err.to_dict()}

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "report": report.to_dict(),
                "preview_after": report.after[:3000],
            }

        operation = stage_config_operation(
            hass,
            action="write",
            relative_path=path,
            content=report.after,
            create_dirs=False,
        )
        result = await async_apply_staged_operation(
            hass, operation["approval_id"], user_consent=False, consent_quote="",
        )
        return {
            "success": True,
            "message": f"Patched and applied: {path} ({len(report.applied)} ops)",
            "report": report.to_dict(),
            **result,
        }

    @staticmethod
    def _normalize_patches(raw_patches: object) -> list[dict[str, Any]] | str:
        if not isinstance(raw_patches, list):
            return "'patches' must be a non-empty list of objects"

        patches: list[dict[str, Any]] = []
        for index, patch in enumerate(raw_patches):
            if isinstance(patch, dict):
                patches.append(patch)
                continue
            if isinstance(patch, str):
                try:
                    parsed = json.loads(patch)
                except json.JSONDecodeError as err:
                    return f"patch #{index} is a string but not valid JSON: {err.msg}"
                if not isinstance(parsed, dict):
                    return f"patch #{index} JSON must decode to an object"
                patches.append(parsed)
                continue
            return f"patch #{index} must be an object"
        return patches


class ValidateServiceTool(llm.Tool):
    name = "ValidateService"
    description = "Validate service call parameters before using ServiceCall. Returns validity, errors, and suggestions."
    parameters = vol.Schema(
        {
            vol.Required("domain"): str,
            vol.Required("service"): str,
            vol.Optional("data", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..domain_registry import validate_service_call

        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})
        result = validate_service_call(domain, service, data)
        return {
            "success": result["valid"],
            "errors": result["errors"],
            "warnings": result["warnings"],
            "suggestions": result["suggestions"],
            "normalized_service": result["normalized_service"],
        }


class ServiceHelpTool(llm.Tool):
    name = "ServiceHelp"
    description = "Get help for a domain or service, including available services, parameter guidance, and accepted values."
    parameters = vol.Schema(
        {vol.Required("domain"): str, vol.Optional("service", default=""): str}
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..domain_registry import get_service_help

        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        help_text = get_service_help(domain, service if service else None)
        return {"success": True, "help": help_text}


class SmartDiscoveryTool(llm.Tool):
    name = "SmartDiscovery"
    description = """Smart entity discovery with multiple matching modes:
- by pattern: name_pattern="*motion*" or "*temperature*"
- by inferred type: inferred_type="person_detection"/"temperature"/"door_window" and more
- by person: person_name to discover related entities
- by pet: pet_name to discover related entities
- combined filtering: area + domain + device_class + state

Available inferred types: person_detection, motion_detection, door_window, temperature, humidity, light_level, power_monitoring, battery, location_tracking"""
    parameters = vol.Schema(
        {
            vol.Optional("area", default=""): str,
            vol.Optional("domain", default=""): str,
            vol.Optional("state", default=""): str,
            vol.Optional("name_contains", default=""): str,
            vol.Optional("name_pattern", default=""): str,
            vol.Optional("device_class", default=""): str,
            vol.Optional("inferred_type", default=""): str,
            vol.Optional("person_name", default=""): str,
            vol.Optional("pet_name", default=""): str,
            vol.Optional("limit", default=20): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from ..smart_discovery import get_smart_discovery

        discovery = get_smart_discovery(hass)
        args = tool_input.tool_args
        person_name = args.get("person_name", "")
        pet_name = args.get("pet_name", "")
        limit = min(args.get("limit", 20), 50)

        if person_name:
            results = await discovery.discover_person_entities(person_name, limit)
            return discovery.format_results(results, "person", person_name)

        if pet_name:
            results = await discovery.discover_pet_entities(pet_name, limit)
            return discovery.format_results(results, "pet", pet_name)

        results = await discovery.discover_entities(
            area=args.get("area") or None,
            domain=args.get("domain") or None,
            state=args.get("state") or None,
            name_contains=args.get("name_contains") or None,
            name_pattern=args.get("name_pattern") or None,
            device_class=args.get("device_class") or None,
            inferred_type=args.get("inferred_type") or None,
            limit=limit,
            assistant=llm_context.assistant,
        )

        query_type = "general"
        query = ""
        if args.get("inferred_type"):
            query_type = "inferred"
            query = args["inferred_type"]
        elif args.get("area"):
            query_type = "area"
            query = args["area"]
        elif args.get("name_pattern"):
            query_type = "pattern"
            query = args["name_pattern"]

        return discovery.format_results(results, query_type, query)


class EntityQueryTool(llm.Tool):
    name = "EntityQuery"
    description = "Query a Home Assistant entity state. Use this to get current device state, sensor values, and similar information. Supports entity IDs and fuzzy name matching."
    parameters = vol.Schema({vol.Required("entity_id"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        entity_id = tool_input.tool_args.get("entity_id", "")
        state = hass.states.get(entity_id)
        if state:
            if not entity_is_exposed(hass, entity_id, llm_context):
                return {"success": False, "error": f"Entity {entity_id} not found"}
            return {
                "success": True,
                "entity_id": entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
                "name": state.name,
            }

        from ..smart_discovery import get_smart_discovery

        discovery = get_smart_discovery(hass)
        results = await discovery.discover_entities(
            name_contains=entity_id,
            limit=5,
            assistant=llm_context.assistant if llm_context else None,
        )

        if results:
            matched_entity_id = _extract_discovered_entity_id(results)
            state = hass.states.get(matched_entity_id) if matched_entity_id else None
            if state and entity_is_exposed(hass, matched_entity_id, llm_context):
                return {
                    "success": True,
                    "entity_id": matched_entity_id,
                    "state": state.state,
                    "attributes": dict(state.attributes),
                    "name": state.name,
                    "matched_from": entity_id,
                }

        return {"success": False, "error": f"Entity {entity_id} not found"}


class ServiceCallTool(llm.Tool):
    name = "ServiceCall"
    description = """Call any Home Assistant service. Params: domain, service, data (dict), or flat service fields.
RULES:
1. entity_id MUST be provided. User device names are fuzzy-matched automatically.
2. Put service parameters as real fields, not boolean words. Correct: color_name="white"; wrong: white=true.
3. Service names are auto-routed per domain (e.g. vacuum turn_on→start, cover turn_on→open_cover).
4. Common parameter styles:
   light.turn_on: brightness(0-255), brightness_pct(0-100), color_name(str), rgb_color([r,g,b]), color_temp_kelvin(number)
   climate.set_temperature: temperature(number), hvac_mode(cool/heat/auto/dry/fan_only/off)
   fan.set_percentage: percentage(0-100); media_player.volume_set: volume_level(0-1)
   cover.set_cover_position / valve.set_valve_position: position(0-100)
5. Never omit parameters the user mentioned. Never add parameters the user didn't mention.
Use ListServices/ServiceHelp to discover available services and parameters."""
    parameters = vol.Schema(
        {
            vol.Required("domain"): str,
            vol.Required("service"): str,
            vol.Optional("data", default={}): dict,
        },
        extra=vol.ALLOW_EXTRA,
    )

    _RESERVED_KEYS = frozenset({"domain", "service", "data"})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})
        overflow = {
            k: v for k, v in tool_input.tool_args.items()
            if k not in self._RESERVED_KEYS
        }
        if overflow:
            if not isinstance(data, dict):
                data = {}
            data = {**overflow, **data}

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        if "json" in data and len(data) == 1:
            try:
                inner = json.loads(data["json"]) if isinstance(data["json"], str) else data["json"]
                data = inner if isinstance(inner, dict) else {}
            except Exception:
                data = {}

        if not isinstance(data, dict):
            data = {}
        sanitized: dict[str, object] = {}
        for k, v in data.items():
            if isinstance(k, str):
                sanitized[k] = v
        data = sanitized
        from ..domain_registry import normalize_service_data, get_action_service, fuzzy_resolve_service
        service = get_action_service(domain, service)
        ha_services = hass.services.async_services_for_domain(domain) if domain else {}
        if service not in ha_services:
            resolved = fuzzy_resolve_service(domain, service)
            if resolved and resolved in ha_services:
                service = resolved
        if domain == "light" and service in {"turn_on", "toggle"}:
            bad_color_keys = [
                key for key, value in data.items()
                if isinstance(key, str)
                and isinstance(value, bool)
                and value
                and key.strip().lower() in {
                    "white", "red", "green", "blue", "yellow", "purple",
                    "pink", "orange", "cyan", "magenta", "warm_white",
                    "cold_white", "warm", "cool",
                }
            ]
            if bad_color_keys:
                return {
                    "success": False,
                    "error": "Invalid light color parameter shape",
                    "retryable": True,
                    "domain": domain,
                    "service": service,
                    "bad_data": data,
                    "required_shape": {
                        "entity_id": "light.xxx",
                        "color_name": bad_color_keys[0],
                    },
                    "hint": "Do not send color words as boolean keys. Put the color value in color_name, rgb_color, or color_temp_kelvin.",
                }
        data = normalize_service_data(domain, service, data)
        data = await _async_normalize_camera_service_paths(hass, domain, service, data)

        if domain == "camera" and service in {"turn_on", "turn_off"}:
            return {
                "success": False,
                "error": "camera.turn_on/turn_off is not a reliable action for many camera entities. Use camera.snapshot or camera.record with an explicit entity_id instead.",
                "suggestions": {
                    "preferred_services": ["camera.snapshot", "camera.record"],
                    "required_fields": ["entity_id", "filename"],
                },
            }

        service_data, target = _extract_service_target(data)
        requires_target = await _service_requires_explicit_target(hass, domain, service)
        has_target = _has_explicit_target(service_data, target)
        if domain == "camera" and service in {"record", "snapshot"} and not has_target:
            return {
                "success": False,
                "error": f"camera.{service} requires an explicit camera entity_id target.",
                "suggestions": {
                    "required_fields": ["entity_id", "filename"],
                    "discovery_hint": "Use CameraCapture with camera_entity='list' to discover available cameras before calling this service.",
                    "example": {
                        "entity_id": "camera.your_camera_entity",
                        "filename": str(output_dir_path(hass) / ("camera.mp4" if service == "record" else "camera.jpg")),
                    },
                },
            }
        if requires_target and not has_target:
            return _build_missing_target_response(
                hass=hass,
                llm_context=llm_context,
                domain=domain,
                service=service,
                data=data,
                tool=self,
            )

        if "entity_id" in service_data or "entity_id" in target:
            entity_id = service_data.get("entity_id", target.get("entity_id"))
            if isinstance(entity_id, list):
                entity_id = entity_id[0] if entity_id else None
                if "entity_id" in service_data:
                    service_data["entity_id"] = entity_id
                else:
                    target["entity_id"] = entity_id
            if entity_id and not hass.states.get(str(entity_id)):
                from ..smart_discovery import get_smart_discovery
                discovery = get_smart_discovery(hass)

                _CROSS_DOMAINS = {"light": ["switch"], "switch": ["light"]}
                results = await discovery.discover_entities(
                    name_contains=entity_id,
                    domain=domain if domain else None,
                    limit=5,
                    assistant=llm_context.assistant if llm_context else None,
                )
                if not results and domain in _CROSS_DOMAINS:
                    for alt in _CROSS_DOMAINS[domain]:
                        results = await discovery.discover_entities(
                            name_contains=entity_id,
                            domain=alt,
                            limit=5,
                            assistant=llm_context.assistant if llm_context else None,
                        )
                        if results:
                            break

                matched_entity_id = None
                if results:
                    matched_entity_id = _extract_discovered_entity_id(results)
                    if matched_entity_id:
                        if "entity_id" in service_data:
                            service_data["entity_id"] = matched_entity_id
                        else:
                            target["entity_id"] = matched_entity_id
                if not matched_entity_id:
                    private_results = await discovery.discover_entities(
                        name_contains=entity_id,
                        domain=domain if domain else None,
                        limit=1,
                        skip_expose_check=True,
                    )
                    if private_results:
                        return privacy_blocked_response(entity_id)
                    if domain:
                        domain_resp = domain_unexposed_response(hass, entity_id, domain, llm_context)
                        if domain_resp:
                            return domain_resp
                    from homeassistant.helpers.llm import _get_exposed_entities

                    exposed_entities = (
                        _get_exposed_entities(hass, llm_context.assistant)
                        if llm_context.assistant
                        else {}
                    )
                    exposed = self._get_exposed_entities_list(domain, exposed_entities)
                    return {
                        "success": False,
                        "error": f"Entity not found: {entity_id}",
                        "available_entities": exposed[:10],
                    }
            if entity_id and hass.states.get(str(entity_id)) and not entity_is_exposed(hass, str(entity_id), llm_context):
                return privacy_blocked_response(entity_id)

        if domain == "cover":
            service, service_data = _resolve_cover_service_for_features(
                hass,
                service,
                service_data,
                target,
            )

        verify_entity_ids = _extract_entity_ids(service_data, target)
        before_states = {
            entity_id: (state.state if (state := hass.states.get(entity_id)) else None)
            for entity_id in verify_entity_ids
        }

        try:
            await hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=True,
                target=target or None,
            )
            response: dict[str, object] = {
                "success": True,
                "message": f"Successfully called {domain}.{service}",
                "domain": domain,
                "service": service,
                "data": service_data,
                "target": target,
            }
            if domain == "camera" and service in {"record", "snapshot"}:
                filename = service_data.get("filename")
                if isinstance(filename, str) and filename.strip():
                    verification = await _verify_generated_service_file(
                        hass,
                        filename,
                        timeout_seconds=max(
                            3,
                            int(service_data.get("duration", 0) or 0) + 5,
                        ),
                    )
                    response["verification"] = verification
                    if not bool(verification.get("verified")):
                        response["success"] = False
                        response["error"] = f"{domain}.{service} did not produce a verified output file"
                        response["message"] = f"Called {domain}.{service}, but output verification failed"
            if verify_entity_ids:
                verification = await _verify_entity_control_state(
                    hass,
                    verify_entity_ids,
                    service,
                    before_states,
                )
                response["verification"] = verification
                if not bool(verification.get("verified")):
                    response["success"] = False
                    response["error"] = f"{domain}.{service} did not verify target state change"
                    response["message"] = f"Called {domain}.{service}, but state verification failed"
            return response
        except Exception as err:
            _LOGGER.error("ServiceCall failed: %s.%s - %s", domain, service, err)
            return {"success": False, "error": str(err)}

    def _get_exposed_entities_list(self, domain: str, exposed_entities: dict) -> list:
        entities = exposed_entities.get("entities", {}) if exposed_entities else {}
        result = []
        for entity_id, info in entities.items():
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            result.append({"entity_id": entity_id, "names": info.get("names", "")})
        return result


class GetLiveContextTool(llm.Tool):
    name = "GetLiveContext"
    description = """Get the real-time state of exposed entities. Use this to answer questions about current device state.

Parameters:
- domain: optional domain filter (for example light, switch, sensor)
- area: optional area filter
- limit: optional maximum result count (default 50, max 100)"""
    parameters = vol.Schema(
        {
            vol.Optional("domain", default=""): str,
            vol.Optional("area", default=""): str,
            vol.Optional("limit", default=50): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from homeassistant.helpers import area_registry as ar, entity_registry as er

        domain_filter = tool_input.tool_args.get("domain", "")
        area_filter = tool_input.tool_args.get("area", "").lower()
        limit = min(tool_input.tool_args.get("limit", 50), 100)

        area_reg = ar.async_get(hass)
        entity_reg = er.async_get(hass)

        target_area_id = None
        if area_filter:
            for area in area_reg.async_list_areas():
                if area_filter in area.name.lower():
                    target_area_id = area.id
                    break

        entities = {}
        count = 0
        for state in hass.states.async_all():
            if count >= limit:
                break
            if not entity_is_exposed(hass, state.entity_id, llm_context):
                continue
            if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
                continue
            if target_area_id:
                entity_entry = entity_reg.async_get(state.entity_id)
                if entity_entry and entity_entry.area_id != target_area_id:
                    continue
            entities[state.entity_id] = {
                "name": state.name,
                "state": state.state,
                "domain": state.domain,
            }
            count += 1

        return {
            "success": True,
            "entities": entities,
            "count": count,
            "limited": count >= limit,
        }


class ListServicesTool(llm.Tool):
    name = "ListServices"
    description = "List all available services for a domain. Use this to inspect what can be called on a given domain."
    parameters = vol.Schema({vol.Required("domain"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        domain = tool_input.tool_args.get("domain", "")
        services = hass.services.async_services().get(domain, {})
        if services:
            service_list = []
            for name, service_obj in services.items():
                description = (
                    getattr(service_obj, "description", "")
                    if hasattr(service_obj, "description")
                    else ""
                )
                service_list.append(
                    f"- {domain}.{name}: {description[:100]}"
                    if description
                    else f"- {domain}.{name}"
                )
            return {
                "success": True,
                "domain": domain,
                "services": service_list,
                "count": len(service_list),
            }
        return {"success": False, "error": f"Domain {domain} does not exist or has no services"}


class RegistryTool(llm.Tool):
    name = "Registry"
    description = (
        "Manage Home Assistant registries: area, floor, label, category, entity. "
        "Actions: list, get, create, update, delete, remove, rename, expose. "
        "Top-level fields: registry, action, area_id, floor_id, label_id, category_id, entity_id, scope, params. "
        "label get/update/delete accept label_id or name. label create is idempotent by name. "
        "For label rename, use action=update with params={name: new_name}; label action=rename is treated as update. "
        "entity params support labels (replace), labels_add (append), labels_remove. "
        "Each label item may be an id, a name, or an object with name/icon/color/description; "
        "unknown names are auto-created, existing labels have missing fields filled in only. "
        "Use action=list on the label registry to discover valid color values. "
        "entity expose: params={should_expose: true/false, assistant: 'conversation'} to expose/hide entity from assistant."
    )
    parameters = vol.Schema(
        {
            vol.Required("registry"): vol.In(["area", "floor", "label", "category", "entity"]),
            vol.Required("action"): vol.In(["list", "get", "create", "update", "delete", "remove", "rename", "expose"]),
            vol.Optional("area_id", default=""): str,
            vol.Optional("floor_id", default=""): str,
            vol.Optional("label_id", default=""): str,
            vol.Optional("category_id", default=""): str,
            vol.Optional("entity_id", default=""): str,
            vol.Optional("scope", default=""): str,
            vol.Optional("params", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        registry_type = tool_input.tool_args.get("registry", "")
        action = tool_input.tool_args.get("action", "")
        params = tool_input.tool_args.get("params") or {}
        if registry_type == "label" and action == "rename":
            action = "update"

        try:
            if registry_type == "area":
                return await self._handle_area(hass, action, tool_input, params)
            if registry_type == "floor":
                return await self._handle_floor(hass, action, tool_input, params)
            if registry_type == "label":
                return await self._handle_label(hass, action, tool_input, params)
            if registry_type == "category":
                return await self._handle_category(hass, action, tool_input, params)
            if registry_type == "entity":
                return await self._handle_entity(hass, action, tool_input, params)
            return {"success": False, "error": f"Unknown registry: {registry_type}"}
        except Exception as err:
            _LOGGER.error("RegistryTool error: %s", err)
            return {"success": False, "error": str(err)}

    async def _handle_area(
        self, hass: HomeAssistant, action: str, tool_input: llm.ToolInput, params: dict
    ) -> JsonObjectType:
        from homeassistant.helpers import area_registry as ar

        registry = ar.async_get(hass)
        area_id = tool_input.tool_args.get("area_id", "") or params.get("area_id", "")

        if action == "list":
            areas = [
                {
                    "area_id": entry.id,
                    "name": entry.name,
                    "icon": entry.icon,
                    "floor_id": entry.floor_id,
                    "aliases": list(entry.aliases),
                    "labels": list(entry.labels),
                }
                for entry in registry.async_list_areas()
            ]
            return {"success": True, "areas": areas, "count": len(areas)}

        if action == "get":
            if not area_id:
                return {"success": False, "error": "area_id is required for get"}
            entry = registry.async_get_area(area_id)
            if not entry:
                return {"success": False, "error": f"Area not found: {area_id}"}
            return {
                "success": True,
                "area_id": entry.id,
                "name": entry.name,
                "icon": entry.icon,
                "floor_id": entry.floor_id,
                "aliases": list(entry.aliases),
                "labels": list(entry.labels),
                "picture": entry.picture,
            }

        if action == "create":
            name = params.get("name", "")
            if not name:
                return {"success": False, "error": "name is required for create"}
            data = {"name": name}
            for key in ("icon", "floor_id", "picture", "temperature_entity_id", "humidity_entity_id"):
                if key in params:
                    data[key] = params[key]
            if "aliases" in params:
                data["aliases"] = set(params["aliases"])
            if "labels" in params:
                data["labels"] = set(params["labels"])
            entry = registry.async_create(**data)
            return {"success": True, "area_id": entry.id, "name": entry.name}

        if action == "update":
            if not area_id:
                return {"success": False, "error": "area_id is required for update"}
            data = {"area_id": area_id}
            for key in ("name", "icon", "floor_id", "picture", "temperature_entity_id", "humidity_entity_id"):
                if key in params:
                    data[key] = params[key]
            if "aliases" in params:
                data["aliases"] = set(params["aliases"])
            if "labels" in params:
                data["labels"] = set(params["labels"])
            entry = registry.async_update(**data)
            return {"success": True, "area_id": entry.id, "name": entry.name}

        if action == "delete":
            if not area_id:
                return {"success": False, "error": "area_id is required for delete"}
            registry.async_delete(area_id)
            return {"success": True, "message": f"Deleted area {area_id}"}

        return {"success": False, "error": f"Unknown action for area: {action}"}

    async def _handle_floor(
        self, hass: HomeAssistant, action: str, tool_input: llm.ToolInput, params: dict
    ) -> JsonObjectType:
        from homeassistant.helpers import floor_registry as fr

        registry = fr.async_get(hass)
        floor_id = tool_input.tool_args.get("floor_id", "") or params.get("floor_id", "")

        if action == "list":
            floors = [
                {
                    "floor_id": entry.floor_id,
                    "name": entry.name,
                    "icon": entry.icon,
                    "level": entry.level,
                    "aliases": list(entry.aliases),
                }
                for entry in registry.async_list_floors()
            ]
            return {"success": True, "floors": floors, "count": len(floors)}

        if action == "get":
            if not floor_id:
                return {"success": False, "error": "floor_id is required for get"}
            entry = registry.async_get_floor(floor_id)
            if not entry:
                return {"success": False, "error": f"Floor not found: {floor_id}"}
            return {
                "success": True,
                "floor_id": entry.floor_id,
                "name": entry.name,
                "icon": entry.icon,
                "level": entry.level,
                "aliases": list(entry.aliases),
            }

        if action == "create":
            name = params.get("name", "")
            if not name:
                return {"success": False, "error": "name is required for create"}
            data = {"name": name}
            for key in ("icon", "level"):
                if key in params:
                    data[key] = params[key]
            if "aliases" in params:
                data["aliases"] = set(params["aliases"])
            entry = registry.async_create(**data)
            return {"success": True, "floor_id": entry.floor_id, "name": entry.name}

        if action == "update":
            if not floor_id:
                return {"success": False, "error": "floor_id is required for update"}
            data = {"floor_id": floor_id}
            for key in ("name", "icon", "level"):
                if key in params:
                    data[key] = params[key]
            if "aliases" in params:
                data["aliases"] = set(params["aliases"])
            entry = registry.async_update(**data)
            return {"success": True, "floor_id": entry.floor_id, "name": entry.name}

        if action == "delete":
            if not floor_id:
                return {"success": False, "error": "floor_id is required for delete"}
            registry.async_delete(floor_id)
            return {"success": True, "message": f"Deleted floor {floor_id}"}

        return {"success": False, "error": f"Unknown action for floor: {action}"}

    async def _handle_label(
        self, hass: HomeAssistant, action: str, tool_input: llm.ToolInput, params: dict
    ) -> JsonObjectType:
        from homeassistant.helpers import label_registry as lr
        from homeassistant.components.config.label_registry import (
            SUPPORTED_LABEL_THEME_COLORS,
        )

        registry = lr.async_get(hass)
        raw_identifier = (
            tool_input.tool_args.get("label_id", "")
            or params.get("label_id", "")
            or params.get("name", "")
        )

        def _resolve_label_id(ident: str) -> str | None:
            if not ident:
                return None
            if registry.async_get_label(ident):
                return ident
            ident_lower = ident.strip().lower()
            for entry in registry.async_list_labels():
                if entry.name.lower() == ident_lower:
                    return entry.label_id
            return None

        label_id = None
        if action in ("get", "update", "delete"):
            label_id = _resolve_label_id(raw_identifier)

        if action == "list":
            labels = [
                {
                    "label_id": entry.label_id,
                    "name": entry.name,
                    "icon": entry.icon,
                    "color": entry.color,
                    "description": entry.description,
                }
                for entry in registry.async_list_labels()
            ]
            return {
                "success": True,
                "labels": labels,
                "count": len(labels),
                "supported_colors": sorted(SUPPORTED_LABEL_THEME_COLORS),
            }

        if action == "get":
            if not raw_identifier:
                return {"success": False, "error": "label_id or name is required for get"}
            if not label_id:
                return {
                    "success": False,
                    "error": f"Label not found by id or name: '{raw_identifier}'. Use action=list to see available labels.",
                }
            entry = registry.async_get_label(label_id)
            return {
                "success": True,
                "label_id": entry.label_id,
                "name": entry.name,
                "icon": entry.icon,
                "color": entry.color,
                "description": entry.description,
            }

        if action == "create":
            name = params.get("name", "")
            if not name:
                return {"success": False, "error": "name is required for create"}
            existing = registry.async_get_label_by_name(name)
            if existing:
                return {
                    "success": True,
                    "label_id": existing.label_id,
                    "name": existing.name,
                    "color": existing.color,
                    "icon": existing.icon,
                    "description": existing.description,
                    "message": "Label already exists; returning existing entry.",
                }
            data = {"name": name}
            for key in ("icon", "color", "description"):
                if key in params:
                    data[key] = params[key]
            entry = registry.async_create(**data)
            return {
                "success": True,
                "label_id": entry.label_id,
                "name": entry.name,
                "color": entry.color,
                "icon": entry.icon,
                "description": entry.description,
            }

        if action == "update":
            if not raw_identifier:
                return {"success": False, "error": "label_id or name is required for update"}
            if not label_id:
                return {
                    "success": False,
                    "error": f"Label not found by id or name: '{raw_identifier}'. Use action=list to see available labels.",
                }
            data = {"label_id": label_id}
            for key in ("name", "icon", "color", "description"):
                if key in params:
                    data[key] = params[key]
            entry = registry.async_update(**data)
            return {
                "success": True,
                "label_id": entry.label_id,
                "name": entry.name,
                "color": entry.color,
                "icon": entry.icon,
                "description": entry.description,
            }

        if action == "delete":
            if not raw_identifier:
                return {"success": False, "error": "label_id or name is required for delete"}
            if not label_id:
                return {
                    "success": False,
                    "error": f"Label not found by id or name: '{raw_identifier}'. Use action=list to see available labels.",
                }
            registry.async_delete(label_id)
            return {"success": True, "message": f"Deleted label {label_id}"}

        return {"success": False, "error": f"Unknown action for label: {action}"}

    async def _handle_category(
        self, hass: HomeAssistant, action: str, tool_input: llm.ToolInput, params: dict
    ) -> JsonObjectType:
        from homeassistant.helpers import category_registry as cr

        registry = cr.async_get(hass)
        scope = tool_input.tool_args.get("scope", "") or params.get("scope", "")
        category_id = tool_input.tool_args.get("category_id", "") or params.get("category_id", "")

        if action == "list":
            if not scope:
                return {"success": False, "error": "scope is required for list (e.g. 'automation', 'entity')"}
            categories = [
                {
                    "category_id": entry.category_id,
                    "name": entry.name,
                    "icon": entry.icon,
                }
                for entry in registry.async_list_categories(scope=scope)
            ]
            return {"success": True, "categories": categories, "count": len(categories), "scope": scope}

        if action == "get":
            if not scope or not category_id:
                return {"success": False, "error": "scope and category_id are required for get"}
            entry = registry.async_get_category(scope=scope, category_id=category_id)
            if not entry:
                return {"success": False, "error": f"Category not found: {category_id} in scope {scope}"}
            return {
                "success": True,
                "category_id": entry.category_id,
                "name": entry.name,
                "icon": entry.icon,
                "scope": scope,
            }

        if action == "create":
            if not scope:
                return {"success": False, "error": "scope is required for create"}
            name = params.get("name", "")
            if not name:
                return {"success": False, "error": "name is required for create"}
            data = {"scope": scope, "name": name}
            if "icon" in params:
                data["icon"] = params["icon"]
            entry = registry.async_create(**data)
            return {"success": True, "category_id": entry.category_id, "name": entry.name, "scope": scope}

        if action == "update":
            if not scope or not category_id:
                return {"success": False, "error": "scope and category_id are required for update"}
            data = {"scope": scope, "category_id": category_id}
            for key in ("name", "icon"):
                if key in params:
                    data[key] = params[key]
            entry = registry.async_update(**data)
            return {"success": True, "category_id": entry.category_id, "name": entry.name}

        if action == "delete":
            if not scope or not category_id:
                return {"success": False, "error": "scope and category_id are required for delete"}
            registry.async_delete(scope=scope, category_id=category_id)
            return {"success": True, "message": f"Deleted category {category_id} in scope {scope}"}

        return {"success": False, "error": f"Unknown action for category: {action}"}

    async def _handle_entity(
        self, hass: HomeAssistant, action: str, tool_input: llm.ToolInput, params: dict
    ) -> JsonObjectType:
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        entity_id = tool_input.tool_args.get("entity_id", "") or params.get("entity_id", "")

        if action == "list":
            entities = []
            for entry in list(registry.entities.values())[:100]:
                entities.append({
                    "entity_id": entry.entity_id,
                    "name": entry.name or entry.original_name,
                    "area_id": entry.area_id,
                    "labels": list(entry.labels),
                    "disabled_by": entry.disabled_by.value if entry.disabled_by else None,
                })
            return {"success": True, "entities": entities, "count": len(registry.entities), "shown": len(entities)}

        if action == "get":
            if not entity_id:
                return {"success": False, "error": "entity_id is required for get"}
            entry = registry.async_get(entity_id)
            if not entry:
                return {"success": False, "error": f"Entity not found: {entity_id}"}
            return {
                "success": True,
                "entity_id": entry.entity_id,
                "name": entry.name,
                "original_name": entry.original_name,
                "area_id": entry.area_id,
                "device_id": entry.device_id,
                "labels": list(entry.labels),
                "categories": dict(entry.categories),
                "icon": entry.icon,
                "disabled_by": entry.disabled_by.value if entry.disabled_by else None,
                "hidden_by": entry.hidden_by.value if entry.hidden_by else None,
            }

        if action in ("update", "rename"):
            if not entity_id:
                return {"success": False, "error": "entity_id is required for update"}
            entry = registry.async_get(entity_id)
            if not entry:
                return {"success": False, "error": f"Entity not found: {entity_id}"}

            changes = {}
            for key in ("name", "icon", "area_id", "device_class"):
                if key in params:
                    changes[key] = params[key]
            if "new_entity_id" in params:
                changes["new_entity_id"] = params["new_entity_id"]

            labels_touched = any(
                k in params for k in ("labels", "labels_add", "labels_remove")
            )
            if labels_touched:
                from homeassistant.helpers import label_registry as lr

                lreg = lr.async_get(hass)

                def _resolve_or_create(item, *, auto_create: bool) -> str | None:
                    """Accept a string (id/name) or a dict with {name, icon, color, description}."""
                    if isinstance(item, dict):
                        ident = str(item.get("name") or item.get("label_id") or "").strip()
                        extras = {
                            k: item[k]
                            for k in ("icon", "color", "description")
                            if k in item and item[k] is not None
                        }
                    else:
                        ident = str(item or "").strip()
                        extras = {}
                    if not ident:
                        return None
                    if lreg.async_get_label(ident):
                        existing_id = ident
                    else:
                        by_name = lreg.async_get_label_by_name(ident)
                        existing_id = by_name.label_id if by_name else None
                    if existing_id:
                        return existing_id
                    if auto_create:
                        try:
                            created = lreg.async_create(name=ident, **extras)
                            return created.label_id
                        except Exception as err:
                            _LOGGER.warning(
                                "Failed to auto-create label %r: %s", ident, err
                            )
                            return None
                    return None

                def _coerce_to_list(raw) -> list:
                    """Accept list/tuple/set, single string, delimited string, or single dict.
                    List items may be strings or dicts with {name, icon, color, description}."""
                    if raw is None:
                        return []
                    if isinstance(raw, dict):
                        return [raw]
                    if isinstance(raw, (list, tuple, set)):
                        out = []
                        for v in raw:
                            if isinstance(v, dict):
                                out.append(v)
                            else:
                                s = str(v).strip()
                                if s:
                                    out.append(s)
                        return out
                    if isinstance(raw, str):
                        text = raw.strip()
                        if not text:
                            return []
                        import re as _re
                        parts = _re.split(r"[,\uFF0C\u3001;\uFF1B/|\n]+", text)
                        parts = [p.strip() for p in parts if p.strip()]
                        return parts or [text]
                    s = str(raw).strip()
                    return [s] if s else []

                def _resolve_list(values, *, auto_create: bool) -> tuple[set[str], list[str]]:
                    resolved: set[str] = set()
                    unknown: list[str] = []
                    for v in _coerce_to_list(values):
                        rid = _resolve_or_create(v, auto_create=auto_create)
                        if rid:
                            resolved.add(rid)
                        else:
                            label = v.get("name") if isinstance(v, dict) else v
                            unknown.append(str(label))
                    return resolved, unknown

                current = set(entry.labels)
                unknown_all: list[str] = []

                if "labels" in params:
                    new_set, unk = _resolve_list(params["labels"], auto_create=True)
                    unknown_all.extend(unk)
                    current = new_set
                if "labels_add" in params:
                    add_set, unk = _resolve_list(params["labels_add"], auto_create=True)
                    unknown_all.extend(unk)
                    current = current | add_set
                if "labels_remove" in params:
                    rem_set, unk = _resolve_list(params["labels_remove"], auto_create=False)
                    current = current - rem_set

                changes["labels"] = current
                if unknown_all:
                    _LOGGER.info(
                        "Labels could not be resolved/created: %s", unknown_all
                    )

            if "disabled_by" in params:
                if params["disabled_by"] is None:
                    changes["disabled_by"] = None
                else:
                    changes["disabled_by"] = er.RegistryEntryDisabler.USER
            if "hidden_by" in params:
                if params["hidden_by"] is None:
                    changes["hidden_by"] = None
                else:
                    changes["hidden_by"] = er.RegistryEntryHider.USER
            if "categories" in params:
                categories = dict(entry.categories)
                for scope, cat_id in params["categories"].items():
                    if cat_id is None and scope in categories:
                        del categories[scope]
                    elif cat_id is not None:
                        categories[scope] = cat_id
                changes["categories"] = categories

            if not changes:
                return {"success": False, "error": "No changes specified in params"}

            updated = registry.async_update_entity(entity_id, **changes)
            result_entity_id = changes.get("new_entity_id", entity_id)
            result: dict[str, Any] = {
                "success": True,
                "entity_id": result_entity_id,
                "message": f"Updated entity {entity_id}"
                + (f" -> {result_entity_id}" if "new_entity_id" in changes else ""),
            }
            if labels_touched:
                result["labels"] = sorted(updated.labels)
                if "unknown_all" in locals() and unknown_all:
                    result["unresolved_labels"] = unknown_all
            return result

        if action == "remove":
            if not entity_id:
                return {"success": False, "error": "entity_id is required for remove"}
            if entity_id not in registry.entities:
                return {"success": False, "error": f"Entity not found: {entity_id}"}
            registry.async_remove(entity_id)
            return {"success": True, "message": f"Removed entity {entity_id}"}

        if action == "expose":
            if not entity_id:
                return {"success": False, "error": "entity_id is required for expose"}
            should_expose = params.get("should_expose", True)
            assistant = params.get("assistant", "conversation")
            from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
            async_expose_entity(hass, assistant, entity_id, bool(should_expose))
            word = "Exposed" if should_expose else "Unexposed"
            return {"success": True, "message": f"{word} {entity_id} to {assistant}"}

        return {"success": False, "error": f"Unknown action for entity: {action}"}


class ScriptExecuteTool(llm.Tool):
    name = "ScriptExecute"
    description = "Execute a Home Assistant script."
    parameters = vol.Schema(
        {
            vol.Required("script_id"): str,
            vol.Optional("variables", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        script_id = tool_input.tool_args.get("script_id", "")
        variables = tool_input.tool_args.get("variables", {})
        try:
            if not script_id.startswith("script."):
                script_id = f"script.{script_id}"
            await hass.services.async_call(
                "script",
                "turn_on",
                {"entity_id": script_id, "variables": variables},
                blocking=True,
            )
            return {"success": True, "message": f"Executed {script_id}"}
        except Exception as err:
            return {"success": False, "error": str(err)}


class HistoryQueryTool(llm.Tool):
    name = "HistoryQuery"
    description = "Query entity history."
    parameters = vol.Schema(
        {
            vol.Required("entity_id"): str,
            vol.Optional("hours", default=24): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from datetime import datetime, timedelta

        entity_id = tool_input.tool_args.get("entity_id", "")
        if hass.states.get(entity_id) and not entity_is_exposed(hass, entity_id, llm_context):
            return {"success": False, "error": f"Entity {entity_id} not found"}
        hours = tool_input.tool_args.get("hours", 24)
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import (
                state_changes_during_period,
            )

            start = datetime.now() - timedelta(hours=hours)
            history = await get_instance(hass).async_add_executor_job(
                state_changes_during_period, hass, start, None, entity_id
            )
            if entity_id in history:
                states = [
                    {"state": state.state, "time": state.last_changed.isoformat()}
                    for state in history[entity_id][-20:]
                ]
                return {"success": True, "entity_id": entity_id, "history": states}
            return {"success": False, "error": "No history found"}
        except Exception as err:
            return {"success": False, "error": str(err)}


class AreaDevicesTool(llm.Tool):
    name = "AreaDevices"
    description = "Get all devices in a specific area."
    parameters = vol.Schema({vol.Required("area"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        from homeassistant.helpers import (
            area_registry as ar,
            device_registry as dr,
            entity_registry as er,
        )

        area_name = tool_input.tool_args.get("area", "").lower()
        area_reg = ar.async_get(hass)
        device_reg = dr.async_get(hass)
        entity_reg = er.async_get(hass)

        target_area = None
        for area in area_reg.async_list_areas():
            if area_name in area.name.lower():
                target_area = area
                break

        if not target_area:
            return {
                "success": False,
                "error": f"Area '{area_name}' not found",
                "available_areas": [area.name for area in area_reg.async_list_areas()],
            }

        devices = []
        for device in device_reg.devices.values():
            if device.area_id == target_area.id:
                entities = [
                    entity.entity_id
                    for entity in entity_reg.entities.values()
                    if entity.device_id == device.id
                ]
                devices.append({"name": device.name, "entities": entities})

        return {"success": True, "area": target_area.name, "devices": devices}


class BatchControlTool(llm.Tool):
    name = "BatchControl"
    description = "Control multiple devices in one batch. Use entity_ids when known. Otherwise use discovery filters domain/area/state/name_contains. Domain-aware actions are normalized internally: for vacuum, turn_on means start cleaning and turn_off means return to base; for cover, turn_on/open means open and turn_off means close; for lock, turn_on means unlock and turn_off means lock. For 'turn off all lights', use domain='light', state='on', action='turn_off'. For 'open/start all vacuums', use domain='vacuum', action='turn_on'. Do not ask the user to list entities when domain/filter is enough."
    parameters = vol.Schema(
        {
            vol.Optional("entity_ids", default=[]): list,
            vol.Required("action"): vol.In(["turn_on", "turn_off", "toggle"]),
            vol.Optional("data", default={}): dict,
            vol.Optional("domain", default=""): str,
            vol.Optional("area", default=""): str,
            vol.Optional("state", default=""): str,
            vol.Optional("name_contains", default=""): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        entity_ids = tool_input.tool_args.get("entity_ids", [])
        action = tool_input.tool_args.get("action", "turn_on")
        data = tool_input.tool_args.get("data", {})
        entity_ids = [str(entity_id) for entity_id in entity_ids if isinstance(entity_id, str)]
        if not entity_ids:
            domain = str(tool_input.tool_args.get("domain", "") or "").strip()
            area = str(tool_input.tool_args.get("area", "") or "").strip()
            state = str(tool_input.tool_args.get("state", "") or "").strip()
            name_contains = str(tool_input.tool_args.get("name_contains", "") or "").strip()
            if not domain:
                return {
                    "success": False,
                    "error": "entity_ids or domain is required",
                    "hint": "For 'all lights', call BatchControl with domain='light' and optionally state='on'.",
                }
            from ..smart_discovery import get_smart_discovery
            discovery = get_smart_discovery(hass)
            discovered = await discovery.discover_entities(
                domain=domain,
                area=area or None,
                state=state or None,
                name_contains=name_contains or None,
                limit=100,
                assistant=llm_context.assistant if llm_context else None,
            )
            entity_ids = [
                str(item.get("entity_id"))
                for item in discovered
                if isinstance(item, dict) and item.get("entity_id")
            ]
            if not entity_ids:
                return {
                    "success": False,
                    "error": "no matching entities found",
                    "filters": {
                        "domain": domain,
                        "area": area,
                        "state": state,
                        "name_contains": name_contains,
                    },
                }
        before_states = {
            entity_id: (state.state if (state := hass.states.get(entity_id)) else None)
            for entity_id in entity_ids
        }
        results = []
        called_services: dict[str, str] = {}
        for entity_id in entity_ids:
            try:
                domain = entity_id.split(".")[0]
                from ..domain_registry import get_action_service, normalize_service_data, fuzzy_resolve_service
                service = get_action_service(domain, action)
                ha_services = hass.services.async_services_for_domain(domain)
                if service not in ha_services:
                    resolved = fuzzy_resolve_service(domain, service)
                    if resolved and resolved in ha_services:
                        service = resolved
                called_services[entity_id] = service
                svc_data = normalize_service_data(domain, service, dict(data))
                if domain == "cover":
                    service, service_payload = _resolve_cover_service_for_features(
                        hass,
                        service,
                        {"entity_id": entity_id, **svc_data},
                    )
                    called_services[entity_id] = service
                    svc_data = {
                        k: v for k, v in service_payload.items()
                        if k != "entity_id"
                    }
                await hass.services.async_call(
                    domain,
                    service,
                    {"entity_id": entity_id, **svc_data},
                    blocking=True,
                )
                results.append({"entity_id": entity_id, "service": service, "success": True})
            except Exception as err:
                results.append(
                    {"entity_id": entity_id, "success": False, "error": str(err)}
                )
        verification = await _verify_entity_control_state(
            hass,
            entity_ids,
            called_services,
            before_states,
        )
        by_entity = {
            str(item.get("entity_id")): item
            for item in verification.get("entities", [])
            if isinstance(item, dict)
        }
        for item in results:
            entity_id = item.get("entity_id")
            if isinstance(entity_id, str) and entity_id in by_entity:
                item["verification"] = by_entity[entity_id]
                if not bool(by_entity[entity_id].get("verified")):
                    item["success"] = False
                    item["error"] = "state verification failed"
        return {
            "success": all(bool(item.get("success")) for item in results),
            "results": results,
            "verification": verification,
        }


class NotifyTool(llm.Tool):
    name = "Notify"
    description = (
        "Send a notification. "
        "target: 'persistent_notification' (default), "
        "'wechat:account_id:user_id', 'qq:user:openid', 'qq:group:group_openid', "
        "'qq:channel:channel_id', or a notify service target."
    )
    parameters = vol.Schema(
        {
            vol.Required("message"): str,
            vol.Optional("title", default="AI Assistant"): str,
            vol.Optional("target", default="persistent_notification"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        message = tool_input.tool_args.get("message", "")
        title = tool_input.tool_args.get("title", "AI Assistant")
        target = tool_input.tool_args.get("target", "persistent_notification")
        try:
            if target.startswith("wechat:"):
                await async_send_im_payload(hass, target, message=message)
            elif target.startswith("qq:"):
                await async_send_im_payload(hass, target, message=message)
            elif target == "persistent_notification":
                await hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {"message": message, "title": title},
                    blocking=True,
                )
            else:
                await hass.services.async_call(
                    "notify",
                    target,
                    {"message": message, "title": title},
                    blocking=True,
                )
            return {"success": True, "message": "Notification sent"}
        except Exception as err:
            return {"success": False, "error": str(err)}


class IntentCallTool(llm.Tool):
    name = "IntentCall"
    description = (
        "Execute third-party Home Assistant intent handlers only. "
        "Do NOT use this for Claw Assistant plugins, plugin tools, skills, slash commands, or tools already listed in the function schema. "
        "Claw plugin tools are separate tools and must be called directly by their tool name.\n"
        "action=list: Discover all available intents, their descriptions, and slot definitions (REQUIRED/optional). "
        "Call this only when the user explicitly asks for a Home Assistant intent handler or no Claw tool/plugin/tool schema applies.\n"
        "action=call: Execute an intent. You MUST pass all REQUIRED slot values in the 'slots' dict.\n"
        "RULES: Do NOT omit REQUIRED slots. Do NOT invent intent names - use action=list first. "
        "Show the result 'speech' to user as-is if it contains markdown or images."
    )
    parameters = vol.Schema({
        vol.Required("action", description="'list' to discover available intents, 'call' to execute one"): vol.In(["list", "call"]),
        vol.Optional("intent_type", description="The intent_type string returned by action=list"): str,
        vol.Optional("slots", description="Flat dict of slot name-value pairs. Must include all REQUIRED slots returned by action=list."): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from homeassistant.helpers import intent as intent_mod

        action = tool_input.tool_args.get("action", "")
        if not action:
            return {"success": False, "error": "Missing required parameter: action. Use 'list' or 'call'."}

        if action == "list":
            handlers = intent_mod.async_get(hass)
            items = []
            for h in handlers:
                if h.intent_type.startswith("Hass"):
                    continue
                slot_list = []
                if h.slot_schema:
                    for k, v in h.slot_schema.items():
                        key_name = k.schema if hasattr(k, "schema") else str(k)
                        required = isinstance(k, vol.Required)
                        desc = getattr(k, "description", "") or ""
                        default = getattr(k, "default", None)
                        type_name = "string"
                        if v is bool:
                            type_name = "boolean"
                        elif v is int or v == vol.Coerce(int):
                            type_name = "integer"
                        slot_entry = {
                            "name": key_name,
                            "required": required,
                            "type": type_name,
                            "description": desc,
                        }
                        if default is not None and default is not vol.UNDEFINED:
                            slot_entry["default"] = str(default)
                        slot_list.append(slot_entry)
                items.append({
                    "intent_type": h.intent_type,
                    "description": h.description or "",
                    "slots": slot_list,
                })
            return {
                "success": True,
                "count": len(items),
                "intents": items,
                "usage": "To call an intent: action='call', intent_type='<intent_type>', slots={<slot_name>: <value>, ...}",
            }

        if action == "call":
            intent_type = tool_input.tool_args.get("intent_type", "")
            if not intent_type:
                return {"success": False, "error": "intent_type is required"}
            raw_slots = tool_input.tool_args.get("slots") or {}
            slots = {k: {"value": v} for k, v in raw_slots.items()}
            try:
                result = await intent_mod.async_handle(
                    hass,
                    "claw_assistant",
                    intent_type,
                    slots=slots,
                )
                speech = ""
                if result.speech:
                    speech = (
                        result.speech.get("plain", {}).get("speech", "")
                        if isinstance(result.speech, dict)
                        else str(result.speech)
                    )
                ret: dict[str, Any] = {"success": True, "speech": speech}
                if "![" in speech and "](" in speech:
                    ret["display_hint"] = "Response contains markdown images. Show the speech content to user as-is, do NOT rewrite image URLs."
                return ret
            except Exception as err:
                return {"success": False, "error": str(err)}

        return {"success": False, "error": f"Unknown action: {action}"}
