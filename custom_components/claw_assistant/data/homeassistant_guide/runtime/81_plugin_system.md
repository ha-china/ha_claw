<!-- version: 1 -->
# Plugin System

Hermes-compatible plugin architecture. Plugins extend Claw Assistant with custom tools, context engines, hooks, and slash commands. All operations are hot — no HA restart required.

---

## Directory Layout

```
.storage/claw_assistant/plugins/
└── <plugin_name>/
    ├── plugin.yaml        # manifest (required)
    ├── __init__.py         # entry point with register(ctx) (required)
    └── ...                 # additional modules
```

## Manifest — `plugin.yaml`

```yaml
name: my-plugin
version: 1.0.0
description: What this plugin does
author: Author Name
kind: standalone          # standalone | privileged
pip_dependencies: []      # pip packages to install beforehand
requires_env: []          # required environment variables
provides_tools:           # tool names this plugin registers
  - my_tool_name
provides_hooks: []        # hook events this plugin listens to
```

### Kind Semantics

| Kind | Execution | hass access | Use case |
|------|-----------|-------------|----------|
| `standalone` | Isolated subprocess | No | Pure data transforms, external API calls |
| `privileged` | In-process | Full via `PluginContext` | State access, service calls, hooks, context engines |

Auto-promotion: a `standalone` plugin is promoted to `privileged` if `__init__.py` contains `(hass`, `(ctx:`, `(ctx,`, `register_context_engine`, or `register_hook`.

## Entry Point — `__init__.py`

```python
def register(ctx):
    ctx.register_tool(
        name="weather_lookup",
        schema={
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "description": "Look up current weather for a city",
        },
        handler=_handle_weather,
        description="Look up current weather for a city",
    )

def _handle_weather(args):
    city = args["city"]
    return {"success": True, "result": f"Weather for {city}: 22°C, sunny"}
```

## PluginContext API

Passed as `ctx` to `register(ctx)` in privileged plugins.

**Registration:**
- `ctx.register_tool(name, schema, handler, description)` — add a callable tool
- `ctx.register_command(name, handler, description)` — add a `/name` chat command
- `ctx.register_hook(event, callback)` — subscribe to lifecycle events
- `ctx.register_context_engine(engine)` — set a context compression engine
- `ctx.register_skill(name, path)` — register an embedded skill markdown

**Home Assistant access (async):**
- `await ctx.call_service(domain, service, data)`
- `await ctx.fire_event(event_type, data)`
- `await ctx.get_state(entity_id)` → `dict | None`
- `await ctx.set_state(entity_id, state, attrs)`
- `await ctx.get_areas()` → `list[dict]`
- `await ctx.get_devices(area_id=None)` → `list[dict]`
- `await ctx.get_entities(domain=None, area_id=None)` → `list[dict]`
- `await ctx.get_services(domain=None)` → `dict`

**Utilities:**
- `ctx.dispatch_tool(name, args)` — call another registered tool
- `ctx.config_dir` — HA config directory path
- `ctx.hass` — direct HomeAssistant instance

---

## Operations

### Chat Commands (user)

| Command | Effect |
|---------|--------|
| `/plugin list` | Show all installed plugins with status |
| `/plugin status` | Show active plugins and their tool names |
| `/plugin load <name>` | Hot-load a plugin |
| `/plugin unload <name>` | Remove from memory (files remain) |
| `/plugin reload <name>` | Unload then re-load |
| `/plugin install <git_url>` | `git clone` from GitHub + auto-load |
| `/plugin uninstall <name>` | Unload + delete directory from disk |

### AI Tool — `PluginManager`

| Action | Required params | Effect |
|--------|----------------|--------|
| `list` | — | All installed plugins with metadata |
| `loaded` | — | Currently active plugins and tools |
| `load` | `plugin_name` | Hot-load |
| `unload` | `plugin_name` | Remove from memory |
| `hot_reload` | `plugin_name` | Unload + load |
| `reload_all` | — | Reload entire plugin store |
| `install` | `git_url` | Clone from GitHub + auto-load |
| `uninstall` | `plugin_name` | Unload + delete from disk |
| `validate` | `source_path` | Check if source is valid plugin |
| `guide` | `plugin_name` | Installation instructions |
| `call_tool` | `tool_name`, `tool_args` | Invoke a loaded plugin tool |
| `pending` | — | List pending approval requests |
| `cancel_approval` | `approval_id` | Cancel a pending approval |

### Direct Tool Invocation

Once loaded, plugin tools appear as **top-level tools** in the AI tool surface. Call them directly by name — no need to go through `PluginManager.call_tool` unless bridging is preferred. Plugin tool descriptions are prefixed with `[Plugin: <name>]`.

---

## Hermes Compatibility

Shim modules are auto-installed for plugins that import Hermes APIs:

- `agent.context_engine.ContextEngine` — abstract base for context engines
- `hermes_cli.config.get_hermes_home()` — returns `$HERMES_HOME` or `~/.hermes`

## Approval Flow

Privileged tool calls require user consent:

1. AI calls tool **without** `approval_id` → call is staged, approval prompt returned
2. User confirms → AI re-calls with `approval_id` + `user_consent=True` → executed
3. Standalone tools with `requires_approval=False` skip this flow entirely

## Lifecycle

1. **Discovery** — scan `plugins/` for directories with `plugin.yaml`
2. **Validation** — check manifest fields, verify `__init__.py` exists
3. **Analysis** — inspect source for tool definitions, determine standalone vs privileged
4. **Loading** — privileged: call `register(ctx)`; standalone: build subprocess runner
5. **Tool injection** — wrap handlers as `llm.Tool`, inject into runtime tool surface
6. **Hot ops** — load/unload/reload at any time; tool cache is invalidated automatically

## Troubleshooting

- **Plugin not loading** — check `plugin.yaml` exists and is valid YAML; check `__init__.py` has `register(ctx)`
- **Tools not visible to AI** — verify plugin shows `loaded=True` in `/plugin status`; call `PluginManager(action="reload_all")` to force refresh
- **Import errors** — install `pip_dependencies` manually first; check `requires_env` vars are set
- **Hermes plugin fails** — ensure it uses `from agent.context_engine import ContextEngine`, not a private Hermes import path
