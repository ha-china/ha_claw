<!-- version: 2 -->
# HAControl - Advanced HA Control + Shell

## Actions

| Action | Purpose | Params |
|--------|---------|--------|
| shell | Run shell command | command |
| check_config | Validate configuration.yaml | - |
| list_integrations | List all integrations | - |
| get_integration | Get integration info | domain |
| list_entities_by_integration | List entities | entry_id |
| reload_integration | Reload integration | entry_id |
| rename_entry | Rename config entry | entry_id, name |
| reload_themes | Reload themes | - |
| reload_resources | Reload Lovelace resources | - |
| reload_scripts | Reload scripts | - |
| reload_automations | Reload automations | - |
| get_system_log | Get system log | - |
| get_error_log | Get error log | - |
| get_diagnostics | Get diagnostics | domain, entry_id |

## Examples

```json
// Shell command
{"action": "shell", "params": {"command": "ls -la /config"}}
{"action": "shell", "params": {"command": "cat /config/configuration.yaml"}}

// Check config before restart
{"action": "check_config"}

// Get integration diagnostics
{"action": "get_diagnostics", "params": {"domain": "zha", "entry_id": "abc123"}}

// Reload after config change
{"action": "reload_automations"}
{"action": "reload_scripts"}
{"action": "reload_integration", "params": {"entry_id": "abc123"}}

// System logs
{"action": "get_system_log"}
{"action": "get_error_log"}
```

## Important

- Before modifying automations.yaml/configuration.yaml, ask user confirmation
- Prefer Automation tool for automation CRUD
- Prefer ConfigFile for file operations with staging
- Use check_config before restart to avoid breaking HA
