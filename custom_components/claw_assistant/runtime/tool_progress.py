def tool_progress_line(name: str, args: dict | None, lang: str = "en") -> str:
    args = args or {}
    desc = _tool_desc(name, args, lang)
    return f"┊ *{desc}*\n"


def _esc(text: str) -> str:
    for ch in ("*", "_", "`", "{", "}", "[", "]", "(", ")", "#", "~", "|", "\\"):
        text = text.replace(ch, "")
    return text


def _tool_desc(name: str, a: dict, lang: str) -> str:
    zh = lang.startswith("zh")
    e = _esc

    if name == "GetLiveContext":
        return "⚡️ 我正在思考中..." if zh else "⚡️ I'm thinking about it...."
    if name == "BatchControl":
        ids = a.get("entity_ids", [])
        raw_act = str(a.get("action", ""))
        cnt = len(ids) if isinstance(ids, list) else "?"
        if zh:
            act_zh = {"turn_on": "打开", "turn_off": "关闭", "toggle": "切换"}.get(raw_act, e(raw_act))
            return f"🔗 正在{act_zh} {cnt} 个设备..."
        return f"🔗 Batch {e(raw_act)} x{cnt} devices..."
    if name == "ServiceCall":
        d = e(str(a.get("domain", "")))
        s = e(str(a.get("service", "")))
        if zh:
            return f"🔗 正在调用 {d}.{s}..." if d else "🔗 正在调用服务..."
        return f"🔗 Calling {d}.{s}..." if d else "🔗 Calling service..."
    if name == "EntityQuery":
        eid = e(str(a.get("entity_id", "")))[:25]
        if zh:
            return f"💫 正在查询 {eid}..." if eid else "💫 正在查询设备..."
        return f"💫 Querying {eid}..." if eid else "💫 Querying entity..."
    if name == "WebSearch":
        q = e(str(a.get("query", "")))[:20]
        if zh:
            return f"🌎 正在搜索: {q}..." if q else "🌎 正在联网搜索..."
        return f"🌎 Searching: {q}..." if q else "🌎 Web search..."
    if name == "StockQuery":
        c = e(str(a.get("codes", "")))[:15]
        if zh:
            return f"🪙 正在查询行情: {c}..." if c else "🪙 正在查询行情..."
        return f"🪙 {c}..." if c else "🪙 Stock query..."
    if name == "UrlFetch":
        u = e(str(a.get("url", "")))[:30]
        if zh:
            return f"💫 正在抓取: {u}..." if u else "💫 正在抓取网页..."
        return f"💫 Fetching: {u}..." if u else "💫 Fetching URL..."
    if name == "HistoryQuery":
        eid = e(str(a.get("entity_id", "")))[:20]
        h = a.get("hours", 24)
        if zh:
            return f"💫 正在查询 {eid} 近{h}小时历史..." if eid else "💫 正在查询历史数据..."
        return f"💫 History: {eid} {h}h..." if eid else "💫 History {h}h..."
    if name == "ExecutePython":
        return "🧬 正在执行代码..." if zh else "🧬 Executing code..."
    if name == "CameraAnalyze":
        cam = e(str(a.get("camera_entity", "")))[:20]
        mode = str(a.get("mode", "snapshot")).lower()
        if mode == "analyze":
            return (f"📷 正在分析摄像头: {cam}..." if cam else "📷 正在分析摄像头...") if zh else (f"📷 Analyzing: {cam}..." if cam else "📷 Analyzing camera...")
        return (f"📷 正在获取摄像头: {cam}..." if cam else "📷 正在查询摄像头...") if zh else (f"📷 Camera: {cam}..." if cam else "📷 Camera query...")
    if name == "ThinkContinue":
        return "⚡️ 正在思考中..." if zh else "⚡️ Deep reasoning..."
    if name == "ParallelToolCall":
        tools = a.get("tools", [])
        cnt = len(tools) if isinstance(tools, list) else "?"
        return f"⚡️ 正在并行执行 {cnt} 个任务..." if zh else f"⚡️ Parallel x{cnt}..."
    if name == "SmartDiscovery":
        hint = e(str(a.get("area", "") or a.get("domain", "")))[:15]
        if zh:
            return f"💫 正在搜索: {hint}..." if hint else "💫 正在智能搜索..."
        return f"💫 Discovering: {hint}..." if hint else "💫 Smart discovery..."
    if name == "AreaDevices":
        area = e(str(a.get("area", "")))[:15]
        if zh:
            return f"💫 正在获取{area}的设备..." if area else "💫 正在获取区域设备..."
        return f"💫 Area devices: {area}..." if area else "💫 Listing area devices..."
    if name == "Automation":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 正在{act}自动化..." if act else "🔗 正在管理自动化..."
        return f"🔗 Automation: {act}..." if act else "🔗 Managing automation..."
    if name == "Script":
        act = e(str(a.get("action", "")))
        sid = e(str(a.get("script_id", "") or a.get("entity_id", "")))[:20]
        if zh:
            _SC_ZH = {
                "list": "列举", "get": "查询", "create": "创建",
                "update": "更新", "delete": "删除", "run": "执行",
            }
            act_zh = _SC_ZH.get(act, act)
            icon = "💫" if act in ("list", "get") else "🔗"
            if sid and act:
                return f"{icon} 正在{act_zh}脚本: {sid}..."
            if act:
                return f"{icon} 正在{act_zh}脚本..."
            return "🔗 正在管理脚本..."
        icon = "💫" if act in ("list", "get") else "🔗"
        if sid and act:
            return f"{icon} Script {act}: {sid}..."
        if act:
            return f"{icon} Script {act}..."
        return "🔗 Managing script..."
    if name == "ListServices":
        d = e(str(a.get("domain", "")))
        if zh:
            return f"💫 正在列举 {d} 服务..." if d else "💫 正在列举可用服务..."
        return f"💫 Services: {d}..." if d else "💫 Listing services..."
    if name == "ScriptExecute":
        sid = e(str(a.get("script_id", "")))[:20]
        if zh:
            return f"🔗 正在执行脚本: {sid}..." if sid else "🔗 正在执行脚本..."
        return f"🔗 Running script: {sid}..." if sid else "🔗 Running script..."
    if name == "Notify":
        t = e(str(a.get("target", "")))[:20]
        if zh:
            return f"✉️ 正在发送通知: {t}..." if t else "✉️ 正在发送通知..."
        return f"✉️ Sending notification: {t}..." if t else "✉️ Sending notification..."
    if name == "ConfigEntries":
        act = str(a.get("action", "")).strip()
        params = a.get("params", {}) if isinstance(a.get("params"), dict) else {}
        handler = e(str(params.get("handler", "")))[:15]
        _CE_ZH = {
            "config_entries/flow/init": f"正在安装集成 {handler}" if handler else "正在初始化安装流程",
            "config_entries/flow/configure": "正在提交配置",
            "config_entries/flow/abort": "正在取消安装流程",
            "config_entries/get": "正在查询已安装集成",
            "config_entries/get_single": "正在查询集成详情",
            "config_entries/delete": "正在删除集成",
            "config_entries/reload": "正在重载集成",
            "config_entries/update": "正在更新集成设置",
            "config_entries/disable": "正在禁用/启用集成",
            "config_entries/options/init": "正在打开选项配置",
            "config_entries/options/configure": "正在提交选项",
            "config_entries/subentries/flow/init": "正在添加子条目",
            "config_entries/subentries/flow/configure": "正在配置子条目",
            "config_entries/flow_handlers": "正在查询可用集成",
        }
        _CE_EN = {
            "config_entries/flow/init": f"Installing {handler}" if handler else "Initializing setup flow",
            "config_entries/flow/configure": "Submitting config",
            "config_entries/flow/abort": "Aborting flow",
            "config_entries/get": "Checking installed integrations",
            "config_entries/get_single": "Checking entry details",
            "config_entries/delete": "Deleting integration",
            "config_entries/reload": "Reloading integration",
            "config_entries/update": "Updating integration",
            "config_entries/disable": "Toggling integration",
            "config_entries/options/init": "Opening options",
            "config_entries/options/configure": "Submitting options",
            "config_entries/subentries/flow/init": "Adding subentry",
            "config_entries/subentries/flow/configure": "Configuring subentry",
            "config_entries/flow_handlers": "Listing available integrations",
        }
        if zh:
            desc = _CE_ZH.get(act, f"管理集成: {e(act)[:15]}")
            return f"🔗 {desc}..."
        desc = _CE_EN.get(act, f"Config: {e(act)[:15]}")
        return f"🔗 {desc}..."
    if name == "HAControl":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 正在执行: {act}..." if act else "🔗 正在执行系统操作..."
        return f"🔗 HA control: {act}..." if act else "🔗 System operation..."
    if name == "HACS":
        act = e(str(a.get("action", "")))
        if zh:
            return f"📦 HACS: {act}..." if act else "📦 正在管理 HACS..."
        return f"📦 HACS: {act}..." if act else "📦 Managing HACS..."
    if name == "SystemControl":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 系统控制: {act}..." if act else "🔗 正在执行系统控制..."
        return f"🔗 System: {act}..." if act else "🔗 System control..."
    if name == "ConversationMemory":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🧬 记忆: {act}..." if act else "🧬 正在处理记忆..."
        return f"🧬 Memory: {act}..." if act else "🧬 Managing memory..."
    if name == "GetConversationHistory":
        return "💫 正在获取对话历史..." if zh else "💫 Loading conversation history..."
    if name == "InstallSkill":
        n = e(str(a.get("name", "")))[:20]
        if zh:
            return f"📦 正在安装技能: {n}..." if n else "📦 正在安装技能..."
        return f"📦 Installing skill: {n}..." if n else "📦 Installing skill..."
    if name == "ListInstalledSkills":
        return "📦 正在列举已安装技能..." if zh else "📦 Listing installed skills..."
    if name == "GetSkillIndex":
        kw = e(str(a.get("keyword", "")))[:20]
        if zh:
            return f"💫 正在检索技能: {kw}..." if kw else "💫 正在列举技能索引..."
        return f"💫 Skill index: {kw}..." if kw else "💫 Listing skill index..."
    if name == "GetInstalledSkill":
        n = e(str(a.get("name", "")))[:20]
        if zh:
            return f"📦 正在读取技能: {n}..." if n else "📦 正在读取技能..."
        return f"📦 Reading skill: {n}..." if n else "📦 Reading skill..."
    if name == "HomeAssistantGuide":
        act = e(str(a.get("action", "")))
        if zh:
            return f"💫 正在查阅指南: {act}..." if act else "💫 正在查阅 HA 指南..."
        return f"💫 HA guide: {act}..." if act else "💫 Reading HA guide..."
    if name == "SetMasterPrompt":
        return "🔗 正在设置主提示词..." if zh else "🔗 Updating master prompt..."
    if name == "GetMasterPrompt":
        return "💫 正在读取主提示词..." if zh else "💫 Reading master prompt..."
    if name == "ListWorkspaceDocs":
        return "💫 正在列举工作区文档..." if zh else "💫 Listing workspace docs..."
    if name == "GetWorkspaceDoc":
        n = e(str(a.get("name", "")))[:20]
        if zh:
            return f"💫 正在读取文档: {n}..." if n else "💫 正在读取文档..."
        return f"💫 Reading doc: {n}..." if n else "💫 Reading document..."
    if name == "SetWorkspaceDoc":
        n = e(str(a.get("name", "")))[:20]
        if zh:
            return f"🔗 正在写入文档: {n}..." if n else "🔗 正在写入文档..."
        return f"🔗 Writing doc: {n}..." if n else "🔗 Writing document..."
    if name == "HeartbeatManager":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 心跳任务: {act}..." if act else "🔗 正在管理心跳任务..."
        return f"🔗 Heartbeat: {act}..." if act else "🔗 Managing heartbeat..."
    if name == "CustomEntityManager":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 自定义实体: {act}..." if act else "🔗 正在管理自定义实体..."
        return f"🔗 Custom entity: {act}..." if act else "🔗 Managing custom entity..."
    if name == "HelperManager":
        act = e(str(a.get("action", "")))
        if zh:
            return f"🔗 辅助实体: {act}..." if act else "🔗 正在管理辅助实体..."
        return f"🔗 Helper: {act}..." if act else "🔗 Managing helper..."
    if name == "GetSystemIndex":
        return "💫 正在获取系统概况..." if zh else "💫 Loading system index..."
    if name == "SetConversationState":
        reason = e(str(a.get("reason", "")))[:20]
        if zh:
            return f"🔗 正在设置状态: {reason}..." if reason else "🔗 正在设置对话状态..."
        return f"🔗 Setting state: {reason}..." if reason else "🔗 Setting conversation state..."
    if name == "AgentHandoff":
        d = e(str(a.get("direction", "")))
        if zh:
            return f"🔗 正在切换 Agent: {d}..." if d else "🔗 正在切换 Agent..."
        return f"🔗 Agent handoff: {d}..." if d else "🔗 Agent handoff..."
    if name == "NextAgentHandoff":
        return "🔗 正在切换至下一个 Agent..." if zh else "🔗 Handing off to next agent..."
    if name == "ValidateService":
        d = e(str(a.get("domain", "")))
        s = e(str(a.get("service", "")))
        if zh:
            return f"💫 正在验证 {d}.{s}..." if d else "💫 正在验证服务..."
        return f"💫 Validating: {d}.{s}..." if d else "💫 Validating service..."
    if name == "ServiceHelp":
        d = e(str(a.get("domain", "")))
        if zh:
            return f"💫 正在查询 {d} 帮助..." if d else "💫 正在查询服务帮助..."
        return f"💫 Help: {d}..." if d else "💫 Service help..."
    if name == "ConfigFile":
        act = e(str(a.get("action", "")))
        p = e(str(a.get("path", "")))[:20]
        if zh:
            return f"🔗 配置文件: {act} {p}..." if act else "🔗 正在操作配置文件..."
        return f"🔗 Config: {act} {p}..." if act else "🔗 Config file..."
    if name == "DeleteSkill":
        n = e(str(a.get("name", "")))[:20]
        if zh:
            return f"🔗 正在删除技能: {n}..." if n else "🔗 正在删除技能..."
        return f"🔗 Deleting skill: {n}..." if n else "🔗 Deleting skill..."
    if name == "UpsertGuideDoc":
        return "🔗 正在更新指南文档..." if zh else "🔗 Updating guide doc..."
    if name == "DeleteGuideDoc":
        return "🔗 正在删除指南文档..." if zh else "🔗 Deleting guide doc..."
    if name == "GetSelfChangelog":
        return "💫 正在查看变更日志..." if zh else "💫 Reading changelog..."
    if name == "ReviewSelfSkills":
        return "💫 正在自我审查技能..." if zh else "💫 Reviewing skills..."
    if name == "ProposeSelfEdit":
        return "🔗 正在提交自编辑提案..." if zh else "🔗 Proposing self-edit..."
    if name == "ListProposals":
        return "💫 正在列举待审提案..." if zh else "💫 Listing proposals..."
    if name == "GetProposal":
        return "💫 正在读取提案详情..." if zh else "💫 Reading proposal..."
    if name == "DiscardProposal":
        return "🔗 正在丢弃提案..." if zh else "🔗 Discarding proposal..."
    if name == "ApplyProposal":
        return "🔗 正在应用提案..." if zh else "🔗 Applying proposal..."
    if name == "IntentCall":
        act = e(str(a.get("action", "")))
        it = e(str(a.get("intent_type", "")))[:20]
        if zh:
            if act == "list":
                return "💫 正在列举意图处理器..."
            return f"🔗 正在调用意图: {it}..." if it else "🔗 正在调用意图..."
        if act == "list":
            return "💫 Listing intents..."
        return f"🔗 Intent: {it}..." if it else "🔗 Calling intent..."
    if name == "Registry":
        reg = e(str(a.get("registry", "")))
        act = e(str(a.get("action", "")))
        if zh:
            _REG_ZH = {
                "area": "区域", "floor": "楼层", "label": "标签",
                "category": "分类", "entity": "实体",
            }
            _ACT_ZH = {
                "list": "列举", "get": "查询", "create": "创建",
                "update": "更新", "delete": "删除", "remove": "移除",
                "rename": "重命名",
            }
            reg_zh = _REG_ZH.get(reg, reg)
            act_zh = _ACT_ZH.get(act, act)
            icon = "💫" if act in ("list", "get") else "🔗"
            if reg and act:
                return f"{icon} 正在{act_zh}{reg_zh}..."
            return f"{icon} 正在操作注册表..."
        icon = "💫" if act in ("list", "get") else "🔗"
        if reg and act:
            return f"{icon} Registry {reg}.{act}..."
        return f"{icon} Registry op..."

    if name == "DashboardCard":
        act = e(str(a.get("action", "")))
        _DC_ZH = {
            "check_dependency": "检查依赖",
            "list_dashboards": "列举仪表盘",
            "get_dashboard": "查看仪表盘",
            "add_view": "添加视图",
            "add_card": "添加卡片",
            "update_card": "更新卡片",
            "remove_card": "删除卡片",
        }
        _DC_EN = {
            "check_dependency": "Checking dependency",
            "list_dashboards": "Listing dashboards",
            "get_dashboard": "Reading dashboard",
            "add_view": "Adding view",
            "add_card": "Adding card",
            "update_card": "Updating card",
            "remove_card": "Removing card",
        }
        if zh:
            desc = _DC_ZH.get(act, f"仪表盘操作: {act}")
            return f"🎨 正在{desc}..."
        desc = _DC_EN.get(act, f"Dashboard: {act}")
        return f"🎨 {desc}..."
    if name.startswith("Hass"):
        eid = e(str(a.get("name", a.get("entity_id", ""))))[:22]
        tag = name.replace("Hass", "")
        _HA_ZH = {
            "TurnOn": "打开", "TurnOff": "关闭", "Toggle": "切换",
            "GetState": "获取状态", "Nevermind": "取消",
            "SetPosition": "调整位置", "StopMoving": "停止移动",
            "StartTimer": "设置定时器", "CancelTimer": "取消定时器",
            "CancelAllTimers": "取消所有定时器",
            "IncreaseTimer": "延长定时器", "DecreaseTimer": "缩短定时器",
            "PauseTimer": "暂停定时器", "UnpauseTimer": "恢复定时器",
            "TimerStatus": "查询定时器状态",
            "GetCurrentDate": "获取日期", "GetCurrentTime": "获取时间",
            "Respond": "回复", "Broadcast": "广播",
            "ClimateGetTemperature": "获取温度", "ClimateSetTemperature": "设置温度",
            "LightSet": "调整灯光",
            "MediaPause": "暂停播放", "MediaUnpause": "恢复播放",
            "MediaNext": "下一曲", "MediaPrevious": "上一曲",
            "MediaPlayerMute": "静音", "MediaPlayerUnmute": "取消静音",
            "SetVolume": "设置音量", "SetVolumeRelative": "调整音量",
            "MediaSearchAndPlay": "搜索并播放",
            "OpenCover": "打开窗帘", "CloseCover": "关闭窗帘",
            "FanSetSpeed": "调整风速",
            "HumidifierSetpoint": "设置湿度", "HumidifierMode": "设置加湿模式",
            "VacuumStart": "启动清扫", "VacuumReturnToBase": "返回基座",
            "VacuumCleanArea": "区域清扫",
            "LawnMowerStartMowing": "启动割草", "LawnMowerDock": "返回基座",
            "GetWeather": "获取天气",
            "ShoppingListAddItem": "添加购物项", "ShoppingListCompleteItem": "完成购物项",
            "ShoppingListLastItems": "查看最近购物项",
            "ListAddItem": "添加列表项", "ListCompleteItem": "完成列表项",
            "ListRemoveItem": "删除列表项",
        }
        _QUERY_TAGS = {
            "GetState", "TimerStatus", "GetCurrentDate", "GetCurrentTime",
            "ClimateGetTemperature", "GetWeather", "ShoppingListLastItems",
        }
        icon = "💫" if tag in _QUERY_TAGS else "🔗"
        if zh:
            zh_tag = _HA_ZH.get(tag, tag)
            return f"{icon} 正在{zh_tag} {eid}..." if eid else f"{icon} 正在{zh_tag}..."
        return f"{icon} {tag}: {eid}..." if eid else f"{icon} {tag}..."

    hint = _short_hint(a)
    return f"💫 {name}: {hint}..." if hint else f"💫 {name}..."


def _short_hint(args: dict) -> str:
    if not args:
        return ""
    for k in ("action", "entity_id", "name", "query", "domain", "slug", "path"):
        v = args.get(k)
        if v and isinstance(v, str):
            v = _esc(v)
            return v[:20] if len(v) <= 20 else v[:17] + "..."
    return ""
