"""V1.6 store-capacity separation and real-construction gate.

These tests stop at the runtime construction boundary. They do not invoke a provider,
Brave, Agnes, Computer effects, or a live harness.
"""
from types import SimpleNamespace

import pytest

from application import agent_loop as agent_loop_module
from application.agent_loop import VerificationResult
from application.agent_runtime import build_agent_loop
from application.source_facts import (
    DeepReadCandidate,
    ResearchBudget,
    SourceCandidate,
    SourceFactRejected,
    SourceFactStore,
)
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import Executor


def _candidate(index: int, publisher: str = "NASA"):
    url = f"https://{publisher.lower()}.example/source-{index}"
    item = {"title": f"{publisher} source {index}", "url": url, "description": "fact"}
    search_id, digest = f"search-{index}", f"{index + 1:064d}"
    return SourceCandidate(search_id, item, digest, "DATA_ONLY", "TOOL_RESULT"), DeepReadCandidate(
        f"read-{index}", search_id, digest, url, url, f"{publisher} fact {index}", False, None,
        f"{index + 11:064d}", "DATA_ONLY", "COMPUTER_OBSERVATION"
    )


def _admit(store, index: int, publisher: str = "NASA"):
    search, read = _candidate(index, publisher)
    store.admit(search)
    return store.admit_deep_read(read, lambda _: True)


def test_v16_store_capacity_is_independent_from_legacy_deep_read_validation():
    store = SourceFactStore(max_deep_reads=2, max_search_facts=4, max_source_context_chars=7200,
                            max_admitted_sources=4)
    for index in range(4):
        _admit(store, index)
    assert len(store.deep_facts()) == 4

    with pytest.raises(SourceFactRejected, match="deep_read_limit"):
        _admit(store, 4)

    with pytest.raises(ValueError, match="max_deep_reads_invalid"):
        SourceFactStore(max_deep_reads=4, max_admitted_sources=4)
    assert SourceFactStore(max_deep_reads=2, max_admitted_sources=4)._max_admitted_sources == 4


def test_v16_duplicate_publishers_remain_stored_until_required_publisher_arrives():
    store = SourceFactStore(max_deep_reads=2, max_search_facts=4, max_source_context_chars=7200,
                            max_admitted_sources=4)
    _admit(store, 0, "NASA")
    _admit(store, 1, "NASA")
    _admit(store, 2, "ESA")
    assert len(store.deep_facts()) == 3
    assert [fact.source_url for fact in store.deep_facts()] == [
        "https://nasa.example/source-0", "https://nasa.example/source-1", "https://esa.example/source-2"
    ]


def test_v16_real_runtime_construction_gate_constructs_store_before_provider(monkeypatch):
    constructed = []
    real_store = agent_loop_module.SourceFactStore

    class SpyStore(real_store):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            constructed.append(self)

    monkeypatch.setattr(agent_loop_module, "SourceFactStore", SpyStore)

    class Provider:
        provider_id = "agnes"

        def complete(self, prompt):
            raise AssertionError("provider boundary must not be crossed")

    class Port:
        def observe(self):
            raise RuntimeError("stop after construction")

    class SearchExecutor(Executor):
        action = opcode = "web.search"

        def run(self, resource, args, identity, binding=None):
            raise AssertionError("search boundary must not be crossed")

        def effects(self):
            return ()

    budget = ResearchBudget(target_valid_sources=2, max_search_attempts=4, max_deep_read_attempts=4)
    loop, _session = build_agent_loop(
        Provider(), Port(), lambda _observation: VerificationResult(True, {}),
        allowed_origins=("https://websitewombat.app",),
        search=SimpleNamespace(allowed_domains=("nasa.gov",), result_limit=1, provider_ref="test", max_searches=4),
        search_executor=SearchExecutor(), allow_read=False, research=budget,
        max_deep_reads=2, max_admitted_sources=4, sleep=lambda _: None,
    )
    result = loop.run(AgentGoal("construction", "goal", max_steps=1))

    assert result.status == "OBSERVATION_FAILED"
    assert len(constructed) == 1
    assert constructed[0]._max_deep_reads == 2
    assert constructed[0]._max_admitted_sources == 4

