from __future__ import annotations

_MESSAGES: dict[str, dict[str, str]] = {
    "agents_unavailable": {
        "zh": "所有 AI 助手当前不可用，请稍后再试",
        "en": "All AI agents are currently unavailable, please try again later",
    },
    "agents_starting": {
        "zh": "系统正在启动中，助手尚未就绪，请稍等片刻再试。",
        "en": "The system is still starting up. Agents are not ready yet — please wait a moment.",
    },
    "agents_all_failed": {
        "zh": "已尝试所有可用的助手，均未能成功响应，请确认配置是否正确。",
        "en": "All available agents were tried but none responded successfully. Please verify your configuration.",
    },
    "agents_none_configured": {
        "zh": "未配置任何外部 AI 助手，请在设置中添加。",
        "en": "No external AI agent is configured. Please add one in settings.",
    },
    "no_valid_input": {
        "zh": "未收到有效输入，请重试",
        "en": "No valid input was received. Please try again.",
    },
    "budget_exhausted": {
        "zh": "迭代预算已耗尽，请开始新对话",
        "en": "Iteration budget exhausted. Please start a new conversation.",
    },
    "hook_not_ready": {
        "zh": "运行时钩子未初始化，请重新启动系统生效",
        "en": "claw_assistant runtime hook is not initialized",
    },
    "attachment_only_input": {
        "zh": "用户发送了一张图片，请描述其内容。",
        "en": "The user sent an image, please describe its content.",
    },
    "err_service_unavailable": {
        "zh": "AI 服务暂时不可用，稍后会自动重试，您也可以过一会儿再问一次。",
        "en": "The AI service is temporarily unavailable. It will retry automatically; please try again shortly.",
    },
    "err_model_not_found": {
        "zh": "助手当前没有可用的模型通道（{model}），请在设置里配好渠道后再试。",
        "en": "No available channel for model {model}. Please configure one and try again.",
    },
    "err_rate_limited": {
        "zh": "请求太频繁，AI 服务限流中，请稍等片刻再试。",
        "en": "Rate limited by the AI service. Please wait a moment and try again.",
    },
    "err_auth_failed": {
        "zh": "AI 服务认证失败，请检查 API 密钥是否正确。",
        "en": "AI service authentication failed. Please check your API key.",
    },
    "err_quota_exceeded": {
        "zh": "AI 服务额度已用完，请检查账户余额或升级套餐。",
        "en": "AI service quota exceeded. Please check your account balance or upgrade your plan.",
    },
    "err_context_too_long": {
        "zh": "对话内容过长，AI 无法处理。请开始新对话或缩短消息。",
        "en": "Conversation too long for the AI model. Please start a new conversation or shorten your message.",
    },
    "err_content_filtered": {
        "zh": "内容被 AI 安全过滤器拦截，请换一种方式描述。",
        "en": "Content was blocked by the AI safety filter. Please rephrase your message.",
    },
    "err_timeout": {
        "zh": "AI 服务响应超时，请稍后再试。",
        "en": "AI service timed out. Please try again shortly.",
    },
    "err_connection": {
        "zh": "无法连接到 AI 服务，请检查网络或稍后再试。",
        "en": "Cannot connect to the AI service. Please check your network or try again later.",
    },
    "err_generic_api": {
        "zh": "AI 服务返回错误：{detail}",
        "en": "The AI service returned an error: {detail}",
    },
    "err_model_placeholder": {
        "zh": "目标模型",
        "en": "the target model",
    },
    "cmd_new_done": {
        "zh": "已开始新对话。",
        "en": "Started a new conversation.",
    },
    "cmd_reset_done": {
        "zh": "已重置当前对话。",
        "en": "Reset the current conversation.",
    },
    "cmd_stop_done": {
        "zh": "已停止当前运行。",
        "en": "Stopped the current run.",
    },
    "cmd_stop_none": {
        "zh": "当前没有正在运行的任务。",
        "en": "No active run to stop.",
    },
    "cmd_help_header": {
        "zh": "可用命令：",
        "en": "Available commands:",
    },
    "cmd_help_footer": {
        "zh": "输入 /help <命令> 查看详情。",
        "en": "Use /help <command> for details.",
    },
    "cmd_help_usage": {
        "zh": "用法",
        "en": "Usage",
    },
    "cmd_help_category": {
        "zh": "分类",
        "en": "Category",
    },
    "cmd_help_not_found": {
        "zh": "未找到命令: {name}",
        "en": "Command not found: {name}",
    },
    "cmd_category_session": {
        "zh": "会话",
        "en": "Session",
    },
    "cmd_category_skills": {
        "zh": "技能",
        "en": "Skills",
    },
    "cmd_category_config": {
        "zh": "配置",
        "en": "Config",
    },
    "cmd_category_info": {
        "zh": "信息",
        "en": "Info",
    },
    "cmd_skill_commands": {
        "zh": "技能命令",
        "en": "Skill commands",
    },
    "cmd_skill_conflicts": {
        "zh": "被隐藏的冲突技能命令",
        "en": "Hidden conflicting skill commands",
    },
    "cmd_commands_suffix": {
        "zh": "命令",
        "en": "commands",
    },
    "cmd_goal_pending_unbound": {
        "zh": "后台目标正在继续运行，但当前会话没有绑定目标状态。",
        "en": "A background goal is still running, but the current conversation is not bound to its goal state.",
    },
    "cmd_model_no_config": {
        "zh": "未找到 claw_assistant 配置。",
        "en": "claw_assistant configuration not found.",
    },
    "cmd_model_no_agents": {
        "zh": "当前没有可用的外部 AI 助手。",
        "en": "No external AI agents available.",
    },
    "cmd_model_header": {
        "zh": "可用模型：",
        "en": "Available models:",
    },
    "cmd_model_tag_primary": {
        "zh": "主力",
        "en": "primary",
    },
    "cmd_model_tag_fallback": {
        "zh": "备用",
        "en": "fallback",
    },
    "cmd_model_tag_third": {
        "zh": "第三",
        "en": "third",
    },
    "cmd_model_switch_hint": {
        "zh": "切换: /model <序号>           设为主力\n       /model <序号> fallback  设为备用\n       /model <序号> third     设为第三（可选）\n       /model third none     清除第三",
        "en": "Switch: /model <number>            set as primary\n        /model <number> fallback   set as fallback\n        /model <number> third      set as third (optional)\n        /model third none          clear third",
    },
    "cmd_model_third_cleared": {
        "zh": "已清除第三模型 ✓",
        "en": "Third model cleared ✓",
    },
    "cmd_model_invalid_idx": {
        "zh": "无效序号: {idx}，请输入 /model 查看列表。",
        "en": "Invalid number: {idx}. Use /model to see the list.",
    },
    "cmd_model_out_of_range": {
        "zh": "序号超出范围 (1-{max})，请输入 /model 查看列表。",
        "en": "Number out of range (1-{max}). Use /model to see the list.",
    },
    "cmd_model_switched": {
        "zh": "已将 {name} 设为{role}模型 ✓",
        "en": "{name} set as {role} model ✓",
    },
}


def t(key: str, language: str | None = None) -> str:
    entry = _MESSAGES.get(key)
    if not entry:
        return key
    from .reply_formatter import is_chinese
    lang = "zh" if is_chinese(language) else "en"
    return entry.get(lang, entry.get("en", key))
