<!-- version: 1 -->
# HelperManager - Create HA Helpers

## Supported Types

- input_boolean
- input_number
- input_text
- input_select
- input_datetime
- input_button
- timer
- counter
- template (sensor/binary_sensor)

## Actions

| Action | Params |
|--------|--------|
| create | helper_type, name, + type-specific params |
| list | helper_type (optional) |
| delete | entity_id OR helper_type + name |

## Examples

```json
// Input boolean
{"action": "create", "helper_type": "input_boolean", "name": "Away Mode", "icon": "mdi:home"}

// Input number
{"action": "create", "helper_type": "input_number", "name": "Target Temp", "min": 16, "max": 30, "step": 0.5, "unit_of_measurement": "°C"}

// Input select
{"action": "create", "helper_type": "input_select", "name": "Mode", "options": ["Home", "Away", "Sleep"]}

// Timer
{"action": "create", "helper_type": "timer", "name": "Cooking Timer", "duration": "00:30:00"}

// Template sensor
{"action": "create", "helper_type": "template", "name": "Power Usage", "state_template": "{{ states('sensor.power') | float * 2 }}"}

// Delete
{"action": "delete", "entity_id": "input_boolean.away_mode"}
```

## Notes

- Use this tool, NOT HAControl/shell
- All params are flat (no nested dict)
- Template sensors support Jinja2
