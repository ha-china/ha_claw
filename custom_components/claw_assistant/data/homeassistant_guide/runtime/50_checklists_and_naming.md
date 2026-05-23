<!-- version: 2 -->
# Checklists

## Pre-Change Checklist

1. Confirm target entities exist and are available (GetLiveContext or EntityQuery)
2. Assign risk tier (High/Medium/Low per safety rules)
3. For High/Medium: get explicit user confirmation
4. For platform changes: verify backup exists or create one
5. For automation changes: read current config first (Automation get)

## Post-Change Checklist

1. Verify target entity states changed as expected (EntityQuery)
2. Check system logs for errors (HAControl get_system_log)
3. For automations: check traces for execution success
4. Summarize what changed to user

## Naming Conventions

| Item | Convention | Example |
|------|-----------|----------|
| Automation | Descriptive, action-based | Motion Light Living Room |
| Script | Verb + target | Welcome Home Lights |
| Helper | Purpose + type hint | Target Temp, Away Mode |
| Custom Entity | Function + unit | Power Usage (W) |
| Scene | Context/mood based | Movie Time, Good Night |
