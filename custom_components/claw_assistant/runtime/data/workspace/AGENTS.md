<!-- version: 9.2.1 -->
# Workspace Governance

## 文档用途
- SOUL=风格/语调
- IDENTITY=AI身份
- USER=家庭公共信息
- MEMORY=家庭公共记忆（MemoryGraph 中 user=NULL 的数据）
- HEARTBEAT=定时任务
- TOOLS=环境凭证

## 记忆隔离机制
所有记忆存储在 MemoryGraph（graph.db）中，按 user 列区分归属：

| user 值 | 含义 | 举例 |
|---------|------|------|
| NULL | 家庭公共记忆 | "家里养了一只猫" |
| HA用户UUID | 该 HA 登录用户的私有记忆 | "我喜欢吃辣" |
| shadow:wechat:xxx | 微信用户的私有记忆 | "我家的地址是解放路" |

- 写入时自动标记所属用户（user 列）
- 读取时自动按当前用户过滤，仅返回该用户私有数据 + 公共知识
- 其他用户的私有记忆在代码层不可见，无需 AI 手动判断

## 核心纪律
- 不凭空创造值
- 破坏性操作需用户确认
- 不泄露机密
- 记忆隔离由系统自动保证，AI 无需干预用户数据边界
