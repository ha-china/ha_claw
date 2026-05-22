# Skill Tools

## Skill Markdown Structure

Skills MUST follow this YAML frontmatter + markdown format:

```markdown
---
name: skill-name
description: One-line description of what this skill does
keywords:
  - keyword1
  - keyword2
tags:
  - tag1
category: category-name
---

# Skill Title

Skill content in markdown...

## Section 1

Instructions, examples, etc.
```

### Required Frontmatter Fields

| Field | Description |
|-------|-------------|
| name | Unique skill identifier (kebab-case) |
| description | Brief one-line description for catalog display |

### Optional Frontmatter Fields

| Field | Description |
|-------|-------------|
| keywords | List of search keywords for skill discovery |
| tags | List of tags for categorization |
| category | Category name for grouping |
| platforms | List of platforms (e.g., voice, text, frontend) |
| metadata.claw.requires_tools | Tools this skill requires |
| metadata.claw.config | Config keys this skill uses |

### Example Skill

```markdown
---
name: light-control-guide
description: Guide for controlling lights in Home Assistant
keywords:
  - light
  - brightness
  - color
tags:
  - automation
  - control
category: device-control
---

# Light Control Guide

This skill provides guidance for controlling lights.

## Basic Commands

- Turn on: `light.turn_on` 
- Turn off: `light.turn_off` 
- Set brightness: `light.turn_on` with `brightness_pct` 
```

---

## ListInstalledSkills

List all installed skills. No params.

## GetInstalledSkill

Read skill content.

```json
{"name": "skill-slug"}
```

## InstallSkill

Install new skill. The `markdown` field MUST include proper YAML frontmatter.

```json
{"name": "my-skill", "markdown": "---\nname: my-skill\ndescription: What this skill does\n---\n\n# Skill Content\n...", "overwrite": false}
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
