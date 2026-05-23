<!-- version: 2 -->
# DashboardCard Tool Guide

Create and manage persistent Lovelace dashboard cards. NOT for dynamic/temporary effects — use FrontendInspect exec_js for those.

## Actions

| Action | Purpose | Required Params |
|--------|---------|-----------------|
| check_dependency | Verify html-card-pro installed | - |
| list_dashboards | List all dashboards | - |
| get_dashboard | Get dashboard config | dashboard_url |
| get_card | Get specific card config | dashboard_url, view_index, card_index |
| add_view | Add new view | dashboard_url, title, icon |
| add_card | Add card to view | dashboard_url, view_index, content/card_config/card_yaml |
| update_card | Replace full card | dashboard_url, view_index, card_index, content/card_config |
| patch_card | Surgical edit (preferred) | dashboard_url, view_index, card_index, patches |
| remove_card | Delete card | dashboard_url, view_index, card_index |
| remove_view | Delete view | dashboard_url, view_index |
| verify_card | Audit card rendering | dashboard_url, view_index, card_index |
| get_doc | Read html-card-pro API docs | doc_name |

## Mandatory Workflow

1. **check_dependency** — if not installed, auto-install via HACS
2. **get_doc** — CRITICAL: add_card REJECTS content if you skip this. doc_name: style, js_api, data_binding, card_config, examples, efficiency
3. **list_dashboards** → **get_dashboard** — inspect view types
4. **add_card** or **patch_card** — write content
5. **verify_card** — confirms card exists + audits entity/service references + checks for rendering errors (hui-error-card, hpc-error-banner)

## Patch-First Rule (mandatory for edits)

When modifying existing cards, MUST use `patch_card` — never re-emit full content. Fall back to `update_card` only when change covers >50% of the card.

```json
{
  "action": "patch_card",
  "dashboard_url": "lovelace",
  "view_index": 0,
  "card_index": 1,
  "patches": [
    {"op": "replace", "anchor": "old text", "new_text": "new text"},
    {"op": "insert_after", "anchor": "</style>", "new_text": "\n.new-class { color: red; }"}
  ],
  "dry_run": true
}
```

Ops: replace, insert_before, insert_after, delete, prepend, append, create. Use `dry_run=true` to preview diff first.

## View Types

| Type | Description | section_index |
|------|-------------|---------------|
| masonry | Traditional grid layout | Not used |
| sections | Column-based layout | -1 (auto) or specific index |

## Verification Rule

After add/update/patch: ALWAYS use `verify_card`. NEVER use `get_card` or `get_dashboard` to verify — they are expensive. Use `get_card` ONLY to read full config before editing.

## Response Handling

Every action returns `_action_required` field — YOU MUST follow those instructions.

## Works With FrontendInspect

- FrontendInspect = SEE and INTERACT (view rendered cards, click, scroll)
- DashboardCard = MODIFY config (create/edit/delete persistent cards)
- Workflow: FrontendInspect → DashboardCard → FrontendInspect (verify)
