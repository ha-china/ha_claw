<!-- version: 1 -->
# BatchControl - Control Multiple Devices

## Params

| Param | Description |
|-------|-------------|
| entity_ids | List of entity IDs |
| domain | Filter by domain |
| area | Filter by area |
| state | Filter by state (on/off) |
| name_contains | Filter by name |
| action | turn_on / turn_off / toggle |
| data | Additional service data |

## Examples

```json
// Turn off all lights
{"domain": "light", "action": "turn_off"}

// Turn off lights that are on
{"domain": "light", "state": "on", "action": "turn_off"}

// Turn on living room lights
{"area": "living_room", "domain": "light", "action": "turn_on"}

// Specific entities
{"entity_ids": ["light.a", "light.b"], "action": "turn_on", "data": {"brightness_pct": 50}}
```

## Domain-Aware Actions

| Domain | turn_on | turn_off |
|--------|---------|----------|
| vacuum | start cleaning | return to base |
| cover | open | close |
| lock | unlock | lock |

## Notes

- Use discovery filters OR entity_ids, not both
- For single device, use ServiceCall or Hass* intents
