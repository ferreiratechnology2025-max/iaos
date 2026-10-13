"""Skill contract - Phase 0.

A Skill is EDITORIAL COGNITION CONTEXT: text that shapes what the model is asked to think about.
It sits above Proposal and nowhere else:

    Intent -> Skill Resolution -> Skill Context -> Cognition -> Proposal -> Governance -> Execution

Skill != agent runtime != tool != capability != authority.  The schema is CLOSED and deliberately has no
field for tools, permissions, capabilities, credentials, AuthorizedAction, ExecutionEnvelope or hooks:
a document that carries one is rejected, not ignored.  This module imports only the standard library.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Tuple

SKILL_SCHEMA_VERSION = 1

_SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_PROVENANCE_FIELDS = ("source", "source_path", "source_revision")
_SKILL_FIELDS = (
    "schema_version", "skill_id", "name", "domain", "description",
    "mission", "critical_rules", "workflow", "deliverables", "evaluation_criteria",
    "provenance",
)
_LIST_FIELDS = ("critical_rules", "workflow", "deliverables", "evaluation_criteria")


class SkillContractError(ValueError):
    """The document is not a valid, closed SkillDefinition."""


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise SkillContractError(f"invalid:{name}")
    return value


def _texts(name: str, value: Any) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) == 0:
        raise SkillContractError(f"invalid:{name}")
    out = [_text(f"{name}[]", item) for item in value]
    return tuple(out)


def _closed(name: str, doc: Any, allowed: Tuple[str, ...]) -> None:
    if not isinstance(doc, Mapping):
        raise SkillContractError(f"invalid:{name}")
    keys = set(doc.keys())
    expected = set(allowed)
    if keys != expected:
        unknown = sorted(str(k) for k in keys - expected)
        missing = sorted(expected - keys)
        raise SkillContractError(f"closed:{name}:unknown={unknown}:missing={missing}")


@dataclass(frozen=True)
class SkillProvenance:
    source: str
    source_path: str
    source_revision: str

    def __post_init__(self) -> None:
        _text("provenance.source", self.source)
        _text("provenance.source_path", self.source_path)
        _text("provenance.source_revision", self.source_revision)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in _PROVENANCE_FIELDS}

    @classmethod
    def from_dict(cls, doc: Any) -> "SkillProvenance":
        _closed("provenance", doc, _PROVENANCE_FIELDS)
        return cls(**{k: doc[k] for k in _PROVENANCE_FIELDS})


@dataclass(frozen=True)
class SkillDefinition:
    schema_version: int
    skill_id: str
    name: str
    domain: str
    description: str
    mission: str
    critical_rules: Tuple[str, ...]
    workflow: Tuple[str, ...]
    deliverables: Tuple[str, ...]
    evaluation_criteria: Tuple[str, ...]
    provenance: SkillProvenance

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SKILL_SCHEMA_VERSION:
            raise SkillContractError("invalid:schema_version")
        if not isinstance(self.skill_id, str) or _SKILL_ID.match(self.skill_id) is None:
            raise SkillContractError("invalid:skill_id")
        for name in ("name", "domain", "description", "mission"):
            _text(name, getattr(self, name))
        for name in _LIST_FIELDS:
            object.__setattr__(self, name, _texts(name, getattr(self, name)))
        if not isinstance(self.provenance, SkillProvenance):
            raise SkillContractError("invalid:provenance")

    def to_dict(self) -> dict:
        out: dict = {}
        for name in _SKILL_FIELDS:
            value = getattr(self, name)
            if name == "provenance":
                out[name] = value.to_dict()
            elif name in _LIST_FIELDS:
                out[name] = list(value)
            else:
                out[name] = value
        return out

    @classmethod
    def from_dict(cls, doc: Any) -> "SkillDefinition":
        _closed("skill", doc, _SKILL_FIELDS)
        values = {k: doc[k] for k in _SKILL_FIELDS}
        values["provenance"] = SkillProvenance.from_dict(values["provenance"])
        return cls(**values)
