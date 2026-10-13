import os

import pytest

from governance.aep_adapter import AdapterError, AEPAdapter, AEPSpy
from governance.aep_executors import default_registry
from governance.aep_runtime import AEPRuntime
from governance.auth_registry import create_authorization_registry
from governance.approval import ApprovalGate
from governance.authorization import Authorizer
from governance.budget import Budget
from governance.canonicalize import Canonicalizer, ResourceResolver
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.handle_binding import BindingTable
from governance.policy import Policy


class Env:
    def __init__(self, tmp_path, store=None, adapter_cls=AEPAdapter, gate=None, registry=None, opener=None,
                 runtime_cls=AEPRuntime, view_wrap=None, handle_wrap=None):
        base = os.path.realpath(str(tmp_path))
        self.base = base
        self.root = os.path.join(base, "ws")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "a.txt"), "w") as fh:
            fh.write("hello")
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(base, "outside.txt"), "w") as fh:
            fh.write("secret")
        self.evil_dir = os.path.join(base, "ws_evil")  # shares the root as string prefix
        os.makedirs(self.evil_dir)
        with open(os.path.join(self.evil_dir, "secret.txt"), "w") as fh:
            fh.write("secret")

        self.store = store if store is not None else InMemoryEvidenceStore()
        self.bindings = BindingTable(opener=opener)
        self.resolver = ResourceResolver(self.root, self.bindings)
        self.canonicalizer = Canonicalizer(self.resolver)
        self.policy = Policy(self.resolver.root)
        self.gate = gate if gate is not None else ApprovalGate()
        self.budget = Budget()
        self.issuer, self.view = create_authorization_registry()  # Governance writes, AEP reads
        self.ledger, self.consume_handle = create_consumption_ledger()  # AEP consumes
        self.authorizer = Authorizer(
            self.canonicalizer, self.policy, self.gate, self.budget, self.store, self.issuer
        )
        self.executors = registry if registry is not None else default_registry()
        view = view_wrap(self.view) if view_wrap else self.view
        handle = handle_wrap(self.consume_handle) if handle_wrap else self.consume_handle
        self.runtime = runtime_cls(self.executors, self.bindings, view, handle)
        self.spy = AEPSpy(self.runtime)
        self.adapter = adapter_cls(self.spy, bindings=self.bindings)
        self.adapter_calls = 0
        self.adapter_error = None
        self.last_result = None
        self._n = 0

    def path(self, name):
        return os.path.join(self.root, name)

    def read(self, raw, source="user", intent=None):
        return self.proposal("fs.read", {"path": raw}, source, intent)

    def write(self, raw, content="x", source="user", intent=None):
        return self.proposal("fs.write", {"path": raw, "content": content}, source, intent)

    def proposal(self, action, args, source="user", intent=None, provenance="auto"):
        self._n += 1
        intent = intent or "i-%d" % self._n
        if provenance == "auto":
            provenance = Provenance(source, intent)
        return Proposal(intent, action, args, provenance)

    def run(self, proposal):
        outcome = self.authorizer.authorize(proposal)
        self.adapter_error = None
        if outcome.authorized_action is not None:
            self.adapter_calls += 1
            try:
                self.last_result = self.adapter.execute(outcome.authorized_action, self.store)
            except AdapterError as exc:
                self.adapter_error = exc
        return outcome

    def oracle_violations(self):
        from governance import oracle

        return oracle.check(self.store.events(), self.spy.calls, self.adapter.effect_log())


@pytest.fixture
def make_env(tmp_path):
    def _make(**kw):
        return Env(tmp_path, **kw)

    return _make


@pytest.fixture
def env(make_env):
    return make_env()
