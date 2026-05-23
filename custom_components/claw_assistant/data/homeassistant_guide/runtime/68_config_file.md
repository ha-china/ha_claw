<!-- version: 2 -->
# ConfigFile - Access Config Directory

## Actions

| Action | Purpose | Params |
|--------|---------|--------|
| list | List directory | path |
| read | Read file | path |
| stage_write | Stage file write | path, content |
| stage_append | Stage append | path, content |
| stage_mkdir | Stage mkdir | path |
| stage_delete | Stage delete | path |
| apply | Apply staged changes | approval_id, user_consent, consent_quote |
| cancel | Cancel staged changes | approval_id |
| list_pending | List pending changes | - |

## Workflow

```
1. stage_write path="sensors.yaml" content="..."
   → Returns approval_id

2. Describe change to user, get confirmation

3. apply approval_id="xxx" user_consent=true consent_quote="User confirmed the change"
```

## Delete Workflow

Delete is destructive — requires explicit consent:

```
1. stage_delete path="old_file.yaml"
2. Explain to user what/why
3. apply approval_id="xxx" user_consent=true consent_quote="User approved deletion"
```

## Notes

- write/append/mkdir auto-apply on `apply`
- delete requires user_consent=true + consent_quote
- For automations.yaml, prefer Automation tool
