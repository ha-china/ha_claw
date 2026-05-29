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
        "zh": "系统正在初始化中，请稍等片刻再试。",
        "en": "The system is initializing. Please wait a moment and try again.",
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
    "cmd_help_footer": {
        "zh": "输入 `/help 命令名` 查看详情。",
        "en": "Use `/help command_name` for details.",
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
    "cmd_category_plugin": {
        "zh": "插件",
        "en": "Plugin",
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
        "zh": "\u200b`/model 序号` — 设为主力\n\u200b`/model 序号 fallback` — 设为备用\n\u200b`/model 序号 third` — 设为第三（可选）\n\u200b`/model third none` — 清除第三",
        "en": "\u200b`/model number` — set as primary\n\u200b`/model number fallback` — set as fallback\n\u200b`/model number third` — set as third (optional)\n\u200b`/model third none` — clear third",
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
    "delegation_help_title": {
        "zh": "⚫️ 子代理系统 (/ooo)",
        "en": "⚫️ Subagent System (/ooo)",
    },
    "delegation_help_usage": {
        "zh": "用法",
        "en": "Usage",
    },
    "delegation_help_start": {
        "zh": "启动子代理执行任务",
        "en": "Start a subagent to execute a task",
    },
    "delegation_help_list": {
        "zh": "查看正在运行的子代理",
        "en": "List running subagents",
    },
    "delegation_help_stop": {
        "zh": "停止指定子代理",
        "en": "Stop a specific subagent",
    },
    "delegation_help_examples": {
        "zh": "示例",
        "en": "Examples",
    },
    "delegation_help_description": {
        "zh": "子代理在隔离的上下文中运行，有自己的对话和工具集。完成后只返回摘要，中间过程不会污染主对话。AI 也可以通过 DelegateTask 工具调用子代理。",
        "en": "Subagents run in isolated contexts with their own conversation and toolset. Only the summary is returned; intermediate steps don't pollute the main conversation. AI can also invoke subagents via the DelegateTask tool.",
    },
    "delegation_no_active": {
        "zh": "⚪️ 当前没有运行中的子代理。",
        "en": "⚪️ No active subagents.",
    },
    "delegation_active_header": {
        "zh": "⚫️ 运行中的子代理：",
        "en": "⚫️ Running subagents:",
    },
    "delegation_tool_calls": {
        "zh": "工具调用",
        "en": "tool calls",
    },
    "delegation_stop_no_id": {
        "zh": "🟤 请提供要停止的子代理 ID。用 `/ooo list` 查看运行中的子代理。",
        "en": "🟤 Please provide the subagent ID to stop. Use `/ooo list` to see running subagents.",
    },
    "delegation_stop_requested": {
        "zh": "⚪️ 已请求停止子代理 `{task_id}`。",
        "en": "⚪️ Stop requested for subagent `{task_id}`.",
    },
    "delegation_stop_not_found": {
        "zh": "🟤 未找到子代理 `{task_id}`。用 `/ooo list` 查看运行中的子代理。",
        "en": "🟤 Subagent `{task_id}` not found. Use `/ooo list` to see running subagents.",
    },
    "delegation_complete": {
        "zh": "⚫️ 子代理完成",
        "en": "⚫️ Subagent completed",
    },
    "delegation_timeout": {
        "zh": "🟤 子代理超时",
        "en": "🟤 Subagent timed out",
    },
    "delegation_cancelled": {
        "zh": "⚪️ 子代理已取消",
        "en": "⚪️ Subagent cancelled",
    },
    "delegation_failed": {
        "zh": "🟤 子代理失败",
        "en": "🟤 Subagent failed",
    },
    "delegation_duration": {
        "zh": "耗时",
        "en": "Duration",
    },
    "delegation_result": {
        "zh": "结果",
        "en": "Result",
    },
    "delegation_error": {
        "zh": "错误",
        "en": "Error",
    },
    "delegation_no_summary": {
        "zh": "（无摘要）",
        "en": "(no summary)",
    },
    "delegation_spawn_paused": {
        "zh": "子代理生成已暂停",
        "en": "Subagent spawning is paused",
    },
    "delegation_depth_exceeded": {
        "zh": "已达到最大嵌套深度 ({depth})",
        "en": "Maximum nesting depth reached ({depth})",
    },
    "delegation_goal_required": {
        "zh": "需要提供 goal 参数",
        "en": "goal parameter is required",
    },
    "delegation_too_many_tasks": {
        "zh": "任务过多：提供了 {count} 个，最大允许 {max}",
        "en": "Too many tasks: {count} provided, max is {max}",
    },
    "delegation_task_missing_goal": {
        "zh": "任务 {index} 缺少 goal",
        "en": "Task {index} is missing a goal",
    },
    "delegation_subagent_prompt_intro": {
        "zh": "你是一个专注的子代理，正在处理一个特定的委派任务。",
        "en": "You are a focused subagent working on a specific delegated task.",
    },
    "delegation_subagent_prompt_task": {
        "zh": "你的任务",
        "en": "YOUR TASK",
    },
    "delegation_subagent_prompt_context": {
        "zh": "上下文",
        "en": "CONTEXT",
    },
    "delegation_subagent_prompt_instructions": {
        "zh": "使用可用的工具完成任务。完成后，提供清晰简洁的摘要：\n- 你做了什么\n- 发现或完成了什么\n- 遇到的任何问题\n\n保持简洁 — 你的回复将作为摘要返回给父代理。",
        "en": "Complete this task using the tools available to you. When finished, provide a clear, concise summary of:\n- What you did\n- What you found or accomplished\n- Any issues encountered\n\nBe thorough but concise -- your response is returned to the parent agent as a summary.",
    },
    "delegation_subagent_prompt_orchestrator": {
        "zh": "## 子代理生成能力（协调者角色）\n你可以使用 DelegateTask 工具生成自己的子代理来并行化独立工作。\n\n**何时委派：**\n- 目标可以分解为 2+ 个独立的子任务\n- 子任务是推理密集型的，会淹没你的上下文\n\n**何时不委派：**\n- 单步机械工作 — 直接做\n- 你可以用一两个工具调用完成的简单任务",
        "en": "## Subagent Spawning (Orchestrator Role)\nYou have access to the delegate_task tool and CAN spawn your own subagents to parallelize independent work.\n\nWHEN to delegate:\n- The goal decomposes into 2+ independent subtasks\n- A subtask is reasoning-heavy and would flood your context\n\nWHEN NOT to delegate:\n- Single-step mechanical work — do it directly\n- Trivial tasks you can execute in one or two tool calls",
    },
    "delegation_subagent_prompt_leaf": {
        "zh": "## 限制\n作为叶子代理，你不能使用 DelegateTask 工具。专注于直接完成任务。",
        "en": "## Restrictions\nAs a leaf agent, you cannot use the DelegateTask tool. Focus on completing the task directly.",
    },
    "delegation_ooo_instruction": {
        "zh": "[用户通过 /ooo 请求后台任务] 请使用 DelegateTask 工具执行以下任务，将结果汇报给用户：",
        "en": "[User requested background task via /ooo] Use the DelegateTask tool to execute the following task and report results:",
    },
    "delegation_spawned": {
        "zh": "子代理已启动，后台运行中。使用 /ooo list 查看状态。",
        "en": "Subagent spawned and running in background. Use /ooo list to check status.",
    },
}


def t(key: str, language: str | None = None) -> str:
    entry = _MESSAGES.get(key)
    if not entry:
        return key
    from ..output.reply_formatter import is_chinese
    lang = "zh" if is_chinese(language) else "en"
    return entry.get(lang, entry.get("en", key))
