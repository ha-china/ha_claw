

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util.json import JsonObjectType

from ..runtime.ha_guide_store import (
    async_delete_runtime_guide_doc,
    async_upsert_runtime_guide_doc,
    get_homeassistant_guide_doc,
    list_homeassistant_guide_docs,
)
from ..runtime.self_edit import (
    async_apply_proposal,
    async_discard_proposal,
    async_list_proposals,
    async_read_changelog,
    async_read_proposal,
    async_stage_proposal,
)
from ..runtime.memory_store import (
    async_delete_memory_entry,
    async_get_memory_entry,
    async_list_memory_entries,
    async_save_memory_entry_result,
)
from ..runtime.skill_store import (
    async_delete_skill,
    async_install_skill,
    get_installed_skill,
    list_installed_skills,
)
from ..runtime.text_patch import PatchError, apply_patches

_LOGGER = logging.getLogger(__name__)







class DeleteSkillTool(llm.Tool):
    name = "DeleteSkill"
    description = (
        "Delete one installed Markdown skill. Every deletion is audited in "
        "changelog.jsonl. Params: name (skill slug or file stem), reason (optional)"
    )
    parameters = vol.Schema(
        {
            vol.Required("name"): str,
            vol.Optional("reason", default=""): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        name = (args.get("name") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not name:
            return {"success": False, "error": "name is required"}
        try:
            path = await async_delete_skill(hass, name, reason=reason)
        except FileNotFoundError as err:
            return {"success": False, "error": str(err)}
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {
            "success": True,
            "deleted": path.stem,
            "path": str(path),
            "message": f"Skill deleted: {path.stem}",
        }







class UpsertGuideDocTool(llm.Tool):
    name = "UpsertGuideDoc"
    description = (
        "Create or overwrite one runtime Home Assistant guide Markdown doc. "
        "Only writes under data/homeassistant_guide/runtime/. "
        "Params: relative_path (e.g. '30_safety_and_workflows.md'), markdown, reason (optional)"
    )
    parameters = vol.Schema(
        {
            vol.Required("relative_path"): str,
            vol.Required("markdown"): str,
            vol.Optional("reason", default=""): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        relative_path = (args.get("relative_path") or "").strip()
        markdown = args.get("markdown") or ""
        reason = (args.get("reason") or "").strip()
        try:
            path = await async_upsert_runtime_guide_doc(
                hass, relative_path, markdown, reason=reason
            )
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {
            "success": True,
            "path": str(path),
            "relative_path": relative_path,
            "message": f"Guide doc upserted: runtime/{relative_path}",
        }


class DeleteGuideDocTool(llm.Tool):
    name = "DeleteGuideDoc"
    description = (
        "Delete one runtime Home Assistant guide Markdown doc. "
        "Cannot remove anything under source/ (those are pristine originals). "
        "Params: relative_path, reason (optional)"
    )
    parameters = vol.Schema(
        {
            vol.Required("relative_path"): str,
            vol.Optional("reason", default=""): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        relative_path = (args.get("relative_path") or "").strip()
        reason = (args.get("reason") or "").strip()
        try:
            path = await async_delete_runtime_guide_doc(
                hass, relative_path, reason=reason
            )
        except FileNotFoundError as err:
            return {"success": False, "error": str(err)}
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {
            "success": True,
            "path": str(path),
            "relative_path": relative_path,
            "message": f"Guide doc deleted: runtime/{relative_path}",
        }







class GetSelfChangelogTool(llm.Tool):
    name = "GetSelfChangelog"
    description = (
        "Read the append-only self-edit audit log. Use this to answer "
        "'what did I change last time?'. Params: limit (default 20), "
        "target_type (optional: skill|guide)"
    )
    parameters = vol.Schema(
        {
            vol.Optional("limit", default=20): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=200)
            ),
            vol.Optional("target_type", default=""): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        limit = int(args.get("limit") or 20)
        target_type_raw = (args.get("target_type") or "").strip().lower()
        target_type = target_type_raw or None
        entries = await async_read_changelog(
            hass, limit=limit, target_type=target_type
        )
        return {
            "success": True,
            "count": len(entries),
            "target_type_filter": target_type,
            "entries": entries,
        }







class ProposeSelfEditTool(llm.Tool):
    name = "ProposeSelfEdit"
    description = (
        "Stage a self-edit proposal for human approval. Use this during "
        "reflection instead of editing directly. Covers self-evolution "
        "(skills/guides) and memory hygiene (purification + boundary). "
        "Params: target_type (skill|guide|memory), target_id "
        "(skill slug, guide relative_path, OR memory key), "
        "action (create|update|patch|delete), markdown (required for create/update; "
        "for memory it is the new value), reason. "
        "PATCH-FIRST RULE: When modifying an existing skill or guide, prefer "
        "action=patch with anchor-based ops instead of re-emitting the full markdown. "
        "patch params: patches=[{op, anchor, new_text, occurrence?, regex?, count?}], dry_run=true/false. "
        "Ops: replace | insert_before | insert_after | delete | prepend | append. "
        "Use GetInstalledSkill first to read the current content and locate exact anchors."
    )
    parameters = vol.Schema(
        {
            vol.Required("target_type"): str,
            vol.Required("target_id"): str,
            vol.Required("action"): str,
            vol.Optional("markdown", default=""): str,
            vol.Required("reason"): str,
            vol.Optional("patches", default=[]): list,
            vol.Optional("dry_run", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        target_type = (args.get("target_type") or "").strip().lower()
        target_id = (args.get("target_id") or "").strip()
        action = (args.get("action") or "").strip().lower()
        markdown = args.get("markdown") or ""
        reason = (args.get("reason") or "").strip()
        patches = args.get("patches", [])
        dry_run = bool(args.get("dry_run", False))

        if action == "patch":
            return await self._patch_target(
                hass, target_type, target_id, patches, reason, dry_run,
            )

        try:
            proposal = await async_stage_proposal(
                hass,
                target_type=target_type,
                target_id=target_id,
                action=action,
                proposed_markdown=markdown,
                reason=reason,
                slug_hint=f"{target_type}-{target_id}-{action}",
            )
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {
            "success": True,
            "message": (
                f"Proposal staged for {target_type}/{target_id} ({action}). "
                f"Awaiting human approval via ApplyProposal."
            ),
            **proposal,
        }

    async def _patch_target(
        self,
        hass: HomeAssistant,
        target_type: str,
        target_id: str,
        patches: list,
        reason: str,
        dry_run: bool,
    ) -> JsonObjectType:
        if not isinstance(patches, list) or not patches:
            return {"success": False, "error": "'patches' must be a non-empty list"}
        if not target_id:
            return {"success": False, "error": "'target_id' is required"}

        if target_type == "skill":
            try:
                skill_data = await hass.async_add_executor_job(get_installed_skill, target_id)
            except (ValueError, FileNotFoundError) as err:
                return {"success": False, "error": str(err)}
            original = skill_data.get("markdown", "")
        elif target_type == "guide":
            try:
                doc = get_homeassistant_guide_doc(target_id)
            except (ValueError, FileNotFoundError) as err:
                return {"success": False, "error": str(err)}
            original = doc.get("markdown", "") if doc else ""
        else:
            return {"success": False, "error": f"patch not supported for target_type={target_type!r}; use update instead"}

        if not original:
            return {"success": False, "error": f"{target_type}/{target_id} has no content to patch"}

        label = f"{target_type}/{target_id}"
        try:
            report = apply_patches(original, patches, label=label)
        except PatchError as err:
            return {"success": False, "error": str(err), **err.to_dict()}

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "report": report.to_dict(),
                "preview_after": report.after[:3000],
            }

        try:
            proposal = await async_stage_proposal(
                hass,
                target_type=target_type,
                target_id=target_id,
                action="update",
                proposed_markdown=report.after,
                reason=f"[patch] {reason}" if reason else "[patch]",
                slug_hint=f"{target_type}-{target_id}-patch",
            )
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {
            "success": True,
            "message": (
                f"Patch proposal staged for {target_type}/{target_id} "
                f"({len(report.applied)} ops). Awaiting human approval via ApplyProposal."
            ),
            "report": report.to_dict(),
            **proposal,
        }


class ListProposalsTool(llm.Tool):
    name = "ListProposals"
    description = "List pending self-edit proposals. No parameters."
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        proposals = await async_list_proposals(hass)
        return {
            "success": True,
            "count": len(proposals),
            "proposals": proposals,
        }


class GetProposalTool(llm.Tool):
    name = "GetProposal"
    description = "Read the full body of one pending proposal. Params: slug"
    parameters = vol.Schema({vol.Required("slug"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        slug = (tool_input.tool_args.get("slug") or "").strip()
        try:
            proposal = await async_read_proposal(hass, slug)
        except FileNotFoundError as err:
            return {"success": False, "error": str(err)}
        return {"success": True, **proposal}


class DiscardProposalTool(llm.Tool):
    name = "DiscardProposal"
    description = (
        "Remove one pending proposal without applying it. "
        "Params: slug"
    )
    parameters = vol.Schema({vol.Required("slug"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        slug = (tool_input.tool_args.get("slug") or "").strip()
        removed = await async_discard_proposal(hass, slug)
        if not removed:
            return {"success": False, "error": f"Proposal not found: {slug}"}
        return {"success": True, "slug": slug, "discarded": True}


async def _apply_skill_proposal(
    hass: HomeAssistant, frontmatter: dict[str, Any], body: str
) -> dict[str, Any]:
    action = str(frontmatter.get("action") or "").lower()
    target_id = str(frontmatter.get("target_id") or "").strip()
    approver = str(frontmatter.get("approved_by") or "human")
    reason = f"approved proposal: {frontmatter.get('reason', '')}".strip()
    if not target_id:
        raise ValueError("target_id is missing from proposal frontmatter")
    if action in {"create", "update"}:
        path = await async_install_skill(
            hass,
            target_id,
            body,
            overwrite=True,
            actor=f"approved_by:{approver}",
            reason=reason,
        )
        return {"action": action, "path": str(path)}
    if action == "delete":
        path = await async_delete_skill(
            hass, target_id, actor=f"approved_by:{approver}", reason=reason
        )
        return {"action": "delete", "path": str(path)}
    raise ValueError(f"Unsupported skill action: {action!r}")


async def _apply_guide_proposal(
    hass: HomeAssistant, frontmatter: dict[str, Any], body: str
) -> dict[str, Any]:
    action = str(frontmatter.get("action") or "").lower()
    target_id = str(frontmatter.get("target_id") or "").strip()
    approver = str(frontmatter.get("approved_by") or "human")
    reason = f"approved proposal: {frontmatter.get('reason', '')}".strip()
    if not target_id:
        raise ValueError("target_id is missing from proposal frontmatter")



    relative_path = target_id.split("/", 1)[1] if "/" in target_id else target_id

    if action in {"create", "update"}:
        path = await async_upsert_runtime_guide_doc(
            hass,
            relative_path,
            body,
            actor=f"approved_by:{approver}",
            reason=reason,
        )
        return {"action": action, "path": str(path)}
    if action == "delete":
        path = await async_delete_runtime_guide_doc(
            hass,
            relative_path,
            actor=f"approved_by:{approver}",
            reason=reason,
        )
        return {"action": "delete", "path": str(path)}
    raise ValueError(f"Unsupported guide action: {action!r}")


async def _apply_memory_proposal(
    hass: HomeAssistant, frontmatter: dict[str, Any], body: str
) -> dict[str, Any]:
    action = str(frontmatter.get("action") or "").lower()
    target_id = str(frontmatter.get("target_id") or "").strip()
    approver = str(frontmatter.get("approved_by") or "human")
    reason = f"approved proposal: {frontmatter.get('reason', '')}".strip()
    if not target_id:
        raise ValueError("target_id (memory key) is missing from proposal frontmatter")

    if action == "delete":
        path, deleted = await async_delete_memory_entry(hass, target_id)
        return {
            "action": "delete",
            "path": str(path),
            "deleted": deleted,
            "approver": approver,
            "reason": reason,
        }
    if action in {"create", "update"}:
        if not body.strip():
            raise ValueError("memory proposal body must contain the new value")
        result = await async_save_memory_entry_result(hass, target_id, body)
        return {
            "action": action,
            "path": result.get("path", ""),
            "key": result.get("key", target_id),
            "status": result.get("status", ""),
            "approver": approver,
            "reason": reason,
        }
    raise ValueError(f"Unsupported memory action: {action!r}")


_EXECUTORS = {
    "skill": _apply_skill_proposal,
    "guide": _apply_guide_proposal,
    "memory": _apply_memory_proposal,
}


class ApplyProposalTool(llm.Tool):
    name = "ApplyProposal"
    description = (
        "Approve and apply one pending proposal. Mutations flow through the "
        "regular Upsert/Delete helpers so the changelog records the approver. "
        "Params: slug, approved_by (default 'human')"
    )
    parameters = vol.Schema(
        {
            vol.Required("slug"): str,
            vol.Optional("approved_by", default="human"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        args = tool_input.tool_args
        slug = (args.get("slug") or "").strip()
        approved_by = (args.get("approved_by") or "human").strip() or "human"
        try:
            result = await async_apply_proposal(
                hass, slug, _EXECUTORS, approved_by=approved_by
            )
        except FileNotFoundError as err:
            return {"success": False, "error": str(err)}
        except ValueError as err:
            return {"success": False, "error": str(err)}
        return {"success": True, **result}







class ReviewSelfSkillsTool(llm.Tool):
    name = "ReviewSelfSkills"
    description = (
        "Return a compact self-critique briefing covering skills, guide docs, "
        "self-edit changelog AND the curated memory state (key list, char "
        "usage, duplicate-suspect hints). Use this at the end of a "
        "conversation (or when failures repeat) to decide whether any "
        "skill/guide/memory entry needs updating, deleting, or creating, "
        "then stage proposals via ProposeSelfEdit (do NOT edit directly). "
        "Memory hygiene covers self-purification (drop dups/stale), "
        "self-evolution (consolidate fragments), and self-boundary "
        "(only stable user-level facts belong here; transient or task "
        "context belongs in conversation history or graph memory). "
        "Params: limit (default 10)"
    )
    parameters = vol.Schema(
        {
            vol.Optional("limit", default=10): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=100)
            ),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        limit = int(tool_input.tool_args.get("limit") or 10)
        skills = await hass.async_add_executor_job(list_installed_skills)
        guide_docs = await hass.async_add_executor_job(list_homeassistant_guide_docs)
        recent_changes = await async_read_changelog(hass, limit=limit)
        pending = await async_list_proposals(hass)
        memory_entries = await async_list_memory_entries(hass)
        memory_snapshot = _build_memory_snapshot(memory_entries)
        return {
            "success": True,
            "instructions": (
                "Review installed skills, guides, recent self-edits AND curated "
                "memory. Stage every change via ProposeSelfEdit (never edit "
                "directly in this reflection turn). For memory: enforce "
                "self-purification (drop dups/stale), self-evolution "
                "(consolidate fragments under canonical keys), and "
                "self-boundary (stable user-level facts only). A human "
                "approves or discards each proposal."
            ),
            "installed_skills": [
                {"slug": s.get("slug"), "title": s.get("title"), "description": s.get("description")}
                for s in skills
            ],
            "guide_runtime_docs": [
                {"path": g.get("path"), "title": g.get("title")}
                for g in guide_docs
                if g.get("collection") == "runtime"
            ],
            "recent_changes": recent_changes,
            "pending_proposals": pending,
            "memory_snapshot": memory_snapshot,
        }


def _build_memory_snapshot(entries: list[dict[str, str]]) -> dict[str, Any]:
    total_chars = sum(len(e.get("key", "")) + len(e.get("value", "")) + 6 for e in entries)
    seen: dict[str, list[str]] = {}
    for entry in entries:
        normalized = " ".join(str(entry.get("value", "")).lower().split())
        seen.setdefault(normalized, []).append(entry.get("key", ""))
    duplicate_suspects = [
        {"value_preview": value[:60], "keys": keys}
        for value, keys in seen.items()
        if value and len(keys) > 1
    ]
    return {
        "entry_count": len(entries),
        "total_chars": total_chars,
        "keys": [entry.get("key") for entry in entries],
        "entries": [
            {"key": entry.get("key"), "value_preview": entry.get("value", "")[:120]}
            for entry in entries
        ],
        "duplicate_suspects": duplicate_suspects,
        "boundary_policy": (
            "Keep only stable, user-level, durable facts. "
            "Move task-specific or session context to conversation history. "
            "Move structured/relational knowledge to MemoryGraph."
        ),
    }
