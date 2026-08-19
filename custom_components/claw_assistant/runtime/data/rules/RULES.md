---
rules:
- id: rule_001
  enabled: true
  category: always
  description: 回复使用中文，除非用户使用其他语言
- id: rule_002
  enabled: true
  category: never
  description: 不要删除或修改自动化规则
- id: rule_003
  enabled: false
  category: never
  description: 不要操作安全相关设备（门锁、警报）
- id: rule_004
  enabled: true
  category: reply
  description: 控制设备时，回复控制在 20 字以内
---

## 硬边界规则

本文件的 YAML frontmatter 是规则的唯一数据源；下方正文仅作说明，修改正文不会生效。
规则会注入 AI 的 system prompt，启用后 AI 必须无条件遵守。
