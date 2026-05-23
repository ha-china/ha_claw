<!-- version: 1 -->
# SystemControl - System Settings

## Actions

| Action | Params | Description |
|--------|--------|-------------|
| set_output_mode | value | Set AI output style |
| set_global_inject | value | Set global prompt injection |
| get_status | - | Get current system status |

## Output Modes

| Mode | Description |
|------|-------------|
| normal/default/auto | Standard output |
| brief | Concise responses |
| detailed | Verbose explanations |
| list | List format |
| code | Code-focused output |

## Examples

```json
// Set brief mode
{"action": "set_output_mode", "value": "brief"}

// Get status
{"action": "get_status"}
```

## Notes

- Internal system tool
- Rarely needed by users
- Output mode affects AI response style
