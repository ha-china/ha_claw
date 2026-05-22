# ServiceCall - Call Any HA Service

## Params

| Param | Description |
|-------|-------------|
| domain | Service domain (light, switch, climate...) |
| service | Service name (turn_on, turn_off, set_temperature...) |
| entity_id | Target entity (fuzzy match supported) |
| data | Additional service data (dict) |

## Examples

```json
// Turn on light
{"domain": "light", "service": "turn_on", "entity_id": "light.living_room"}

// Set brightness
{"domain": "light", "service": "turn_on", "entity_id": "light.bedroom", "data": {"brightness_pct": 50}}

// Set color
{"domain": "light", "service": "turn_on", "entity_id": "light.desk", "data": {"rgb_color": [255, 0, 0]}}

// Climate
{"domain": "climate", "service": "set_temperature", "entity_id": "climate.ac", "data": {"temperature": 24}}

// Lock
{"domain": "lock", "service": "lock", "entity_id": "lock.front_door"}

// Scene
{"domain": "scene", "service": "turn_on", "entity_id": "scene.movie_time"}
```

## When to Use

**Prefer Intent tools first** (HassLightSet, HassTurnOn/Off, etc.) for:
- light, switch, cover, climate, fan, vacuum, media_player

**Use ServiceCall for**:
- lock, alarm_control_panel, siren, remote, camera
- scene, notify, input_*, counter, timer
- Any service not covered by intents

## Param Discovery

If unsure about params:
```
1. ListServices domain="light"
2. ServiceHelp domain="light" service="turn_on"
3. ServiceCall with correct params
```

## Notes

- entity_id supports fuzzy matching ("living room light" → light.living_room)
- data params are flat or nested dict
- Use real param names (brightness_pct, not user's colloquial terms)
