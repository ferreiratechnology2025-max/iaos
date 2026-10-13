"""G10: the oracle and the test-suite must have teeth.

Real source mutants run in an isolated copy: GREEN on the pristine copy,
RED on each mutant. The project tree itself is never modified.
"""
import copy
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from governance import oracle

ROOT = pathlib.Path(__file__).resolve().parents[2]

MUTANTS = {
    "prefix_containment": (
        "governance/policy.py",
        "return Path(path) == Path(root) or Path(root) in Path(path).parents",
        "return path.startswith(root)",
    ),
    "symlink_bypass": (
        "governance/handle_binding.py",
        "            final_path = final_path_of_fd(fd)",
        "            final_path = os.path.abspath(path)",
    ),
    "authority_amplification": (
        "governance/approval.py",
        'return ApprovalDecision(Decision.REVIEW, "approval_required")',
        'return ApprovalDecision(Decision.ALLOW, "approval_required")',
    ),
    "evidence_bypass": (
        "governance/authorization.py",
        "return self._evidence_failed(base)",
        "pass",
    ),
}


def _copy_project(dst):
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git", ".venv")
    shutil.copytree(ROOT / "governance", dst / "governance", ignore=ignore)
    shutil.copytree(ROOT / "audit", dst / "audit", ignore=ignore)
    shutil.copytree(ROOT / "evidence", dst / "evidence", ignore=ignore)
    shutil.copytree(ROOT / "docs" / "evidence", dst / "docs" / "evidence", ignore=ignore)
    shutil.copytree(ROOT / "claims", dst / "claims", ignore=ignore)
    shutil.copytree(ROOT / "assurance", dst / "assurance", ignore=ignore)
    shutil.copytree(ROOT / "skills", dst / "skills", ignore=ignore)
    shutil.copytree(ROOT / "skilleval", dst / "skilleval", ignore=ignore)
    shutil.copytree(ROOT / "cognitive", dst / "cognitive", ignore=ignore)
    shutil.copytree(ROOT / "application", dst / "application", ignore=ignore)
    shutil.copytree(ROOT / "scripts", dst / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "tests", dst / "tests", ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", dst / "pyproject.toml")
    for driver in (
        dst / "tests" / "adversarial" / "test_g10_oracle_teeth.py",
        dst / "tests" / "test_aep_mutants.py",
        dst / "tests" / "test_envelope_mutants.py",
        dst / "tests" / "test_correspondence_mutants.py",
        dst / "tests" / "test_skill_contract_mutants.py",
        dst / "tests" / "test_minimal_change_pilot.py",
    ):
        if driver.exists():
            driver.unlink()  # mutant drivers never run inside a mutant copy


def _pytest(cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


def test_g10_pristine_copy_is_green(tmp_path):
    _copy_project(tmp_path)
    r = _pytest(tmp_path)
    assert r.returncode == 0, r.stdout[-2000:]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_g10_mutant_is_killed(tmp_path, name):
    rel, old, new = MUTANTS[name]
    _copy_project(tmp_path)
    target = tmp_path / rel
    src = target.read_text()
    assert old in src, "mutation site not found: %s" % name
    target.write_text(src.replace(old, new, 1))
    r = _pytest(tmp_path)
    assert r.returncode != 0, "mutant %s SURVIVED" % name
    # restore proof: the project source itself still contains the original line
    assert old in (ROOT / rel).read_text()


# --- oracle teeth on tampered evidence ---------------------------------------

def _auth(**kw):
    ev = {"seq": 0, "prev_hash": "0" * 64, "hash": "h0", "event_id": "ev-0",
          "kind": "AUTHORIZATION", "final_decision": "ALLOW",
          "policy_decision": "ALLOW", "approval_decision": "ALLOW", "budget_decision": "ALLOW",
          "intent_id": "i-1", "opcode": "FS_READ", "resource": "/r/a", "args": []}
    ev.update(kw)
    return ev


def _res(seq=1, prev="h0", **kw):
    ev = {"seq": seq, "prev_hash": prev, "hash": "h%d" % seq, "event_id": "ev-%d" % seq,
          "kind": "EXECUTION_RESULT", "authorization_id": "ev-0", "execution_id": "x-1",
          "intent_id": "i-1", "opcode": "FS_READ", "resource": "/r/a", "args": [], "ok": True}
    ev.update(kw)
    return ev


GOOD = [("BOOT",), ("EXEC", "FS_READ", "/r/a", ()), ("HALT",)]


def test_g10_oracle_accepts_consistent_trace():
    assert oracle.check([_auth(), _res()], GOOD) == []


def test_g10_oracle_flags_exec_without_authorization():
    assert oracle.check([], GOOD)


def test_g10_oracle_flags_exec_after_denied_authorization():
    assert oracle.check([_auth(final_decision="DENY", policy_decision="DENY"), _res()], GOOD)


def test_g10_oracle_flags_non_unanimous_allow():
    assert oracle.check([_auth(approval_decision="REVIEW"), _res()], GOOD)


def test_g10_oracle_flags_result_without_or_beyond_its_authorization():
    assert oracle.check([_auth(), _res(authorization_id="ev-99")], GOOD)
    assert oracle.check([_auth(), _res(resource="/r/other")], GOOD)
    assert oracle.check([_auth(), _res(intent_id="i-2")], GOOD)


def test_g10_oracle_flags_effect_without_authorization_or_record():
    effect = {"opcode": "FS_READ", "resource": "/r/a", "args": []}
    assert oracle.check([_auth(), _res()], GOOD, [effect]) == []
    assert oracle.check([], [], [effect])  # effect, no authorization
    assert oracle.check([_auth(), _res()], GOOD, [dict(effect, resource="/r/b")])
    assert oracle.check([_auth(), _res()], GOOD, [effect, effect])  # beyond cardinality


def test_g10_oracle_flags_executed_identity_different_from_authorized():
    auth = _auth(resource_identity="7:100")
    assert oracle.check([auth, _res(resource_identity="7:100")], GOOD) == []
    assert oracle.check([auth, _res(resource_identity="7:999")], GOOD)  # same path, other file
    effect = {"opcode": "FS_READ", "resource": "/r/a", "args": [], "resource_identity": "7:100"}
    assert oracle.check([auth, _res(resource_identity="7:100")], GOOD, [effect]) == []
    assert oracle.check([auth, _res(resource_identity="7:100")], GOOD,
                        [dict(effect, resource_identity="7:999")])


def test_g10_oracle_flags_exec_outside_boot_and_broken_chain():
    assert oracle.check([_auth(), _res()], [("EXEC", "FS_READ", "/r/a", ())])
    assert oracle.check([_auth(), _res(prev="WRONG")], GOOD)


def test_oracle_known_blind_spot(make_env):
    """Semantic error that is self-consistent: approval returns ALLOW for a
    write that should have been REVIEW. Evidence is coherent, so the oracle
    passes; only the integration-level assertion catches it."""
    from governance.approval import ApprovalGate
    from governance.contract import ApprovalDecision, Decision, RiskLevel

    class AlwaysAllow(ApprovalGate):
        def decide(self, canon, risk, policy):
            return ApprovalDecision(Decision.ALLOW, "bug")

    env = make_env(gate=AlwaysAllow())
    proposal = env.write("n.txt", "d")
    canon = env.canonicalizer.canonicalize(proposal)
    correct = ApprovalGate().decide(canon, RiskLevel.MEDIUM, env.policy.evaluate(canon))
    assert correct.decision is Decision.REVIEW  # what a correct gate says
    out = env.run(proposal)
    assert out.final_decision.decision is Decision.ALLOW  # wrong: should be REVIEW
    assert oracle.check(env.store.events(), env.spy.calls) == []  # ...and the oracle is blind to it
