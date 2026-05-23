<!-- version: 2 -->
# ConfigEntries - Integration Management

Preferred params envelope: `{action: "...", params: {...}}`. Form field values go directly in params alongside flow_id (no `user_input` wrapper).

## Install Workflow (2 calls max)

```
1. config_entries/flow/init  params: {handler: "xiaomi_miio"}
   → Returns flow_id + data_schema fields

2. config_entries/flow/configure  params: {flow_id: "xxx", host: "192.168.1.100", token: "..."}
   → Done or next step
```

Do NOT call list/get/descriptions first — go straight to flow/init.

## Check Existing

```json
{"action": "config_entries/get", "params": {"domain": "xiaomi_miio"}}
```

## Options Flow (multi-step wizard)

```
1. config_entries/options/init  params: {entry_id: "abc123"}
   → Returns flow_id + form or menu

2. If menu: pick next_step_id from menu_options → options/configure
   If form: fill fields → options/configure
   Keep calling options/configure with SAME flow_id until "create_entry" (done).
```

If options/init returns "Invalid handler", the integration uses **subentries** instead.

## Subentry Flow

```
1. config_entries/get_supported_subentry_types  params: {entry_id: "abc123"}
2. config_entries/subentries/list  params: {entry_id: "abc123"}
3. config_entries/subentries/flow/init  params: {entry_id: "abc123", subentry_type: "..."}
   → Returns flow_id + form
4. config_entries/subentries/flow/configure  params: {flow_id: "xxx", field1: value1}
```

Modify existing: add `subentry_id` to step 3. Delete: `config_entries/subentries/delete` with `entry_id` + `subentry_id`.

## Other Actions

```json
{"action": "config_entries/delete", "params": {"entry_id": "abc123"}}
{"action": "config_entries/reload", "params": {"entry_id": "abc123"}}
{"action": "config_entries/flow/init", "params": {"handler": "domain", "entry_id": "abc123"}}
```

Last example is **reconfigure** — same as install but with existing entry_id.

## Discipline

1. Follow exact workflow steps — no exploratory calls
2. Only change what user asked for
3. Check `response.config_method` to pick options vs subentries
4. When response contains `next_action`, follow it exactly
5. Don't stop mid-flow — incomplete flows are discarded
