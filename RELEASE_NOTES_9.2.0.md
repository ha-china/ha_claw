# Claw Assistant v9.2.0

## What's Fixed

### Command Cleanup & History Conflicts (Core Fix)

Previously `/new`, `/reset`, `/stop` and `/history clear` left behind stale data that
would resurface when the assistant panel was reopened or the browser refreshed. This
release eliminates those ghost-history issues end-to-end.

**Backend**

- **Command responses no longer written to history** — `/new`, `/reset`, `/stop`,
  `/history`, skill and plugin commands are meta-operations; their confirmation messages
  will no longer appear as conversation turns when history is resumed.

- **`/new` fully clears history** — History is now cleared for both the previous
  continuous-conversation ID *and* the current conversation ID, regardless of whether
  the continuous-conversation mode is enabled. Previously history was only wiped when
  continuous mode was active, so a plain `/new` on a normal session left old turns on
  disk and they would replay on the next open.

- **`/reset` clears history** — `_clear_conversation_runtime` already wiped runtime
  state; it now also calls `get_conversation_history().clear()` so the disk copy is
  gone too.

- **`/stop` cleans up tool state** — After cancelling the running task,
  `_stop_conversation_runtime` now clears `tool_calls_state` and `tool_results_state`
  so stale tool data from the interrupted turn cannot bleed into the next turn's history
  entry. The live-turn snapshot is also marked `active=False / status=stopped` so the
  frontend snapshot-restore path does not replay interrupted tool activity.

**Frontend**

- **`_cleanupToolArtifacts()`** — New helper that atomically clears
  `__clawToolActivities`, `__clawTurnParts`, resets `_turnEnded` / `_mdStreamActive`,
  and removes all `.claw-ta-panel` / `.claw-ta-card` DOM nodes. Called on every
  hard-reset command.

- **`/new`, `/reset`, `/history clear` detected in `_addMessage`** — Frontend now
  reacts to all three reset-type commands (previously only `/new` was handled).
  Each triggers `_cleanupToolArtifacts()` plus full conversation array reset.

- **`/stop` frontend guard (`__clawIsStopReset`)** — Sets a short-lived flag (3 s,
  cleared immediately on the next `startTurn`) that blocks:
  - late-arriving stream deltas (`clawApplyLiveDelta`, `__clawOnStreamDelta`)
  - stream-end re-render (`__clawOnStreamEnd`)
  - snapshot-restore history pull from the status-bar poller

  Pending tool cards are marked `result: {stopped: true}` and immediately collapsed.

- **Snapshot-restore guarded by `__clawIsNewReset` / `__clawIsStopReset`** — The
  status-bar polling loop no longer pulls stale history back into a freshly cleared
  conversation.

- **Snapshot DOM cleaned before apply** — Old `.claw-ta-panel` nodes are removed
  before the new conversation array is written, preventing duplicated tool cards
  on reconnect.

### Tool Call History Resume

- **Complete tool data stored** — `tracked_async_call_tool` (internal LLM path) and
  `turn_kernel` now store the full dict (`tool_call_id`, `tool_name`, `tool_args`,
  `tool_result`, `success`, `error`) instead of only the tool name string. History
  resume can now reconstruct full tool activity cards.

- **`MAX_TOOL_CALLS_DISPLAY` cap removed** — The previous hard limit of 3 tool calls
  per turn returned by `chat_history_api` has been removed; all tool calls are now
  returned.

- **Restored cards render collapsed** — After history resume `_turnEnded=true` and
  `_mdStreamActive=false` are set so tool cards immediately render in the collapsed
  panel state rather than appearing as in-progress.

- **`tool_calls: {}` passed to `ha-assist-chat`** — Prevents the official HA
  component from rendering its own thinking-header / tool-calls UI that conflicted
  with the custom tool cards.

### Other Fixes

- **Tool card flicker on stream events** — `_renderFinal` no longer destroys
  `.claw-md-mixed` when tool activities are present; the in-place update path now
  falls through to a full re-render when cards are missing rather than silently
  dropping them.

- **Dashboard card prompt aligned with html-pro-card guide** — Added alternative
  styles, full Claw JS API reference, TypeScript examples, motion specs, entity
  type taxonomy, and design inspiration sources.

- **Blocking disk I/O removed** — `_ensure_prompt_store_fresh` no longer falls back
  to a synchronous disk read on the event loop.

- **Slash commands excluded from stitched history** — `/new`, `/reset` etc. are
  stripped from the LLM context window so the model never sees command turns.

- **Voice / text channel handling separated** — Voice pipeline sessions and text
  sessions no longer share state that caused incorrect context bleed.

- **Parallel tool invocation loop fixed** — Repeated identical tool calls in the
  same step are now de-duplicated.

- **Smart entity discovery** — Entity queries now route through the discovery layer
  for better resolution of ambiguous names.

- **Prompt cache prefix stabilised** — Cache prefix is now deterministic across turns
  to maximise provider-side cache hits.

## Upgrade Notes

No configuration changes required. Clear your browser cache after upgrading to ensure
the updated `ha_crack.js` is loaded.
