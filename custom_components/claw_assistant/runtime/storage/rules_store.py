"""Hard-boundary rules store.

Rules are persisted as YAML frontmatter in ``data/rules/RULES.md`` under the
integration data directory (``get_data_dir()``). The frontmatter is the single
source of truth; the Markdown body below the frontmatter is display-only
documentation for humans and is preserved across writes.

Rules are injected into the AI system prompt by ``master_prompt`` (see
``render_rules_prompt``). Whenever a rule is mutated (toggle / add / delete)
the master prompt cache is invalidated so the change takes effect on the next
prompt build without a restart.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

try:
    import yaml
except ImportError:  # pragma: no cover - HA always ships PyYAML
    yaml = None

# Relative path of the rules document under the data directory.
_RULES_RELATIVE_PATH = Path("rules") / "RULES.md"

# Matches a YAML frontmatter block: ---\n ... \n---\n?
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", flags=re.DOTALL)

# Canonical default rules, mirrored by the bundled document
# ``runtime/data/rules/RULES.md`` which ``init_storage`` seeds into the data
# directory on first run.
DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "rule_001",
        "enabled": True,
        "category": "always",
        "description": "回复使用中文，除非用户使用其他语言",
    },
    {
        "id": "rule_002",
        "enabled": True,
        "category": "never",
        "description": "不要删除或修改自动化规则",
    },
    {
        "id": "rule_003",
        "enabled": False,
        "category": "never",
        "description": "不要操作安全相关设备（门锁、警报）",
    },
    {
        "id": "rule_004",
        "enabled": True,
        "category": "reply",
        "description": "控制设备时，回复控制在 20 字以内",
    },
)

# Display-only Markdown body appended below the frontmatter.
_DEFAULT_BODY = (
    "## 硬边界规则\n"
    "\n"
    "本文件的 YAML frontmatter 是规则的唯一数据源；下方正文仅作说明，修改正文不会生效。\n"
    "规则会注入 AI 的 system prompt，启用后 AI 必须无条件遵守。\n"
)

# Rendering order / labels for the prompt section.
_CATEGORY_ORDER: tuple[str, ...] = ("always", "never", "reply")
_CATEGORY_LABELS: dict[str, str] = {
    "always": "始终遵循",
    "never": "绝对禁止",
    "reply": "回复要求",
}


def _rules_path() -> Path:
    """Return the absolute path of the rules document."""
    return get_data_dir() / _RULES_RELATIVE_PATH


def _ensure_rules_dir() -> Path:
    """Create the rules directory if missing and return the target path."""
    path = _rules_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        LOGGER.warning("Failed to create rules directory %s: %s", path.parent, exc)
    return path


def _read_document() -> str:
    """Read the raw rules document text; empty string when missing/unreadable."""
    try:
        path = _rules_path()
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_rules(content: str) -> list[dict[str, Any]]:
    """Parse the YAML frontmatter of the document into cleaned rule dicts."""
    if yaml is None:
        return []
    match = _FRONTMATTER_RE.match(content or "")
    if not match:
        return []
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except Exception as exc:
        LOGGER.warning("Failed to parse rules frontmatter: %s", exc)
        return []
    raw_rules = data.get("rules") or []
    if not isinstance(raw_rules, list):
        return []
    rules: list[dict[str, Any]] = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("id") or f"rule_{index + 1:03d}")
        enabled = bool(item.get("enabled", True))
        category = str(item.get("category") or "always").strip() or "always"
        description = str(item.get("description") or "").strip()
        rules.append({
            "id": rule_id,
            "enabled": enabled,
            "category": category,
            "description": description,
        })
    return rules


def _serialize_document(rules: list[dict[str, Any]], body: str = "") -> str:
    """Serialize rules into the frontmatter document format."""
    frontmatter = ""
    if yaml is not None:
        frontmatter = yaml.safe_dump(
            {"rules": rules},
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    body_text = (body or _DEFAULT_BODY).strip()
    return f"---\n{frontmatter}---\n\n{body_text}\n"


def _write_document(rules: list[dict[str, Any]]) -> None:
    """Persist rules to the document, preserving the existing body when present."""
    existing = ""
    try:
        path = _rules_path()
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            match = _FRONTMATTER_RE.match(raw)
            if match:
                existing = raw[match.end():].strip()
    except OSError:
        pass
    path = _ensure_rules_dir()
    try:
        path.write_text(_serialize_document(rules, existing), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("Failed to write rules document %s: %s", path, exc)


def _invalidate_master_prompt_cache() -> None:
    """Invalidate the master prompt cache so rule changes take effect."""
    try:
        from ..llm.master_prompt import invalidate_master_prompt_cache
        invalidate_master_prompt_cache()
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.debug("Failed to invalidate master prompt cache: %s", exc)


def load_rules() -> list[dict[str, Any]]:
    """Load all rules from disk (file missing -> empty list, no error)."""
    return _parse_rules(_read_document())


def list_rules() -> list[dict[str, Any]]:
    """Public listing API — same as :func:`load_rules`.

    The document is seeded with the bundled defaults by ``init_storage`` and
    recreated on the first write; read-only calls never create the file.
    """
    return load_rules()


def get_active_rules(category: str | None = None) -> list[dict[str, Any]]:
    """Return enabled rules, optionally filtered by category."""
    rules = load_rules()
    result = [r for r in rules if r.get("enabled")]
    if category:
        result = [r for r in result if r.get("category") == category]
    return result


def render_rules_prompt() -> str:
    """Render enabled rules as a system-prompt section grouped by category.

    Returns an empty string when there are no enabled rules, so callers can
    skip the section entirely.
    """
    rules = get_active_rules()
    if not rules:
        return ""
    groups: dict[str, list[str]] = {}
    for rule in rules:
        groups.setdefault(rule["category"], []).append(rule["description"])
    ordered = [
        cat for cat in _CATEGORY_ORDER if cat in groups
    ] + sorted(cat for cat in groups if cat not in _CATEGORY_ORDER)
    lines = [
        "## 硬边界规则（Hard Boundary Rules）",
        "以下规则是硬性约束，AI 必须无条件遵守，任何情况下都不得违反：",
    ]
    for cat in ordered:
        lines.append(f"### {_CATEGORY_LABELS.get(cat, cat)}")
        for description in groups[cat]:
            lines.append(f"- {description}")
    return "\n".join(lines)


def toggle_rule(rule_id: str, enabled: bool) -> bool:
    """Enable/disable a rule by id. Returns False when the id is unknown."""
    rules = load_rules()
    found = False
    for rule in rules:
        if rule["id"] == rule_id:
            rule["enabled"] = bool(enabled)
            found = True
            break
    if not found:
        return False
    _write_document(rules)
    _invalidate_master_prompt_cache()
    return True


def add_rule(category: str, description: str) -> dict[str, Any] | None:
    """Append a new enabled rule. Returns the created rule or None on error."""
    category = str(category or "").strip() or "always"
    description = str(description or "").strip()
    if not description:
        return None
    rules = load_rules()
    used = {rule["id"] for rule in rules}
    counter = 1
    while f"rule_{counter:03d}" in used:
        counter += 1
    rule: dict[str, Any] = {
        "id": f"rule_{counter:03d}",
        "enabled": True,
        "category": category,
        "description": description,
    }
    rules.append(rule)
    _write_document(rules)
    _invalidate_master_prompt_cache()
    return rule


def delete_rule(rule_id: str) -> bool:
    """Remove a rule by id. Returns False when the id is unknown."""
    rules = load_rules()
    before = len(rules)
    rules = [rule for rule in rules if rule["id"] != rule_id]
    if len(rules) == before:
        return False
    _write_document(rules)
    _invalidate_master_prompt_cache()
    return True
