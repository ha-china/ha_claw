# Tool Selection — Intent vs ServiceCall Routing

ALWAYS prefer Hass* intent tools for device control. They handle entity matching, area resolution, and error reporting natively. Only fall back to ServiceCall when NO intent covers the operation.

### Intent tools cover these device types:

- light: HassLightSet (brightness/color/temperature), HassTurnOn/Off
- switch/any toggle: HassTurnOn/Off
- cover: HassSetPosition, HassStopMoving, HassTurnOn/Off (open/close)
- climate: HassClimateSetTemperature, HassTurnOn/Off
- fan: HassFanSetSpeed, HassTurnOn/Off
- humidifier: HassHumidifierSetpoint, HassHumidifierMode, HassTurnOn/Off
- vacuum: HassVacuumStart, HassVacuumReturnToBase, HassVacuumCleanArea
- lawn_mower: HassLawnMowerStartMowing, HassLawnMowerDock
- media_player: HassMediaPause/Unpause, HassMediaNext/Previous, HassMediaPlayerMute/Unmute, HassSetVolume, HassSetVolumeRelative, HassMediaSearchAndPlay, HassTurnOn/Off
- todo: HassListAddItem, HassListCompleteItem, HassListRemoveItem
- shopping_list: HassShoppingListAddItem/CompleteItem/LastItems
- timer: HassStartTimer, HassCancelTimer, HassIncreaseTimer, HassDecreaseTimer, HassPauseTimer, HassUnpauseTimer, HassTimerStatus (only if device supports timers)
- assist_satellite: HassBroadcast

### ServiceCall needed for (no intent exists):

- alarm_control_panel: alarm_arm_away/home/night, alarm_disarm, alarm_trigger
- lock: lock, unlock, open
- siren: turn_on (with tone/volume/duration), turn_off
- remote: send_command, learn_command, turn_on/off
- camera: turn_on/off, enable_motion_detection, snapshot
- scene: scene.turn_on (activate scene)
- notify: notify.send_message, persistent_notification.create
- input_boolean/number/select/text/datetime/button: set_value, press, select_option
- number/select/text/button entities: set_value, press, select_option
- calendar: create_event, delete_event
- automation: trigger, turn_on/off (or use Automation tool)
- script: run (or use ScriptExecute tool)

### Decision flow:

1. Device on/off/toggle → HassTurnOn/Off (works for ALL device types)
2. Device-specific control → Check intent list above first
3. No matching intent → ServiceCall (use ListServices if unsure about params)
4. Don't know entity_id → SmartDiscovery or AreaDevices
5. Need current state → GetLiveContext or EntityQuery
6. Multiple independent ops → ParallelToolCall
7. Install integration → ConfigEntries (flow/init → flow/configure)
8. Create automation → HAControl
9. System/config tasks → HAControl, ConfigFile, HelperManager

## Common Task Patterns

**Todo/reminder**: Use HassListAddItem intent directly with the todo list name. If no todo entity exists, use ConfigEntries to install local_todo first.

**Scheduling**: Prefer calendar.create_event (ServiceCall) for time-based events. Use timer intents only for countdowns.

**Check state**: GetLiveContext first. If entity missing, it's not exposed — ask user.

**One check is enough**: Don't chain ListServices/ServiceHelp calls hunting for something. If first lookup fails, ask user.
