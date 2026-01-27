
import logging
import json
import os
import random
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from .. import conversation

_LOGGER = logging.getLogger(__name__)

CURRENT_ROLE = {"role": None, "prompt": None}

SKILLS = {
    "RolePlay": {"name": "角色扮演", "desc": "扮演特定角色", "trigger": "扮演、假装、模仿", "rules": "完全进入角色，用角色语气说话"},
    "LanguageStyle": {"name": "语言风格", "desc": "调整回复风格", "trigger": "简单点说、正式点、幽默点", "rules": "小学生风格用简单词，正式风格用书面语"},
    "DeviceControl": {"name": "设备控制", "desc": "开关灯、空调等", "trigger": "开灯、关空调、调亮度", "rules": "执行后简短确认"},
    "WeatherInfo": {"name": "天气信息", "desc": "查天气预报", "trigger": "天气怎么样、会下雨吗", "rules": "说温度和天气，有异常提醒"},
    "ReminderManage": {"name": "提醒管理", "desc": "设置查询提醒", "trigger": "提醒我、有什么提醒", "rules": "确认时间和内容"},
    "SceneControl": {"name": "场景控制", "desc": "执行预设场景", "trigger": "回家模式、睡眠模式", "rules": "执行后确认"},
    "MediaControl": {"name": "媒体控制", "desc": "播放音乐视频", "trigger": "播放、暂停、下一首", "rules": "简短确认操作"},
    "HomeStatus": {"name": "家居状态", "desc": "查设备状态", "trigger": "灯开着吗、温度多少", "rules": "直接回答状态"},
    "SmartSuggestion": {"name": "智能建议", "desc": "给出建议推荐", "trigger": "穿什么、吃什么", "rules": "给2-3个选项"}
}

_ROLEPLAY_PRESETS = None

def get_roleplay_presets():
    global _ROLEPLAY_PRESETS
    if _ROLEPLAY_PRESETS is None:
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "roleplay_presets.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                _ROLEPLAY_PRESETS = json.load(f)
        except Exception as e:
            _LOGGER.warning(f"加载角色预设失败: {e}")
            _ROLEPLAY_PRESETS = {}
    return _ROLEPLAY_PRESETS

async def async_setup_skills(hass: HomeAssistant):

    if hass.data.get("kadermanager", {}).get("_skills_registered"):
        return
    intent.async_register(hass, AISkillsIntent())
    intent.async_register(hass, RolePlayIntent())
    intent.async_register(hass, LanguageStyleIntent())
    intent.async_register(hass, WebSearchIntent())
    intent.async_register(hass, ClickIntent())
    intent.async_register(hass, QueryIntent())
    intent.async_register(hass, AIOutputIntent())
    intent.async_register(hass, SmartIntent())
    intent.async_register(hass, ExplainIntent())
    intent.async_register(hass, NotifyIntent())
    intent.async_register(hass, GlobalInjectIntent())
    intent.async_register(hass, CameraAnalyzeIntent())
    intent.async_register(hass, GenerateImageIntent())
    hass.data.setdefault("kadermanager", {})["_skills_registered"] = True
    _LOGGER.info("AI Skills Package registered (13 intents with tool chain support)")

class AISkillsIntent(intent.IntentHandler):

    intent_type = "AISkills"
    description = "AI技能包使用指南，包含角色扮演、语言风格、设备控制、日历查询、天气、提醒、场景、媒体、状态、建议等技能"
    slot_schema = {}
    async def async_handle(self, intent_obj: intent.Intent):
        doc = "【AI技能包】"
        for sid, s in SKILLS.items():
            doc += f"{s['name']}({sid}):{s['desc']}，触发词:{s['trigger']}，规则:{s['rules']}。"
        doc += "【通用规则】用小学三年级能听懂的话回复，不啰嗦，不确定说不知道。"
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_speech(doc)
        return response

class RolePlayIntent(intent.IntentHandler):

    intent_type = "RolePlay"
    description = "角色扮演技能。重要：此工具只在用户明确说'扮演XX'时调用一次！设置角色后，后续对话直接用角色语气回复，绝对不要再调用此工具！"
    slot_schema = {vol.Optional("role", description="要扮演的角色"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        global CURRENT_ROLE
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        role = slots.get("role", {}).get("value", "").lower().strip()
        text_input = (intent_obj.text_input or "").lower().strip()
        _LOGGER.info(f"RolePlay: role={role}, text_input={text_input}, CURRENT_ROLE={CURRENT_ROLE}")
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        exit_keywords = ["退出", "取消", "停止", "恢复", "不要", "不扮演", "结束", "正常"]
        if any(kw in text_input for kw in exit_keywords) or any(kw in role for kw in exit_keywords):
            CURRENT_ROLE = {"role": None, "prompt": None}
            hass.data["ha_crack_roleplay"] = {"role": None, "prompt": None}
            msg = "好的，已退出角色扮演，恢复正常对话。"
            response.async_set_speech(msg)
            return response
        
        search_text = role or text_input
        for prefix in ["你是", "扮演", "切换", "变成", "当", "做"]:
            if prefix in search_text:
                search_text = search_text.split(prefix)[-1].strip()
                break
        
        matched_preset = None
        for key, preset in get_roleplay_presets().items():
            aliases_lower = [a.lower() for a in preset["aliases"]]
            name_lower = preset["name"].lower()
            if search_text in aliases_lower or search_text == key or search_text == name_lower:
                matched_preset = preset
                break
            if any(alias in search_text for alias in aliases_lower) or search_text in name_lower or name_lower in search_text:
                matched_preset = preset
                break
        
        if matched_preset:
            CURRENT_ROLE = {"role": matched_preset["name"], "prompt": matched_preset["prompt"]}
            greeting = matched_preset.get("greeting", f"好的，我现在是{matched_preset['name']}了！")
            msg = greeting
        elif role and role in ["随机", "random", "任意", "随便"]:
            random_preset = random.choice(list(get_roleplay_presets().values()))
            CURRENT_ROLE = {"role": random_preset["name"], "prompt": random_preset["prompt"]}
            greeting = random_preset.get("greeting", f"好的，我现在是{random_preset['name']}了！")
            msg = greeting
        elif role:
            CURRENT_ROLE = {"role": role, "prompt": f"你现在扮演{role}，用这个角色的语气和风格说话"}
            msg = f"好的，我现在是{role}了！"
        else:
            random_preset = random.choice(list(get_roleplay_presets().values()))
            CURRENT_ROLE = {"role": random_preset["name"], "prompt": random_preset["prompt"]}
            greeting = random_preset.get("greeting", f"好的，我现在是{random_preset['name']}了！")
            msg = greeting
        
        hass.data["ha_crack_roleplay"] = CURRENT_ROLE.copy()
        _LOGGER.info(f"RolePlay set to hass.data: {hass.data['ha_crack_roleplay']}")
        response.async_set_speech(msg)
        return response

_LANGUAGE_STYLES = None

def get_language_styles():
    global _LANGUAGE_STYLES
    if _LANGUAGE_STYLES is None:
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "language_styles.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                _LANGUAGE_STYLES = json.load(f)
        except Exception as e:
            _LOGGER.warning(f"加载语言风格失败: {e}")
            _LANGUAGE_STYLES = {}
    return _LANGUAGE_STYLES

class LanguageStyleIntent(intent.IntentHandler):

    intent_type = "LanguageStyle"
    description = "语言风格技能，调整回复风格"
    slot_schema = {vol.Optional("style", description="语言风格"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        global CURRENT_ROLE
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        style = slots.get("style", {}).get("value", "").strip()
        text_input = (intent_obj.text_input or "").strip()
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        search_text = style or text_input
        matched_style = None
        for key, desc in get_language_styles().items():
            if key in search_text:
                matched_style = (key, desc)
                break
        
        if matched_style:
            CURRENT_ROLE = {"role": f"{matched_style[0]}风格", "prompt": f"用{matched_style[1]}回复"}
            hass.data["ha_crack_roleplay"] = CURRENT_ROLE.copy()
            msg = f"好的，接下来我会{matched_style[1]}回复你。"
        else:
            msg = "你想要什么风格？支持：喜怒哀乐、萌呆傲娇、燃丧甜苦等60+种情绪风格"
        response.async_set_speech(msg)
        return response

def _fire_thought_event(hass, thought: str):

    hass.data["ha_crack_current_thought"] = thought
    hass.bus.async_fire("ha_crack_thought", {"thought": thought})
    _LOGGER.info(f"Intent思考: {thought[:100]}")


class WebSearchIntent(intent.IntentHandler):

    intent_type = "WebSearch"
    description = "联网搜索，获取实时信息。返回完整提取内容，AI需要分析并总结给用户。"
    slot_schema = {vol.Optional("query", description="搜索内容"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        query = slots.get("query", {}).get("value", "") or intent_obj.text_input
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        _fire_thought_event(hass, f"用户想搜索'{query}'，我使用WebSearch工具联网获取实时信息")
        
        try:
            from ..services.web_search import WebSearch
            from ..utils.text_compressor import TextCompressor
            
            async with WebSearch() as ws:
                results = await ws.search(query, 5)
                if results:
                    parts = []
                    total_len = 0
                    max_total = 8000
                    
                    for r in results:
                        if total_len >= max_total:
                            break
                        
                        content = r.content or ""
                        if not content and r.snippet:
                            content = r.snippet
                        
                        if content:
                            remaining = max_total - total_len
                            per_result_max = min(2500, remaining)
                            
                            if len(content) > per_result_max:
                                compressor = TextCompressor(target_length=per_result_max)
                                compressed = compressor.compress(content)
                                content = compressed.text
                            
                            parts.append(f"【{r.title}】\n{content}")
                            total_len += len(content)
                    
                    if parts:
                        text = f"搜索'{query}'结果如下，请仔细阅读并根据内容回答用户问题：\n\n" + "\n\n---\n\n".join(parts)
                        _LOGGER.info(f"WebSearchIntent 返回 {len(parts)} 条结果，总长度 {len(text)}")
                    else:
                        text = f"搜索'{query}'找到{len(results)}条结果，但未能提取有效内容。"
                    response.async_set_speech(text)
                else:
                    response.async_set_speech(f"未找到'{query}'的相关结果")
        except Exception as e:
            _LOGGER.error(f"WebSearchIntent error: {e}")
            response.async_set_speech(f"搜索失败: {e}")
        return response

class ClickIntent(intent.IntentHandler):

    intent_type = "Click"
    description = "点击链接或按钮"
    slot_schema = {vol.Optional("target", description="点击目标"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        target = slots.get("target", {}).get("value", "")
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        _fire_thought_event(hass, f"用户想点击'{target}'，我使用FrontendControl工具执行点击操作")
        
        if target:
            hass.data.setdefault("ha_crack_click", {})["target"] = target
            response.async_set_speech(f"正在点击: {target}")
        else:
            response.async_set_speech("请指定要点击的目标")
        return response

class QueryIntent(intent.IntentHandler):

    intent_type = "Query"
    description = "查询设备状态或信息"
    slot_schema = {vol.Optional("entity", description="查询实体"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        entity = slots.get("entity", {}).get("value", "")
        
        _fire_thought_event(hass, f"用户想查询'{entity}'的状态，我使用EntityQuery工具获取实体信息")
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        if entity:
            state = hass.states.get(entity)
            if state:
                response.async_set_speech(f"{state.name}: {state.state}")
            else:
                response.async_set_speech(f"未找到实体: {entity}")
        else:
            response.async_set_speech("请指定要查询的实体")
        return response

class AIOutputIntent(intent.IntentHandler):

    intent_type = "AIOutput"
    description = "控制AI输出方式：简洁/详细/列表/代码"
    slot_schema = {vol.Optional("mode", description="输出模式"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        mode = slots.get("mode", {}).get("value", "normal")
        
        _fire_thought_event(hass, f"用户想切换输出模式为'{mode}'，我调整回复风格")
        
        hass.data.setdefault("ha_crack_output", {})["mode"] = mode
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        modes = {"brief": "简洁", "detailed": "详细", "list": "列表", "code": "代码"}
        response.async_set_speech(f"已切换到{modes.get(mode, mode)}输出模式")
        return response

class SmartIntent(intent.IntentHandler):

    intent_type = "Smart"
    description = "智能判断并执行操作"
    slot_schema = {vol.Optional("action", description="操作"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        text = intent_obj.text_input or ""
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        if any(k in text for k in ["搜索", "查", "找"]):
            _fire_thought_event(hass, f"分析用户意图：检测到搜索需求，使用WebSearch工具链")
            response.async_set_speech("检测到搜索意图，正在联网...")
        elif any(k in text for k in ["开", "关", "调"]):
            _fire_thought_event(hass, f"分析用户意图：检测到设备控制需求，使用SmartDiscovery→ServiceCall工具链")
            response.async_set_speech("检测到控制意图，正在执行...")
        elif any(k in text for k in ["什么", "怎么", "为什么"]):
            _fire_thought_event(hass, f"分析用户意图：检测到问答需求，进行深度思考")
            response.async_set_speech("检测到问答意图，正在思考...")
        else:
            _fire_thought_event(hass, f"分析用户意图：'{text}'，智能选择最佳工具链")
            response.async_set_speech("正在智能分析您的需求...")
        return response

class ExplainIntent(intent.IntentHandler):

    intent_type = "Explain"
    description = "解释说明概念或操作"
    slot_schema = {vol.Optional("topic", description="主题"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        topic = slots.get("topic", {}).get("value", "") or intent_obj.text_input
        
        _fire_thought_event(hass, f"用户想了解'{topic}'，我需要用通俗易懂的方式解释")
        
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_speech(f"让我来解释一下'{topic}'...")
        return response

class NotifyIntent(intent.IntentHandler):

    intent_type = "Notify"
    description = "发送通知到设备"
    slot_schema = {
        vol.Optional("message", description="通知内容"): str,
        vol.Optional("target", description="通知目标"): str
    }
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        message = slots.get("message", {}).get("value", "")
        target = slots.get("target", {}).get("value", "")
        
        _fire_thought_event(hass, f"用户想发送通知'{message}'，我使用Notify工具发送持久通知")
        
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        if message:
            try:
                await hass.services.async_call("notify", "persistent_notification", {"message": message, "title": "AI通知"})
                response.async_set_speech(f"已发送通知: {message}")
            except Exception as e:
                response.async_set_speech(f"发送通知失败: {e}")
        else:
            response.async_set_speech("请指定通知内容")
        return response

class GlobalInjectIntent(intent.IntentHandler):

    intent_type = "GlobalInject"
    description = "注入全局上下文到所有对话"
    slot_schema = {vol.Optional("context", description="注入内容"): str}
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        context = slots.get("context", {}).get("value", "")
        
        _fire_thought_event(hass, f"用户想设置全局上下文'{context[:50]}'，我将其注入到后续所有对话")
        
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        if context:
            hass.data.setdefault("ha_crack_global", {})["inject"] = context
            response.async_set_speech(f"已注入全局上下文: {context[:50]}...")
        else:
            current = hass.data.get("ha_crack_global", {}).get("inject", "")
            response.async_set_speech(f"当前全局上下文: {current[:50] if current else '无'}")
        return response


class CameraAnalyzeIntent(intent.IntentHandler):

    intent_type = "CameraAnalyze"
    description = "分析摄像头画面。当用户说'摄像头'、'看看摄像头'、'分析画面'、'摄像头里有什么'时调用此工具。分析结果需重点描述画面细节，100-400字，包括：人物位置动作、物品摆放、光线环境、异常情况等"
    slot_schema = {
        vol.Optional("camera", description="摄像头实体ID或名称，如camera.xxx或'客厅摄像头'"): str,
        vol.Optional("question", description="要问的问题，如'有人吗'、'门关了吗'"): str
    }
    
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        camera = slots.get("camera", {}).get("value", "")
        question = slots.get("question", {}).get("value", "请详细描述画面内容，包括：1)人物位置和动作 2)物品摆放 3)光线环境 4)任何异常情况。100-400字")
        
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        camera_entities = [s.entity_id for s in hass.states.async_all("camera")]
        
        if not camera_entities:
            response.async_set_speech("未找到任何摄像头设备")
            return response
        
        target_camera = None
        if camera:
            if camera.startswith("camera."):
                target_camera = camera if camera in camera_entities else None
            else:
                for cam_id in camera_entities:
                    state = hass.states.get(cam_id)
                    name = state.attributes.get("friendly_name", "") if state else ""
                    if camera.lower() in name.lower() or camera.lower() in cam_id.lower():
                        target_camera = cam_id
                        break
        
        if not target_camera:
            target_camera = camera_entities[0]
        
        cam_state = hass.states.get(target_camera)
        cam_name = cam_state.attributes.get("friendly_name", target_camera) if cam_state else target_camera
        
        _fire_thought_event(hass, f"用户想查看摄像头'{cam_name}'，我调用ai_hub.analyze_image分析画面")
        
        try:
            result = await hass.services.async_call(
                "ai_hub", "analyze_image",
                {
                    "message": question,
                    "image_entity": target_camera,
                    "model": "glm-4.6v-flash",
                    "temperature": 0.5,
                    "max_tokens": 1024,
                    "stream": False
                },
                blocking=True,
                return_response=True
            )
            
            if result and isinstance(result, dict):
                analysis = result.get("response", result.get("text", str(result)))
                response.async_set_speech(f"【{cam_name}】{analysis}")
            else:
                response.async_set_speech(f"已请求分析{cam_name}，请稍候查看结果")
        except Exception as e:
            _LOGGER.error(f"CameraAnalyzeIntent error: {e}")
            response.async_set_speech(f"分析摄像头失败: {e}")
        
        return response

class GenerateImageIntent(intent.IntentHandler):

    intent_type = "GenerateImage"
    description = "生成图片。当用户说'生成图片'、'画一张'、'创建图片'、'生成一张XX图'时调用此工具"
    slot_schema = {
        vol.Required("prompt", description="图片描述，如'一只可爱的小猫'"): str,
        vol.Optional("size", description="图片尺寸，如1024x1024"): str
    }
    
    async def async_handle(self, intent_obj: intent.Intent):
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        prompt = slots.get("prompt", {}).get("value", "")
        size = slots.get("size", {}).get("value", "1024x1024")
        
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        
        if not prompt:
            response.async_set_speech("请描述您想生成的图片内容")
            return response
        
        _fire_thought_event(hass, f"用户想生成图片'{prompt}'，我调用ai_hub.generate_image")
        
        try:
            result = await hass.services.async_call(
                "ai_hub", "generate_image",
                {"prompt": prompt, "size": size},
                blocking=True, return_response=True
            )
            
            if result and isinstance(result, dict):
                if result.get("success"):
                    image_url = result.get("image_url", "")
                    if image_url:
                        response.async_set_speech(f"图片已生成：\n![{prompt}]({image_url})")
                    else:
                        response.async_set_speech("图片生成成功，但无法获取URL")
                else:
                    response.async_set_speech(f"生成失败: {result.get('error', '未知错误')}")
            else:
                response.async_set_speech("图片生成请求已发送")
        except Exception as e:
            _LOGGER.error(f"GenerateImageIntent error: {e}")
            response.async_set_speech(f"生成图片失败: {e}")
        
        return response


def get_skill_rules(skill_id: str) -> dict:
    return SKILLS.get(skill_id, {})

def get_all_skills() -> dict:
    return SKILLS
