# HA Runtime Guide

## Index
- **10** Intent slot reference
- **15** Intent vs ServiceCall routing
- **20** ServiceCall-only domain index
- **30** Safety rules
- **40** Workflow playbooks
- **50** Checklists

## Core Rules
- Device control → Intent first, ServiceCall only when no intent
- Service params → `ListServices` + `ServiceHelp` (runtime query, not guide)
- Integration management → `ConfigEntries` (not HAControl/shell)
- All actions via internal tools, never external shell
