from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any


VALID_OPS = frozenset({
    "replace",
    "insert_before",
    "insert_after",
    "delete",
    "prepend",
    "append",
    "create",
})

_ANCHORLESS_OPS = frozenset({"prepend", "append", "create"})


class PatchError(ValueError):

    def __init__(self, message: str, *, index: int, patch: dict[str, Any], hint: str = ""):
        super().__init__(message)
        self.index = index
        self.patch = patch
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "patch_index": self.index,
            "patch": _safe_patch_echo(self.patch),
            "hint": self.hint,
        }


@dataclass
class PatchReport:
    before: str
    after: str
    applied: list[dict[str, Any]] = field(default_factory=list)
    diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_chars": len(self.before),
            "after_chars": len(self.after),
            "applied": self.applied,
            "diff": self.diff,
        }




def apply_patches(
    text: str,
    patches: list[dict[str, Any]],
    *,
    label: str = "text",
    diff_context: int = 2,
    diff_limit: int = 4000,
) -> PatchReport:
    if not isinstance(patches, list) or not patches:
        raise PatchError(
            "patches must be a non-empty list",
            index=-1,
            patch={},
            hint="Pass at least one patch operation.",
        )

    current = text
    applied: list[dict[str, Any]] = []

    for i, raw in enumerate(patches):
        if not isinstance(raw, dict):
            raise PatchError(
                f"patch #{i} must be a dict",
                index=i,
                patch={"_raw": repr(raw)[:120]},
            )
        op = raw.get("op")
        if op not in VALID_OPS:
            raise PatchError(
                f"patch #{i}: unknown op {op!r}",
                index=i,
                patch=raw,
                hint=f"Valid ops: {sorted(VALID_OPS)}",
            )

        new_current, summary = _apply_one(current, raw, index=i)
        applied.append(summary)
        current = new_current

    return PatchReport(
        before=text,
        after=current,
        applied=applied,
        diff=_unified_diff(text, current, label=label, n=diff_context, limit=diff_limit),
    )




def _apply_one(text: str, patch: dict[str, Any], *, index: int) -> tuple[str, dict[str, Any]]:
    op: str = patch["op"]
    new_text: str = patch.get("new_text", "") or ""

    if op == "create":
        if text.strip() and not patch.get("force"):
            raise PatchError(
                "create refused: text is not empty",
                index=index,
                patch=patch,
                hint="Use op=replace with an anchor, or pass force=true to overwrite.",
            )
        return new_text, {"op": op, "chars": len(new_text)}

    if op == "prepend":
        return new_text + text, {"op": op, "chars": len(new_text)}

    if op == "append":
        return text + new_text, {"op": op, "chars": len(new_text)}

    anchor = patch.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise PatchError(
            f"patch #{index}: op={op} requires a non-empty 'anchor'",
            index=index,
            patch=patch,
            hint="Anchor must be a literal substring (or regex if regex=true).",
        )

    spans = _find_spans(text, anchor, is_regex=bool(patch.get("regex")))
    if not spans:
        raise PatchError(
            f"patch #{index}: anchor not found",
            index=index,
            patch=patch,
            hint=_suggest_near(text, anchor),
        )

    expected = patch.get("count")
    if isinstance(expected, int) and expected >= 0 and len(spans) != expected:
        raise PatchError(
            f"patch #{index}: expected count={expected} matches, got {len(spans)}",
            index=index,
            patch=patch,
        )

    occurrence = patch.get("occurrence", "unique")
    chosen = _pick_spans(spans, occurrence, index=index, patch=patch)

    result = text
    for start, end in sorted(chosen, key=lambda s: s[0], reverse=True):
        result = _splice(result, start, end, new_text, op)

    return result, {
        "op": op,
        "matches": len(spans),
        "applied_to": len(chosen),
        "chars": len(new_text),
    }


def _find_spans(text: str, anchor: str, *, is_regex: bool) -> list[tuple[int, int]]:
    if is_regex:
        try:
            pattern = re.compile(anchor, re.DOTALL)
        except re.error as err:
            raise PatchError(
                f"invalid regex anchor: {err}",
                index=-1,
                patch={"anchor": anchor},
            ) from err
        return [(m.start(), m.end()) for m in pattern.finditer(text)]

    spans: list[tuple[int, int]] = []
    start = 0
    n = len(anchor)
    while True:
        i = text.find(anchor, start)
        if i < 0:
            break
        spans.append((i, i + n))
        start = i + max(1, n)
    return spans


def _pick_spans(
    spans: list[tuple[int, int]],
    occurrence: Any,
    *,
    index: int,
    patch: dict[str, Any],
) -> list[tuple[int, int]]:
    if occurrence == "unique":
        if len(spans) != 1:
            raise PatchError(
                f"patch #{index}: anchor matched {len(spans)} times, expected exactly 1",
                index=index,
                patch=patch,
                hint=(
                    "Pass occurrence='first'|'last'|'all'|<int>, add 'count', "
                    "or extend the anchor with more surrounding context."
                ),
            )
        return spans
    if occurrence == "first":
        return [spans[0]]
    if occurrence == "last":
        return [spans[-1]]
    if occurrence == "all":
        return spans
    if isinstance(occurrence, int):
        if 0 <= occurrence < len(spans):
            return [spans[occurrence]]
        raise PatchError(
            f"patch #{index}: occurrence index {occurrence} out of range (have {len(spans)})",
            index=index,
            patch=patch,
        )
    raise PatchError(
        f"patch #{index}: invalid occurrence {occurrence!r}",
        index=index,
        patch=patch,
        hint="Use 'unique', 'first', 'last', 'all', or an integer.",
    )


def _splice(text: str, start: int, end: int, new_text: str, op: str) -> str:
    if op == "replace":
        return text[:start] + new_text + text[end:]
    if op == "insert_before":
        return text[:start] + new_text + text[start:]
    if op == "insert_after":
        return text[:end] + new_text + text[end:]
    if op == "delete":
        return text[:start] + text[end:]
    raise PatchError(f"unexpected splice op: {op}", index=-1, patch={"op": op})


def _unified_diff(before: str, after: str, *, label: str, n: int, limit: int) -> str:
    if before == after:
        return ""
    b = before.splitlines(keepends=True)
    a = after.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(b, a, fromfile=f"a/{label}", tofile=f"b/{label}", n=n)
    )
    if len(diff) > limit:
        diff = diff[:limit] + "\n... [diff truncated]\n"
    return diff


def _suggest_near(text: str, anchor: str, *, window: int = 40) -> str:
    needle = anchor.strip().splitlines()[0][:40] if anchor else ""
    if not needle:
        return "Anchor is empty or whitespace-only."
    lines = text.splitlines()
    close = difflib.get_close_matches(needle, lines, n=3, cutoff=0.5)
    if close:
        preview = " | ".join(line.strip()[:80] for line in close)
        return f"No exact match. Nearest lines: {preview}"
    head = text[:window].replace("\n", "\\n")
    return f"No exact match and no close lines found. Text starts with: {head!r}"


def _safe_patch_echo(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if isinstance(value, str) and len(value) > 200:
            out[key] = value[:200] + f"... [+{len(value) - 200} chars]"
        else:
            out[key] = value
    return out
