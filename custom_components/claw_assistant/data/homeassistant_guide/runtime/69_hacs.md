<!-- version: 2 -->
# HACS - Manage HACS Store

For THIRD-PARTY integrations/plugins ONLY.

## Routing Rule — Check Native First

1. ConfigEntries `config_entries/flow_handlers` with `params:{query:"name"}` — check native
2. If `already_installed=true` → use existing entry
3. If `native_available=true` → use ConfigEntries flow/init
4. Only use HACS when no native handler is available

## Actions

| Action | Purpose | Params |
|--------|---------|--------|
| list | List installed repos | category, page, page_size |
| search | Search HACS cache | query, category, page, page_size |
| github_search | Search GitHub remotely | query |
| info | Get repo details + README | repository |
| install | Install repo | repository, category, source |
| update | Update repo | repository |
| uninstall | Uninstall repo | repository |
| remove | Remove from HACS registry | repository |
| manage/edit | View/update settings | repository, params (version/show_beta/state) |
| open_add_integration | Open HA add-integration flow | - |

## Category Semantics

| Category | Tab | Content | Post-install |
|----------|-----|---------|-------------|
| plugin | Dashboard | Frontend cards (mushroom, bubble-card) | MUST `HAControl reload_resources` |
| integration | Integration | Backend integrations | Needs HA restart |
| theme | Theme | Frontend themes | MUST `HAControl reload_resources` |
| template | Template | Jinja templates | No refresh needed |

Do NOT default to 'integration' for a dashboard card — use 'plugin'.

## Examples

```json
{"action": "search", "query": "mushroom", "category": "plugin"}
{"action": "install", "repository": "piitaya/lovelace-mushroom", "category": "plugin"}
{"action": "list", "category": "integration", "page": 1, "page_size": 15}
{"action": "github_search", "query": "bubble card"}
{"action": "info", "repository": "Clooos/bubble-card"}
```

## Notes

- `repository` accepts `owner/repo` or full URL
- Pagination: `page` (default 1) + `page_size` (default 15)
- After plugin install/update/uninstall: call `HAControl action=reload_resources`
- After integration install: may need HA restart + ConfigEntries to configure
