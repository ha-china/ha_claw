<!-- version: 2 -->
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

## Common Domain Params

| Domain | Service | Key Params |
|--------|---------|------------|
| light | turn_on | brightness_pct(0-100), color_name, rgb_color([r,g,b]), color_temp_kelvin, hs_color |
| climate | set_temperature | temperature, hvac_mode(heat/cool/auto/off) |
| climate | set_hvac_mode | hvac_mode |
| fan | set_percentage | percentage(0-100) |
| fan | set_preset_mode | preset_mode |
| cover | set_cover_position | position(0-100) |
| media_player | volume_set | volume_level(0.0-1.0) |
| media_player | play_media | media_content_id, media_content_type |
| alarm_control_panel | alarm_arm_away | code |
| lock | lock/unlock | code (if required) |

## Notes

- entity_id supports fuzzy matching ("living room light" → light.living_room)
- data params are flat or nested dict
- Use real param names (brightness_pct, not "brightness" in other languages)
- When in doubt: ListServices → ServiceHelp → ServiceCall
