<!-- version: 1 -->
# Self-Edit Tools (Proposal System)

All self-edits go through proposal → approval workflow.

## ReviewSelfSkills

Get reflection briefing before proposing edits.

```json
{"limit": 10}
```

Returns: skills, guide docs, recent changelog, pending proposals.

## ProposeSelfEdit

Stage a proposal (never writes directly).

| Param | Description |
|-------|-------------|
| target_type | skill / guide / memory |
| target_id | slug / relative_path / key |
| action | create / update / delete |
| markdown | New content |
| reason | Why this change |

```json
{
  "target_type": "skill",
  "target_id": "my-skill",
  "action": "update",
  "markdown": "# Updated Content",
  "reason": "Fixed typo"
}
```

## ListProposals

List pending proposals. No params.

## GetProposal

Read proposal body.

```json
{"slug": "proposal-slug"}
```

## ApplyProposal

Approve and apply.

```json
{"slug": "proposal-slug", "approved_by": "user"}
```

## DiscardProposal

Remove without applying.

```json
{"slug": "proposal-slug"}
```

## GetSelfChangelog

Read audit log.

```json
{"limit": 20, "target_type": "skill"}
```

## Guide Doc Tools

### UpsertGuideDoc

Create/overwrite runtime guide.

```json
{"relative_path": "80_custom.md", "markdown": "...", "reason": "Added custom guide"}
```

### DeleteGuideDoc

Delete runtime guide (source/ protected).

```json
{"relative_path": "80_custom.md", "reason": "No longer needed"}
```
