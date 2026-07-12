# Claw Assistant 多用户个性化记忆 — 设计方案

> 版本：v2.0（修正版） | 日期：2026-07-12 | 基线：upstream 9.2.0
> 
> 设计理念：**个性化服务**，而非安全隔离。家庭环境中不同成员使用助手时，
> 每个人的偏好、习惯、记忆能独立记录并准确回馈，让 AI 做到"拿对的，给对的人"。

---

## 1. 问题背景

上游 claw_assistant 9.2.0 能**识别当前是谁在说话**（HA 登录用户返回 UUID，IM 渠道返回 `shadow:xxx`），但**记忆图谱（MemoryGraph / graph_store）没有按用户区分**。所有用户的偏好和记忆存在同一个 `nodes` 表里，recall 时不过滤。

**当前的问题**：

```
用户 A："我喜欢吃辣"
  → 存入 nodes，没有 user 标记
用户 B："我有什么喜好？"
  → recall 全量搜索
  → 可能返回 A 的"吃辣"（串了）
  → AI 可能给 B 错误的个性化回复
```

**目标**：在数据库层给每条记忆打上用户标签，recall 时只拿当前用户的记忆 + 公共知识，让每个成员都得到准确的个性化服务。

---

## 2. 核心思路

**一句话**：`nodes` 表加一个 `user` 列，写入时打标，读取时按 `user=?` 过滤。

```
nodes 表
id | title | body    | kind        | user
5  | 喜欢  | 吃辣    | preference  | a1b2c3d4-...    ← A 说"我喜欢吃辣"
4  | 地址  | 解放路  | fact        | x1y2z3-...      ← B 说"我家在解放路"
3  | 天气  | 深圳    | fact        | NULL            ← 公共常识（谁都能看）

A 问"我有什么喜好" → WHERE MATCH '喜好' AND (user='a1b2c3d4-...' OR user IS NULL)
  → 只拿到 A 的"吃辣" + 公共"天气" → AI 给 A 个性化回复 ✓

B 问"我有什么喜好" → WHERE MATCH '喜好' AND (user='x1y2z3-...' OR user IS NULL)
  → 只拿到 B 的"解放路" + 公共"天气" → AI 给 B 不同的个性化回复 ✓
```

**这不是"锁"——这是"精准路由"。** 同一条 SQL，不是为了防 B 偷看 A，而是为了确保 A 拿到的全是 A 自己的信息。

---

## 3. 用户身份解析

### 3.1 谁在说话

`conversation.py` 的 `resolve_user_key()` 决定当前对话属于谁：

| 场景 | 产出 | 示例 |
|------|------|------|
| HA App 登录用户 | HA 用户 UUID | `"a1b2c3d4-..."` |
| IM 已关联到 HA 用户 | HA 用户 UUID | `"a1b2c3d4-..."` |
| IM 未关联（微信/飞书/QQ 等） | `shadow:{渠道}:{外部ID}` | `"shadow:wechat:wxid_abc"` |
| 无法识别 | `None` | 不写 user 标记（进公共层） |

### 3.2 人设注入

```python
user_key = "a1b2c3d4-..."  # 或 "shadow:wechat:xxx"
PersonaStore.build_system_prompt(user_key) → prompt 告诉 AI "当前用户是谁"
```

AI 拿到 prompt → 后续的所有记忆操作都带上这个 user_key。

---

## 4. 调用链：user_key 如何传递

### 4.1 写入（"记住我喜欢吃辣"）

```
微信消息 → conversation.py
  → resolve_user_key() → "shadow:wechat:xxx"
  → misc_tools.py → memory_store.py
    → async_remember(user="shadow:wechat:xxx")
      → graph_service.async_remember(user="shadow:wechat:xxx")
        → graph_store.upsert_node(user="shadow:wechat:xxx")
```

### 4.2 读取（"我有什么喜好？"）

```
同一用户再次发消息 → conversation.py
  → resolve_user_key() → "shadow:wechat:xxx"
  → misc_tools.py → async_recall("喜好", user="shadow:wechat:xxx")
    → graph_service.async_recall(user="shadow:wechat:xxx")
      → graph_store.recall(user="shadow:wechat:xxx")
        → SQL: WHERE MATCH ? AND (user=? OR user IS NULL)
```

### 4.3 贯穿全链路

user_key 从 `conversation.py` 开始，经过 `orchestrator/prompting/internal_llm` → `misc_tools/self_edit_tools` → `memory_store/workspace_store` → `graph_service` → 最终落到 `graph_store`。中间 12 个文件，每个都必须透传这个参数，否则 user 在半路丢了，graph_store 拿不到 → 不过滤 → 个性化就失效了。

---

## 5. 要改的 12 个文件

| # | 文件 | 改动 | 行数估计 |
|---|------|------|---------|
| 1 | `runtime/storage/graph_store.py` | nodes 表加 user 列 + 幂等迁移 + recall/upsert/link/neighbors 加 user 参数 + WHERE 过滤 | ~+60 |
| 2 | `runtime/storage/graph_service.py` | 所有公开方法加 user 透传 | ~+20 |
| 3 | `runtime/storage/md_to_graph.py` | reindex/reindex_many 加 user 参数 | ~+6 |
| 4 | `runtime/storage/memory_store.py` | `async_save_memory_entry_result` 调用 async_remember 时传 user | ~+16 |
| 5 | `runtime/storage/workspace_store.py` | 透传 user_key | ~+34 |
| 6 | `conversation.py` | 抽 `resolve_user_key` 为模块级函数放文件末尾；类内 `_resolve_user_key` 简化为代理 | ~+50 |
| 7 | `runtime/agent/orchestrator.py` | 透传 user_key | ~+1 |
| 8 | `runtime/llm/internal_llm.py` | 透传 user_key 到 prompt 构建 | ~+22 |
| 9 | `runtime/llm/prompting.py` | 透传 user_key | ~+3 |
| 10 | `tools/misc_tools.py` | 工具解析 user_key 并透传 | ~+21 |
| 11 | `tools/self_edit_tools.py` | 惰性 import 规避循环导入 + 透传 user | ~+11 |
| 12 | `runtime/utils/self_edit.py` | 接收 user_key 参数 | ~+10 |

**估算总改动：~254 行新增**

---

## 6. 执行计划

```
T1: graph_store.py          ← 基座，无依赖
  ↓
T2: graph_service.py        ← 紧接 graph_store
    md_to_graph.py
  ↓
T3: conversation.py         ← 关键 ⚠️ 注意类结构！
  ↓
T4: memory_store.py         ← 依赖 graph_service + conversation
    workspace_store.py
  ↓
T5: internal_llm.py         ← prompt 链透传
    prompting.py
    orchestrator.py
  ↓
T6: misc_tools.py           ← 工具层
    self_edit_tools.py
    self_edit.py
  ↓
T7: 新增：测试文件          ← 单测验证
  ↓
部署：SCP + 重启 + 发消息验证
```

---

## 7. 关键注意事项（来自上次教训）

### ⚠️ conversation.py 类结构

`resolve_user_key` 必须是**模块级函数，放在文件末尾**。不能插在类体中段（0 缩进），否则 Python 会把类从那里腰斩结束，导致 `async_process` 掉出类外 → HA 调基类 → 助手不回复。

正确写法：
```python
# ... 类定义结束 ...

# === 文件末尾 ===
def resolve_user_key(user_id, conversation_id):
    """模块级函数，解析用户身份"""
    if user_id:
        return user_id
    if conversation_id:
        mapped = MappingStore.resolve_by_conversation_id(conversation_id)
        if mapped:
            return mapped
        # ... IM 通道识别 ...
    return None
```

### ⚠️ 循环导入

`self_edit_tools.py` 如果顶层写 `from ..conversation import resolve_user_key` 会触发 `conversation→chat_commands→registry→tools→self_edit_tools→conversation` 环。一律改成函数内惰性 import：

```python
# 错误（顶层 import）
from ..conversation import resolve_user_key

# 正确（函数内惰性）
def some_method(self):
    from ..conversation import resolve_user_key
    resolve_user_key(...)
```

### ⚠️ 部署验证顺序

```
① SCP 全部文件到 /config/custom_components/claw_assistant/
② docker restart homeassistant
③ docker logs 确认 claw_assistant 无 Traceback（无加载错误）
④ 发一条消息到助手 → 确认能正常回复 ← 这是关键！上次漏了这步
⑤ 用两个不同身份各存一条记忆 → 分别查询 → 确认各自的记忆准确返回
```

---

## 8. 数据库 schema 变化

### nodes 表（唯一需要改的表）

```sql
-- 当前
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_doc TEXT,
    confidence REAL DEFAULT 1.0,
    pinned INTEGER DEFAULT 0,
    checksum TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 目标
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_doc TEXT,
    confidence REAL DEFAULT 1.0,
    pinned INTEGER DEFAULT 0,
    checksum TEXT UNIQUE NOT NULL,
    user TEXT,                        -- ← 新增：标记所属用户
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### 迁移（幂等，已存在的库自动加列）

```python
cursor = self._conn.execute("PRAGMA table_info(nodes)")
cols = {row[1] for row in cursor.fetchall()}
if "user" not in cols:
    self._conn.execute("ALTER TABLE nodes ADD COLUMN user TEXT")
```

**edges 表不改。** 关联关系不需要按用户区分，个性化服务用不到边的隔离。

### recall 核心 SQL

```python
def recall(self, query, user=None):
    sql = """
        SELECT n.*, bm25(nodes_fts) AS rank
        FROM nodes_fts
        JOIN nodes n ON nodes_fts.rowid = n.id
        WHERE nodes_fts MATCH ?
    """
    params = [query]
    if user is not None:
        sql += " AND (n.user = ? OR n.user IS NULL)"
        params.append(user)
    sql += " ORDER BY rank"
    return self._conn.execute(sql, params).fetchall()
```

---

## 9. 测试要点

### graph_store 层测试

| 用例 | 验证 |
|------|------|
| user="alice" 写入，user="alice" 读取 | ✅ 能读到 |
| user="alice" 写入，user="bob" 读取 | ❌ 读不到（各自数据独立） |
| user=NULL 写入（公共），任意用户读取 | ✅ 都能读到（公共知识共享） |
| recall 时不传 user | 不过滤，返回全量（兼容旧行为） |

### graph_service 透传测试

mock graph_store，验证 async_remember / async_recall / async_link 的 user 参数正确穿透。

---

## 10. 分支与版本

- 从 `main`（`5ca1b67` = upstream 9.2.0）拉 `feat/hard-isolation`
- 全部改动落在此分支，`main` 保持干净不动
- 版本号不改（仍 9.2.0），因为未向上游提交 PR
- 上一次尝试的备份：标签 `abandoned/hard-isolation-20260712`

---

## 附录：IM 渠道识别

| 前缀 | 渠道 | user_key 示例 |
|------|------|--------------|
| `wechat:` | 微信 | `shadow:wechat:wxid_xxx` |
| `feishu:` | 飞书 | `shadow:feishu:ou_xxx` |
| `dingtalk:` | 钉钉 | `shadow:dingtalk:xxx` |
| `qq:` | QQ | `shadow:qq:xxx` |
| `wecom:` | 企业微信 | `shadow:wecom:xxx` |
| `xiaoyi:` | 小艺 | `shadow:xiaoyi:xxx` |
| HA App | HA 登录用户 | `a1b2c3d4-...`（UUID） |
