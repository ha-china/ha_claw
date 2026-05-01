

from __future__ import annotations

from .skill_store import (
    load_homeassistant_priority_skill_block,
    load_master_prompt,
    load_runtime_prompt_doc,
    load_skill_catalog_prompt,
)


_SKILL_INDEX_GUIDANCE = (
    "Skill bodies are not in prompt. Fetch relevant ones with "
    "`GetInstalledSkill(name=\"<slug>\")`; do not assume contents."
)


def build_master_prompt_sections(*, user_text: str = "") -> tuple[str, ...]:

    del user_text  # skills now always ship as an index, never keyword-matched bodies

    sections: list[str] = []

    priority_skill_block = load_homeassistant_priority_skill_block()
    if priority_skill_block:
        sections.append(priority_skill_block)

    master_prompt = load_master_prompt()
    if master_prompt:
        sections.append(master_prompt)

    memory_routing_guidance = load_runtime_prompt_doc("memory_routing")
    if memory_routing_guidance:
        sections.append(memory_routing_guidance)

    skill_catalog = load_skill_catalog_prompt(exclude_homeassistant_priority=True)
    if skill_catalog:
        sections.append(
            f"## Installed Skill Index\n{skill_catalog}\n\n{_SKILL_INDEX_GUIDANCE}"
        )

    return tuple(section for section in sections if section.strip())


def apply_master_prompt_layers(base_prompt: str, *, user_text: str = "") -> str:

    sections = [base_prompt, *build_master_prompt_sections(user_text=user_text)]
    return "\n\n".join(section for section in sections if section.strip())
