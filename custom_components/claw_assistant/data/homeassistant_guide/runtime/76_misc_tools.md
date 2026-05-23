<!-- version: 2 -->
# Misc Tools

## ParallelToolCall

Run 2+ independent tools in parallel.

```json
{
  "tools": [
    {"name": "EntityQuery", "args": {"entity_id": "light.a"}},
    {"name": "EntityQuery", "args": {"entity_id": "light.b"}}
  ]
}
```

## Notify

Send notification.

| Param | Default |
|-------|---------|
| message | (required) |
| title | "AI Assistant" |
| target | persistent_notification |

```json
{"message": "Task completed", "title": "Notification"}
{"message": "Hello", "target": "notify.mobile_app"}
```

## AgentHandoff

Consult another AI agent.

```json
{
  "agent_id": "conversation.other_agent",
  "question": "How to do X?",
  "context": "Background info",
  "intent": "consult"
}
```

intent: consult / request / review

## NextAgentHandoff

Shortcut for next available agent.

```json
{"question": "Help with X", "context": "..."}
```

## SetConversationState

Set conversation state (complex multi-turn only).

```json
{"expecting_response": true, "reason": "Waiting for user choice"}
```

Do NOT use for simple queries.

## HeartbeatManager

Manage scheduled follow-up tasks (replaces blind polling).

| Action | Params |
|--------|--------|
| list | - |
| upsert | slug, title, schedule, objective, steps, notes, status, enabled, delete_after_success, notify_channel |
| delete | slug |
| record | slug, note |
| clear_state | slug |

```json
{
  "action": "upsert",
  "slug": "daily-check",
  "title": "Daily System Check",
  "schedule": "0 9 * * *",
  "objective": "Check system status and report issues",
  "steps": ["get_system_log", "check integrations", "report"],
  "enabled": true,
  "delete_after_success": false,
  "notify_channel": "wechat:account_id:user_id"
}
{"action": "record", "slug": "daily-check", "note": "All systems normal"}
{"action": "list"}
```

`notify_channel` format: `wechat:account_id:user_id` or `qq:user:openid`.

## ReadFile

Read temp/output file.

| Action | Params |
|--------|--------|
| read | path, offset, max_chars |
| search | path, query, context_chars |
| search_fuzzy | path, query |
| info | path |

```json
{"action": "read", "path": "/tmp/output.txt"}
{"action": "search", "path": "/tmp/log.txt", "query": "error"}
```

## GetConversationHistory

Inspect or manage conversation history across sessions.

| Action | Purpose | Key Params |
|--------|---------|------------|
| get | Current conversation turns | max_turns, include_tools |
| recent | Recent turns across ALL conversations | recent_minutes (default 60) |
| clear | Wipe history | scope (current/all) |
| stats | History statistics | - |

```json
{"action": "recent", "recent_minutes": 30}
{"action": "get", "max_turns": 10, "include_tools": true}
{"action": "clear", "scope": "current"}
```

Use `recent` when the window/conversation_id was closed or changed.
