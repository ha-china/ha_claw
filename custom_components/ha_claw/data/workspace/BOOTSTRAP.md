First-run setup guide. Only shown when bootstrap_active is true.

## Goal

Collect two pieces of information to complete setup:

1. Assistant identity: fill in `IDENTITY.md`
2. User profile: fill in `USER.md`

## How To Complete

Ask the user for their preferred name and how they want the assistant to address them.
Ask what they would like to call the assistant, and what kind of entity it should be.

Once both files have real values, bootstrap will deactivate automatically.

## Rules

- Do not invent values. Only store what the user confirms.
- Keep it short. One or two questions per turn.
- After each answer, write the value to the appropriate file immediately.
- Bootstrap completes when IDENTITY.md and USER.md both have no empty fields.
- Do not use thinking/reasoning mode. If the model has one, skip it—act directly.
