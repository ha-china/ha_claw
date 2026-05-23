<!-- version: 2 -->
# Memory Tools

## ConversationMemory - Simple Key-Value

For user preferences and short facts. Auto-injected into prompt.

| Action | Params |
|--------|--------|
| save | key, value |
| get | key |
| list | - |
| clear | - |

```json
{"action": "save", "key": "user_name", "value": "John"}
{"action": "save", "key": "fav_color", "value": "blue", "target": "user"}
{"action": "get", "key": "user_name"}
{"action": "list"}
```

`target`: `memory` (default, AI workspace) or `user` (user-facing preferences).

## MemoryGraph - Knowledge Graph

For decisions, bug fixes, causal links needing graph traversal.

| Action | Params |
|--------|--------|
| recall | query, kinds?, limit?, expand? |
| remember | kind, title, body, source_doc?, confidence?, pinned? |
| link | src_id, dst_id, relation, weight? |
| pin | id, pinned? |
| forget | id |
| get | id |
| stats | - |
| cleanup | - (dedup + remove junk + rebuild edges) |

Backed by SQLite + FTS5 with BM25 ranking.

### Kind Types

decision, bug_fix, preference, observation, fact, rule, insight

### Examples

```json
{"action": "remember", "kind": "decision", "title": "Chose plan A", "body": "Better performance", "confidence": 0.9}
{"action": "recall", "query": "plan", "limit": 5, "expand": true}
{"action": "link", "src_id": 1, "dst_id": 2, "relation": "caused_by", "weight": 1.0}
{"action": "pin", "id": 1, "pinned": true}
{"action": "forget", "id": 3}
{"action": "stats"}
{"action": "cleanup"}
```

`expand`: follow edges to return linked nodes. `pin`: prevent cleanup from removing.

## Notes

- Do NOT use both tools for the same fact
- ConversationMemory: simple preferences
- MemoryGraph: complex relationships
