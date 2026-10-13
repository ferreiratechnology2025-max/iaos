"""ExecutionEnvelope & authorization-correspondence contract, Phase 0.

C11-C23 plus the normative-order, purity, immutability and capability tests.
Every "no effect" assertion looks at the executor's real effect log, the AEP's
own call log and the consumption ledger, not only at an exception.
"""
import ast
import dataclasses
import gc
import inspect
import os
import pathlib
import threading
import types

import pytest

import governance
from governance import envelope as envelope_mod
from governance.aep_adapter import AdapterError, AEPAdapter
from governance.aep_executors import (
    AEPExecutorRegistry, Executor, FsReadResult, default_registry,
)
from governance.aep_runtime import AEPRuntime
from governance.auth_registry import (
    AuthorizationIssuer, AuthorizationRecord, AuthorizationView, RecordStatus,
    create_authorization_registry,
)
from governance.consumption import (
    ConsumeResult, ConsumptionHandle, ConsumptionLedger, create_consumption_ledger,
)
from governance.contract import Decision
from governance.envelope import (
    EnvelopeRefused, ExecutionEnvelope, JCSDomainError, SCHEMA_VERSION, authority_digest,
    authorizes_static, canonical_bytes, effect_conforms, loads_canonical_domain,
    static_mismatch, validate_envelope,
)
from governance.exec_observer import EffectExecutionObservation
from governance.handle_binding import BindingTable

from aep_support import envelope_for, record_for

PKG = pathlib.Path(governance.__file__).parent


# ---- helpers ---------------------------------------------------------------------

class RecView:
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def get_issued(self, authorization_id):
        self._log.append("get_issued")
        return self._inner.get_issued(authorization_id)


class RecHandle:
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def consume_once(self, authorization_id, execution_id):
        self._log.append("consume")
        return self._inner.consume_once(authorization_id, execution_id)


def recording_env(make_env, **kw):
    log = []
    env = make_env(
        view_wrap=lambda v: RecView(v, log), handle_wrap=lambda h: RecHandle(h, log), **kw
    )
    return env, log


def authorize(env, raw="a.txt"):
    out = env.authorizer.authorize(env.read(raw))
    assert out.final_decision.decision is Decision.ALLOW
    return out.authorized_action


def envelope_of(env, action):
    return env.adapter._translate(action)


def assert_nothing_happened(env, log=None, *, consumed=0):
    assert env.spy.calls == []
    assert env.adapter.effect_log() == []
    assert env.ledger.consumed_count() == consumed
    if log is not None:
        assert "consume" not in log or consumed > 0


# ---- C11 closed schema -----------------------------------------------------------

def test_c11_extra_envelope_field_is_rejected_before_lookup_and_consume(make_env):
    env, log = recording_env(make_env)
    wire = envelope_of(env, authorize(env)).to_wire()
    wire["extra"] = "x"
    with pytest.raises(EnvelopeRefused, match="invalid_envelope"):
        env.runtime.submit(wire)
    assert log == []  # SCHEMA_BEFORE_LOOKUP: neither lookup nor consume happened
    assert_nothing_happened(env)


def test_c11_missing_nested_and_attribute_smuggled_fields_are_rejected(make_env):
    env, log = recording_env(make_env)
    good = envelope_of(env, authorize(env))
    bad_wires = []
    w = good.to_wire(); del w["intent_id"]; bad_wires.append(w)
    w = good.to_wire(); w["authorized_args"] = [["k", "v", "extra"]]; bad_wires.append(w)
    w = good.to_wire(); w["authorized_args"] = [["k", 1]]; bad_wires.append(w)
    w = good.to_wire(); w["resource_binding_id"] = 7; bad_wires.append(w)
    for wire in bad_wires:
        with pytest.raises(EnvelopeRefused, match="invalid_envelope"):
            env.runtime.submit(wire)
    smuggled = envelope_of(env, authorize(env))
    object.__setattr__(smuggled, "target", "/etc/passwd")  # attribute on the instance, not a field
    with pytest.raises(EnvelopeRefused, match="invalid_envelope"):
        env.runtime.submit(smuggled)
    with pytest.raises(EnvelopeRefused, match="unsupported_schema"):
        env.runtime.submit(dataclasses.replace(good, schema_version=2))
    assert log == []
    assert_nothing_happened(env)


# ---- C12 digest ------------------------------------------------------------------

def test_c12_authorization_digest_mismatch_is_rejected_without_consuming(make_env):
    env, log = recording_env(make_env)
    action = authorize(env)
    good = envelope_of(env, action)
    with pytest.raises(EnvelopeRefused, match="digest_mismatch"):
        env.runtime.submit(dataclasses.replace(good, authorization_digest="0" * 64))
    assert log == ["get_issued"]
    assert_nothing_happened(env)
    assert env.ledger.consumed_by(action.authorization_id) is None


# ---- C13 / C13B lookup -----------------------------------------------------------

def test_c13_authorization_not_found_is_rejected(make_env):
    env, log = recording_env(make_env)
    good = envelope_of(env, authorize(env))
    with pytest.raises(EnvelopeRefused, match="no_valid_authorization"):
        env.runtime.submit(dataclasses.replace(good, authorization_id="ev-does-not-exist"))
    assert log == ["get_issued"]
    assert_nothing_happened(env)


@pytest.mark.parametrize("status", [RecordStatus.PENDING, RecordStatus.REVOKED, RecordStatus.DENIED])
def test_c13b_authorization_not_issued_is_rejected_with_zero_consume(make_env, status):
    env, log = recording_env(make_env)
    rec = record_for("ev-np", "i", "fs.read", "FS_READ", (), "b-1", env.path("a.txt"), "1:1", status=status)
    env.issuer.issue(rec)
    assert env.view.get_issued("ev-np") is None
    with pytest.raises(EnvelopeRefused, match="no_valid_authorization"):
        env.runtime.submit(envelope_for(rec))
    assert log == ["get_issued"]  # consume count = 0
    assert_nothing_happened(env)


# ---- C14 / C15 consumption -------------------------------------------------------

def test_c14_replay_sequential_second_consume_fails(env):
    action = authorize(env)
    first = env.adapter.execute(action, env.store)
    assert first.ok
    assert env.ledger.consumed_by(action.authorization_id) == first.execution_id
    with pytest.raises(AdapterError, match="replay_denied"):
        env.adapter.execute(action, env.store)
    assert len(env.adapter.effect_log()) == 1
    assert env.consume_handle.consume_once(action.authorization_id, "x-other") is ConsumeResult.ALREADY_CONSUMED
    assert env.ledger.consumed_by(action.authorization_id) == first.execution_id  # winner unchanged


def test_c15_concurrent_consume_once_has_exactly_one_winner_real_threads():
    ledger, handle = create_consumption_ledger()  # the REAL ledger, not a mock
    n_threads = 8
    for i in range(200):
        barrier = threading.Barrier(n_threads)
        results = []

        def worker(t, i=i, barrier=barrier, results=results):
            barrier.wait()
            results.append(handle.consume_once("auth-%d" % i, "x-%d-%d" % (i, t)))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert results.count(ConsumeResult.CONSUMED) == 1, "iteration %d: %r" % (i, results)
        assert results.count(ConsumeResult.ALREADY_CONSUMED) == n_threads - 1
        assert ledger.consumed_by("auth-%d" % i) is not None
    assert ledger.consumed_count() == 200


def test_c15_concurrent_execute_end_to_end_exactly_one_effect(env):
    action = authorize(env)
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            outcomes.append(("ok", env.adapter.execute(action, env.store)))
        except AdapterError as exc:
            outcomes.append(("refused", str(exc)))

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    winners = [o for o in outcomes if o[0] == "ok"]
    assert len(winners) == 1 and winners[0][1].ok and winners[0][1].output == "hello"
    assert [o[1] for o in outcomes if o[0] == "refused"] == ["replay_denied"] * (n_threads - 1)
    assert len(env.adapter.effect_log()) == 1  # EXEC eligibility = 1
    assert [c for c in env.spy.calls if c[0] == "EXEC"].__len__() == 1
    assert env.oracle_violations() == []


# ---- C16 burn semantics ----------------------------------------------------------

class BoomExecutor(Executor):
    action, opcode, arg_names, binds_identity = "fs.read", "FS_READ", (), True

    def run(self, resource, args, identity, binding=None):
        binding.close()
        raise RuntimeError("boom")

    def effects(self):
        return ()


def test_c16a_effect_failure_after_consume_burns_the_authorization(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": BoomExecutor()}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok is False and "boom" in result.error
    assert env.ledger.consumed_by(action.authorization_id) == result.execution_id
    assert env.adapter.effect_log() == []
    with pytest.raises(AdapterError, match="replay_denied"):
        env.adapter.execute(action, env.store)  # retry = REJECT


class FailingHandoff(AEPRuntime):
    def _handoff(self):
        raise RuntimeError("handoff exploded")


def test_c16b_handoff_failure_after_consume_burns_the_authorization(make_env):
    env = make_env(runtime_cls=FailingHandoff)
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    assert result.ok is False and "handoff exploded" in result.error
    assert env.ledger.consumed_by(action.authorization_id) == result.execution_id
    assert env.adapter.effect_log() == [] and env.spy.calls == []  # effect count = 0, handler never began
    assert env.bindings.pending() == 0  # nothing left open
    with pytest.raises(AdapterError, match="replay_denied"):
        env.adapter.execute(action, env.store)  # retry = REJECT
    assert env.adapter.effect_log() == []


# ---- normative order -------------------------------------------------------------

def test_order_valid_flow_is_lookup_then_consume(make_env):
    env, log = recording_env(make_env)
    result = env.adapter.execute(authorize(env), env.store)
    assert result.ok and log == ["get_issued", "consume"]


@pytest.mark.parametrize("tamper", [
    lambda e: dataclasses.replace(e, opcode="fs.write"),
    lambda e: dataclasses.replace(e, authorized_args=(("x", "y"),)),
    lambda e: dataclasses.replace(e, resource_binding_id="b-other"),
    lambda e: dataclasses.replace(e, intent_id="someone-else"),
    lambda e: dataclasses.replace(e, authorization_digest="f" * 64),
], ids=["opcode", "args", "binding", "intent", "digest"])
def test_static_failure_does_not_consume_and_authorization_stays_available(make_env, tamper):
    env, log = recording_env(make_env)
    action = authorize(env)
    good = envelope_of(env, action)
    with pytest.raises(EnvelopeRefused):
        env.runtime.submit(tamper(good))
    assert log == ["get_issued"], "static validation must precede consume"
    assert env.ledger.consumed_by(action.authorization_id) is None  # still AVAILABLE
    assert env.spy.calls == [] and env.adapter.effect_log() == []
    out = env.runtime.submit(good)  # the legitimate envelope still works afterwards
    assert out.content == "hello"


def test_registry_and_effect_validation_also_precede_consume(make_env):
    env, log = recording_env(make_env)
    rec = record_for("ev-nb", "i", "fs.read", "FS_READ", (), None, env.path("a.txt"), "1:1")
    env.issuer.issue(rec)  # binds identity but carries no binding id
    with pytest.raises(EnvelopeRefused, match="identity_required"):
        env.runtime.submit(envelope_for(rec))
    assert log == ["get_issued"] and env.ledger.consumed_count() == 0


# ---- C17 metadata can not change the target --------------------------------------

def test_c17_metadata_channel_cannot_change_the_target(make_env):
    env, log = recording_env(make_env)
    secret_bid, secret_path, _ = env.bindings.open(os.path.join(env.base, "outside.txt"))
    wire = envelope_of(env, authorize(env)).to_wire()
    for field in ("metadata", "metadata_binding_id", "path", "target", "resource"):
        poisoned = dict(wire)
        poisoned[field] = {"resource_binding_id": secret_bid} if field == "metadata" else secret_bid
        with pytest.raises(EnvelopeRefused, match="invalid_envelope"):
            env.runtime.submit(poisoned)
    assert log == []
    assert env.bindings.read_log() == ()  # the secret handle was never read
    assert_nothing_happened(env)
    env.bindings.release(secret_bid)


# ---- C18 / C19 capability confinement --------------------------------------------

def _reachable(root, stop_at=()):
    seen, stack, out = set(), [root], []
    skip = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
            types.CodeType, types.FrameType)
    while stack:
        o = stack.pop()
        if id(o) in seen or isinstance(o, skip):
            continue
        seen.add(id(o))
        out.append(o)
        if o is not root and isinstance(o, stop_at):
            continue  # the AEP boundary: it legitimately holds its own capabilities
        stack.extend(gc.get_referents(o))
    return out


FORBIDDEN_FOR_ADAPTER = (AuthorizationIssuer, ConsumptionHandle, ConsumptionLedger,
                         AuthorizationView)


def test_c18_adapter_has_no_issuer_capability(env):
    graph = _reachable(env.adapter, stop_at=(AEPRuntime,))
    assert any(isinstance(o, AEPRuntime) for o in graph), "walk must reach the AEP boundary"
    assert not [o for o in graph if isinstance(o, AuthorizationIssuer)]
    from governance.auth_registry import _AuthorizationStore
    assert not [o for o in graph if isinstance(o, _AuthorizationStore)]  # no mutable registry either
    assert not [o for o in graph if isinstance(o, AuthorizationView)]  # the adapter is not even a reader


def test_c19_adapter_has_no_consumption_capability(env):
    graph = _reachable(env.adapter, stop_at=(AEPRuntime,))
    assert not [o for o in graph if isinstance(o, ConsumptionHandle)]
    assert not [o for o in graph if isinstance(o, ConsumptionLedger)]
    assert not hasattr(env.adapter, "consume_once") and not hasattr(env.adapter, "issue")


def test_runtime_holds_a_view_and_a_handle_never_an_issuer(env):
    graph = _reachable(env.runtime)
    assert not [o for o in graph if isinstance(o, AuthorizationIssuer)]
    assert [o for o in graph if isinstance(o, AuthorizationView)]
    assert [o for o in graph if isinstance(o, ConsumptionHandle)]


def _public(cls):
    return {n for n, _ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}


def test_issued_only_view_and_narrow_capability_apis():
    assert _public(AuthorizationView) == {"get_issued"}
    assert _public(AuthorizationIssuer) == {"issue"}
    assert _public(ConsumptionHandle) == {"consume_once"}
    assert _public(ConsumptionLedger) == {"consumed_by", "consumed_count"}  # nothing un-consumes


def _call_names(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            yield f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_capability_operations_are_called_only_from_their_owners():
    owners = {
        "AuthorizationRecord": {"authorization.py", "auth_registry.py"},
        "issue": {"authorization.py"},
        "consume_once": {"aep_runtime.py", "consumption.py", "tool_kernel.py"},
        "get_issued": {"aep_runtime.py", "auth_registry.py"},
    }
    for path in sorted(PKG.glob("*.py")):
        names = set(_call_names(ast.parse(path.read_text())))
        for op, allowed in owners.items():
            if op in names:
                assert path.name in allowed, (op, path.name)


def test_adapter_imports_neither_registry_nor_ledger():
    imported = set()
    for node in ast.walk(ast.parse((PKG / "aep_adapter.py").read_text())):
        if isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").lstrip("."))
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not ({"auth_registry", "consumption", "aep_runtime", "aep_executors"} & imported)


# ---- C20 canonical serialization (RFC 8785 / JCS) ---------------------------------

def test_c20_jcs_equivalence_same_structure_same_bytes_and_digest():
    a = {"b": [1, 2, {"y": "é", "x": None}], "a": True}
    b = {"a": True, "b": [1, 2, {"x": None, "y": "é"}]}
    assert canonical_bytes(a) == canonical_bytes(b) == b'{"a":true,"b":[1,2,{"x":null,"y":"\xc3\xa9"}]}'
    d1 = authority_digest("a", "i", "fs.read", (("k", "v"),), "b-1")
    d2 = authority_digest("a", "i", "fs.read", [["k", "v"]], "b-1")
    assert d1 == d2


def test_c20_jcs_rfc8785_key_ordering_uses_utf16_code_units():
    obj = {"€": "Euro", "\r": "CR", "דּ": "Hebrew", "1": "One",
           "\U0001F600": "Emoji", "\u0080": "C1", "ö": "o-umlaut"}
    keys = [k for k in loads_canonical_domain(canonical_bytes(obj).decode("utf-8"))]
    assert keys == ["\r", "1", "\u0080", "ö", "€", "\U0001F600", "דּ"]  # RFC 8785 sec. 3.2.3


def test_c20_jcs_string_escaping_and_domain_limits():
    assert canonical_bytes("a\"b\\c\n\x01\x7f") == b'"a\\"b\\\\c\\n\\u0001\x7f"'
    for bad in (1.0, 0.5, float("nan"), 2 ** 53, {1: "x"}, "\ud800", object(), {"k": b"bytes"}):
        with pytest.raises(JCSDomainError):
            canonical_bytes(bad)
    assert canonical_bytes(True) != canonical_bytes(1)  # bool is not int
    assert canonical_bytes(2 ** 53 - 1) == b"9007199254740991"
    with pytest.raises(JCSDomainError):
        loads_canonical_domain('{"a":1,"a":2}')  # duplicate keys forbidden
    with pytest.raises(JCSDomainError):
        loads_canonical_domain('{"a":1.5}')
    with pytest.raises(JCSDomainError):
        loads_canonical_domain('{"a":NaN}')


def test_c20_there_is_one_canonical_serializer():
    src = (PKG / "aep_adapter.py").read_text() + (PKG / "auth_registry.py").read_text()
    assert "json.dumps" not in src and "import json" not in src
    # the digest used by the record and by the adapter is the same function
    rec = record_for("ev-1", "i", "fs.read", "FS_READ", (), "b-1")
    assert rec.authority_digest() == authority_digest("ev-1", "i", "fs.read", (), "b-1")


# ---- C21 authority-field change -----------------------------------------------------

@pytest.mark.parametrize("field,value,code", [
    ("opcode", "fs.write", "opcode_mismatch"),
    ("authorized_args", (("k", "other"),), "args_mismatch"),
    ("resource_binding_id", "b-other", "binding_mismatch"),
    ("authorization_id", "ev-other", "authorization_id_mismatch"),
    ("intent_id", "i-other", "intent_mismatch"),
    ("authorization_digest", "0" * 64, "digest_mismatch"),
])
def test_c21_changing_any_authority_field_is_a_mismatch_and_changes_the_digest(field, value, code):
    rec = record_for("ev-1", "i", "fs.read", "FS_READ", (("k", "v"),), "b-1", "/p", "1:1")
    good = envelope_for(rec)
    assert static_mismatch(rec, good) is None and authorizes_static(rec, good)
    bad = dataclasses.replace(good, **{field: value})
    assert static_mismatch(rec, bad) == code and not authorizes_static(rec, bad)
    if field != "authorization_digest":
        mapping = {"opcode": "action", "authorized_args": "canonical_args",
                   "resource_binding_id": "resource_binding_id", "authorization_id": "authorization_id",
                   "intent_id": "intent_id"}
        changed = dataclasses.replace(rec, **{mapping[field]: value})
        assert changed.authority_digest() != rec.authority_digest()


def test_c21_no_partial_match_is_authorization():
    rec = record_for("ev-1", "i", "fs.read", "FS_READ", (), "b-1")
    good = envelope_for(rec)
    for field, value in (("opcode", "x"), ("intent_id", "x"), ("resource_binding_id", "x")):
        assert not authorizes_static(rec, dataclasses.replace(good, **{field: value}))
    assert not authorizes_static(dataclasses.replace(rec, execution_cardinality="MULTI"), good)


# ---- static predicate is pure; records are immutable; no TTL -----------------------

def test_static_predicate_is_pure():
    tree = ast.parse(inspect.getsource(envelope_mod))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "static_mismatch")
    assert [a.arg for a in fn.args.args] == ["record", "envelope"]  # only immutable inputs
    assert not any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(fn))
    assert set(_call_names(fn)) <= {"canonical_bytes", "_pairs", "authority_digest"}
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not ({"time", "datetime", "os", "random", "threading", "socket", "subprocess"} & imports)
    rec = record_for("ev-1", "i", "fs.read", "FS_READ", (("k", "v"),), "b-1")
    e = envelope_for(rec)
    before = (dataclasses.astuple(rec), dataclasses.astuple(e))
    results = {static_mismatch(rec, e) for _ in range(50)}
    assert results == {None} and (dataclasses.astuple(rec), dataclasses.astuple(e)) == before


def test_authorization_record_is_immutable_and_registry_returns_a_stable_snapshot():
    issuer, view = create_authorization_registry()
    args = [["k", "v"]]
    rec = AuthorizationRecord("ev-1", "i", "fs.read", args, "b-1", "FS_READ", "/p", "1:1")
    args[0][1] = "mutated-after-construction"
    assert rec.canonical_args == (("k", "v"),) and isinstance(rec.canonical_args[0], tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.action = "shell.exec"
    issuer.issue(rec)
    got = view.get_issued("ev-1")
    assert got == rec
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.resource_binding_id = "b-evil"
    with pytest.raises(ValueError, match="already_issued"):
        issuer.issue(dataclasses.replace(rec, action="shell.exec"))  # never replaced
    assert view.get_issued("ev-1") == rec
    for f in dataclasses.fields(rec):
        v = getattr(rec, f.name)
        assert v is None or isinstance(v, (str, int, tuple, RecordStatus)), f.name


def test_phase0_has_no_ttl_expiration_or_revocation_api():
    names = {f.name for f in dataclasses.fields(AuthorizationRecord)}
    assert not {n for n in names if any(w in n for w in ("ttl", "expire", "expiry", "valid_until"))}
    assert not [m for m in _public(AuthorizationIssuer) if "revoke" in m or "update" in m or "replace" in m]


# ---- C22 / C23 effect safety by construction + audit ------------------------------

def test_c22_effect_uses_the_bound_handle(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    obs = result.observation
    assert result.ok and result.output == "hello"
    assert obs.path_open_calls_during_exec == 0  # EXEC never opened a path
    assert obs.bound_handle_reads == 1  # it read the handle bound during authorization
    assert obs.observed_resource_identity == action.resource_identity
    assert obs.resource_binding_id == action.resource_binding_id
    record = env.view.get_issued(action.authorization_id)
    assert effect_conforms(envelope_of(env, action), record, obs)
    assert env.runtime.observations() == [obs]
    ev = [e for e in env.store.events() if e["kind"] == "EXECUTION_RESULT"][0]
    assert ev["path_open_calls_during_exec"] == 0 and ev["bound_handle_reads"] == 1


class ReopenByPathExecutor(Executor):
    """Deliberate mutant: ignores the bound handle and opens the PATH again."""

    action, opcode, arg_names, binds_identity = "fs.read", "FS_READ", (), True

    def run(self, resource, args, identity, binding=None):
        binding.close()
        with open(resource, "rb") as fh:
            data = fh.read()
        return FsReadResult(data.decode("utf-8"), identity)

    def effects(self):
        return ()


def test_c23_reopen_by_path_is_made_observable_and_does_not_conform(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    obs = result.observation
    assert obs.path_open_calls_during_exec >= 1 and obs.bound_handle_reads == 0
    record = env.view.get_issued(action.authorization_id)
    assert not effect_conforms(envelope_of(env, action), record, obs)


def test_c23_reopen_by_path_after_a_namespace_swap_touches_the_wrong_file_and_is_flagged(make_env):
    env = make_env(registry=AEPExecutorRegistry({"fs.read": ReopenByPathExecutor()}))
    action = authorize(env)
    outside = os.path.join(env.base, "outside.txt")
    os.rename(env.path("a.txt"), env.path("a.orig"))
    try:
        os.symlink(outside, env.path("a.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    result = env.adapter.execute(action, env.store)
    assert result.output == "secret"  # the mutant really reached the wrong resource ...
    record = env.view.get_issued(action.authorization_id)
    assert not effect_conforms(envelope_of(env, action), record, result.observation)  # ... and the audit says so


def test_effect_conforms_is_false_for_missing_or_foreign_observations(env):
    action = authorize(env)
    result = env.adapter.execute(action, env.store)
    e, rec, obs = envelope_of(env, action), env.view.get_issued(action.authorization_id), result.observation
    assert effect_conforms(e, rec, obs)
    assert not effect_conforms(e, rec, None)
    for change in ({"execution_id": "x-other"}, {"resource_binding_id": "b-other"},
                   {"bound_handle_reads": 0}, {"bound_handle_reads": 2},
                   {"path_open_calls_during_exec": 1}, {"observed_resource_identity": "9:9"},
                   {"observed_resource_identity": None}):
        assert not effect_conforms(e, rec, dataclasses.replace(obs, **change)), change
