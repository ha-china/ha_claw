# Skill Tools

## ListInstalledSkills

List all installed skills. No params.

## GetInstalledSkill

Read skill content.

```json
{"name": "skill-slug"}
```

## InstallSkill

Install new skill.

```json
{"name": "my-skill", "markdown": "# Skill Content\n...", "overwrite": false}
```

## DeleteSkill

Delete skill (audited).

```json
{"name": "skill-slug", "reason": "No longer needed"}
```

## HomeAssistantGuide

Read bundled HA guide.

| Action | Params |
|--------|--------|
| overview | - |
| list | - |
| get | name |
| search | query, limit |

```json
{"action": "overview"}
{"action": "get", "name": "automation"}
{"action": "search", "query": "integration install"}
```

## Workspace Docs

### ListWorkspaceDocs

List workspace markdown docs. No params.

### GetWorkspaceDoc

Read workspace doc.

```json
{"name": "AGENTS.md"}
```

### SetWorkspaceDoc

Write workspace doc.

```json
{"name": "NOTES.md", "markdown": "# Notes\n..."}
```

## Master Prompt

### GetMasterPrompt

Read current master prompt. No params.

### SetMasterPrompt

Set master prompt.

```json
{"markdown": "# Master Prompt\n..."}
```
