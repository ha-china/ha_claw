# Registry - Manage HA Registries

## Registries

| Registry | Purpose |
|----------|---------|
| area | Rooms/areas |
| floor | Floors |
| label | Labels/tags |
| category | Categories |
| entity | Entity settings |

## Actions

| Action | Purpose | Params |
|--------|---------|--------|
| list | List all items | registry |
| get | Get item details | registry, *_id |
| create | Create item | registry, params |
| update | Update item | registry, *_id, params |
| delete | Delete item | registry, *_id |
| rename | Rename (alias for update) | registry, *_id, params |

## Examples

```json
// Create area
{"registry": "area", "action": "create", "params": {"name": "Living Room"}}

// Rename area
{"registry": "area", "action": "update", "area_id": "living_room", "params": {"name": "Main Living Room"}}

// Assign entity to area
{"registry": "entity", "action": "update", "entity_id": "light.xxx", "params": {"area_id": "living_room"}}

// Create label
{"registry": "label", "action": "create", "params": {"name": "Important", "color": "red"}}
```

## Notes

- Use for area/floor/label management
- Entity registry for entity-level settings
- Label rename: action=update with params:{name:new_name}
