# Entity Tools

## CustomEntityManager

Create dynamic AI entities under claw_assistant device.

| Platform | Purpose |
|----------|---------|
| sensor | Jinja2 template sensor |
| binary_sensor | Jinja2 template binary sensor |
| switch | Toggle switch |
| button | Press action button |

### Create

```json
{
  "action": "create",
  "platform": "sensor",
  "name": "Power Usage",
  "state_template": "{{ states('sensor.power') | float * 2 }}",
  "unit_of_measurement": "W",
  "device_class": "power"
}
```

```json
{
  "action": "create",
  "platform": "button",
  "name": "Reset Counter",
  "press_action": "counter.reset",
  "icon": "mdi:refresh"
}
```

### List

```json
{"action": "list"}
```

### Edit

```json
{
  "action": "edit",
  "entity_id": "sensor.power_usage",
  "state_template": "{{ states('sensor.power') | float * 3 }}"
}
```

### Delete

```json
{"action": "delete", "entity_id": "sensor.power_usage"}
```

## ExposeEntity

Expose/unexpose entities to conversation.

### List Unexposed

```json
{"action": "list", "domain": "light"}
```

### Expose

```json
{"action": "expose", "entity_id": "light.bedroom", "expose": true}
```

**Privacy**: Before exposing, inform user that data stays local.

## IntentCall

List or call third-party Home Assistant intent handlers only.

Do not use `IntentCall` for Claw plugins, plugin tools, skills, slash commands, or tools already listed in the function schema. Claw plugin tools are separate tools and must be called directly by their tool name.

### List

```json
{"action": "list"}
```

### Call

```json
{
  "action": "call",
  "intent_type": "HassLightSet",
  "slots": {"name": "客厅灯", "brightness": 80}
}
```
