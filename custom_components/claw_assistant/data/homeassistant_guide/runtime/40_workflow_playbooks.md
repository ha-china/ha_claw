<!-- version: 2 -->
# Workflow Playbooks

## Automation Triage
1. Confirm the trigger event actually happened.
2. Check the conditions at trigger time.
3. Review the trace path and isolate the failing node.
4. Propose the smallest safe fix.
5. Verify with a concrete checklist after the change.

## New Automation Design
1. Define the trigger and debounce behavior.
2. Define override helpers or manual escape hatches.
3. Choose the correct automation mode.
4. Add timeout and error handling.
5. Add observability so the user can debug it later.

## Dashboard Refactor
1. Prioritize the top user actions first.
2. Build a clear status hierarchy.
3. Consolidate duplicate cards and controls.
4. Verify unavailable-state behavior and navigation.

## Integration Install
1. ConfigEntries flow/init with handler → fill form → flow/configure.
2. If flow returns more steps, keep configuring.
3. Verify with ConfigEntries get.

## Diagnostics / Repair
1. HAControl get_system_log → identify errors.
2. HAControl get_error_log → full error context.
3. Isolate the integration/entity causing issues.
4. Reload integration or check config.
5. Report findings and propose fix.

## Execution Rule
- When the user asks "how to do this in HA", use `HomeAssistantGuide` tool to pull the relevant playbook first.
- Then translate the playbook into concrete tool calls.
