
from __future__ import annotations
import logging
import json
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

_LOGGER = logging.getLogger(__name__)


class GetSystemIndexTool(llm.Tool):
    name = "GetSystemIndex"
    description = """获取系统结构索引（带缓存）。返回轻量级的系统概览，包含：
- areas: 区域列表及实体/设备数量
- domains: 域列表及实体数量
- device_classes: 各域的设备类别
- people: 人员列表及状态
- automations: 自动化列表
- scripts: 脚本列表

索引自动缓存5分钟，状态变化时自动刷新。"""
    parameters = vol.Schema({
        vol.Optional("force_refresh", default=False): bool,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..index_manager import get_index_manager
        
        force_refresh = tool_input.tool_args.get("force_refresh", False)
        
        manager = await get_index_manager(hass, llm_context.assistant)
        
        if force_refresh:
            await manager._async_refresh_index()
        
        index = await manager.get_index()
        
        return {
            "success": True,
            "cached": not force_refresh,
            **index
        }


class SetConversationStateTool(llm.Tool):
    name = "SetConversationState"
    description = """设置对话状态。用于告诉系统你是否期待用户回复。
    
- expecting_response=true: 你在等待用户回复（如提问、确认）
- expecting_response=false: 任务已完成，不需要用户回复

在完成任务后调用此工具，帮助系统正确管理对话流程。"""
    parameters = vol.Schema({
        vol.Required("expecting_response"): bool,
        vol.Optional("reason", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        expecting = tool_input.tool_args.get("expecting_response", False)
        reason = tool_input.tool_args.get("reason", "")
        
        hass.data.setdefault("ha_crack", {})
        hass.data["ha_crack"]["expecting_response"] = expecting
        hass.data["ha_crack"]["conversation_state_reason"] = reason
        
        _LOGGER.debug("SetConversationState: expecting=%s, reason=%s", expecting, reason)
        
        return {
            "success": True,
            "expecting_response": expecting,
            "message": "对话状态已更新" if expecting else "任务完成，对话可结束"
        }


class ValidateServiceTool(llm.Tool):
    name = "ValidateService"
    description = "验证服务调用参数。在调用ServiceCall前使用，返回是否有效、错误信息、参数建议。"
    parameters = vol.Schema({
        vol.Required("domain"): str,
        vol.Required("service"): str,
        vol.Optional("data", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..domain_registry import validate_service_call
        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})
        result = validate_service_call(domain, service, data)
        return {"success": result["valid"], "errors": result["errors"], "warnings": result["warnings"], "suggestions": result["suggestions"], "normalized_service": result["normalized_service"]}


class ServiceHelpTool(llm.Tool):
    name = "ServiceHelp"
    description = "获取域或服务的帮助信息，包括可用服务、参数说明、取值范围等。"
    parameters = vol.Schema({
        vol.Required("domain"): str,
        vol.Optional("service", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from ..domain_registry import get_service_help
        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        help_text = get_service_help(domain, service if service else None)
        return {"success": True, "help": help_text}


class SmartDiscoveryTool(llm.Tool):
    name = "SmartDiscovery"
    description = """智能发现实体。支持多种发现模式：
- 按模式: name_pattern="*motion*" 或 "*temperature*"
- 按推断类型: inferred_type="person_detection"/"temperature"/"door_window"等
- 按人员: person_name="小明" 发现关联实体
- 按宠物: pet_name="旺财" 发现关联实体
- 组合过滤: area+domain+device_class+state

可用推断类型: person_detection, motion_detection, door_window, temperature, humidity, light_level, power_monitoring, battery, location_tracking"""
    parameters = vol.Schema({
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
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
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
    description = "查询Home Assistant实体状态。用于获取设备当前状态、传感器数值等。"
    parameters = vol.Schema({
        vol.Required("entity_id"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        entity_id = tool_input.tool_args.get("entity_id", "")
        state = hass.states.get(entity_id)
        if state:
            return {"success": True, "entity_id": entity_id, "state": state.state, "attributes": dict(state.attributes), "name": state.name}
        return {"success": False, "error": f"Entity {entity_id} not found"}


class ServiceCallTool(llm.Tool):
    name = "ServiceCall"
    description = """调用Home Assistant服务。用于控制设备、执行自动化等。
常用服务示例：
- homeassistant.restart: 重启HA
- homeassistant.reload_core_config: 重新加载核心配置
- light.turn_on/turn_off: 控制灯光 (data: {entity_id, brightness, color_temp})
- switch.turn_on/turn_off: 控制开关 (data: {entity_id})
- climate.set_temperature: 设置温度 (data: {entity_id, temperature})
- automation.trigger: 触发自动化 (data: {entity_id})
- script.turn_on: 运行脚本 (data: {entity_id})
- notify.xxx: 发送通知 (data: {message, title})"""
    parameters = vol.Schema({
        vol.Required("domain"): str,
        vol.Required("service"): str,
        vol.Optional("data", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        _LOGGER.warning(f"=== ServiceCallTool被调用 === args={tool_input.tool_args}")
        
        domain = tool_input.tool_args.get("domain", "")
        service = tool_input.tool_args.get("service", "")
        data = tool_input.tool_args.get("data", {})
        
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                data = {}
        
        if "json" in data and len(data) == 1:
            try:
                inner = json.loads(data["json"]) if isinstance(data["json"], str) else data["json"]
                data = inner if isinstance(inner, dict) else {}
            except:
                data = {}
        
        if "entity_id" in data:
            entity_id = data["entity_id"]
            if not hass.states.get(entity_id):
                from homeassistant.helpers.llm import _get_exposed_entities
                exposed_entities = _get_exposed_entities(hass, llm_context.assistant) if llm_context.assistant else {}
                
                matched = self._find_entity_by_name(hass, entity_id, domain, exposed_entities)
                if matched:
                    _LOGGER.warning(f"实体名称匹配: {entity_id} -> {matched}")
                    data["entity_id"] = matched
                else:
                    exposed = self._get_exposed_entities_list(domain, exposed_entities)
                    _LOGGER.warning(f"找不到实体: {entity_id}, 可用: {len(exposed)}个")
                    return {"success": False, "error": f"找不到实体: {entity_id}", "available_entities": exposed[:10]}
        
        _LOGGER.warning(f"ServiceCall执行: {domain}.{service} with data={data}")
        try:
            await hass.services.async_call(domain, service, data, blocking=True)
            _LOGGER.warning(f"ServiceCall成功: {domain}.{service}")
            return {"success": True, "message": f"已成功调用 {domain}.{service}", "domain": domain, "service": service, "data": data}
        except Exception as e:
            _LOGGER.error(f"ServiceCall失败: {domain}.{service} - {e}")
            return {"success": False, "error": str(e)}
    
    def _find_entity_by_name(self, hass: HomeAssistant, name: str, domain: str, exposed_entities: dict) -> str | None:
        name_lower = name.lower().replace("_", " ").replace("-", " ")
        candidates = []
        
        entities = exposed_entities.get("entities", {}) if exposed_entities else {}
        
        for entity_id, info in entities.items():
            if domain and not entity_id.startswith(f"{domain}."):
                continue
            
            names = info.get("names", "").lower()
            
            if name_lower == entity_id.lower():
                return entity_id
            if name_lower in names:
                candidates.append((entity_id, 1))
            elif name_lower in entity_id.lower():
                candidates.append((entity_id, 2))
        
        if candidates:
            candidates.sort(key=lambda x: x[1])
            return candidates[0][0]
        return None
    
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
    description = """获取暴露实体的实时状态。用于回答关于设备当前状态的问题。
    
参数：
- domain: 可选，按域过滤（如 light, switch, sensor）
- area: 可选，按区域过滤
- limit: 可选，限制返回数量（默认50，最大100）"""
    parameters = vol.Schema({
        vol.Optional("domain", default=""): str,
        vol.Optional("area", default=""): str,
        vol.Optional("limit", default=50): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from homeassistant.components.homeassistant import async_should_expose
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
            if llm_context.assistant and not async_should_expose(hass, llm_context.assistant, state.entity_id):
                continue
            if domain_filter and not state.entity_id.startswith(f"{domain_filter}."):
                continue
            if target_area_id:
                entity_entry = entity_reg.async_get(state.entity_id)
                if entity_entry and entity_entry.area_id != target_area_id:
                    continue
            entities[state.entity_id] = {"name": state.name, "state": state.state, "domain": state.domain}
            count += 1
        
        return {"success": True, "entities": entities, "count": count, "limited": count >= limit}


class ListServicesTool(llm.Tool):
    name = "ListServices"
    description = "列出指定域的所有可用服务。用于查询某个域有哪些服务可以调用。"
    parameters = vol.Schema({
        vol.Required("domain"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        domain = tool_input.tool_args.get("domain", "")
        services = hass.services.async_services().get(domain, {})
        if services:
            service_list = []
            for name, svc in services.items():
                desc = getattr(svc, 'description', '') if hasattr(svc, 'description') else ''
                service_list.append(f"- {domain}.{name}: {desc[:100]}" if desc else f"- {domain}.{name}")
            return {"success": True, "domain": domain, "services": service_list, "count": len(service_list)}
        return {"success": False, "error": f"域 {domain} 不存在或没有服务"}


class AutomationTool(llm.Tool):
    name = "Automation"
    description = """管理自动化。支持列出、触发、启用、禁用自动化。

注意：不支持创建自动化，请用户在HA界面手动创建。"""
    parameters = vol.Schema({
        vol.Required("action"): vol.In(["list", "trigger", "enable", "disable"]),
        vol.Optional("entity_id", default=""): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "list")
        entity_id = tool_input.tool_args.get("entity_id", "")
        
        try:
            if action == "list":
                automations = [s for s in hass.states.async_all() if s.entity_id.startswith("automation.")]
                return {"success": True, "automations": [{"entity_id": a.entity_id, "name": a.name, "state": a.state} for a in automations]}
            
            elif action == "trigger" and entity_id:
                await hass.services.async_call("automation", "trigger", {"entity_id": entity_id}, blocking=True)
                return {"success": True, "message": f"Triggered {entity_id}"}
            
            elif action == "enable" and entity_id:
                await hass.services.async_call("automation", "turn_on", {"entity_id": entity_id}, blocking=True)
                return {"success": True, "message": f"Enabled {entity_id}"}
            
            elif action == "disable" and entity_id:
                await hass.services.async_call("automation", "turn_off", {"entity_id": entity_id}, blocking=True)
                return {"success": False, "message": f"Disabled {entity_id}"}
            
            return {"success": False, "error": "Invalid action or missing required parameters"}
        except Exception as e:
            _LOGGER.error(f"AutomationTool error: {e}")
            return {"success": False, "error": str(e)}


class ScriptExecuteTool(llm.Tool):
    name = "ScriptExecute"
    description = "执行Home Assistant脚本。"
    parameters = vol.Schema({
        vol.Required("script_id"): str,
        vol.Optional("variables", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        script_id = tool_input.tool_args.get("script_id", "")
        variables = tool_input.tool_args.get("variables", {})
        try:
            if not script_id.startswith("script."):
                script_id = f"script.{script_id}"
            await hass.services.async_call("script", "turn_on", {"entity_id": script_id, "variables": variables}, blocking=True)
            return {"success": True, "message": f"Executed {script_id}"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class HistoryQueryTool(llm.Tool):
    name = "HistoryQuery"
    description = "查询实体历史状态。"
    parameters = vol.Schema({
        vol.Required("entity_id"): str,
        vol.Optional("hours", default=24): int,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from datetime import datetime, timedelta
        entity_id = tool_input.tool_args.get("entity_id", "")
        hours = tool_input.tool_args.get("hours", 24)
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import state_changes_during_period
            start = datetime.now() - timedelta(hours=hours)
            history = await get_instance(hass).async_add_executor_job(
                state_changes_during_period, hass, start, None, entity_id
            )
            if entity_id in history:
                states = [{"state": s.state, "time": s.last_changed.isoformat()} for s in history[entity_id][-20:]]
                return {"success": True, "entity_id": entity_id, "history": states}
            return {"success": False, "error": "No history found"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class AreaDevicesTool(llm.Tool):
    name = "AreaDevices"
    description = "获取指定区域内的所有设备。"
    parameters = vol.Schema({
        vol.Required("area"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
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
            return {"success": False, "error": f"Area '{area_name}' not found", "available_areas": [a.name for a in area_reg.async_list_areas()]}
        
        devices = []
        for device in device_reg.devices.values():
            if device.area_id == target_area.id:
                entities = [e.entity_id for e in entity_reg.entities.values() if e.device_id == device.id]
                devices.append({"name": device.name, "entities": entities})
        
        return {"success": True, "area": target_area.name, "devices": devices}


class BatchControlTool(llm.Tool):
    name = "BatchControl"
    description = "批量控制多个设备。"
    parameters = vol.Schema({
        vol.Required("entity_ids"): list,
        vol.Required("action"): vol.In(["turn_on", "turn_off", "toggle"]),
        vol.Optional("data", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        entity_ids = tool_input.tool_args.get("entity_ids", [])
        action = tool_input.tool_args.get("action", "turn_on")
        data = tool_input.tool_args.get("data", {})
        results = []
        for eid in entity_ids:
            try:
                domain = eid.split(".")[0]
                await hass.services.async_call(domain, action, {"entity_id": eid, **data}, blocking=True)
                results.append({"entity_id": eid, "success": True})
            except Exception as e:
                results.append({"entity_id": eid, "success": False, "error": str(e)})
        return {"success": True, "results": results}


class NotifyTool(llm.Tool):
    name = "Notify"
    description = "发送通知。"
    parameters = vol.Schema({
        vol.Required("message"): str,
        vol.Optional("title", default="AI助手"): str,
        vol.Optional("target", default="persistent_notification"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        message = tool_input.tool_args.get("message", "")
        title = tool_input.tool_args.get("title", "AI助手")
        target = tool_input.tool_args.get("target", "persistent_notification")
        try:
            if target == "persistent_notification":
                await hass.services.async_call("persistent_notification", "create", {"message": message, "title": title}, blocking=True)
            else:
                await hass.services.async_call("notify", target, {"message": message, "title": title}, blocking=True)
            return {"success": True, "message": "Notification sent"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class FireEventTool(llm.Tool):
    name = "FireEvent"
    description = "触发Home Assistant事件。用于与自动化通信或触发自定义事件。"
    parameters = vol.Schema({
        vol.Required("event_type"): str,
        vol.Optional("event_data", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        event_type = tool_input.tool_args.get("event_type", "")
        event_data = tool_input.tool_args.get("event_data", {})
        try:
            hass.bus.async_fire(event_type, event_data)
            return {"success": True, "message": f"Event {event_type} fired", "data": event_data}
        except Exception as e:
            return {"success": False, "error": str(e)}


class InjectJSTool(llm.Tool):
    name = "InjectJS"
    description = """注入JavaScript代码到前端执行。用于创建视觉效果、操作页面、深度DOM操作等。


- softNavigate(path): 软导航到页面，如 HACrack.softNavigate('/config')
- clickSidebar(text): 点击侧边栏项目，如 HACrack.clickSidebar('设置')
- getSidebarItems(): 获取侧边栏所有项目列表
- navigate(path): 硬导航(会刷新页面)

- clickByText(text): 按文字点击按钮，如 HACrack.clickByText('保存')
- clickByIndex(index): 按索引点击，先用getClickables()获取列表
- getClickables(): 获取所有可点击元素列表
- click(selector): 按CSS选择器点击

- fillInput(index, value): 填充输入框
- getInputs(): 获取所有输入框列表

- toast(msg): 显示提示
- dialog(title, msg): 显示对话框

- deepQuery(selector): 深度查询单个元素
- deepQueryAll(selector): 深度查询所有元素
- click(selector): 点击元素
- clickByText(text): 按文字点击
- clickByIndex(index): 按索引点击
- getClickables(): 获取所有可点击元素列表
- getInputs(): 获取所有输入框列表
- fillInput(index/selector, value): 填充输入框

- injectCSS(css): 注入CSS样式
- injectHTML(selector, html, position): 注入HTML
- setStyle(selector, {styles}): 设置元素样式
- hide(selector)/show(selector): 隐藏/显示元素
- remove(selector): 删除元素
- highlight(selector, color, duration): 高亮元素

- injectGlobalCSS(css, id): 全局CSS注入
- injectSidebarCSS(css): 侧边栏CSS注入
- injectPanelCSS(css): 面板CSS注入
- injectDialogCSS(css): 对话框CSS注入
- injectAllCSS(css): 注入到所有Shadow DOM
- injectJS(code, id): 注入JS代码
- injectShadowJS(element, code, id): 注入到Shadow DOM
- injectModule(url, id): 注入ES模块
- injectLink(href, rel, id): 注入link标签
- injectMeta(name, content, id): 注入meta标签
- injectFont(fontFamily, src, id): 注入字体
- injectAll({css, js, module, link, font}): 批量注入

- wait(ms): 等待毫秒
- waitFor(selector, timeout): 等待元素出现
- observe(selector, callback): 观察元素出现

- hass(): 获取hass对象
- getStates(): 获取所有状态
- getState(entityId): 获取单个实体状态
- callService(domain, service, data): 调用服务
- fireEvent(type, data): 触发事件
- subscribe(eventType, callback): 订阅事件

- getPageInfo(): 获取页面信息
- debug(): 输出调试信息
- screenshot(): 输出页面结构"""
    parameters = vol.Schema({
        vol.Required("code"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        code = tool_input.tool_args.get("code", "")
        if not code:
            return {"success": False, "error": "缺少code参数"}
        pending_js = hass.data.setdefault("ha_crack_pending_js", [])
        pending_js.append(code)
        return {"success": True, "message": "JS已执行"}


class HAControlTool(llm.Tool):
    name = "HAControl"
    description = """Home Assistant高级控制。用于控制HA界面、系统和查询集成信息。
可用操作：
- list_integrations: 列出所有已安装的集成
- get_integration: 获取指定集成的详细信息 (params: {domain: "集成域名"})
- list_entities_by_integration: 列出指定集成的所有实体 (params: {domain: "集成域名"})
- reload_integration: 重载指定集成 (params: {domain: "集成域名"})
- rename_entry: 重命名配置条目 (params: {domain: "集成域名", name: "新名称"})
- navigate: 切换页面 (path: "/lovelace", "/config", "/developer-tools/service"等)
- reload_themes/reload_resources/reload_scripts/reload_automations: 重新加载
- show_toast: 显示提示消息 (message)
- show_dialog: 显示对话框 (title, message)"""
    parameters = vol.Schema({
        vol.Required("action"): str,
        vol.Optional("params", default={}): vol.Any(dict, str),
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        params = tool_input.tool_args.get("params", {})
        
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except:
                params = {}
        
        _LOGGER.warning(f"=== HAControlTool === action={action}, params={params}")
        
        if action == "list_integrations":
            from homeassistant.loader import async_get_integrations
            entries = hass.config_entries.async_entries()
            integrations = {}
            for entry in entries:
                domain = entry.domain
                if domain not in integrations:
                    integrations[domain] = {"count": 0, "entries": []}
                integrations[domain]["count"] += 1
                integrations[domain]["entries"].append({
                    "title": entry.title,
                    "state": entry.state.value if hasattr(entry.state, 'value') else str(entry.state),
                    "entry_id": entry.entry_id[:8]
                })
            return {"success": True, "integrations": integrations, "total": len(entries)}
        
        elif action == "get_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "缺少domain参数"}
            entries = [e for e in hass.config_entries.async_entries() if e.domain == domain]
            if not entries:
                return {"success": False, "error": f"未找到集成: {domain}"}
            result = []
            for entry in entries:
                result.append({
                    "title": entry.title,
                    "domain": entry.domain,
                    "state": entry.state.value if hasattr(entry.state, 'value') else str(entry.state),
                    "entry_id": entry.entry_id,
                    "data": {k: "***" if "key" in k.lower() or "token" in k.lower() or "password" in k.lower() else v for k, v in entry.data.items()}
                })
            return {"success": True, "integration": domain, "entries": result}
        
        elif action == "list_entities_by_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "缺少domain参数"}
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(hass)
            entities = []
            for entity in registry.entities.values():
                if entity.platform == domain:
                    state = hass.states.get(entity.entity_id)
                    entities.append({
                        "entity_id": entity.entity_id,
                        "name": entity.name or entity.original_name,
                        "state": state.state if state else "unknown",
                        "device_class": entity.device_class or entity.original_device_class
                    })
            return {"success": True, "integration": domain, "entities": entities, "count": len(entities)}
        
        elif action == "navigate":
            path = params.get("path", "/lovelace")
            hass.bus.async_fire("ha_crack_frontend", {"action": "navigate", "params": {"path": path}})
            return {"success": True, "message": f"导航到 {path}"}
        
        elif action in ["reload_themes", "reload_resources", "reload_scripts", "reload_automations"]:
            service_map = {
                "reload_themes": ("frontend", "reload_themes"),
                "reload_resources": ("lovelace", "reload_resources"),
                "reload_scripts": ("script", "reload"),
                "reload_automations": ("automation", "reload"),
            }
            domain, service = service_map[action]
            await hass.services.async_call(domain, service, {}, blocking=True)
            return {"success": True, "message": f"已重新加载 {action}"}
        
        elif action == "reload_integration":
            domain = params.get("domain", "")
            if not domain:
                return {"success": False, "error": "需要指定集成域名 (domain)"}
            try:
                await hass.services.async_call("homeassistant", "reload_config_entry", {"entry_id": domain}, blocking=True)
            except:
                entries = hass.config_entries.async_entries(domain)
                if entries:
                    for entry in entries:
                        await hass.config_entries.async_reload(entry.entry_id)
                    return {"success": True, "message": f"已重载集成 {domain}"}
                return {"success": False, "error": f"未找到集成 {domain}"}
            return {"success": True, "message": f"已重载集成 {domain}"}
        
        elif action == "rename_entry":
            domain = params.get("domain", "")
            new_name = params.get("name", "")
            if not domain or not new_name:
                return {"success": False, "error": "需要指定集成域名 (domain) 和新名称 (name)"}
            entries = hass.config_entries.async_entries(domain)
            if entries:
                for entry in entries:
                    hass.config_entries.async_update_entry(entry, title=new_name)
                return {"success": True, "message": f"已将 {domain} 重命名为 {new_name}"}
            return {"success": False, "error": f"未找到集成 {domain}"}
        
        elif action == "show_toast":
            message = params.get("message", "")
            hass.bus.async_fire("ha_crack_frontend", {"action": "toast", "params": {"message": message}})
            return {"success": True, "message": "提示已显示"}
        
        elif action == "show_dialog":
            title = params.get("title", "")
            message = params.get("message", "")
            hass.bus.async_fire("ha_crack_frontend", {"action": "dialog", "params": {"title": title, "message": message}})
            return {"success": True, "message": "对话框已显示"}
        
        return {"success": False, "error": f"未知操作: {action}"}


class FrontendControlTool(llm.Tool):
    name = "FrontendControl"
    description = """前端高级控制工具。执行预定义的前端操作，无需编写JS代码。

可用操作：
- get_page_info: 获取当前页面信息(URL、按钮数、输入框数等)
- get_clickables: 获取所有可点击元素列表(前20个)
- get_inputs: 获取所有输入框列表
- get_sidebar: 获取侧边栏所有项目列表
- click_by_text: 按文字点击元素 (params: {text: "按钮文字"})
- click_by_index: 按索引点击元素 (params: {index: 0})
- fill_input: 填充输入框 (params: {index: 0, value: "内容"})
- navigate: 导航到页面 (params: {path: "/lovelace"})
- inject_css: 注入CSS样式 (params: {css: "body{...}"})
- show_effect: 显示视觉效果 (params: {type: "sakura|firework|snow|confetti"})"""
    parameters = vol.Schema({
        vol.Required("action"): str,
        vol.Optional("params", default={}): dict,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        params = tool_input.tool_args.get("params", {})
        
        pending_js = hass.data.setdefault("ha_crack_pending_js", [])
        
        frontend_state = hass.data.get("ha_crack_frontend_state", {})
        
        async def wait_for_state(key, timeout=1.0):
            import asyncio
            frontend_state["_updated"] = False
            for _ in range(int(timeout * 10)):
                await asyncio.sleep(0.1)
                if frontend_state.get("_updated") and key in frontend_state:
                    return frontend_state.get(key)
            return frontend_state.get(key, {})
        
        if action == "get_page_info":
            pending_js.append("HACrack.reportState({pageInfo: HACrack.getPageInfo()});")
            data = await wait_for_state("pageInfo")
            return {"success": True, "data": data}
        
        elif action == "get_clickables":
            pending_js.append("HACrack.reportState({clickables: HACrack.getClickables().slice(0,20)});")
            data = await wait_for_state("clickables")
            return {"success": True, "data": data}
        
        elif action == "get_inputs":
            pending_js.append("HACrack.reportState({inputs: HACrack.getInputs()});")
            data = await wait_for_state("inputs")
            return {"success": True, "data": data}
        
        elif action == "get_sidebar":
            from homeassistant.components.frontend import DATA_PANELS
            panels = hass.data.get(DATA_PANELS, {})
            sidebar_items = []
            for url_path, panel in panels.items():
                if panel.sidebar_title:
                    sidebar_items.append({
                        "text": panel.sidebar_title,
                        "path": f"/{url_path}",
                        "icon": panel.sidebar_icon,
                        "visible": panel.sidebar_default_visible
                    })
            return {"success": True, "data": sidebar_items}
        
        elif action == "click_by_text":
            text = params.get("text", "")
            pending_js.append(f"HACrack.clickByText({json.dumps(text)});")
            return {"success": True, "message": f"已发送点击指令: {text}"}
        
        elif action == "click_by_index":
            index = params.get("index", 0)
            pending_js.append(f"HACrack.clickByIndex({index});")
            return {"success": True, "message": f"已发送点击指令: index={index}"}
        
        elif action == "fill_input":
            index = params.get("index", 0)
            value = params.get("value", "")
            pending_js.append(f"HACrack.fillInput({index}, {json.dumps(value)});")
            return {"success": True, "message": f"已发送填充指令: index={index}"}
        
        elif action == "navigate":
            path = params.get("path", "/lovelace")
            pending_js.append(f"HACrack.navigate({json.dumps(path)});")
            return {"success": True, "message": f"已发送导航指令: {path}"}
        
        elif action == "inject_css":
            css = params.get("css", "")
            pending_js.append(f"HACrack.injectCSS({json.dumps(css)});")
            return {"success": True, "message": "已注入CSS样式"}
        
        elif action == "show_effect":
            effect_type = params.get("type", "sakura")
            effects = {
                "sakura": self._get_sakura_effect(),
                "firework": self._get_firework_effect(),
                "snow": self._get_snow_effect(),
                "confetti": self._get_confetti_effect(),
            }
            code = effects.get(effect_type, effects["sakura"])
            pending_js.append(code)
            return {"success": True, "message": f"已添加{effect_type}效果"}
        
        return {"success": False, "error": f"未知操作: {action}"}
    
    def _get_sakura_effect(self):
        return """
(function(){
    const canvas = document.createElement('canvas');
    canvas.id = 'ha-crack-effect';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9998;';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const petals = [];
    for(let i=0;i<50;i++) petals.push({x:Math.random()*canvas.width,y:Math.random()*canvas.height-canvas.height,size:Math.random()*10+5,speed:Math.random()*2+1,angle:Math.random()*Math.PI*2,spin:Math.random()*0.1-0.05});
    function draw(){
        ctx.clearRect(0,0,canvas.width,canvas.height);
        petals.forEach(p=>{
            ctx.save();
            ctx.translate(p.x,p.y);
            ctx.rotate(p.angle);
            ctx.fillStyle='rgba(255,182,193,0.8)';
            ctx.beginPath();
            ctx.moveTo(0,-p.size/2);
            ctx.bezierCurveTo(p.size/2,-p.size/2,p.size/2,p.size/2,0,p.size);
            ctx.bezierCurveTo(-p.size/2,p.size/2,-p.size/2,-p.size/2,0,-p.size/2);
            ctx.fill();
            ctx.restore();
            p.y+=p.speed;
            p.x+=Math.sin(p.angle)*0.5;
            p.angle+=p.spin;
            if(p.y>canvas.height){p.y=-20;p.x=Math.random()*canvas.width;}
        });
        requestAnimationFrame(draw);
    }
    draw();
    setTimeout(()=>canvas.remove(),10000);
})();
"""
    
    def _get_firework_effect(self):
        return """
(function(){
    const canvas = document.createElement('canvas');
    canvas.id = 'ha-crack-effect';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9998;';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const particles = [];
    function explode(x,y){
        const color = `hsl(${Math.random()*360},100%,50%)`;
        for(let i=0;i<30;i++){
            const angle = Math.random()*Math.PI*2;
            const speed = Math.random()*5+2;
            particles.push({x,y,vx:Math.cos(angle)*speed,vy:Math.sin(angle)*speed,life:1,color});
        }
    }
    function draw(){
        ctx.fillStyle='rgba(0,0,0,0.1)';
        ctx.fillRect(0,0,canvas.width,canvas.height);
        particles.forEach((p,i)=>{
            p.x+=p.vx;p.y+=p.vy;p.vy+=0.1;p.life-=0.02;
            if(p.life>0){
                ctx.fillStyle=p.color;
                ctx.globalAlpha=p.life;
                ctx.beginPath();
                ctx.arc(p.x,p.y,3,0,Math.PI*2);
                ctx.fill();
            }
        });
        ctx.globalAlpha=1;
        if(Math.random()<0.05)explode(Math.random()*canvas.width,Math.random()*canvas.height*0.5);
        requestAnimationFrame(draw);
    }
    explode(canvas.width/2,canvas.height/2);
    draw();
    setTimeout(()=>canvas.remove(),8000);
})();
"""
    
    def _get_snow_effect(self):
        return """
(function(){
    const canvas = document.createElement('canvas');
    canvas.id = 'ha-crack-effect';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9998;';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const flakes = [];
    for(let i=0;i<100;i++) flakes.push({x:Math.random()*canvas.width,y:Math.random()*canvas.height,r:Math.random()*3+1,speed:Math.random()*2+0.5});
    function draw(){
        ctx.clearRect(0,0,canvas.width,canvas.height);
        ctx.fillStyle='white';
        flakes.forEach(f=>{
            ctx.beginPath();
            ctx.arc(f.x,f.y,f.r,0,Math.PI*2);
            ctx.fill();
            f.y+=f.speed;
            f.x+=Math.sin(f.y*0.01)*0.5;
            if(f.y>canvas.height){f.y=0;f.x=Math.random()*canvas.width;}
        });
        requestAnimationFrame(draw);
    }
    draw();
    setTimeout(()=>canvas.remove(),10000);
})();
"""
    
    def _get_confetti_effect(self):
        return """
(function(){
    const canvas = document.createElement('canvas');
    canvas.id = 'ha-crack-effect';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9998;';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const confetti = [];
    const colors = ['#f00','#0f0','#00f','#ff0','#f0f','#0ff'];
    for(let i=0;i<100;i++) confetti.push({x:Math.random()*canvas.width,y:-20,w:Math.random()*10+5,h:Math.random()*5+3,color:colors[Math.floor(Math.random()*colors.length)],speed:Math.random()*3+2,angle:Math.random()*Math.PI*2,spin:Math.random()*0.2-0.1});
    function draw(){
        ctx.clearRect(0,0,canvas.width,canvas.height);
        confetti.forEach(c=>{
            ctx.save();
            ctx.translate(c.x,c.y);
            ctx.rotate(c.angle);
            ctx.fillStyle=c.color;
            ctx.fillRect(-c.w/2,-c.h/2,c.w,c.h);
            ctx.restore();
            c.y+=c.speed;
            c.x+=Math.sin(c.angle)*0.5;
            c.angle+=c.spin;
            if(c.y>canvas.height){c.y=-20;c.x=Math.random()*canvas.width;}
        });
        requestAnimationFrame(draw);
    }
    draw();
    setTimeout(()=>canvas.remove(),10000);
})();
"""


class HACSTool(llm.Tool):
    name = "HACS"
    description = """HACS商店工具。
⚠️ 安装前必须先用github_search搜索！禁止猜测仓库路径！
- action=github_search: 联网搜索GitHub (query="关键词") - 安装前必须先调用！
- action=install: 安装集成 (repository="owner/repo") - 只能用github_search返回的full_name
- action=list: 列出已安装的仓库
- action=info: 获取项目详情+README
- action=open_add_integration: 打开HA添加集成页面并搜索 (query="关键词")"""
    parameters = vol.Schema({
        vol.Required("action"): str,
        vol.Optional("repository", default=""): str,
        vol.Optional("query", default=""): str,
        vol.Optional("category", default="integration"): str,
    })

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> JsonObjectType:
        action = tool_input.tool_args.get("action", "")
        repository = tool_input.tool_args.get("repository", "")
        query = tool_input.tool_args.get("query", "")
        category = tool_input.tool_args.get("category", "integration")
        
        if repository:
            import re
            match = re.search(r'github\.com/([^/]+/[^/]+)', repository)
            if match:
                repository = match.group(1).rstrip('/')
            repository = repository.split('?')[0].split('#')[0].rstrip('/')
        
        try:
            hacs_data = hass.data.get("hacs")
            if not hacs_data:
                return {"success": False, "error": "HACS未安装"}
            
            if action == "list":
                repos = []
                for repo in hacs_data.repositories.list_all:
                    if repo.data.installed:
                        info = {"name": repo.data.name, "full_name": repo.data.full_name, "installed": repo.data.installed_version}
                        latest = repo.data.last_version or repo.data.last_commit
                        if latest and latest != repo.data.installed_version:
                            info["latest"] = latest
                            info["update_available"] = True
                        repos.append(info)
                return {"success": True, "total": len(repos), "repositories": repos}
            
            elif action == "search":
                if not query:
                    return {"success": False, "error": "需要搜索词 (query)"}
                results = []
                query_lower = query.lower()
                for repo in hacs_data.repositories.list_all:
                    if query_lower in repo.data.name.lower() or query_lower in (repo.data.description or "").lower() or query_lower in " ".join(repo.data.topics or []).lower():
                        results.append({
                            "name": repo.data.name,
                            "full_name": repo.data.full_name,
                            "description": repo.data.description[:200] if repo.data.description else "",
                            "installed": repo.data.installed,
                            "stars": repo.data.stargazers_count,
                        })
                        if len(results) >= 20:
                            break
                return {"success": True, "results": results}
            
            elif action == "github_search":
                if not query:
                    return {"success": False, "error": "需要搜索词 (query)"}
                
                import aiohttp
                from urllib.parse import quote
                results = []
                
                async with aiohttp.ClientSession() as session:
                    for search_query in [query, f"{query} home assistant", f"{query} hass"]:
                        async with session.get(
                            f"https://api.github.com/search/repositories?q={quote(search_query)}&sort=stars&per_page=10",
                            headers={"Accept": "application/vnd.github.v3+json"}
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                for item in data.get("items", []):
                                    full_name = item.get("full_name")
                                    if full_name and not any(r["full_name"] == full_name for r in results):
                                        results.append({
                                            "name": item.get("name"),
                                            "full_name": full_name,
                                            "description": item.get("description", "")[:150] if item.get("description") else "",
                                            "stars": item.get("stargazers_count"),
                                        })
                                        if len(results) >= 15:
                                            break
                        if len(results) >= 15:
                            break
                
                results.sort(key=lambda x: x.get("stars", 0), reverse=True)
                return {"success": True, "results": results[:15], "hint": "请使用full_name进行安装"}
            
            elif action == "info":
                if not repository or "/" not in repository:
                    return {"success": False, "error": "需要GitHub仓库路径，格式: owner/repo"}
                
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://api.github.com/repos/{repository}") as resp:
                        if resp.status != 200:
                            return {"success": False, "error": f"GitHub API 错误: {resp.status}"}
                        repo_data = await resp.json()
                    
                    async with session.get(f"https://api.github.com/repos/{repository}/readme", headers={"Accept": "application/vnd.github.raw"}) as resp:
                        readme = ""
                        if resp.status == 200:
                            readme_raw = await resp.text()
                            readme = readme_raw[:2000] if len(readme_raw) > 2000 else readme_raw
                
                return {
                    "success": True,
                    "name": repo_data.get("name"),
                    "full_name": repo_data.get("full_name"),
                    "description": repo_data.get("description"),
                    "stars": repo_data.get("stargazers_count"),
                    "topics": repo_data.get("topics", []),
                    "readme": readme,
                }
            
            elif action == "install":
                if not repository or "/" not in repository:
                    return {"success": False, "error": "需要GitHub仓库路径，格式: owner/repo"}
                
                from custom_components.hacs.enums import HacsCategory
                category_map = {"integration": HacsCategory.INTEGRATION, "plugin": HacsCategory.PLUGIN, "theme": HacsCategory.THEME}
                hacs_category = category_map.get(category, HacsCategory.INTEGRATION)
                
                existing = hacs_data.repositories.get_by_full_name(repository)
                if existing and existing.data.installed:
                    await existing.async_download_repository()
                    return {"success": True, "message": f"已更新 {repository}"}
                
                await hacs_data.async_register_repository(repository, hacs_category)
                repo = hacs_data.repositories.get_by_full_name(repository)
                if repo:
                    await repo.async_download_repository()
                    domain = repo.data.domain or repo.data.name.replace("-", "_").replace(" ", "_").lower()
                    pending_js = hass.data.setdefault("ha_crack_pending_js", [])
                    js_code = f"""
(async()=>{{
    HACrack.softNavigate('/config/integrations/dashboard');
    await new Promise(r=>setTimeout(r,500));
    const addBtn = document.querySelector('ha-fab, [slot="fab"]');
    if(addBtn) addBtn.click();
    await new Promise(r=>setTimeout(r,500));
    const searchInput = document.querySelector('search-input input, ha-textfield input');
    if(searchInput) {{
        searchInput.value = {json.dumps(domain)};
        searchInput.dispatchEvent(new Event('input', {{bubbles:true}}));
    }}
}})();
"""
                    pending_js.append(js_code)
                    return {
                        "success": True, 
                        "message": f"已安装 {repository}", 
                        "domain": domain,
                        "setup_guide": f"1. 页面已跳转到集成配置\n2. 在弹出的对话框中搜索 '{domain}'\n3. 点击找到的集成卡片\n4. 按照向导填写配置信息（如IP地址、账号密码等）\n5. 点击'提交'完成配置"
                    }
                return {"success": False, "error": f"注册失败: {repository}"}
            
            elif action == "open_add_integration":
                search_query = query or ""
                pending_js = hass.data.setdefault("ha_crack_pending_js", [])
                js_code = f"""
(async()=>{{
    HACrack.softNavigate('/config/integrations/dashboard');
    await new Promise(r=>setTimeout(r,500));
    const addBtn = document.querySelector('ha-fab, [slot="fab"]');
    if(addBtn) addBtn.click();
    await new Promise(r=>setTimeout(r,500));
    const searchInput = document.querySelector('search-input input, ha-textfield input');
    if(searchInput) {{
        searchInput.value = {json.dumps(search_query)};
        searchInput.dispatchEvent(new Event('input', {{bubbles:true}}));
    }}
}})();
"""
                pending_js.append(js_code)
                return {"success": True, "message": f"已打开添加集成页面，搜索: {search_query}"}
            
            return {"success": False, "error": f"未知操作: {action}"}
        except Exception as e:
            _LOGGER.error(f"HACS tool error: {e}")
            return {"success": False, "error": str(e)}
