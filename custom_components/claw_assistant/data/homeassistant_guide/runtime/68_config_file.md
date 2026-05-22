# ConfigFile - Access Config Directory

Read/write files in Home Assistant config directory.

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

## Write Workflow

```
ConfigFile action=stage_write path="sensors.yaml" content="..."
→ Returns approval_id, show preview to user

ConfigFile action=apply approval_id="<id>"
→ File written
```

## Delete Workflow

Delete is destructive — requires explicit user consent:

```
ConfigFile action=stage_delete path="old_file.yaml"
→ Returns approval_id, explain to user what will be deleted

ConfigFile action=apply approval_id="<id>" user_consent=true consent_quote="<user's exact words>"
→ File deleted
```

## Notes

- Stage operations return approval_id for preview before apply
- Delete requires user_consent=true with user's actual consent quote
- For automations.yaml, prefer Automation tool
