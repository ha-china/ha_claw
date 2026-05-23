<!-- version: 2 -->
# Query Tools

## GetLiveContext

Get real-time state of all exposed entities. No params.

```json
{}
```

## EntityQuery

Query single entity state. Supports fuzzy matching.

```json
{"entity_id": "living room light"}
{"entity_id": "light.living_room"}
```

## HistoryQuery

Query entity history.

```json
{"entity_id": "sensor.temperature", "hours": 24}
```

## GetSystemIndex

Get system structure overview: areas, floors, domains, device classes, people, automations, scripts.

```json
{"force_refresh": false}
```

Use this first to understand the HA installation before querying specific entities.

## SmartDiscovery

Smart entity discovery with filters.

| Param | Description |
|-------|-------------|
| area | Area name |
| domain | Entity domain |
| state | Current state |
| name_contains | Name filter |
| name_pattern | Regex pattern |
| device_class | Device class |
| inferred_type | Inferred type |
| person_name | Person name |
| limit | Max results |

```json
{"area": "living_room", "domain": "light"}
{"name_contains": "temperature", "domain": "sensor"}
```

## AreaDevices

Get all devices in area.

```json
{"area": "living_room"}
```

## ListServices

List services for domain.

```json
{"domain": "light"}
```

## ServiceHelp

Get service help (schema, fields, description).

```json
{"domain": "light"}
{"domain": "light", "service": "turn_on"}
```

## ValidateService

Validate service call params before executing. Returns errors and suggestions.

```json
{"domain": "light", "service": "turn_on", "data": {"brightness_pct": 50, "entity_id": "light.desk"}}
```

Use when unsure about param correctness — cheaper than a failed ServiceCall.
