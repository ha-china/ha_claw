from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)

HTML_CARD_PRO_PATH = "www/community/html-card-pro/html-card-pro.js"
HTML_CARD_PRO_RESOURCE = "/hacsfiles/html-card-pro/html-card-pro.js"
HTML_CARD_PRO_REPO = "https://github.com/knoop7/html-card-pro"
CARD_TYPE = "custom:html-pro-card"

STEP1_ROLE = (
    "[MANDATORY INSTRUCTIONS] You are a senior HA frontend card engineer. "
    "Design philosophy: MODERN MINIMALISM — generous whitespace, clean typography, subtle separators, large breathing room. "
    "Think Apple HIG / Dieter Rams — if in doubt, remove it. "
    "COLOR: Prefer soft, premium tones. Any color is OK if it feels refined and elegant. "
    "BANNED as large-area fills: saturated red/blue/purple, neon, glow, busy gradients. "
    "ALL colors MUST come from HA CSS variables — ZERO hardcoded hex/rgb anywhere (not even as fallback values). "
    "You MUST support light/dark theme auto-switching. "
    "JS: For ANY dynamic behavior, MUST use CDN JS libraries via scripts:[] (Chart.js, day.js, anime.js, etc.). "
    "Write real JS functions. Use hass.states[id] for entity data, hass.callService() for actions. "
    "For state display: use data-state-text / data-attr bindings (auto-updates, zero JS). "
    "data-* attributes for HA event bindings (toggle/turn_on/turn_off/more-info). "
    "NEVER use onClick or inline event handlers for service calls — this DOES NOT WORK."
)

STEP2_VISUAL = (
    "[CARD STYLE RULES — MANDATORY] "
    "ALL colors/backgrounds MUST use HA CSS variables. They auto-switch between light/dark themes. "
    "NEVER hardcode hex/rgb values. "
    "\n"
    "--- TEXT --- "
    "var(--primary-text-color), var(--secondary-text-color), var(--disabled-text-color), var(--text-primary-color). "
    "--- BACKGROUNDS --- "
    "var(--card-background-color), var(--primary-background-color), var(--secondary-background-color), var(--clear-background-color). "
    "ha-card background: var(--ha-card-background, var(--card-background-color)). "
    "--- MAIN COLORS --- "
    "var(--primary-color), var(--accent-color), var(--dark-primary-color), var(--light-primary-color). "
    "--- STATUS --- "
    "var(--error-color), var(--warning-color), var(--success-color), var(--info-color). "
    "--- BORDERS/DIVIDERS --- "
    "var(--divider-color), var(--outline-color), var(--ha-card-border-color), var(--ha-card-border-radius). "
    "--- SHADOWS --- "
    "var(--ha-card-box-shadow), var(--shadow-color). "
    "--- STATE ICON --- "
    "var(--state-icon-color), var(--state-active-color), var(--state-inactive-color). "
    "--- NAMED COLORS --- "
    "var(--red-color), var(--blue-color), var(--green-color), var(--orange-color), var(--amber-color), "
    "var(--cyan-color), var(--teal-color), var(--purple-color), var(--grey-color), var(--dark-grey-color). "
    "--- RGB VARIANTS (for rgba) --- "
    "rgba(var(--rgb-primary-text-color), 0.6), rgba(var(--rgb-card-background-color), 0.8), "
    "rgba(var(--rgb-primary-color), 0.15). "
    "--- LAYOUT --- "
    "border-radius: 16px (FORCED, no exceptions). padding: 16px. gap: 8px/16px. "
    "Use %/flex/grid for responsive layout. font: inherit ONLY. "
    "--- CARD BACKGROUND/SHADOW RULES (MANDATORY) --- "
    "NEVER set background, background-color, or box-shadow on ANY element (card or inner). "
    "ha-card wrapper provides background and shadow from theme. "
    "NEVER add background overlay on child divs/sections — causes broken layering in light+dark modes. "
    "FORBIDDEN: any background property, any box-shadow, backdrop-filter, glassmorphism, "
    "custom font-family, fixed px widths."
)

API_CARD_CONFIG = (
    "[html-card-pro CARD CONFIG] "
    "type: custom:html-pro-card. content: HTML/CSS/JS string. "
    "do_not_parse: true(recommended, pure HTML+JS). "
    "update_interval: ms (periodic re-render). ignore_line_breaks: true(default). "
    "scripts: [CDN urls] for external JS libs (Chart.js, day.js, anime.js, etc.). "
    "entities: [list] for domains not auto-detected (fan/cover/input_*). "
    "Structure: <style> at top → <div> body → <script> at bottom. "
    "Icons: <ha-icon icon='mdi:xxx'></ha-icon>. Content MUST NOT be empty."
)

API_DATA_BINDING = (
    "[html-card-pro DATA-* BINDING — PREFERRED for interactions, zero JS needed]\n"
    "STRUCTURE: data-entity='entity_id' on WRAPPER element. Action/display attrs on CHILD elements inside it.\n"
    "CLICK ACTIONS (must be CHILD of data-entity wrapper): "
    "data-action='toggle|turn_on|turn_off|more-info' on a child element → click triggers service call. "
    "SHORTCUT: data-entity + data-action='toggle' on SAME element also works (toggle ONLY).\n"
    "STATE DISPLAY (child of data-entity wrapper): "
    "data-state-text → auto-updates textContent with entity state. "
    "data-attr='brightness' → auto-updates textContent with attribute value. "
    "data-friendly-name → auto-updates with friendly_name.\n"
    "RANGE SLIDERS (child of data-entity wrapper): "
    "data-brightness on <input type='range'> → light brightness 0-100→0-255. "
    "data-temperature on <input type='range'> → climate.set_temperature. "
    "data-volume on <input type='range'> → media_player volume 0-100. "
    "data-position on <input type='range'> → cover position. "
    "data-speed on <input type='range'> → fan percentage.\n"
    "OTHER INPUTS (child of data-entity wrapper): "
    "data-option on <select> → input_select. "
    "data-value on <input type='number'> → input_number.\n"
    "LONG PRESS: data-long-press + data-entity → opens more-info dialog.\n"
    "CSS STATE: [data-entity] auto-gets dataset.state. Use [data-entity][data-state='on'] { ... } for conditional styling.\n"
    "DOMAIN MAPPING: toggle auto-maps per domain (button→press, scene→turn_on, script→script.name, automation→trigger)."
)

API_JS_REFERENCE = (
    "[html-card-pro JS API — use ONLY when data-* binding is insufficient] "
    "Script globals: root(ha-card elem), $(sel), $$(sel), hass, config, overlay. "
    "hass.states is a plain Object (NOT array, NO .all(), NO .forEach()): "
    "hass.states['sensor.temp'].state / .attributes.unit_of_measurement "
    "Object.keys(hass.states).filter(id => id.startsWith('light.')) "
    "hass.callService('light','toggle',{entity_id:'light.x'}) → Promise. "
    "document.getElementById/querySelector are OVERRIDDEN to search inside ha-card. "
    "overlay: fixed fullscreen div for popups. "
    "<script> runs ONCE via new Function(). NO top-level await. Use (async()=>{...})(). "
    "For periodic JS updates: set update_interval in card_config. "
    "Store instances on root to avoid re-creating on re-run. "
    "Canvas: CSS vars MUST be resolved via getComputedStyle(document.documentElement).getPropertyValue('--xxx').trim(). "
    "CDN libs: load via scripts:[], check existence before use (if(!window.Chart)return;). "
    "hass.connection.subscribeEntities DOES NOT EXIST — use data-* binding or update_interval instead."
)

API_PITFALLS = (
    "[CORRECT EXAMPLE 1 — simple toggle (same element shortcut)]\n"
    "<style>"
    ".lc{padding:32px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:16px;cursor:pointer}"
    ".lc-icon{width:56px;height:56px;border-radius:16px;display:flex;align-items:center;justify-content:center;"
    "color:var(--secondary-text-color);transition:all .3s ease}"
    "[data-state='on'] .lc-icon{color:var(--state-active-color)}"
    ".lc-name{font-size:16px;font-weight:500;color:var(--primary-text-color)}"
    ".lc-state{font-size:12px;color:var(--secondary-text-color);letter-spacing:0.1em;text-transform:uppercase}"
    "</style>"
    "<div class='lc' data-entity='light.bedroom' data-action='toggle'>"
    "<div class='lc-icon'><ha-icon icon='mdi:lightbulb'></ha-icon></div>"
    "<div class='lc-name'>Bedroom</div>"
    "<div class='lc-state' data-state-text></div>"
    "</div>\n"
    "[CORRECT EXAMPLE 2 — multiple actions (nested children)]\n"
    "<div data-entity='light.bedroom'>"
    "<span data-friendly-name></span>: <span data-state-text></span>"
    "<button data-action='turn_on'>ON</button>"
    "<button data-action='turn_off'>OFF</button>"
    "<button data-action='more-info'>Details</button>"
    "<input type='range' min='0' max='100' data-brightness>"
    "</div>\n"
    "KEY RULES: "
    "1. data-entity on WRAPPER. data-action/data-state-text/data-attr on CHILDREN inside it. "
    "2. SHORTCUT: data-entity + data-action='toggle' on SAME element works for toggle ONLY. "
    "3. ZERO hardcoded hex. All colors from CSS vars. No hex fallback values. "
    "4. rgba(var(--rgb-xxx), alpha) for transparent overlays. "
    "5. Use <script> with hass.callService() ONLY for complex logic that data-* cannot handle.\n"
    "[WRONG PATTERNS — STRICTLY BANNED]\n"
    "WRONG: onClick=\"Light.turnOff('light.4')\" or any inline onClick handler → "
    "No such JS function. Use data-action='toggle'.\n"
    "WRONG: #fff, #1a1a1a, #eee, #666, any hardcoded hex/rgb for colors → "
    "Use var(--primary-text-color), var(--divider-color) etc.\n"
    "WRONG: background: anything / background-color: anything on any element → "
    "Remove entirely. NO background on any element. ha-card handles it.\n"
    "WRONG: box-shadow: anything on any element → "
    "Remove entirely. ha-card provides shadow from theme.\n"
    "WRONG: var(--primary-text-color, #1a1a1a) with hex fallback → "
    "NO fallback. var(--primary-text-color) alone. HA always defines these.\n"
    "WRONG: --bg-primary: var(--ha-card-background, #fff); --border-subtle: var(--divider-color, #eee); → "
    "NEVER create custom CSS variable aliases. NEVER add hex fallback to any var(). "
    "Use HA native variables DIRECTLY. ALL --bg-xxx/--text-xxx/--border-xxx/--color-xxx custom aliases BANNED."
)

STEP4_EFFICIENCY = (
    "[TOKEN SAVING — MANDATORY] "
    "DO NOT generate verbose HTML. Keep content minimal and concise. "
    "For state display: USE data-state-text / data-attr bindings (auto-updates, zero JS). "
    "For controls: USE data-* attributes (zero JS). "
    "For charts/complex UI: USE CDN libs via scripts:[] (e.g. Chart.js, ECharts). "
    "For dynamic data: USE hass.states[id] in <script>, NOT hardcoded values. "
    "NEVER repeat similar HTML blocks — use JS loops."
)

STEP_VERIFY = (
    "[MANDATORY VERIFICATION] "
    "YOU MUST immediately call DashboardCard with action=get_dashboard after any add/update. "
    "Check the card exists at the correct view_index and card_index. "
    "If the card content is wrong or missing, call update_card to fix it. "
    "DO NOT skip this verification step. DO NOT just tell user it worked without checking."
)


class DashboardCardTool(llm.Tool):
    """Create and manage Lovelace dashboard cards using html-card-pro."""

    name = "DashboardCard"
    description = (
        "Create and manage Lovelace dashboard views and cards powered by html-card-pro (custom:html-pro-card). "
        "Actions: check_dependency, list_dashboards, get_dashboard, add_view, add_card, update_card, remove_card, remove_view. "
        "PREREQUISITE: check_dependency first; if not installed, auto-install via HACS tool. "
        "Workflow: check_dependency → list_dashboards → get_dashboard (inspect view types) → add_view or add_card. "
        "Params: action, dashboard_url (url_path, default 'lovelace'), view_index (0-based), card_index (0-based), "
        "section_index (-1=auto, for 'sections' type views that use sections[].cards instead of view.cards), "
        "title (for view), icon (mdi:xxx for view), content (HTML/CSS/JS string), "
        "card_config (dict: do_not_parse, update_interval, ignore_line_breaks, scripts). "
        "VIEW TYPES: masonry/default → cards in view.cards. sections → cards in view.sections[n].cards (use section_index). "
        "Each action returns mandatory instructions in _action_required — YOU MUST follow them."
    )

    parameters = vol.Schema(
        {
            vol.Required("action"): vol.In([
                "check_dependency",
                "list_dashboards",
                "get_dashboard",
                "add_view",
                "add_card",
                "update_card",
                "remove_card",
                "remove_view",
            ]),
            vol.Optional("dashboard_url", default=""): str,
            vol.Optional("view_index", default=0): int,
            vol.Optional("card_index", default=0): int,
            vol.Optional("section_index", default=-1): int,
            vol.Optional("title", default=""): str,
            vol.Optional("icon", default=""): str,
            vol.Optional("content", default=""): str,
            vol.Optional("card_config", default={}): dict,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        action = tool_input.tool_args["action"]
        dashboard_url = tool_input.tool_args.get("dashboard_url", "").strip() or None
        view_index = tool_input.tool_args.get("view_index", 0)
        card_index = tool_input.tool_args.get("card_index", 0)
        section_index = tool_input.tool_args.get("section_index", -1)
        title = tool_input.tool_args.get("title", "").strip()
        icon = tool_input.tool_args.get("icon", "").strip() or None
        content = tool_input.tool_args.get("content", "")
        card_config = tool_input.tool_args.get("card_config", {})

        try:
            if action == "check_dependency":
                return await self._check_dependency(hass)
            if action == "list_dashboards":
                return await self._list_dashboards(hass)
            if action == "get_dashboard":
                return await self._get_dashboard(hass, dashboard_url)
            if action == "add_view":
                return await self._add_view(hass, dashboard_url, title, icon)
            if action == "add_card":
                return await self._add_card(hass, dashboard_url, view_index, section_index, content, card_config)
            if action == "update_card":
                return await self._update_card(hass, dashboard_url, view_index, section_index, card_index, content, card_config)
            if action == "remove_card":
                return await self._remove_card(hass, dashboard_url, view_index, section_index, card_index)
            if action == "remove_view":
                return await self._remove_view(hass, dashboard_url, view_index)
            return {"success": False, "error": f"Unknown action: {action}"}
        except Exception as err:
            _LOGGER.exception("DashboardCardTool error: %s", err)
            return {"success": False, "error": str(err)}

    async def _check_dependency(self, hass: HomeAssistant) -> JsonObjectType:
        file_path = hass.config.path(HTML_CARD_PRO_PATH)
        installed = await hass.async_add_executor_job(os.path.isfile, file_path)
        if not installed:
            return {
                "success": True,
                "installed": False,
                "message": "html-card-pro is NOT installed.",
                "_action_required": (
                    "YOU MUST ask user: 'html-card-pro 未安装，需要我帮你自动安装吗？' "
                    "If user agrees, call HACS tool with: "
                    "action=install, repository=knoop7/html-card-pro, category=plugin. "
                    "After HACS install succeeds, call ServiceCall to reload lovelace resources: "
                    "domain=lovelace, service=reload_resources (or browser_mod.refresh if available). "
                    "Then call DashboardCard check_dependency again to confirm. "
                    "DO NOT proceed to add_card until check_dependency returns installed=true."
                ),
            }
        resource_registered = await self._ensure_resource(hass)
        return {
            "success": True, "installed": True,
            "resource_registered": resource_registered,
            "message": "html-card-pro is installed and ready."
            + (" Resource auto-registered." if resource_registered == "added" else ""),
            "_instructions": STEP1_ROLE,
            "_action_required": "YOU MUST now call DashboardCard with action=list_dashboards to see available dashboards.",
        }

    async def _get_lovelace_config(self, hass: HomeAssistant, dashboard_url: str | None):
        from homeassistant.components.lovelace.const import LOVELACE_DATA, DOMAIN as LL_DOMAIN, ConfigNotFound

        data = hass.data.get(LOVELACE_DATA)
        if data is None:
            return None, "Lovelace not loaded"

        if dashboard_url is None or dashboard_url == "lovelace":
            config_obj = data.dashboards.get(LL_DOMAIN) or data.dashboards.get(None)
        else:
            config_obj = data.dashboards.get(dashboard_url)

        if config_obj is None:
            return None, f"Dashboard '{dashboard_url or 'default'}' not found"

        try:
            ll_config = await config_obj.async_load(False)
        except ConfigNotFound:
            ll_config = {"views": []}
        return config_obj, ll_config

    async def _ensure_resource(self, hass: HomeAssistant) -> str:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        data = hass.data.get(LOVELACE_DATA)
        if data is None:
            return "lovelace_not_loaded"

        resources = data.resources
        if not hasattr(resources, 'async_items') or not hasattr(resources, 'async_create_item'):
            return "yaml_mode"

        try:
            if not resources.loaded:
                await resources.async_load()
                resources.loaded = True
        except Exception:
            pass

        existing = resources.async_items() or []
        for item in existing:
            url = item.get("url", "")
            if "html-card-pro" in url or "html-pro-card" in url:
                return "already_registered"

        try:
            await resources.async_create_item({
                "res_type": "module",
                "url": HTML_CARD_PRO_RESOURCE,
            })
            return "added"
        except Exception as err:
            _LOGGER.warning("Failed to auto-register html-card-pro resource: %s", err)
            return f"register_failed: {err}"

    @staticmethod
    def _resolve_cards(view: dict, section_index: int) -> tuple[list | None, str]:
        view_type = view.get("type", "")
        if view_type == "sections":
            sections = view.get("sections", [])
            if section_index < 0:
                if sections:
                    cards = sections[-1].setdefault("cards", [])
                    return cards, ""
                new_sec = {"type": "grid", "cards": []}
                sections.append(new_sec)
                view["sections"] = sections
                return new_sec["cards"], ""
            if section_index >= len(sections):
                return None, f"section_index {section_index} out of range (0..{len(sections) - 1})"
            cards = sections[section_index].setdefault("cards", [])
            return cards, ""
        cards = view.setdefault("cards", [])
        return cards, ""

    async def _save_config(self, config_obj, ll_config: dict) -> None:
        await config_obj.async_save(ll_config)

    async def _list_dashboards(self, hass: HomeAssistant) -> JsonObjectType:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        data = hass.data.get(LOVELACE_DATA)
        if data is None:
            return {"success": False, "error": "Lovelace not loaded"}

        result = []
        for url_path, db in data.dashboards.items():
            info: dict[str, Any] = {
                "url_path": url_path or "lovelace",
                "mode": db.mode,
            }
            if db.config:
                info["title"] = db.config.get("title", "")
                info["icon"] = db.config.get("icon", "")
            result.append(info)

        return {
            "success": True, "dashboards": result,
            "_action_required": (
                "YOU MUST now either: (1) call get_dashboard with a dashboard_url to inspect views, "
                "or (2) call add_view to create a new page for your card."
            ),
        }

    async def _get_dashboard(self, hass: HomeAssistant, dashboard_url: str | None) -> JsonObjectType:
        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        views_summary = []
        for i, view in enumerate(ll_config.get("views", [])):
            view_type = view.get("type", "masonry")
            info: dict[str, Any] = {
                "index": i,
                "title": view.get("title", ""),
                "icon": view.get("icon", ""),
                "path": view.get("path", ""),
                "view_type": view_type,
            }
            if view_type == "sections":
                sections = view.get("sections", [])
                sec_list = []
                for si, sec in enumerate(sections):
                    sec_cards = sec.get("cards", [])
                    sec_list.append({
                        "section_index": si,
                        "type": sec.get("type", "grid"),
                        "card_count": len(sec_cards),
                        "cards": [
                            {
                                "index": j,
                                "type": c.get("type", ""),
                                "title": c.get("title", c.get("content", "")[:60] if c.get("content") else ""),
                            }
                            for j, c in enumerate(sec_cards)
                        ],
                    })
                info["sections"] = sec_list
                info["section_count"] = len(sections)
            else:
                cards = view.get("cards", [])
                info["card_count"] = len(cards)
                info["cards"] = [
                    {
                        "index": j,
                        "type": c.get("type", ""),
                        "title": c.get("title", c.get("content", "")[:60] if c.get("content") else ""),
                    }
                    for j, c in enumerate(cards)
                ]
            views_summary.append(info)

        return {
            "success": True,
            "dashboard_url": dashboard_url or "lovelace",
            "views": views_summary,
            "_instructions": (
                "Review the views above. "
                "For 'sections' type views: use section_index to target a specific section. "
                "For 'masonry' type views: use add_card directly with view_index. "
                "To add a card, use add_card with the correct view_index (and section_index for sections views)."
            ),
        }

    async def _add_view(
        self, hass: HomeAssistant, dashboard_url: str | None, title: str, icon: str | None
    ) -> JsonObjectType:
        if not title:
            return {"success": False, "error": "title is required for add_view"}

        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        if not isinstance(ll_config, dict):
            ll_config = {"views": []}

        views = ll_config.setdefault("views", [])

        import re
        path = re.sub(r"[^a-z0-9_-]+", "-", title.lower()).strip("-") or f"view-{len(views)}"
        existing_paths = {v.get("path", "") for v in views}
        if path in existing_paths:
            suffix = 2
            while f"{path}-{suffix}" in existing_paths:
                suffix += 1
            path = f"{path}-{suffix}"

        new_view: dict[str, Any] = {
            "title": title,
            "path": path,
            "cards": [],
        }
        if icon:
            new_view["icon"] = icon

        views.append(new_view)
        await self._save_config(config_obj, ll_config)

        return {
            "success": True,
            "message": f"View '{title}' created at index {len(views) - 1}",
            "view_index": len(views) - 1,
            "path": path,
            "dashboard_url": dashboard_url or "lovelace",
            "_style_rules": STEP2_VISUAL,
            "_action_required": (
                f"YOU MUST now call DashboardCard with action=add_card, "
                f"view_index={len(views) - 1}, and your HTML content. "
                f"Follow the _style_rules above when writing content."
            ),
        }

    async def _add_card(
        self,
        hass: HomeAssistant,
        dashboard_url: str | None,
        view_index: int,
        section_index: int,
        content: str,
        card_config: dict,
    ) -> JsonObjectType:
        if not content and not card_config:
            return {"success": False, "error": "content or card_config is required"}

        dep = await self._check_dependency(hass)
        if not dep.get("installed"):
            return dep

        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        views = ll_config.get("views", [])
        if view_index < 0 or view_index >= len(views):
            return {
                "success": False,
                "error": f"view_index {view_index} out of range (0..{len(views) - 1})",
            }

        cards, err = self._resolve_cards(views[view_index], section_index)
        if cards is None:
            return {"success": False, "error": err}

        card: dict[str, Any] = {"type": CARD_TYPE}
        if content:
            card["content"] = content
        card.update(card_config)

        cards.append(card)
        await self._save_config(config_obj, ll_config)

        result: dict[str, Any] = {
            "success": True,
            "message": f"Card added to view {view_index} at card_index {len(cards) - 1}",
            "view_index": view_index,
            "card_index": len(cards) - 1,
            "dashboard_url": dashboard_url or "lovelace",
            "_card_config": API_CARD_CONFIG,
            "_data_binding": API_DATA_BINDING,
            "_pitfalls": API_PITFALLS,
            "_efficiency": STEP4_EFFICIENCY,
            "_action_required": STEP_VERIFY,
        }
        if views[view_index].get("type") == "sections":
            result["section_index"] = section_index if section_index >= 0 else len(views[view_index].get("sections", [])) - 1
        return result

    async def _update_card(
        self,
        hass: HomeAssistant,
        dashboard_url: str | None,
        view_index: int,
        section_index: int,
        card_index: int,
        content: str,
        card_config: dict,
    ) -> JsonObjectType:
        if not content and not card_config:
            return {"success": False, "error": "content or card_config is required"}

        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        views = ll_config.get("views", [])
        if view_index < 0 or view_index >= len(views):
            return {"success": False, "error": f"view_index {view_index} out of range"}

        cards, err = self._resolve_cards(views[view_index], section_index)
        if cards is None:
            return {"success": False, "error": err}
        if card_index < 0 or card_index >= len(cards):
            return {"success": False, "error": f"card_index {card_index} out of range (0..{len(cards) - 1})"}

        if content:
            cards[card_index]["content"] = content
        if card_config:
            cards[card_index].update(card_config)

        await self._save_config(config_obj, ll_config)

        return {
            "success": True,
            "message": f"Card updated at view {view_index}, card {card_index}",
            "current_card": cards[card_index],
            "_js_reference": API_JS_REFERENCE,
            "_pitfalls": API_PITFALLS,
            "_action_required": STEP_VERIFY,
        }

    async def _remove_card(
        self,
        hass: HomeAssistant,
        dashboard_url: str | None,
        view_index: int,
        section_index: int,
        card_index: int,
    ) -> JsonObjectType:
        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        views = ll_config.get("views", [])
        if view_index < 0 or view_index >= len(views):
            return {"success": False, "error": f"view_index {view_index} out of range"}

        cards, err = self._resolve_cards(views[view_index], section_index)
        if cards is None:
            return {"success": False, "error": err}
        if card_index < 0 or card_index >= len(cards):
            return {"success": False, "error": f"card_index {card_index} out of range (0..{len(cards) - 1})"}

        removed = cards.pop(card_index)
        await self._save_config(config_obj, ll_config)

        return {
            "success": True,
            "message": f"Removed card {card_index} from view {view_index}",
            "removed_card_type": removed.get("type", ""),
        }

    async def _remove_view(
        self,
        hass: HomeAssistant,
        dashboard_url: str | None,
        view_index: int,
    ) -> JsonObjectType:
        config_obj, ll_config = await self._get_lovelace_config(hass, dashboard_url)
        if config_obj is None:
            return {"success": False, "error": ll_config}

        views = ll_config.get("views", [])
        if view_index < 0 or view_index >= len(views):
            return {"success": False, "error": f"view_index {view_index} out of range"}

        removed = views.pop(view_index)
        await self._save_config(config_obj, ll_config)

        return {
            "success": True,
            "message": f"Removed view '{removed.get('title', '')}' (was index {view_index})",
        }
