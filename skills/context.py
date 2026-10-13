"""Skill Context: the deterministic text a Skill contributes to COGNITION (above Proposal, nowhere else).

Pure function of a SkillDefinition. It carries no provenance, no tool list, no permission and no instruction to execute:
a Skill context only shapes what the model is asked to think about.
"""
from .contract import SkillDefinition


EPISTEMIC_FRAMING = (
    "The following Skill is procedural guidance for how you should perform the task.\n\n"
    "Its rules and procedures are instructions to you. They are NOT facts about the user's "
    "organization, policies, environment, systems, or constraints.\n\n"
    "Do not state or imply that the organization adopted these rules unless that fact is "
    "explicitly present in task context.\n\n"
    "Apply the procedure without attributing the Skill itself as a source or organizational policy.\n\n"
)


def _numbered(items) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(items, 1))


def _render_skill_body(skill: SkillDefinition) -> str:
    if not isinstance(skill, SkillDefinition):
        raise TypeError("render_skill_context needs a SkillDefinition")
    return (
        f"# Skill: {skill.name}\n"
        f"Domain: {skill.domain}\n"
        f"{skill.description}\n\n"
        f"## Mission\n{skill.mission}\n\n"
        f"## Critical rules\n{_numbered(skill.critical_rules)}\n\n"
        f"## Workflow\n{_numbered(skill.workflow)}\n\n"
        f"## Deliverables\n{_numbered(skill.deliverables)}\n\n"
        f"## Evaluation criteria\n{_numbered(skill.evaluation_criteria)}\n"
    )


def render_legacy_skill_context(skill: SkillDefinition) -> str:
    """Render the pre-framing form for reproducing frozen historical pilots."""
    return _render_skill_body(skill)


def render_skill_context(skill: SkillDefinition) -> str:
    """Render procedural guidance with an explicit boundary against epistemic overclaiming."""
    return EPISTEMIC_FRAMING + _render_skill_body(skill)
