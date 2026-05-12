from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)

_FRONTEND_SNAPSHOT_KEY = "claw_frontend_snapshot"
_FRONTEND_EXEC_QUEUE = "claw_frontend_exec_queue"
_FRONTEND_EXEC_RESULTS = "claw_frontend_exec_results"
_FRONTEND_EXEC_EVENTS = "claw_frontend_exec_events"
_FRONTEND_EXEC_SUBS = "claw_frontend_exec_subs"
_FRONTEND_TEXT_CACHE = "claw_frontend_text_cache"

_SNAPSHOT_TTL = 5


def _domain_data(hass: HomeAssistant) -> dict:
    return hass.data.setdefault("claw_assistant", {})


def store_frontend_snapshot(hass: HomeAssistant, snapshot: dict) -> None:
    _domain_data(hass)[_FRONTEND_SNAPSHOT_KEY] = {
        "ts": time.time(),
        "data": snapshot,
    }


def get_frontend_snapshot(hass: HomeAssistant) -> dict | None:
    entry = _domain_data(hass).get(_FRONTEND_SNAPSHOT_KEY)
    if not entry:
        return None
    if time.time() - entry["ts"] > _SNAPSHOT_TTL:
        return None
    return entry["data"]


def queue_frontend_exec(hass: HomeAssistant, exec_id: str, js_code: str) -> None:
    dd = _domain_data(hass)
    q = dd.setdefault(_FRONTEND_EXEC_QUEUE, [])
    task = {"id": exec_id, "code": js_code}
    q.append(task)
    subs: list = dd.get(_FRONTEND_EXEC_SUBS, [])
    pushed = False
    for sub in subs:
        try:
            sub.send_message({"id": sub.ws_msg_id, "type": "event", "event": {"type": "exec", "task": task}})
            pushed = True
        except Exception:
            pass
    if not pushed:
        hass.bus.async_fire("claw_frontend_exec", {"id": exec_id, "code": js_code})


def store_frontend_exec_result(hass: HomeAssistant, exec_id: str, result: Any) -> None:
    dd = _domain_data(hass)
    results = dd.setdefault(_FRONTEND_EXEC_RESULTS, {})
    results[exec_id] = {"ts": time.time(), "result": result}
    events: dict[str, asyncio.Event] = dd.get(_FRONTEND_EXEC_EVENTS, {})
    ev = events.get(exec_id)
    if ev:
        ev.set()


def store_frontend_text_cache(hass: HomeAssistant, source: str, value: Any) -> None:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if not text:
        return
    cache = _domain_data(hass).setdefault(_FRONTEND_TEXT_CACHE, [])
    cache.append({"ts": time.time(), "source": source, "text": text[:200000]})
    del cache[:-10]


def search_frontend_text_cache(hass: HomeAssistant, query: str, limit: int = 5000) -> dict:
    cache = _domain_data(hass).get(_FRONTEND_TEXT_CACHE, [])
    q = query.casefold()
    remaining = limit
    matches = []
    for entry in reversed(cache):
        text = entry.get("text", "")
        hay = text.casefold()
        start = 0
        while remaining > 0:
            idx = hay.find(q, start)
            if idx < 0:
                break
            radius = min(700, max(120, remaining // 2))
            a = max(0, idx - radius)
            b = min(len(text), idx + len(query) + radius)
            snippet = text[a:b]
            matches.append({
                "source": entry.get("source", ""),
                "offset": idx,
                "text": snippet,
            })
            remaining -= len(snippet)
            start = idx + len(query)
    return {
        "query": query,
        "matches": matches,
        "truncated": remaining <= 0,
        "cache_entries": len(cache),
    }


def pop_frontend_exec_result(hass: HomeAssistant, exec_id: str, timeout: float = 10.0) -> Any:
    results = _domain_data(hass).setdefault(_FRONTEND_EXEC_RESULTS, {})
    if exec_id in results:
        return results.pop(exec_id)["result"]
    return None


async def async_wait_frontend_exec_result(hass: HomeAssistant, exec_id: str, timeout: float = 15.0) -> Any:
    dd = _domain_data(hass)
    results = dd.setdefault(_FRONTEND_EXEC_RESULTS, {})
    if exec_id in results:
        return results.pop(exec_id)["result"]
    events = dd.setdefault(_FRONTEND_EXEC_EVENTS, {})
    ev = asyncio.Event()
    events[exec_id] = ev
    try:
        await asyncio.wait_for(ev.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "error": "timeout",
            "message": f"Frontend did not respond within {timeout}s",
            "_action_required": "Frontend bridge did not acknowledge this operation. Retry the same FrontendInspect action once; do not call internal websocket commands directly.",
        }
    finally:
        events.pop(exec_id, None)
    if exec_id in results:
        return results.pop(exec_id)["result"]
    return {
        "error": "timeout",
        "message": "Event fired but result missing",
        "_action_required": "Frontend bridge result was lost after acknowledgement. Retry the same FrontendInspect action once; do not call internal websocket commands directly.",
    }


class FrontendInspectTool(llm.Tool):
    name = "FrontendInspect"
    description = (
        "Control and inspect the ENTIRE Home Assistant frontend like a real user — see screen, click buttons, fill forms, scroll, navigate. "
        "Works on ANY page: dashboards, settings, integrations, automations, device config, add-ons, logs, dialogs, popups, modals, etc. "
        "This is for UI interaction — NOT for device control or entity state queries. "
        "\n\n"
        "FULL-SCREEN JS EFFECTS (exec_js): "
        "Use action=exec_js to inject ANY JavaScript into the frontend — you have FULL CONTROL over the entire UI. "
        "Capabilities include but are not limited to: full-screen overlays, tutorial masks, step-by-step guided tours, animated popups, "
        "highlight boxes, onboarding wizards, device pairing guides, interactive walkthroughs, toast notifications, progress indicators, "
        "custom modals, floating panels, spotlight effects, DOM manipulation, style injection, event listeners, timers, animations. "
        "If user asks for ANY dynamic visual effect, guided experience, or temporary UI overlay — use exec_js. "
        "This is NOT DashboardCard — DashboardCard is for persistent Lovelace cards only. exec_js is for dynamic, temporary, full-control JS injection. "
        "\n\n"
        "MANDATORY WORKFLOW - You MUST complete BOTH steps before answering about UI content: "
        "1) FIRST action=snapshot → Get DOM structure, card types, entity bindings. "
        "2) SECOND action=exec_js → Get rendered content. Do NOT blindly truncate with slice(); return full text and this tool will split long strings into chunks. "
        "For large pages, exec_js stores full results in an internal text cache; use action=search_cache with query=function/card/dialog/label name to get precise snippets under 5000 chars. "
        "DO NOT stop after snapshot alone — snapshot gives structure, exec_js gives ACTUAL CONTENT. You need BOTH. "
        "If user asks about cards/UI/screen, you MUST call snapshot THEN exec_js before responding. "
        "\n\n"
        "WHAT YOU CAN DO: "
        "- View ANY page content: dashboards, cards, settings panels, integration configs, automation editors, system logs, add-on pages. "
        "- Click ANY element: buttons, links, menu items, cards, icons, toggles, tabs, dialog buttons, popup options. "
        "- Fill ANY form: input fields, textareas, search boxes, config forms, automation conditions, entity selectors. "
        "- Use safe keyboard actions: Enter, Escape, Tab, Arrow keys, Backspace/Delete, repeated navigation key presses. Avoid global shortcut keys. "
        "- Handle dialogs/popups: confirmation dialogs, edit dialogs, more-info popups, config wizards, modal windows. "
        "- Scroll ANY container: page scroll, card lists, long forms, log viewers, dropdown menus. "
        "- Navigate to ANY path: /lovelace, /config, /config/integrations, /config/automation/edit/xxx, /developer-tools, etc. "
        "\n\n"
        "USER INTENT EXAMPLES: "
        "- 'What's on my screen' → snapshot + exec_js, describe ALL visible content. "
        "- 'Check this page for issues' → Look for errors, empty areas, broken layouts, missing data. "
        "- 'Click the add button' / 'Open settings' → tap action with text or selector. "
        "- 'Fill in the name field' → type action with selector/text and value. "
        "- 'Go to integrations' → navigate to /config/integrations. "
        "- 'Scroll down' → scroll action. "
        "- 'Close this popup' → tap the X button or outside the dialog. "
        "- 'What does this dialog say' → snapshot or exec_js to read dialog content. "
        "- 'Help me configure this integration' → Navigate, read forms, fill fields, click submit. "
        "\n\n"
        "WORKS WITH DashboardCard TOOL: "
        "- FrontendInspect = SEE and INTERACT with UI (view cards, click, scroll, read rendered content). "
        "- DashboardCard = MODIFY Lovelace config (create/edit/delete cards, change YAML). "
        "- Typical workflow: Use FrontendInspect to see current dashboard → Use DashboardCard to modify config → Use FrontendInspect to verify changes. "
        "- FrontendInspect shows what user SEES; DashboardCard changes what will be RENDERED. "
        "- If FrontendInspect sees a card type (for example html-pro-card) but cannot read its actual content, DO NOT ask user to paste YAML; call DashboardCard to inspect the current dashboard/card config. "
        "- If user asks 'optimize this card', use FrontendInspect to identify the visible card, then DashboardCard to read/edit the card config, then FrontendInspect again to verify the rendered result. "
        "\n\n"
        "HA FRONTEND DOM STRUCTURE (shadow DOM chain, each → means .shadowRoot): "
        "document → home-assistant → home-assistant-main → ha-drawer → .mdc-drawer-app-content → partial-panel-resolver → "
        "  Dashboard: ha-panel-lovelace → hui-root → hui-view/hui-masonry-view/hui-sections-view → hui-card/hui-* → ha-card → content "
        "  Settings: ha-panel-config → ha-config-* (e.g. ha-config-integrations, ha-config-automation) "
        "  Developer: ha-panel-developer-tools → developer-tools-* "
        "Sidebar: home-assistant-main → ha-sidebar → a.sidebar-list-item "
        "Dialogs float at home-assistant level: home-assistant → ha-more-info-dialog / ha-dialog / ha-voice-command-dialog "
        "Key exec_js patterns: "
        "`document.querySelector('home-assistant').shadowRoot.querySelector('home-assistant-main').shadowRoot` as entry. "
        "`entry.querySelector('ha-drawer').shadowRoot.querySelector('.mdc-drawer-app-content').querySelector('partial-panel-resolver')` for content root. "
        "For cards: continue into `.shadowRoot.querySelector('ha-panel-lovelace').shadowRoot.querySelector('hui-root').shadowRoot.querySelector('hui-view,hui-masonry-view,hui-sections-view')` "
        "For dialogs: `document.querySelector('home-assistant').shadowRoot.querySelector('ha-more-info-dialog,ha-dialog')` "
        "\n\n"
        "DIALOG AUTO-DETECTION: "
        "- When ANY dialog/popup opens in HA, its structure is AUTO-CAPTURED and included in snapshot results as 'active_dialogs'. "
        "- Each dialog snapshot contains: type (host component), title, body (inputs with labels/values/hints), list_items, buttons (with text/role/hints). "
        "- Use the 'hint' field in inputs/buttons to know exactly how to interact: e.g. hint='FrontendInspect type text=\"Name\" value=\"...\"' "
        "- ALWAYS check active_dialogs in snapshot results BEFORE using exec_js to interact with dialogs. "
        "- Do NOT guess dialog DOM structure with exec_js — use the structured active_dialogs data instead. "
        "\n\n"
        "CRITICAL: "
        "- snapshot returns CONTENT AREA (not sidebar). Use it to understand page structure. "
        "- exec_js can run ANY JavaScript — use it to extract text, check element states, get computed values. Full results are cached for search. "
        "- Prefer search_cache for precise targeting inside cached large results: query a selector/card/dialog/function name and inspect matching context only. "
        "- tap works on buttons, links, icons, cards — anything clickable. Use visible text or CSS selector. "
        "- Do NOT call device control tools based on entities seen in UI — user asks about the UI, not device control. "
        "\n\n"
        "ELEMENT TARGETING (Set-of-Mark): "
        "snapshot returns an 'interactables' list — every clickable/typeable element on screen with a numeric idx. "
        "Use idx for PRECISE targeting: tap idx=7 clicks element #7, type idx=12 value='hello' types into element #12. "
        "idx is the PREFERRED way to target elements — it works across shadow DOM boundaries with zero ambiguity. "
        "Fallback: selector or text still work when idx is unavailable. "
        "\n\n"
        "ACTIONS: "
        "snapshot - Get current page DOM structure, interactables list, and displayed data. Call FIRST. "
        "exec_js - Run JavaScript code (js_code required). Call SECOND for detailed text. Results are cached for search_cache. "
        "search_cache - Search cached exec_js/snapshot text. Use query to find exact card/function/dialog/label text; returns <=5000 chars of context. "
        "navigate - Go to a page by path (e.g. /config/integrations). "
        "tap - Click element. PREFER idx=N from snapshot interactables. Fallback: selector or text. "
        "type - Type into input/textarea. PREFER idx=N. Fallback: selector or text. value for content, clear=true to clear first. "
        "key - Send safe keyboard events. Use key='Enter'/'Escape'/'Tab'/'ArrowDown', repeat for continuous press. Do NOT use global shortcuts like Ctrl+A. "
        "scroll - Scroll direction (up/down/left/right), amount in px (default 300)."
    )

    parameters = vol.Schema({
        vol.Required("action"): vol.In(["snapshot", "navigate", "tap", "type", "key", "scroll", "exec_js", "search_cache"]),
        vol.Optional("idx"): int,
        vol.Optional("js_code"): str,
        vol.Optional("selector"): str,
        vol.Optional("text"): str,
        vol.Optional("path"): str,
        vol.Optional("value"): str,
        vol.Optional("query"): str,
        vol.Optional("key"): str,
        vol.Optional("repeat", default=1): int,
        vol.Optional("ctrl", default=False): bool,
        vol.Optional("shift", default=False): bool,
        vol.Optional("alt", default=False): bool,
        vol.Optional("meta", default=False): bool,
        vol.Optional("clear", default=False): bool,
        vol.Optional("direction", default="down"): vol.In(["up", "down", "left", "right"]),
        vol.Optional("amount", default=300): int,
        vol.Optional("depth", default=8): int,
    })

    _DEEP_QUERY = """
    function _collectRoots(node, out, visited) {
        if (!node || visited.has(node)) return out;
        visited.add(node);
        out.push(node);
        var els = node.querySelectorAll('*');
        for (var i = 0; i < els.length; i++) {
            var e = els[i];
            if (e.shadowRoot && !visited.has(e.shadowRoot)) {
                _collectRoots(e.shadowRoot, out, visited);
            }
            if (e.tagName === 'SLOT') {
                var assigned = e.assignedElements ? e.assignedElements({flatten:true}) : [];
                for (var j = 0; j < assigned.length; j++) {
                    if (assigned[j].shadowRoot && !visited.has(assigned[j].shadowRoot)) {
                        _collectRoots(assigned[j].shadowRoot, out, visited);
                    }
                }
            }
        }
        return out;
    }
    function deepQuery(root, sel) {
        var start = root === document ? document : (root.shadowRoot || root);
        var vis = new Set();
        var roots = _collectRoots(start, [], vis);
        if (root === document) {
            var bodyKids = document.body.children;
            for (var k = 0; k < bodyKids.length; k++) {
                if (bodyKids[k].shadowRoot) _collectRoots(bodyKids[k].shadowRoot, roots, vis);
            }
        }
        for (var i = 0; i < roots.length; i++) {
            try { var r = roots[i].querySelector(sel); if (r) return r; } catch(_) {}
        }
        var parts = sel.split(/\\s+/).filter(Boolean);
        if (parts.length > 1) {
            var ancestor = deepQuery(root, parts[0]);
            if (ancestor) {
                var sub = parts.slice(1).join(' ');
                var vis2 = new Set();
                var subRoots = _collectRoots(ancestor.shadowRoot || ancestor, [], vis2);
                for (var si = 0; si < subRoots.length; si++) {
                    try { var sr = subRoots[si].querySelector(sub); if (sr) return sr; } catch(_) {}
                }
            }
        }
        return null;
    }
    """

    _FIND_BY_TEXT = """
    function findByText(root, text) {
        var lc = text.toLowerCase();
        var best = null;
        var bestLen = Infinity;
        var vis = new Set();
        var start = root === document ? document : (root.shadowRoot || root);
        var roots = _collectRoots(start, [], vis);
        if (root === document) {
            var bodyKids = document.body.children;
            for (var bk = 0; bk < bodyKids.length; bk++) {
                if (bodyKids[bk].shadowRoot) _collectRoots(bodyKids[bk].shadowRoot, roots, vis);
            }
        }
        for (var ri = 0; ri < roots.length; ri++) {
            var els = roots[ri].querySelectorAll('*');
            for (var i = 0; i < els.length; i++) {
                var el = els[i];
                var tag = el.tagName.toLowerCase();
                if (tag === 'script' || tag === 'style' || tag === 'svg') continue;
                var al = el.getAttribute('aria-label');
                if (al) {
                    var alc = al.toLowerCase();
                    if (alc.indexOf(lc) !== -1 && al.length < bestLen) {
                        best = el; bestLen = al.length; continue;
                    }
                }
                var lb = el.getAttribute('label');
                if (lb) {
                    var lbc = lb.toLowerCase();
                    if (lbc.indexOf(lc) !== -1 && lb.length < bestLen) {
                        best = el; bestLen = lb.length; continue;
                    }
                }
                var ph = el.getAttribute('placeholder');
                if (ph) {
                    var plc = ph.toLowerCase();
                    if (plc.indexOf(lc) !== -1 && ph.length < bestLen) {
                        best = el; bestLen = ph.length; continue;
                    }
                }
                var ti = el.getAttribute('title');
                if (ti) {
                    var tlc = ti.toLowerCase();
                    if (tlc.indexOf(lc) !== -1 && ti.length < bestLen) {
                        best = el; bestLen = ti.length; continue;
                    }
                }
                var ct = (el.textContent || '').trim();
                if (ct.length > 0 && ct.length < 500) {
                    var ctlc = ct.toLowerCase();
                    if (ctlc === lc) { return el; }
                    if (ctlc.indexOf(lc) !== -1 && ct.length < bestLen) {
                        best = el; bestLen = ct.length;
                    }
                }
            }
        }
        return best;
    }
    """

    @staticmethod
    def _clean(val):
        if isinstance(val, str):
            v = val.strip()
            if not v or v in ('.', '#', '*', '>', '+', '~'):
                return None
            return v
        return val

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        args = tool_input.tool_args
        for k in ("selector", "text", "path", "js_code", "value", "query"):
            if k in args:
                args[k] = self._clean(args[k])
        action = args["action"]

        if action == "snapshot":
            _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
            depth = args.get("depth", 15)
            selector = args.get("selector")
            result = await self._snapshot_with_auto_scroll(hass, depth, selector)
            if result and not isinstance(result, dict):
                result = {"raw": str(result)}
            if result and "error" not in result:
                store_frontend_snapshot(hass, result)
                store_frontend_text_cache(hass, "snapshot", result)
            if isinstance(result, dict):
                nav = result.pop("nav", None)
                active_dialogs = _domain_data(hass).get("claw_active_dialogs")
                if active_dialogs:
                    result["active_dialogs"] = active_dialogs
                out = {"success": True, "snapshot": result}
                if nav:
                    out["nav_hint"] = f"Sidebar has {len(nav)} items. Use navigate action to switch pages."
                return out
            return {"success": "error" not in (result or {}), "snapshot": result}

        elif action == "navigate":
            path = args.get("path")
            if not path:
                return {"error": "path is required for navigate action"}
            if not path.startswith("/"):
                path = "/" + path
            exec_id = f"nav_{int(time.time()*1000)}"
            js = f"""(function(){{history.pushState(null,'',{json.dumps(path)});window.dispatchEvent(new CustomEvent('location-changed'));return {{navigated:{json.dumps(path)}}};}})()"""
            queue_frontend_exec(hass, exec_id, js)
            result = await async_wait_frontend_exec_result(hass, exec_id)
            _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
            await asyncio.sleep(1.5)
            return {"success": True, "result": result}

        elif action == "tap":
            return await self._do_tap(hass, tool_input)

        elif action == "type":
            return await self._do_type(hass, tool_input)

        elif action == "key":
            return await self._do_key(hass, tool_input)

        elif action == "scroll":
            return await self._do_scroll(hass, tool_input)

        elif action == "exec_js":
            js_code = args.get("js_code")
            if not js_code:
                return {"error": "js_code is required for exec_js action"}
            exec_id = f"exec_{int(time.time()*1000)}"
            queue_frontend_exec(hass, exec_id, js_code)
            result = await async_wait_frontend_exec_result(hass, exec_id)
            if result and "error" not in (result if isinstance(result, dict) else {}):
                store_frontend_text_cache(hass, "exec_js", result)
            if isinstance(result, str) and len(result) > 4000:
                size = 4000
                chunks = [result[i:i + size] for i in range(0, len(result), size)]
                result = {
                    "chunked": True,
                    "total_parts": len(chunks),
                    "parts": [
                        {
                            "part": idx + 1,
                            "final": idx == len(chunks) - 1,
                            "text": chunk,
                        }
                        for idx, chunk in enumerate(chunks[:5])
                    ],
                    "has_more": len(chunks) > 5,
                }
            return {"success": "error" not in (result or {}), "result": result}

        elif action == "search_cache":
            query = args.get("query")
            if not query:
                return {"error": "query is required for search_cache action"}
            return {"success": True, "result": search_frontend_text_cache(hass, query)}

        return {"error": f"Unknown action: {action}"}

    async def _do_tap(self, hass, tool_input):
        args = tool_input.tool_args
        idx = args.get("idx")
        selector = args.get("selector")
        text = args.get("text")
        if not idx and not selector and not text:
            return {"error": "tap requires idx, selector, or text to find the element"}
        exec_id = f"tap_{int(time.time()*1000)}"
        find_code = self._build_find_element_js(selector, text, idx=idx)
        js = f"""(function(){{
            {find_code}
            if (!el) return {{error:'element not found',idx:{json.dumps(idx)},selector:{json.dumps(selector)},text:{json.dumps(text)}}};

            function deepEFP(x, y) {{
                var e = document.elementFromPoint(x, y);
                if (!e) return null;
                while (e && e.shadowRoot) {{
                    var deeper = e.shadowRoot.elementFromPoint(x, y);
                    if (!deeper || deeper === e) break;
                    e = deeper;
                }}
                return e;
            }}

            function showRipple(cx, cy) {{
                var overlay = document.getElementById('__claw_tap_overlay');
                if (!overlay) {{
                    overlay = document.createElement('div');
                    overlay.id = '__claw_tap_overlay';
                    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;'
                        + 'pointer-events:none;z-index:2147483647;overflow:visible;';
                    document.documentElement.appendChild(overlay);
                }}
                var dot = document.createElement('div');
                dot.style.cssText = 'position:fixed;pointer-events:none;border-radius:50%;'
                    + 'width:28px;height:28px;left:'+(cx-14)+'px;top:'+(cy-14)+'px;'
                    + 'background:rgba(3,169,244,0.5);box-shadow:0 0 12px 4px rgba(3,169,244,0.3);'
                    + 'transition:transform .4s cubic-bezier(.2,.8,.3,1),opacity .4s ease-out;transform:scale(1);';
                overlay.appendChild(dot);
                requestAnimationFrame(function(){{requestAnimationFrame(function(){{
                    dot.style.transform='scale(2.8)';dot.style.opacity='0';
                }})}});
                setTimeout(function(){{dot.remove()}},450);
            }}

            var ISEL = 'button,a,input,select,textarea,[role=button],[role=menuitem],[role=option],[role=tab],[role=link],[role=switch],ha-icon-button,mwc-icon-button,ha-button,mwc-button,ha-list-item,mwc-list-item,ha-check-list-item,ha-clickable-list-item,ha-dropdown-item';

            function drillDown(node) {{
                if (!node) return null;
                if (node.matches && node.matches(ISEL)) return node;
                var found = deepQuery(node, ISEL);
                if (found) return found;
                return null;
            }}

            function climbUp(node) {{
                if (!node) return null;
                var walk = node;
                var visited = new Set();
                while (walk && !visited.has(walk)) {{
                    visited.add(walk);
                    if (walk.matches && walk.matches(ISEL)) return walk;
                    if (walk.matches && walk.matches('ha-card,ha-integration-card,ha-config-flow-card')) {{
                        var inner = deepQuery(walk, ISEL);
                        if (inner) return inner;
                    }}
                    var next = walk.parentElement;
                    if (!next) {{
                        var rn = walk.getRootNode && walk.getRootNode();
                        next = (rn && rn !== walk && rn.host) ? rn.host : null;
                    }}
                    walk = next;
                }}
                return null;
            }}

            var rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) {{
                var inner = deepQuery(el, ISEL);
                if (inner) {{ el = inner; rect = el.getBoundingClientRect(); }}
            }}
            var cx = rect.left + rect.width/2, cy = rect.top + rect.height/2;
            showRipple(cx, cy);

            var deep = deepEFP(cx, cy);
            var target = drillDown(el) || climbUp(deep) || drillDown(deep) || el;
            var targetTag = (target.tagName||'').toLowerCase();

            target.click();

            if (target.focus) target.focus();
            return {{tapped:true,tag:el.tagName.toLowerCase(),targetTag:targetTag,id:el.id||'',rect:{{x:Math.round(rect.x),y:Math.round(rect.y),w:Math.round(rect.width),h:Math.round(rect.height)}}}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
        out = {"success": result and "error" not in result, "result": result}
        await asyncio.sleep(0.5)
        active_dialogs = _domain_data(hass).get("claw_active_dialogs")
        if active_dialogs:
            out["opened_dialog"] = active_dialogs
            out["dialog_hint"] = "A dialog is now open. Use the 'hint' fields in body/buttons to interact. Do NOT use exec_js to find dialog elements."
        return out

    async def _do_type(self, hass, tool_input):
        args = tool_input.tool_args
        idx = args.get("idx")
        selector = args.get("selector")
        text = args.get("text")
        value = args.get("value") or ""
        clear = args.get("clear", False)
        if not idx and not selector and not text:
            return {"error": "type requires idx, selector, or text to find the input field"}
        exec_id = f"type_{int(time.time()*1000)}"
        find_code = self._build_find_element_js(selector, text, idx=idx)
        js = f"""(function(){{
            {find_code}
            if (!el) return {{error:'input not found',selector:{json.dumps(selector)},text:{json.dumps(text)}}};
            var value = {json.dumps(value)};
            var clear = {json.dumps(clear)};
            function allDeep(root, sel, out, seen) {{
                if (!root || seen.has(root)) return out;
                seen.add(root);
                try {{ root.querySelectorAll && root.querySelectorAll(sel).forEach(function(x){{out.push(x);}}); }} catch(_) {{}}
                var nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (var i = 0; i < nodes.length; i++) {{
                    var n = nodes[i];
                    if (n.shadowRoot) allDeep(n.shadowRoot, sel, out, seen);
                    if (n.tagName === 'SLOT') {{
                        var a = n.assignedElements ? n.assignedElements({{flatten:true}}) : [];
                        for (var j = 0; j < a.length; j++) allDeep(a[j], sel, out, seen);
                    }}
                }}
                return out;
            }}
            function editableScore(x) {{
                if (!x) return -1;
                var tag = (x.tagName || '').toLowerCase();
                if (tag === 'textarea') return 100;
                if (tag === 'input') return 95;
                if (x.isContentEditable || (x.getAttribute && x.getAttribute('contenteditable') != null)) return 90;
                if (x.classList && (x.classList.contains('cm-content') || x.classList.contains('monaco-editor'))) return 80;
                return -1;
            }}
            function findInput(host) {{
                var direct = editableScore(host) >= 0 ? host : null;
                var found = allDeep(host.shadowRoot || host, 'input,textarea,[contenteditable=true],[contenteditable=""],.cm-content,.monaco-editor textarea', [], new Set());
                var best = direct, bs = editableScore(direct);
                for (var i = 0; i < found.length; i++) {{
                    var s = editableScore(found[i]);
                    if (s > bs) {{ best = found[i]; bs = s; }}
                }}
                return best || host;
            }}
            function fire(x, type) {{
                x.dispatchEvent(new Event(type, {{bubbles:true, composed:true}}));
            }}
            function key(x, k) {{
                x.dispatchEvent(new KeyboardEvent('keydown', {{key:k,bubbles:true,cancelable:true,composed:true}}));
                if (k.length === 1) x.dispatchEvent(new KeyboardEvent('keypress', {{key:k,bubbles:true,cancelable:true,composed:true}}));
                x.dispatchEvent(new KeyboardEvent('keyup', {{key:k,bubbles:true,cancelable:true,composed:true}}));
            }}
            var inp = findInput(el);
            inp.focus && inp.focus();
            if (inp.select && clear) inp.select();
            var tag = (inp.tagName || '').toLowerCase();
            var isEditable = inp.isContentEditable || (inp.getAttribute && inp.getAttribute('contenteditable') != null) || (inp.classList && inp.classList.contains('cm-content'));
            if (isEditable) {{
                inp.focus();
                var sel = window.getSelection();
                if (clear && sel) {{
                    var r = document.createRange();
                    r.selectNodeContents(inp);
                    sel.removeAllRanges();
                    sel.addRange(r);
                }}
                document.execCommand('insertText', false, value);
                fire(inp, 'beforeinput');
                fire(inp, 'input');
                fire(inp, 'change');
                return {{typed:true,mode:'contenteditable',tag:tag,value:(inp.innerText||inp.textContent||'').slice(0,100)}};
            }}
            if ('value' in inp) {{
                var proto = tag === 'textarea' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                var nativeSet = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (clear) {{
                    if (nativeSet) nativeSet.call(inp, '');
                    else inp.value = '';
                    fire(inp, 'input');
                }}
                if (nativeSet) nativeSet.call(inp, value);
                else inp.value = value;
                fire(inp, 'beforeinput');
                fire(inp, 'input');
                fire(inp, 'change');
                key(inp, 'Enter');
                return {{typed:true,mode:'value',tag:tag,value:(inp.value||'').slice(0,100)}};
            }}
            return {{error:'target is not editable',tag:tag,id:inp.id||''}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
        return {"success": result and "error" not in result, "result": result}

    async def _do_key(self, hass, tool_input):
        args = tool_input.tool_args
        key = args.get("key")
        if not key:
            return {"error": "key is required for key action"}
        if args.get("ctrl") or args.get("alt") or args.get("meta"):
            return {"error": "global shortcut modifiers ctrl/alt/meta are disabled for safety; use exec_js only when explicitly necessary"}
        repeat = max(1, min(int(args.get("repeat", 1)), 50))
        idx = args.get("idx")
        selector = args.get("selector")
        text = args.get("text")
        ctrl = False
        shift = bool(args.get("shift", False))
        alt = False
        meta = False
        exec_id = f"key_{int(time.time()*1000)}"
        find_code = self._build_find_element_js(selector, text, idx=idx)
        js = f"""
        (function() {{
            {find_code}
            function keyCodeFor(k) {{
                var m = {{
                    Enter:13, Escape:27, Esc:27, Tab:9, Backspace:8, Delete:46,
                    ArrowUp:38, ArrowDown:40, ArrowLeft:37, ArrowRight:39,
                    Home:36, End:35, PageUp:33, PageDown:34, Space:32
                }};
                return m[k] || (k && k.length === 1 ? k.toUpperCase().charCodeAt(0) : 0);
            }}
            function targetEl() {{
                if (el && typeof el.focus === 'function') {{
                    el.focus();
                    return el;
                }}
                var a = document.activeElement;
                while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
                return a || document.body;
            }}
            var t = targetEl();
            var k = {json.dumps(key)};
            var opts = {{
                key: k,
                code: k.length === 1 ? 'Key' + k.toUpperCase() : k,
                keyCode: keyCodeFor(k),
                which: keyCodeFor(k),
                bubbles: true,
                cancelable: true,
                composed: true,
                ctrlKey: {json.dumps(ctrl)},
                shiftKey: {json.dumps(shift)},
                altKey: {json.dumps(alt)},
                metaKey: {json.dumps(meta)}
            }};
            for (var i = 0; i < {repeat}; i++) {{
                t.dispatchEvent(new KeyboardEvent('keydown', opts));
                if (k.length === 1) t.dispatchEvent(new KeyboardEvent('keypress', opts));
                t.dispatchEvent(new KeyboardEvent('keyup', opts));
            }}
            return {{pressed:k, repeat:{repeat}, target:t.tagName ? t.tagName.toLowerCase() : 'document', ctrl:{json.dumps(ctrl)}, shift:{json.dumps(shift)}, alt:{json.dumps(alt)}, meta:{json.dumps(meta)}}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
        return {"success": result and "error" not in result, "result": result}

    async def _snapshot_with_auto_scroll(self, hass, depth, selector):
        _SCROLL_JS = f"""(function(){{
            {self._DEEP_QUERY}
            function deepEFP(x, y) {{
                var e = document.elementFromPoint(x, y);
                if (!e) return null;
                while (e && e.shadowRoot) {{
                    var deeper = e.shadowRoot.elementFromPoint(x, y);
                    if (!deeper || deeper === e) break;
                    e = deeper;
                }}
                return e;
            }}
            function findScroller(node) {{
                var walk = node;
                var visited = new Set();
                while (walk && !visited.has(walk)) {{
                    visited.add(walk);
                    var cs = window.getComputedStyle(walk);
                    var ov = cs.overflowY || cs.overflow;
                    if ((ov === 'auto' || ov === 'scroll') && walk.scrollHeight > walk.clientHeight + 10) return walk;
                    var next = walk.parentElement;
                    if (!next) {{
                        var rn = walk.getRootNode && walk.getRootNode();
                        next = (rn && rn !== walk && rn.host) ? rn.host : null;
                    }}
                    walk = next;
                }}
                return null;
            }}
            function findDataTableScroller() {{
                var roots = _collectRoots(document, [], new Set());
                for (var ri = 0; ri < roots.length; ri++) {{
                    try {{
                        var lv = roots[ri].querySelector('lit-virtualizer[scroller], .mdc-data-table__content.scroller, .ha-scrollbar');
                        if (lv && lv.scrollHeight > lv.clientHeight + 10) return lv;
                    }} catch(_) {{}}
                }}
                return null;
            }}
            var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
            var deepEl = deepEFP(cx, cy);
            var el = findScroller(deepEl) || findDataTableScroller() || document.scrollingElement || document.documentElement;
            el.scrollBy({{left:0, top:600, behavior:'instant'}});
            return true;
        }})()"""
        best = None
        for attempt in range(4):
            exec_id = f"snap_{int(time.time()*1000)}_{attempt}"
            js = self._build_snapshot_js(depth, selector)
            queue_frontend_exec(hass, exec_id, js)
            result = await async_wait_frontend_exec_result(hass, exec_id)
            if not isinstance(result, dict):
                if best is None:
                    best = result
                break
            n = len(result.get("interactables") or [])
            if best is None or n > len((best.get("interactables") if isinstance(best, dict) else None) or []):
                best = result
            if n >= 3 or attempt >= 3:
                break
            await asyncio.sleep(0.15)
            scroll_id = f"autoscr_{int(time.time()*1000)}"
            queue_frontend_exec(hass, scroll_id, _SCROLL_JS)
            await async_wait_frontend_exec_result(hass, scroll_id)
            await asyncio.sleep(0.3)
        return best

    async def _do_scroll(self, hass, tool_input):
        args = tool_input.tool_args
        selector = args.get("selector")
        direction = args.get("direction", "down")
        amount = args.get("amount", 300)
        exec_id = f"scr_{int(time.time()*1000)}"
        dx, dy = 0, 0
        if direction == "down": dy = amount
        elif direction == "up": dy = -amount
        elif direction == "right": dx = amount
        elif direction == "left": dx = -amount
        if selector:
            find_code = self._build_find_element_js(selector, None)
        else:
            find_code = ""
        js = f"""(function(){{
            {self._DEEP_QUERY}
            {find_code}
            if (typeof el === 'undefined' || !el) {{
                function deepEFP(x, y) {{
                    var e = document.elementFromPoint(x, y);
                    if (!e) return null;
                    while (e && e.shadowRoot) {{
                        var deeper = e.shadowRoot.elementFromPoint(x, y);
                        if (!deeper || deeper === e) break;
                        e = deeper;
                    }}
                    return e;
                }}
                function findScroller(node) {{
                    var walk = node;
                    var visited = new Set();
                    while (walk && !visited.has(walk)) {{
                        visited.add(walk);
                        var cs = window.getComputedStyle(walk);
                        var ov = cs.overflowY || cs.overflow;
                        if ((ov === 'auto' || ov === 'scroll') && walk.scrollHeight > walk.clientHeight + 10) {{
                            return walk;
                        }}
                        var next = walk.parentElement;
                        if (!next) {{
                            var rn = walk.getRootNode && walk.getRootNode();
                            next = (rn && rn !== walk && rn.host) ? rn.host : null;
                        }}
                        walk = next;
                    }}
                    return null;
                }}
                function findDataTableScroller() {{
                    var roots = _collectRoots(document, [], new Set());
                    for (var ri = 0; ri < roots.length; ri++) {{
                        try {{
                            var lv = roots[ri].querySelector('lit-virtualizer[scroller], .mdc-data-table__content.scroller, .ha-scrollbar');
                            if (lv && lv.scrollHeight > lv.clientHeight + 10) return lv;
                        }} catch(_) {{}}
                    }}
                    return null;
                }}
                var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
                var deepEl = deepEFP(cx, cy);
                var el = findScroller(deepEl) || findDataTableScroller() || document.scrollingElement || document.documentElement;
            }}
            el.scrollBy({{left:{dx},top:{dy},behavior:'smooth'}});
            return {{scrolled:true,direction:{json.dumps(direction)},amount:{amount},target:el.tagName ? el.tagName.toLowerCase() : 'document'}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        return {"success": True, "result": result}

    @classmethod
    def _build_find_element_js(cls, selector: str | None, text: str | None, idx: int | None = None) -> str:
        parts = [cls._DEEP_QUERY]
        if idx is not None:
            parts.append(f"""
            var el = (function() {{
                var sel = '[data-claw-idx="{idx}"]';
                var found = deepQuery(document, sel);
                return found;
            }})();
            """)
        elif selector:
            parts.append(f"var el = deepQuery(document, {json.dumps(selector)});")
        if text:
            parts.append(cls._FIND_BY_TEXT)
            if selector or idx is not None:
                parts.append(f"if (!el) el = findByText(document, {json.dumps(text)});")
            else:
                parts.append(f"var el = findByText(document, {json.dumps(text)});")
        if idx is None and not selector and not text:
            parts.append("var el = null;")
        return "\n".join(parts)

    @staticmethod
    def _build_snapshot_js(depth: int = 8, selector: str | None = None) -> str:
        return f"""
        (function() {{
            var SKIP = {{'claw-assist-dock':1,'claw-assist-dock-style':1,'ha-sidebar':1}};
            var SKIP_TAG = {{'script':1,'style':1,'link':1,'noscript':1,'svg':1,'path':1,'img':1,'canvas':1,'video':1,'audio':1,'br':1,'hr':1}};
            var PASS_THROUGH = {{'div':1,'span':1,'slot':1,'section':1,'article':1,'main':1,'aside':1,'header':1,'footer':1,'nav':1}};
            var HA_ATTRS = ['state','entity','entity-id','card-type','type','role','aria-label','title','placeholder','value','href','icon','name','panel','part'];
            var MAX_NODES = 3000;
            var nodeCount = 0;
            var MAX_CHILDREN = 50;

            var _idxCounter = 0;
            var _interactables = [];

            var INTERACTIVE_TAGS = {{'a':1,'button':1,'details':1,'embed':1,'input':1,'menu':1,'menuitem':1,'object':1,'select':1,'textarea':1,'summary':1,'dialog':1}};
            var INTERACTIVE_ROLES = {{'button':1,'dialog':1,'treeitem':1,'alert':1,'grid':1,'progressbar':1,'radio':1,'checkbox':1,'menuitem':1,'option':1,'switch':1,'dropdown':1,'scrollbar':1,'combobox':1,'textbox':1,'tabpanel':1,'tab':1,'link':1,'slider':1,'listbox':1,'searchbox':1,'menuitemradio':1,'menuitemcheckbox':1,'tooltip':1,'tree':1,'region':1,'spinbutton':1,'columnheader':1}};
            var HA_INTERACTIVE = {{'ha-icon-button':1,'mwc-icon-button':1,'ha-button':1,'mwc-button':1,'ha-list-item':1,'mwc-list-item':1,'ha-check-list-item':1,'ha-clickable-list-item':1,'ha-dropdown-item':1,'ha-switch':1,'ha-slider':1,'ha-select':1,'ha-textfield':1,'ha-radio':1,'ha-checkbox':1,'ha-fab':1,'ha-chip':1,'ha-assist-chip':1,'ha-icon-next':1,'ha-icon-prev':1,'ha-button-menu':1,'ha-label':1,'md-filled-button':1,'md-outlined-button':1,'md-text-button':1,'md-icon-button':1,'md-filled-tonal-button':1,'md-fab':1,'md-checkbox':1,'md-radio':1,'md-switch':1,'md-slider':1,'md-filled-text-field':1,'md-outlined-text-field':1,'md-filled-select':1,'md-outlined-select':1,'md-list-item':1,'md-menu-item':1,'md-icon-button-toggle':1}};
            var HA_SHADOW_HOSTS = (function() {{
                var tags = [
                    'home-assistant','home-assistant-main','ha-drawer','partial-panel-resolver',
                    'ha-panel-lovelace','ha-panel-config','ha-panel-developer-tools',
                    'hui-root','hui-view','hui-masonry-view','hui-sections-view',
                    'hass-tabs-subpage','hass-tabs-subpage-data-table','hass-subpage',
                    'hass-loading-screen','hass-error-screen',
                    'ha-config-dashboard','ha-config-entities','ha-config-section-entities',
                    'ha-config-devices','ha-config-device-page','ha-config-areas',
                    'ha-config-integrations','ha-config-automation','ha-config-script',
                    'ha-config-scene','ha-config-helpers','ha-config-flow',
                    'ha-config-voice-assistants','ha-config-voice-assistants-expose',
                    'ha-config-entry-page','ha-config-logs','ha-config-info',
                    'ha-data-table','ha-data-table-labels','ha-top-app-bar-fixed','ha-menu-button',
                    'ha-input-search','search-input','ha-search-input',
                    'ha-filter-entities','ha-filter-devices','ha-filter-integrations',
                    'ha-filter-floor-areas','ha-filter-labels','ha-filter-categories',
                    'ha-filter-domains','ha-filter-states','ha-filter-voice-assistants',
                    'ha-more-info-dialog','ha-dialog','ha-voice-command-dialog','dialog',
                    'ha-card','hui-card','ha-form','ha-settings-row','ha-expansion-panel',
                    'ha-sidebar','ha-assist-chat','ha-markdown','ha-markdown-element',
                    'ha-more-info-info','ha-more-info-history','more-info-content',
                    'ha-state-control-toggle','ha-entity-toggle','ha-state-icon',
                    'ha-assist-chip','ha-dropdown','ha-checkbox','ha-radio',
                    'ha-integration-overflow-menu','ha-sub-menu',
                    'lit-virtualizer','ha-alert','ha-tooltip','wa-popup',
                    'ha-label','ha-icon','ha-svg-icon','ha-icon-button',
                    'ha-button','ha-button-menu','mwc-button','md-filled-button',
                    'md-outlined-button','md-text-button','md-icon-button',
                    'ha-dropdown-item','ha-check-list-item','ha-chip-set',
                    'ha-list','ha-list-item','mwc-list','mwc-list-item',
                    'ha-switch','ha-slider','ha-select','ha-textfield',
                    'ha-combo-box','ha-area-picker','ha-entity-picker','ha-device-picker',
                    'ha-selector','ha-selector-entity','ha-selector-device','ha-selector-area',
                    'ha-selector-boolean','ha-selector-select','ha-selector-text',
                    'ha-tab','ha-tabs','ha-bar','ha-circular-progress',
                    'ha-fab','ha-chip','ha-icon-next','ha-icon-prev',
                    'ha-date-input','ha-time-input','ha-duration-input',
                    'ha-dialog-header','ha-dialog-footer',
                    'md-dialog','md-filled-text-field','md-outlined-text-field',
                    'md-filled-select','md-outlined-select','md-list-item','md-menu-item',
                    'md-checkbox','md-radio','md-switch','md-slider','md-fab',
                    'md-filled-tonal-button','md-icon-button-toggle'
                ];
                var m = {{}};
                for (var ti = 0; ti < tags.length; ti++) m[tags[ti]] = 1;
                return m;
            }})();

            var HIGHLIGHT_COLORS = ['#FF0000','#00FF00','#0000FF','#FFA500','#800080','#008080','#FF69B4','#4B0082','#FF4500','#2E8B57','#DC143C','#4682B4'];
            var HIGHLIGHT_CONTAINER_ID = '__claw_highlight_container';

            function isInteractive(el) {{
                if (!el || el.nodeType !== 1) return false;
                var tag = el.tagName.toLowerCase();
                if (INTERACTIVE_TAGS[tag]) return true;
                if (HA_INTERACTIVE[tag]) return true;
                var role = el.getAttribute('role');
                var ariaRole = el.getAttribute('aria-role');
                if (role && INTERACTIVE_ROLES[role]) return true;
                if (ariaRole && INTERACTIVE_ROLES[ariaRole]) return true;
                var tabIdx = el.getAttribute('tabindex');
                if (tabIdx !== null && tabIdx !== '-1') return true;
                if (el.hasAttribute('aria-expanded') || el.hasAttribute('aria-pressed') || el.hasAttribute('aria-selected') || el.hasAttribute('aria-checked')) return true;
                if (el.onclick !== null || el.getAttribute('onclick') !== null || el.hasAttribute('ng-click') || el.hasAttribute('@click') || el.hasAttribute('v-on:click')) return true;
                if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) return true;
                if (el.draggable || el.getAttribute('draggable') === 'true') return true;
                if (el.classList && (el.classList.contains('dropdown-toggle') || el.getAttribute('data-toggle') === 'dropdown' || el.getAttribute('aria-haspopup') === 'true')) return true;
                if (el.classList && el.classList.contains('clickable')) return true;
                if (el.classList && (el.classList.contains('mdc-data-table__row') || el.classList.contains('mdc-data-table__header-cell'))) {{
                    if (el.classList.contains('clickable') || el.getAttribute('role') === 'columnheader') return true;
                }}
                var part = el.getAttribute && el.getAttribute('part');
                if (part && part.split(/\\s+/).indexOf('base') !== -1) return true;
                try {{
                    var listeners = window.getEventListeners ? window.getEventListeners(el) : null;
                    if (listeners && (listeners.click || listeners.mousedown || listeners.touchstart)) return true;
                }} catch(_) {{}}
                return false;
            }}

            function _ancestorContains(top, el) {{
                var cur = top;
                var hops = 0;
                while (cur && hops < 200) {{
                    if (cur === el) return true;
                    if (cur.contains && cur.contains(el)) return true;
                    var p = cur.parentNode;
                    if (!p) break;
                    if (p.nodeType === 11 && p.host) {{
                        cur = p.host;
                    }} else {{
                        cur = p;
                    }}
                    hops++;
                }}
                return false;
            }}

            function isTopElement(el) {{
                var rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return false;
                var inVP = rect.left < window.innerWidth && rect.right > 0 && rect.top < window.innerHeight && rect.bottom > 0;
                if (!inVP) return true;
                var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
                try {{
                    var topEl = document.elementFromPoint(cx, cy);
                    if (!topEl) return false;
                    while (topEl && topEl.shadowRoot) {{
                        var deeper = topEl.shadowRoot.elementFromPoint(cx, cy);
                        if (!deeper || deeper === topEl) break;
                        topEl = deeper;
                    }}
                    if (_ancestorContains(topEl, el)) return true;
                    if (el.contains && el.contains(topEl)) return true;
                    return false;
                }} catch(_) {{ return true; }}
            }}

            function highlightElement(el, idx) {{
                try {{
                    var container = document.getElementById(HIGHLIGHT_CONTAINER_ID);
                    if (!container) {{
                        container = document.createElement('div');
                        container.id = HIGHLIGHT_CONTAINER_ID;
                        container.style.cssText = 'position:fixed;pointer-events:none;top:0;left:0;width:100%;height:100%;z-index:2147483647;';
                        document.body.appendChild(container);
                    }}
                    var rect = el.getBoundingClientRect();
                    if (!rect || rect.width === 0) return;
                    var color = HIGHLIGHT_COLORS[idx % HIGHLIGHT_COLORS.length];
                    var ov = document.createElement('div');
                    ov.style.cssText = 'position:fixed;border:2px solid ' + color + ';background:' + color + '1A;pointer-events:none;box-sizing:border-box;'
                        + 'top:' + rect.top + 'px;left:' + rect.left + 'px;width:' + rect.width + 'px;height:' + rect.height + 'px;';
                    var lb = document.createElement('div');
                    var fs = Math.min(12, Math.max(8, rect.height / 2));
                    lb.style.cssText = 'position:fixed;background:' + color + ';color:#fff;padding:1px 4px;border-radius:4px;font-size:' + fs + 'px;'
                        + 'top:' + (rect.top + 2) + 'px;left:' + (rect.left + rect.width - 22) + 'px;';
                    lb.textContent = idx;
                    container.appendChild(ov);
                    container.appendChild(lb);
                }} catch(_) {{}}
            }}

            function removeHighlights() {{
                if (window.__clawHighlightTimer) {{
                    clearTimeout(window.__clawHighlightTimer);
                    window.__clawHighlightTimer = null;
                }}
                var c = document.getElementById(HIGHLIGHT_CONTAINER_ID);
                if (c) c.remove();
            }}
            removeHighlights();

            function scheduleHighlightCleanup() {{
                if (window.__clawHighlightTimer) clearTimeout(window.__clawHighlightTimer);
                window.__clawHighlightTimer = setTimeout(function() {{
                    var c = document.getElementById(HIGHLIGHT_CONTAINER_ID);
                    if (c) c.remove();
                    window.__clawHighlightTimer = null;
                }}, 8000);
            }}

            function elText(el) {{
                var al = el.getAttribute('aria-label');
                if (al) return al.slice(0,80);
                var lb = el.getAttribute('label');
                if (lb) return lb.slice(0,80);
                if (el.label) return String(el.label).slice(0,80);
                var ti = el.getAttribute('title');
                if (ti) return ti.slice(0,80);
                try {{
                    var rn = el.getRootNode && el.getRootNode();
                    var host = rn && rn.host;
                    if (host) {{
                        var hal = host.getAttribute && host.getAttribute('aria-label');
                        if (hal) return hal.slice(0,80);
                        var hlb = host.getAttribute && host.getAttribute('label');
                        if (hlb) return hlb.slice(0,80);
                        if (host.label) return String(host.label).slice(0,80);
                        var hti = host.getAttribute && host.getAttribute('title');
                        if (hti) return hti.slice(0,80);
                    }}
                }} catch(_) {{}}
                var t = (el.innerText || el.textContent || '').trim();
                if (t && t.length < 200) return t.slice(0,80);
                return '';
            }}

            function markInteractive(el, o) {{
                if (!isInteractive(el)) return;
                var rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;
                if (!isTopElement(el)) return;
                _idxCounter++;
                var idx = _idxCounter;
                el.setAttribute('data-claw-idx', String(idx));
                o.idx = idx;
                highlightElement(el, idx);
                var tag = el.tagName.toLowerCase();
                var entry = {{idx: idx, tag: tag}};
                var part = el.getAttribute('part');
                if (part) entry.part = part;
                try {{
                    var rn = el.getRootNode && el.getRootNode();
                    var host = rn && rn.host;
                    if (host && host.tagName) entry.host = host.tagName.toLowerCase();
                }} catch(_) {{}}
                var text = elText(el);
                if (text) entry.text = text;
                var al = el.getAttribute('aria-label');
                if (al) entry.aria_label = al.slice(0,80);
                var ti = el.getAttribute('title');
                if (ti) entry.title = ti.slice(0,80);
                var role = el.getAttribute('role');
                if (role) entry.role = role;
                var ph = el.getAttribute('placeholder');
                if (ph) entry.placeholder = ph.slice(0,60);
                var tp = el.getAttribute('type');
                if (tp) entry.type = tp;
                var ic = el.getAttribute('icon');
                if (ic) entry.icon = ic;
                var hr = el.getAttribute('href');
                if (hr) entry.href = hr.slice(0,100);
                var val = el.value;
                if (val !== undefined && val !== null && val !== '' && (tag === 'input' || tag === 'textarea' || tag === 'select'))
                    entry.value = String(val).slice(0,60);
                if (el.disabled) entry.disabled = true;
                _interactables.push(entry);
            }}

            function snap(el, d) {{
                if (!el) return null;
                var isHaHost = el.nodeType === 1 && HA_SHADOW_HOSTS[el.tagName.toLowerCase()];
                if (el.nodeType === 1 && !isHaHost && (d <= 0 || nodeCount >= MAX_NODES)) {{
                    var ft = (el.innerText || '').trim();
                    if (ft && ft.length > 0) {{
                        nodeCount++;
                        var leaf = {{tag: el.tagName.toLowerCase(), text: ft.slice(0,150)}};
                        markInteractive(el, leaf);
                        return leaf;
                    }}
                    return null;
                }}
                if (el.nodeType === 3) {{
                    var t = (el.textContent || '').trim();
                    if (!t) return null;
                    nodeCount++;
                    return {{tag:'#text', text:t.slice(0,120)}};
                }}
                if (el.nodeType !== 1) return null;
                var tag = el.tagName.toLowerCase();
                if (SKIP_TAG[tag]) return null;
                if (el.id && SKIP[el.id]) return null;
                if (tag === 'slot') {{
                    var assigned = [];
                    try {{
                        assigned = el.assignedElements ? el.assignedElements({{flatten:true}}) : [];
                    }} catch(_) {{}}
                    var slotChildren = [];
                    for (var sai = 0; sai < Math.min(assigned.length, MAX_CHILDREN) && nodeCount < MAX_NODES; sai++) {{
                        var sac = snap(assigned[sai], Math.max(d, 6));
                        if (sac) slotChildren.push(sac);
                    }}
                    if (slotChildren.length === 1) return slotChildren[0];
                    if (slotChildren.length) return {{tag:'slot', name: el.name || undefined, children: slotChildren}};
                }}
                nodeCount++;
                var effD = isHaHost ? Math.max(d, 3) : d;
                var o = {{tag: tag}};
                if (el.id) o.id = el.id;
                if (el.className && typeof el.className === 'string') {{
                    var cls = el.className.trim();
                    if (cls && !PASS_THROUGH[tag]) o.class = cls.slice(0, 80);
                }}
                var attrs = {{}};
                for (var ai = 0; ai < HA_ATTRS.length; ai++) {{
                    var v = el.getAttribute(HA_ATTRS[ai]);
                    if (v) attrs[HA_ATTRS[ai]] = v.slice(0, 100);
                }}
                if (Object.keys(attrs).length) o.attrs = attrs;
                markInteractive(el, o);
                try {{
                    var eid = el.getAttribute && (el.getAttribute('entity-id') || el.getAttribute('entity'));
                    if (!eid && el.stateObj) eid = el.stateObj.entity_id;
                    if (eid) {{
                        var ha = document.querySelector('home-assistant');
                        var hObj = ha && ha.hass;
                        if (hObj && hObj.states && hObj.states[eid]) {{
                            var st = hObj.states[eid];
                            o.entity = eid;
                            o.state = st.state;
                            var u = st.attributes && st.attributes.unit_of_measurement;
                            if (u) o.unit = u;
                            var fn = st.attributes && st.attributes.friendly_name;
                            if (fn) o.name = fn;
                        }}
                    }}
                }} catch(_) {{}}
                if (tag === 'ha-data-table') {{
                    try {{
                        var dtData = el._filteredData || el.data || [];
                        var dtCols = el.columns || {{}};
                        var children = [];
                        var sr2 = el.shadowRoot;
                        if (sr2) {{
                            var sn2 = sr2.childNodes;
                            for (var si2 = 0; si2 < Math.min(sn2.length, MAX_CHILDREN) && nodeCount < MAX_NODES; si2++) {{
                                var sc = snap(sn2[si2], 6);
                                if (sc) children.push(sc);
                            }}
                        }}
                        var ln2 = el.childNodes;
                        for (var lj = 0; lj < Math.min(ln2.length, MAX_CHILDREN) && nodeCount < MAX_NODES; lj++) {{
                            var lc = snap(ln2[lj], 6);
                            if (lc) children.push(lc);
                        }}
                        var mainCol = null;
                        for (var ck in dtCols) {{ if (dtCols[ck].main) {{ mainCol = ck; break; }} }}
                        var dataRows = [];
                        var maxRows = Math.min(dtData.length, 25);
                        for (var ri = 0; ri < maxRows && nodeCount < MAX_NODES; ri++) {{
                            var row = dtData[ri];
                            if (!row) continue;
                            var rowId = row.entity_id || row.id || row[el.id] || '';
                            var rowName = row.name || (mainCol ? row[mainCol] : '') || '';
                            var rowObj = {{tag: 'tr', text: rowName ? rowName.slice(0,80) : undefined}};
                            if (rowId) rowObj.id = String(rowId).slice(0,80);
                            if (row.entity_id) rowObj.entity_id = row.entity_id;
                            if (row.area) rowObj.area = String(row.area).slice(0,40);
                            if (row.device) rowObj.device = String(row.device).slice(0,40);
                            if (row.domain) rowObj.domain = String(row.domain).slice(0,40);
                            if (row.status) rowObj.status = String(row.status).slice(0,20);
                            if (row.localized_platform) rowObj.platform = String(row.localized_platform).slice(0,30);
                            nodeCount++;
                            dataRows.push(rowObj);
                        }}
                        o.total_rows = dtData.length;
                        o.shown_rows = dataRows.length;
                        if (dtData.length > dataRows.length) {{
                            o.has_more_rows = true;
                            o.next_action = 'scroll down then snapshot again';
                        }}
                        o.children = children;
                        o.data = dataRows.length ? dataRows : undefined;
                    }} catch(_dt) {{
                        o.text = 'data-table (error reading data)';
                    }}
                }}
                else if (effD > 1 && nodeCount < MAX_NODES) {{
                    var children = [];
                    var sr = el.shadowRoot;
                    if (sr) {{
                        var sn = sr.childNodes;
                        for (var i = 0; i < Math.min(sn.length, MAX_CHILDREN) && nodeCount < MAX_NODES; i++) {{
                            var c = snap(sn[i], effD - 1);
                            if (c) children.push(c);
                        }}
                    }}
                    var ln = el.childNodes;
                    for (var j = 0; j < Math.min(ln.length, MAX_CHILDREN) && nodeCount < MAX_NODES; j++) {{
                        var c2 = snap(ln[j], effD - 1);
                        if (c2) children.push(c2);
                    }}
                    if (children.length) {{
                        if (PASS_THROUGH[tag] && !o.id && !o.attrs && !o.entity && !o.idx && children.length === 1) {{
                            return children[0];
                        }}
                        o.children = children;
                    }}
                    else {{
                        var vt = (el.innerText || '').trim();
                        if (vt && vt.length < 200) o.text = vt;
                    }}
                }} else if (effD <= 1) {{
                    var vt2 = (el.innerText || '').trim();
                    if (vt2 && vt2.length < 200) o.text = vt2;
                }}
                if (!o.children && !o.text && !o.id && !o.attrs && !o.entity && !o.state && !o.idx && PASS_THROUGH[tag]) return null;
                return o;
            }}
            var sel = {json.dumps(selector) if selector else 'null'};
            var target;
            if (sel) {{
                target = document.querySelector(sel);
                if (!target) {{
                    var ha = document.querySelector('home-assistant');
                    if (ha && ha.shadowRoot) target = ha.shadowRoot.querySelector(sel);
                }}
            }}
            if (!target) {{
                function dq(root, sel) {{
                    if (!root) return null;
                    var el = root.querySelector ? root.querySelector(sel) : null;
                    if (el) return el;
                    var sr = root.shadowRoot;
                    if (sr) {{ el = dq(sr, sel); if (el) return el; }}
                    var ch = root.querySelectorAll ? root.querySelectorAll('*') : [];
                    for (var ci = 0; ci < Math.min(ch.length, 50); ci++) {{
                        if (ch[ci].shadowRoot) {{ el = dq(ch[ci].shadowRoot, sel); if (el) return el; }}
                    }}
                    return null;
                }}
                var ha = document.querySelector('home-assistant');
                var main = ha && ha.shadowRoot ? ha.shadowRoot.querySelector('home-assistant-main') : null;
                var view = dq(main, 'hui-view,hui-sections-view,hui-masonry-view,hui-panel-view,hui-sidebar-view');
                if (!view) view = dq(main, 'partial-panel-resolver');
                target = view || main || document.body;
            }}
            var navItems = [];
            try {{
                var ha2 = document.querySelector('home-assistant');
                var main2 = ha2 && ha2.shadowRoot ? ha2.shadowRoot.querySelector('home-assistant-main') : null;
                var mainSR2 = main2 && main2.shadowRoot;
                var sidebar = mainSR2 ? mainSR2.querySelector('ha-sidebar') : null;
                var sidebarSR = sidebar && sidebar.shadowRoot;
                if (sidebarSR) {{
                    var items = sidebarSR.querySelectorAll('a.sidebar-list-item');
                    for (var ni = 0; ni < items.length; ni++) {{
                        var lbl = items[ni].getAttribute('data-panel') || items[ni].getAttribute('aria-label') || (items[ni].textContent||'').trim();
                        var hr = items[ni].getAttribute('href') || '';
                        if (lbl) navItems.push({{label: lbl.slice(0,40), href: hr}});
                    }}
                }}
            }} catch(_) {{}}
            var dialogs = [];
            try {{
                var DLG_TAGS = {{'ha-dialog':1,'ha-more-info-dialog':1,'ha-voice-command-dialog':1,'ha-config-flow-card':1,'ha-dialog-date-picker':1}};
                function isDlgVisible(el) {{
                    try {{
                        if (el.open) return true;
                        if (el.hasAttribute && el.hasAttribute('open')) return true;
                        var r = el.getBoundingClientRect();
                        if (r.width > 50 && r.height > 50) return true;
                        var sr = el.shadowRoot;
                        if (sr) {{
                            var inner = sr.querySelector('dialog[open],md-dialog[open],[role="dialog"],.mdc-dialog--open');
                            if (inner) {{
                                var ir = inner.getBoundingClientRect();
                                if (ir.width > 50 && ir.height > 50) return true;
                            }}
                        }}
                    }} catch(_) {{}}
                    return false;
                }}
                function collectDialogs(root) {{
                    if (!root) return;
                    var sr = root.shadowRoot || root;
                    var allEls = sr.querySelectorAll ? sr.querySelectorAll('*') : [];
                    for (var i = 0; i < allEls.length; i++) {{
                        var el = allEls[i];
                        var tn = el.tagName.toLowerCase();
                        if (DLG_TAGS[tn] && isDlgVisible(el)) {{
                            nodeCount = 0;
                            var ds = snap(el, 20);
                            if (ds) {{
                                ds._dialog_host = tn;
                                dialogs.push(ds);
                            }}
                        }}
                    }}
                }}
                var haRoot = document.querySelector('home-assistant');
                if (haRoot && haRoot.shadowRoot) collectDialogs(haRoot.shadowRoot);
                collectDialogs(document.body);
            }} catch(_) {{}}
            var result = {{
                url: location.pathname + location.search,
                title: document.title,
                viewport: {{w: window.innerWidth, h: window.innerHeight}},
                nav: navItems.length ? navItems : undefined,
                content: snap(target, {depth}),
                interactables: _interactables.length ? _interactables : undefined,
                dialogs: dialogs.length ? dialogs : undefined
            }};
            scheduleHighlightCleanup();
            return result;
        }})()
        """
