<!-- version: 1 -->
# Services Quick Reference

Use `ListServices`/`ServiceHelp` at runtime to get exact params. This is just a domain index.

## ServiceCall-only domains (no intent covers these)

| Domain | Key Services | Sensitive? |
|---|---|---|
| alarm_control_panel | arm_away/home/night, disarm(code), trigger | YES |
| lock | lock, unlock, open (code) | YES |
| siren | turn_on(tone/volume/duration), turn_off | — |
| remote | send_command, learn_command | — |
| camera | turn_on/off, enable/disable_motion_detection | — |
| scene | turn_on (activate) | — |
| notify | send_message, persistent_notification.create | — |
| input_number/select/text/datetime/button | set_value, select_option, press | — |
| counter/timer | increment, decrement, start(duration), pause, cancel | — |
| calendar | create_event, list_events | — |
| todo | add_item, update_item, remove_item | — |
| automation | trigger, turn_on/off, reload | — |
| script | turn_on, turn_off, reload | — |

## Third-Party Patterns

- Zigbee/Z-Wave: standard HA domain entities, no special services
- Smart speakers: media_player + notify.alexa_media_*/google
- Robot vacuums: vacuum.* + vacuum.send_command for brand-specific
- Smart locks: lock.lock/unlock + optional code
- IR/RF bridges: remote.send_command + device

## When unsure about params

1. `ListServices` domain → see services
2. `ServiceHelp` domain+service → get schema
3. Execute with `ServiceCall`
