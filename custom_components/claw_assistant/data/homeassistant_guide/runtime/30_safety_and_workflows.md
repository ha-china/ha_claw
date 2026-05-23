<!-- version: 2 -->
# Safety Rules

## Safety Rules

- Read-only stays read-only — never write when user only asked to check
- Write actions: identify affected entities and expected effects before executing
- Sensitive domains require explicit user confirmation:

| Risk Tier | Domains / Actions | Confirm? |
|-----------|-------------------|----------|
| High | lock, alarm_control_panel, camera, restart, backup | Always |
| Medium | automation create/delete, config file write, integration delete | Yes |
| Low | light, switch, scene, media_player | No |

## Workflow Templates

| Workflow | Steps |
|----------|-------|
| Automation triage | trace → identify failing node → smallest fix → verify |
| Dashboard refactor | snapshot → plan → modify → verify render |
| Diagnostics | get_system_log → get_error_log → isolate → fix |
| Backup/rollback | confirm scope → execute → verify restore point |
| Integration repair | get status → reload → check logs → reconfigure if needed |

## Naming Resolution

- Persist user aliases in workspace/memory (ConversationMemory)
- Use `SmartDiscovery` to resolve natural language to entity IDs
- Example: "bedroom light" → SmartDiscovery name_contains="bedroom" domain="light"
