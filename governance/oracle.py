"""Structural oracle. Imports NOTHING from the governance package.

It works on observables only, as plain data: evidence events, what the AEP
received (``AEPSpy.calls``) and the real effects the executor boundary logged
(``AEPAdapter.effect_log()``). It is not a second AEP.

Properties checked:
  EXEC/effect > 0  =>  a matching ALLOW authorization exists
  execution authority  ⊆  authorization authority (opcode, resource, args, intent)
  executed resource identity == authorized resource identity
  one execution per authorization

KNOWN BLIND SPOT: the oracle checks structure, not meaning. If a component
upstream is wrong in a self-consistent way (e.g. approval returns ALLOW where
it should be REVIEW) the evidence is coherent and the oracle passes. Only the
integration tests catch semantic risk/approval errors. It trusts the
identity the executor reports for the handle it read; it cannot verify that
report independently, and it does not compare file content.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _norm_args(args: Any) -> Tuple[Tuple[str, str], ...]:
    return tuple(tuple(a) for a in (args or ()))


def _authority(ev: Dict[str, Any]) -> tuple:
    return (ev.get("opcode"), ev.get("resource"), _norm_args(ev.get("args")))


def check(
    events: Sequence[Dict[str, Any]],
    aep_calls: Sequence[tuple],
    effects: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    violations: List[str] = []

    prev_hash = "0" * 64
    for i, ev in enumerate(events):
        if ev.get("seq") != i:
            violations.append("evidence_seq_gap:%d" % i)
        if ev.get("prev_hash") != prev_hash:
            violations.append("evidence_chain_broken:%d" % i)
        prev_hash = ev.get("hash")

    allowed: Counter = Counter()
    auth_by_id: Dict[Any, Dict[str, Any]] = {}
    for ev in events:
        if ev.get("kind") != "AUTHORIZATION" or ev.get("final_decision") != "ALLOW":
            continue
        components = (
            ev.get("policy_decision"),
            ev.get("approval_decision"),
            ev.get("budget_decision"),
        )
        if any(c != "ALLOW" for c in components):
            violations.append("final_allow_without_unanimous_components:%s" % ev.get("event_id"))
        allowed[_authority(ev)] += 1
        auth_by_id[ev.get("event_id")] = ev

    booted = False
    boots = halts = 0
    execs: Counter = Counter()
    for call in aep_calls:
        name = call[0] if call else None
        if name == "BOOT":
            booted = True
            boots += 1
        elif name == "HALT":
            halts += 1
            booted = False
        elif name == "EXEC":
            if not booted:
                violations.append("exec_outside_boot")
            execs[(call[1], call[2], _norm_args(call[3]))] += 1
        else:
            violations.append("unknown_aep_call:%r" % (name,))
    if boots != halts:
        violations.append("boot_halt_mismatch")

    for key, count in execs.items():
        if count > allowed.get(key, 0):
            violations.append("unauthorized_or_duplicate_exec:%s" % (key[0],))

    results = [e for e in events if e.get("kind") == "EXECUTION_RESULT"]
    if len(results) != sum(execs.values()):
        violations.append("exec_result_count_mismatch")

    per_auth: Counter = Counter()
    for ev in results:
        aid = ev.get("authorization_id")
        auth = auth_by_id.get(aid)
        per_auth[aid] += 1
        if auth is None:
            violations.append("result_without_authorization:%s" % ev.get("event_id"))
            continue
        if ev.get("intent_id") != auth.get("intent_id"):
            violations.append("result_intent_mismatch:%s" % ev.get("event_id"))
        if _authority(ev) != _authority(auth):
            violations.append("execution_exceeds_authorization:%s" % ev.get("event_id"))
        if ev.get("ok") and ev.get("resource_identity") != auth.get("resource_identity"):
            violations.append("result_identity_mismatch:%s" % ev.get("event_id"))
        if not ev.get("execution_id"):
            violations.append("result_without_execution_id:%s" % ev.get("event_id"))
    for aid, count in per_auth.items():
        if count > 1:
            violations.append("multiple_executions_for_authorization:%s" % aid)

    if effects is not None:
        allowed_ident: Counter = Counter(
            _authority(a) + (a.get("resource_identity"),) for a in auth_by_id.values()
        )
        effect_counts: Counter = Counter()
        for eff in effects:
            key = (eff.get("opcode"), eff.get("resource"), _norm_args(eff.get("args")))
            effect_counts[key] += 1
            if allowed_ident.get(key + (eff.get("resource_identity"),), 0) == 0:
                violations.append("effect_identity_not_authorized:%s" % (key[0],))
        ok_results: Counter = Counter()
        for ev in results:
            if ev.get("ok") and ev.get("authorization_id") in auth_by_id:
                ok_results[_authority(ev)] += 1
        for key, count in effect_counts.items():
            if count > allowed.get(key, 0):
                violations.append("effect_without_matching_authorization:%s" % (key[0],))
            if count > ok_results.get(key, 0):
                violations.append("effect_without_execution_record:%s" % (key[0],))
    return violations
