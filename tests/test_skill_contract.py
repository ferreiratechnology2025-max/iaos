"""IAOS_SKILL_CONTRACT_GATE.

SKILL_SCHEMA_CLOSED, SKILL_IMMUTABLE, SKILL_PROVENANCE, SKILL_CAN_INFLUENCE_COGNITION=YES,
SKILL_CAN_CREATE_AUTHORITY / EXECUTE / GRANT_CAPABILITY / BYPASS_GOVERNANCE = NO,
GOVERNANCE / RUNTIME / ASSURANCE (and audit/evidence/claims) IMPORT SKILLS = NO.

KNOWN LIMIT: the import tests are a lint of intent (AST), not a security boundary.
"""
import ast
import copy
import dataclasses
import json
import pathlib
import sys

import pytest

import skills
from skills import SkillContractError, SkillDefinition, SkillProvenance

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
OTHER_PACKAGES = ("governance", "audit", "evidence", "claims", "assurance")

FORBIDDEN_KEYS = (
    "tools", "permissions", "capabilities", "credentials", "authorized_action",
    "execution_envelope", "runtime_hooks", "hooks", "authority",
)


def valid_dict():
    return {
        "schema_version": 1,
        "skill_id": "test.minimal-change",
        "name": "Test Minimal Change",
        "domain": "engineering",
        "description": "Fixture skill used only by the contract tests.",
        "mission": "Change as little as possible to satisfy the requirement.",
        "critical_rules": ["Touch only files the task requires.", "No drive-by refactors."],
        "workflow": ["Read the requirement.", "Locate the smallest change.", "Apply it."],
        "deliverables": ["A minimal diff."],
        "evaluation_criteria": ["Files touched", "Lines changed", "Requirements met"],
        "provenance": {
            "source": "fixture/example-source",
            "source_path": "engineering/example.md",
            "source_revision": "0000000000000000000000000000000000000000",
        },
    }


def build():
    return SkillDefinition.from_dict(valid_dict())


# ---- SKILL_SCHEMA_CLOSED -------------------------------------------------------------------------

def test_valid_definition_round_trips_through_json():
    d = valid_dict()
    s = SkillDefinition.from_dict(d)
    assert s.to_dict() == d
    assert SkillDefinition.from_dict(json.loads(json.dumps(s.to_dict()))) == s


def test_field_set_is_exactly_the_frozen_contract():
    assert [f.name for f in dataclasses.fields(SkillDefinition)] == [
        "schema_version", "skill_id", "name", "domain", "description",
        "mission", "critical_rules", "workflow", "deliverables", "evaluation_criteria", "provenance",
    ]
    assert [f.name for f in dataclasses.fields(SkillProvenance)] == [
        "source", "source_path", "source_revision",
    ]


@pytest.mark.parametrize("key", FORBIDDEN_KEYS + ("anything_else",))
def test_unknown_key_is_rejected(key):
    d = valid_dict()
    d[key] = ["x"]
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("key", FORBIDDEN_KEYS + ("anything_else",))
def test_unknown_provenance_key_is_rejected(key):
    d = valid_dict()
    d["provenance"][key] = "x"
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("field", list(valid_dict().keys()))
def test_missing_field_is_rejected(field):
    d = valid_dict()
    del d[field]
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("bad", [None, "x", 3, [], True])
def test_non_mapping_is_rejected(bad):
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(bad)


@pytest.mark.parametrize("version", [0, 2, "1", 1.0, True, None])
def test_schema_version_must_be_exactly_integer_one(version):
    d = valid_dict()
    d["schema_version"] = version
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("field", ["name", "domain", "description", "mission"])
@pytest.mark.parametrize("bad", ["", "   ", None, 5, ["x"]])
def test_text_fields_must_be_non_blank_strings(field, bad):
    d = valid_dict()
    d[field] = bad
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("field", ["critical_rules", "workflow", "deliverables", "evaluation_criteria"])
@pytest.mark.parametrize("bad", [[], "rule", None, ["ok", ""], ["ok", 3], [["nested"]], {"a": "b"}])
def test_list_fields_must_be_non_empty_lists_of_non_blank_strings(field, bad):
    d = valid_dict()
    d[field] = bad
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


@pytest.mark.parametrize("sid", ["", "Has Space", "UPPER", "-lead", "a/b", "x" * 65, None, 3])
def test_skill_id_must_be_a_stable_slug(sid):
    d = valid_dict()
    d["skill_id"] = sid
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


def test_direct_construction_is_validated_too():
    with pytest.raises(SkillContractError):
        SkillDefinition(
            schema_version=1, skill_id="ok", name="n", domain="d", description="x", mission="m",
            critical_rules=(), workflow=("w",), deliverables=("d",), evaluation_criteria=("e",),
            provenance=SkillProvenance("s", "p", "r"),
        )
    with pytest.raises(SkillContractError):
        SkillDefinition(
            schema_version=1, skill_id="ok", name="n", domain="d", description="x", mission="m",
            critical_rules=("r",), workflow=("w",), deliverables=("d",), evaluation_criteria=("e",),
            provenance={"source": "s", "source_path": "p", "source_revision": "r"},
        )


# ---- SKILL_IMMUTABLE -----------------------------------------------------------------------------

def test_skill_definition_is_frozen():
    s = build()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.mission = "something else"
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.provenance.source = "elsewhere"
    with pytest.raises(dataclasses.FrozenInstanceError):
        del s.name


def test_collections_are_tuples():
    s = build()
    for name in ("critical_rules", "workflow", "deliverables", "evaluation_criteria"):
        assert isinstance(getattr(s, name), tuple)


def test_input_list_mutation_does_not_change_definition():
    d = valid_dict()
    s = SkillDefinition.from_dict(d)
    before = copy.deepcopy(s.to_dict())
    d["critical_rules"].append("injected later")
    d["workflow"][0] = "rewritten later"
    assert s.to_dict() == before


def test_to_dict_returns_independent_copy():
    s = build()
    out = s.to_dict()
    out["critical_rules"].append("x")
    out["provenance"]["source"] = "y"
    assert s.to_dict() == valid_dict()


# ---- SKILL_PROVENANCE ----------------------------------------------------------------------------

@pytest.mark.parametrize("field", ["source", "source_path", "source_revision"])
def test_provenance_is_required_and_non_empty(field):
    for bad in ("", "  ", None, 7):
        d = valid_dict()
        d["provenance"][field] = bad
        with pytest.raises(SkillContractError):
            SkillDefinition.from_dict(d)
    d = valid_dict()
    del d["provenance"][field]
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


def test_provenance_block_itself_is_required():
    d = valid_dict()
    del d["provenance"]
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)
    d["provenance"] = None
    with pytest.raises(SkillContractError):
        SkillDefinition.from_dict(d)


# ---- capabilities of a Skill ---------------------------------------------------------------------

def test_skill_can_influence_cognition_it_carries_text_for_the_model():
    s = build()
    assert s.mission and s.critical_rules and s.workflow


def test_skill_cannot_execute_or_grant_anything_it_exposes_only_serialization():
    public = {n for n in dir(SkillDefinition) if not n.startswith("_") and callable(getattr(SkillDefinition, n))}
    assert public == {"to_dict", "from_dict"}
    public_p = {n for n in dir(SkillProvenance) if not n.startswith("_") and callable(getattr(SkillProvenance, n))}
    assert public_p == {"to_dict", "from_dict"}


def test_skill_has_no_authority_shaped_field():
    names = {f.name for f in dataclasses.fields(SkillDefinition)} | {f.name for f in dataclasses.fields(SkillProvenance)}
    assert not names & set(FORBIDDEN_KEYS)


# ---- import boundary (lint of intent) ------------------------------------------------------------

def _imported_roots(path):
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                roots.add((node.module or "").split(".")[0])
            else:
                roots.add("." * node.level + (node.module or ""))
    return roots


def test_skills_package_imports_only_stdlib_and_itself():
    stdlib = set(sys.stdlib_module_names)
    for path in sorted(SKILLS_DIR.glob("*.py")):
        imported = _imported_roots(path)
        extra = {root for root in imported if root not in stdlib and root not in {"skills", ".contract"}}
        assert not extra, (path.name, extra)


def test_skills_package_names_no_authority_or_execution_types():
    banned = {"AuthorizedAction", "ExecutionEnvelope", "AuthorizationRecord", "Proposal",
              "AEPRuntime", "AEPAdapter", "Executor", "EvidenceAuditor"}
    for path in sorted(SKILLS_DIR.glob("*.py")):
        used = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        assert not used & banned, (path.name, used & banned)


@pytest.mark.parametrize("pkg", OTHER_PACKAGES)
def test_other_packages_do_not_import_skills(pkg):
    files = sorted((ROOT / pkg).rglob("*.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(a.name.split(".")[0] != "skills" for a in node.names), path
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "")
                assert mod.split(".")[0] != "skills", path
                assert all(a.name != "skills" for a in node.names), path


def test_public_surface_is_the_contract_only():
    assert set(skills.__all__) == {"SKILL_SCHEMA_VERSION", "SkillContractError", "SkillDefinition", "SkillProvenance"}
