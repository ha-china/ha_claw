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
    q = _domain_data(hass).setdefault(_FRONTEND_EXEC_QUEUE, [])
    q.append({"id": exec_id, "code": js_code})
    hass.bus.async_fire("claw_frontend_exec", {"id": exec_id, "code": js_code})


def store_frontend_exec_result(hass: HomeAssistant, exec_id: str, result: Any) -> None:
    results = _domain_data(hass).setdefault(_FRONTEND_EXEC_RESULTS, {})
    results[exec_id] = {"ts": time.time(), "result": result}


def pop_frontend_exec_result(hass: HomeAssistant, exec_id: str, timeout: float = 10.0) -> Any:
    results = _domain_data(hass).setdefault(_FRONTEND_EXEC_RESULTS, {})
    if exec_id in results:
        return results.pop(exec_id)["result"]
    return None


async def async_wait_frontend_exec_result(hass: HomeAssistant, exec_id: str, timeout: float = 15.0) -> Any:
    results = _domain_data(hass).setdefault(_FRONTEND_EXEC_RESULTS, {})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if exec_id in results:
            return results.pop(exec_id)["result"]
        await asyncio.sleep(0.3)
    return {"error": "timeout", "message": f"Frontend did not respond within {timeout}s"}


class FrontendInspectTool(llm.Tool):
    name = "FrontendInspect"
    description = (
        "Inspect and interact with the Home Assistant frontend like a real user. "
        "Actions: "
        "snapshot - Read current page DOM tree (URL, visible elements, structure). "
        "navigate - Smooth SPA navigate to a HA page (path e.g. /config, /lovelace/0). "
        "tap - Click/tap an element by CSS selector or visible text. Simulates pointer events. "
        "type - Type text into an input/textarea. Set selector or text to find the field, value for text to type. Set clear=true to clear first. "
        "scroll - Scroll an element or the page. direction=up/down/left/right, amount in px (default 300). "
        "exec_js - Run arbitrary JavaScript and return result."
    )

    parameters = vol.Schema({
        vol.Required("action"): vol.In(["snapshot", "navigate", "tap", "type", "scroll", "exec_js"]),
        vol.Optional("js_code"): str,
        vol.Optional("selector"): str,
        vol.Optional("text"): str,
        vol.Optional("path"): str,
        vol.Optional("value"): str,
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
            var r = roots[i].querySelector(sel);
            if (r) return r;
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
        for k in ("selector", "text", "path", "js_code", "value"):
            if k in args:
                args[k] = self._clean(args[k])
        action = args["action"]

        if action == "snapshot":
            snapshot = get_frontend_snapshot(hass)
            if snapshot:
                return {"success": True, "snapshot": snapshot}
            exec_id = f"snap_{int(time.time()*1000)}"
            depth = args.get("depth", 8)
            selector = args.get("selector")
            js = self._build_snapshot_js(depth, selector)
            queue_frontend_exec(hass, exec_id, js)
            result = await async_wait_frontend_exec_result(hass, exec_id)
            if result and not isinstance(result, dict):
                result = {"raw": str(result)}
            if result and "error" not in result:
                store_frontend_snapshot(hass, result)
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

        elif action == "scroll":
            return await self._do_scroll(hass, tool_input)

        elif action == "exec_js":
            js_code = args.get("js_code")
            if not js_code:
                return {"error": "js_code is required for exec_js action"}
            exec_id = f"exec_{int(time.time()*1000)}"
            queue_frontend_exec(hass, exec_id, js_code)
            result = await async_wait_frontend_exec_result(hass, exec_id)
            return {"success": "error" not in (result or {}), "result": result}

        return {"error": f"Unknown action: {action}"}

    async def _do_tap(self, hass, tool_input):
        args = tool_input.tool_args
        selector = args.get("selector")
        text = args.get("text")
        if not selector and not text:
            return {"error": "tap requires selector or text to find the element"}
        exec_id = f"tap_{int(time.time()*1000)}"
        find_code = self._build_find_element_js(selector, text)
        js = f"""(function(){{
            {find_code}
            if (!el) return {{error:'element not found',selector:{json.dumps(selector)},text:{json.dumps(text)}}};

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
        return {"success": result and "error" not in result, "result": result}

    async def _do_type(self, hass, tool_input):
        args = tool_input.tool_args
        selector = args.get("selector")
        text = args.get("text")
        value = args.get("value") or ""
        clear = args.get("clear", False)
        if not selector and not text:
            return {"error": "type requires selector or text to find the input field"}
        exec_id = f"type_{int(time.time()*1000)}"
        find_code = self._build_find_element_js(selector, text)
        js = f"""(function(){{
            {find_code}
            if (!el) return {{error:'input not found',selector:{json.dumps(selector)},text:{json.dumps(text)}}};
            var inp = el;
            if (el.tagName && !['INPUT','TEXTAREA'].includes(el.tagName)) {{
                inp = deepQuery(el, 'input,textarea') || deepQuery(el, '[contenteditable=true],[contenteditable=""]') || el;
            }}
            inp.focus && inp.focus();
            var isEditable = inp.isContentEditable || (inp.getAttribute && inp.getAttribute('contenteditable') != null);
            if (isEditable) {{
                if ({json.dumps(clear)}) {{
                    inp.textContent = '';
                }}
                inp.focus();
                var sel = window.getSelection();
                if (sel) {{ sel.selectAllChildren(inp); sel.collapseToEnd(); }}
                document.execCommand('insertText', false, {json.dumps(value)});
                inp.dispatchEvent(new Event('input',{{bubbles:true,composed:true}}));
                return {{typed:true,tag:inp.tagName.toLowerCase(),contenteditable:true,value:(inp.textContent||'').slice(0,100)}};
            }}
            if ({json.dumps(clear)}) {{
                inp.value = '';
                inp.dispatchEvent(new Event('input',{{bubbles:true,composed:true}}));
            }}
            var nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')?.set
                || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value')?.set;
            if (nativeSet) {{
                nativeSet.call(inp, {json.dumps(value)});
            }} else {{
                inp.value = {json.dumps(value)};
            }}
            inp.dispatchEvent(new Event('input',{{bubbles:true,composed:true}}));
            inp.dispatchEvent(new Event('change',{{bubbles:true,composed:true}}));
            return {{typed:true,tag:inp.tagName.toLowerCase(),value:inp.value.slice(0,100)}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        _domain_data(hass).pop(_FRONTEND_SNAPSHOT_KEY, None)
        return {"success": result and "error" not in result, "result": result}

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
                var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
                var deepEl = deepEFP(cx, cy);
                var el = findScroller(deepEl) || document.scrollingElement || document.documentElement;
            }}
            el.scrollBy({{left:{dx},top:{dy},behavior:'smooth'}});
            return {{scrolled:true,direction:{json.dumps(direction)},amount:{amount},target:el.tagName ? el.tagName.toLowerCase() : 'document'}};
        }})()"""
        queue_frontend_exec(hass, exec_id, js)
        result = await async_wait_frontend_exec_result(hass, exec_id)
        return {"success": True, "result": result}

    @classmethod
    def _build_find_element_js(cls, selector: str | None, text: str | None) -> str:
        parts = [cls._DEEP_QUERY]
        if selector:
            parts.append(f"var el = deepQuery(document, {json.dumps(selector)});")
        if text:
            parts.append(cls._FIND_BY_TEXT)
            if selector:
                parts.append(f"if (!el) el = findByText(document, {json.dumps(text)});")
            else:
                parts.append(f"var el = findByText(document, {json.dumps(text)});")
        if not selector and not text:
            parts.append("var el = null;")
        return "\n".join(parts)

    @staticmethod
    def _build_snapshot_js(depth: int = 8, selector: str | None = None) -> str:
        return f"""
        (function() {{
            var SKIP = {{'claw-assist-dock':1,'claw-assist-dock-style':1}};
            var SKIP_TAG = {{'ha-voice-command-dialog':1,'script':1,'style':1,'link':1,'noscript':1}};
            var HA_ATTRS = ['state','entity','entity-id','card-type','type','role','aria-label','title','placeholder','value','href','icon','name','panel'];
            function snap(el, d) {{
                if (!el || d <= 0) return null;
                if (el.nodeType === 3) {{
                    var t = (el.textContent || '').trim();
                    return t ? {{tag:'#text', text:t.slice(0,200)}} : null;
                }}
                if (el.nodeType !== 1) return null;
                var tag = el.tagName.toLowerCase();
                if (SKIP_TAG[tag]) return null;
                if (el.id && SKIP[el.id]) return null;
                var o = {{tag: tag}};
                if (el.id) o.id = el.id;
                if (el.className && typeof el.className === 'string') {{
                    var cls = el.className.trim();
                    if (cls) o.class = cls.slice(0, 120);
                }}
                var attrs = {{}};
                for (var ai = 0; ai < HA_ATTRS.length; ai++) {{
                    var v = el.getAttribute(HA_ATTRS[ai]);
                    if (v) attrs[HA_ATTRS[ai]] = v.slice(0, 150);
                }}
                if (Object.keys(attrs).length) o.attrs = attrs;
                if (d > 1) {{
                    var children = [];
                    var sr = el.shadowRoot;
                    if (sr) {{
                        var sn = sr.childNodes;
                        for (var i = 0; i < Math.min(sn.length, 80); i++) {{
                            var c = snap(sn[i], d - 1);
                            if (c) children.push(c);
                        }}
                    }}
                    var ln = el.childNodes;
                    for (var j = 0; j < Math.min(ln.length, 80); j++) {{
                        var c2 = snap(ln[j], d - 1);
                        if (c2) children.push(c2);
                    }}
                    if (children.length) o.children = children;
                }}
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
                var ha = document.querySelector('home-assistant');
                var main = ha && ha.shadowRoot ? ha.shadowRoot.querySelector('home-assistant-main') : null;
                target = main || document.body;
            }}
            var result = {{
                url: location.pathname + location.search,
                title: document.title,
                viewport: {{w: window.innerWidth, h: window.innerHeight}},
                panel: snap(target, {depth})
            }};
            return result;
        }})()
        """
