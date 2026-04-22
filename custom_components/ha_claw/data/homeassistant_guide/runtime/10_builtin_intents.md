# Built-in Intent Quick Reference

All intents use slots: `name`(fuzzy match), `area`, `floor`. Omitting `name` + providing `area` targets all matching entities in that area.

| Intent | Extra Slots | Domain |
|---|---|---|
| HassTurnOn/Off | — | any toggleable |
| HassLightSet | color, temperature, brightness(0-100%) | light |
| HassSetPosition | position(0-100) | cover |
| HassStopMoving | — | cover |
| HassClimateSetTemperature | temperature | climate |
| HassFanSetSpeed | percentage(0-100%) | fan |
| HassHumidifierSetpoint | humidity | humidifier |
| HassHumidifierMode | mode | humidifier |
| HassVacuumStart | — | vacuum |
| HassVacuumReturnToBase | — | vacuum |
| HassVacuumCleanArea | floor | vacuum |
| HassLawnMowerStartMowing | — | lawn_mower |
| HassLawnMowerDock | — | lawn_mower |
| HassMediaPause/Unpause | — | media_player |
| HassMediaNext/Previous | — | media_player |
| HassMediaPlayerMute/Unmute | — | media_player |
| HassSetVolume | volume_level(0-100) | media_player |
| HassMediaSearchAndPlay | search_query, media_type | media_player |
| HassListAddItem | name(list), item | todo |
| HassListCompleteItem | name, item | todo |
| HassListRemoveItem | name, item | todo |
| HassStartTimer | hours, minutes, seconds | timer |
| HassCancelTimer | — | timer |
| HassBroadcast | message | assist_satellite |

Hidden (use alternatives): HassGetState→GetLiveContext, HassGetWeather→ServiceCall, HassToggle→HassTurnOn/Off, timers hidden if device unsupported.
