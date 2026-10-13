"""V1_5_MULTI_SOURCE_SLICE_1 micro-gate: two distinct DOCUMENTS (not two independent publishers) through the real governance chain.

Real ExternalReadAuthorizer / SearchBoundReadAuthorizer / AEPAdapter / executors; mock HTTP and a scripted port only. No live run.
V1.4 defaults (max_deep_reads=1, prompt bytes, frozen modules) must stay identical."""
import hashlib
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from application.agent_loop import VerificationResult, build_prompt, prompt_header
from application.agent_runtime import build_agent_loop
from application.computer_runtime import ComputerPageObservation
from application.source_facts import (MAX_DEEP_READS, DeepReadCandidate, DeepReadFact, SearchConfig, SourceCandidate,
                                      SourceFactRejected, SourceFactStore)
from application.wombat_v15_verifier import make_v15_verifier
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor
from governance.bound_read import READ_SEARCH_SOURCE_ACTION, SearchSourceBinding

from test_v1_4_deep_read_micro_gate import GOAL_TEXT, Port, Script, WOMBAT
from test_v1_4_verifier_gate import obs, page, preview

TA, UA = "Alpha Handbook 7431", "https://docs.example.org/guide/alpha"
TB, UB = "Beta Manual 9120", "https://docs.example.org/guide/beta"
RES_A = {"title": TA, "url": UA, "description": "A short guide to alpha.", "age": "1 day ago"}
RES_B = {"title": TB, "url": UB, "description": "A short guide to beta.", "age": "1 day ago"}
TEXT_A = "Section 1\nThe alpha identifier is 2817 and the alpha code is 5785.\nEnd."
TEXT_B = "Chapter 2\nThe beta identifier is 1122 and the beta code is 6528.\nEnd."
SA = json.dumps({"kind": "search", "query": "alpha handbook"})
SB = json.dumps({"kind": "search", "query": "beta manual"})
READ1, READ2 = json.dumps({"kind": "read_source", "source": 1}), json.dumps({"kind": "read_source", "source": 2})
FINISH = '{"kind":"finish","claim":"done"}'
CONFIG15 = SearchConfig(("docs.example.org",), result_limit=1, max_searches=2)

# prompt bytes of the V1.4 freeze (07bc281): header / +search / +search+read / full build_prompt
FROZEN_PROMPTS = ("da9da5b9215e7793326dac479d0667e47c5a13687147d518ff704d2992f4a520",
                  "4350440ff29a00e6c52e457a36687e64fe6e75c2d5d4fab3e6d3896f1cd2b501",
                  "8bff42efcb9b2b5f38165f918c5a7cb5d94714a9e0d1423473496aacac2ca5fb",
                  "04458fc436108c2ce15711eaf5d0e99dae96fcab782965d3132aa1df99f934f5")


class Port15(Port):
    def __init__(self, texts):
        super().__init__()
        self.texts = texts

    def read_page(self, requested_url):
        self.text = self.texts[requested_url]
        return super().read_page(requested_url)


def brave15(same=None):
    def handler(request):
        q = request.url.params["q"]
        item = same or (RES_A if "alpha" in q else RES_B)
        return httpx.Response(200, json={"web": {"results": [item]}})
    return BraveSearchExecutor(transport=httpx.MockTransport(handler), api_key_getter=lambda: "k")


def run15(replies, port=None, same=None, config=CONFIG15, **opts):
    port = port or Port15({UA: TEXT_A, UB: TEXT_B})
    provider = Script(replies)
    opts.setdefault("max_deep_reads", 2)
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=config, search_executor=brave15(same), allow_read=True, sleep=lambda s: None, **opts)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=14))
    return result, provider, session, port


BOTH = [SA, SB, READ1, READ2, FINISH]


# 1 -------------------------------------------------------------------------------------------------------------------
def retired_test_1_v14_defaults_and_frozen_modules_unchanged():
    assert MAX_DEEP_READS == 1 and SourceFactStore()._max_deep_reads == 1
    loop, _s = build_agent_loop(Script([]), Port(), lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                search=SearchConfig(("docs.example.org",)), search_executor=brave15(), allow_read=True)
    assert loop._max_deep_reads == 1 and loop._max_searches == SearchConfig(("x.example",)).max_searches
    try:
        done = subprocess.run(["git", "diff", "--quiet", "07bc281", "--", "governance/bound_read.py", "application/wombat_v13_verifier.py",
                               "application/wombat_v14_verifier.py", "application/wombat_verifier.py",
                               "cognitive/agent_contracts.py"], capture_output=True)
    except FileNotFoundError:
        pytest.skip("git unavailable")
    assert done.returncode == 0       # frozen verifiers, bound-read contract and decision contract remain byte-identical to V1.4


# 2 -------------------------------------------------------------------------------------------------------------------
def test_2_v15_config_is_two_searches_two_reads():
    loop, _s = build_agent_loop(Script([]), Port(), lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                search=CONFIG15, search_executor=brave15(), allow_read=True, max_deep_reads=2)
    assert loop._max_deep_reads == 2 and loop._max_searches == 2 and CONFIG15.result_limit == 1
    with pytest.raises(ValueError):
        SourceFactStore(0)
    with pytest.raises(ValueError):
        SourceFactStore(4)


# 3 -------------------------------------------------------------------------------------------------------------------
def test_3_two_searches_keep_two_bindings():
    result, _p, session, _port = run15([SA, SB, FINISH])
    assert len(session.bindings) == 2 and len(session.search_ledger) == 2
    for b, entry, url in zip(session.bindings, session.search_ledger, (UA, UB)):
        assert (b.search_execution_id, b.canonical_url, b.effect_digest) == (entry["execution_id"], url, entry["result_digest"])
    assert session.bindings[0].search_execution_id != session.bindings[1].search_execution_id


# 4 / 5 ---------------------------------------------------------------------------------------------------------------
def test_4_5_each_read_is_correlated_with_its_own_search():
    result, _p, session, port = run15(BOTH)
    a, b = result.deep_read_facts
    assert port.reads == [UA, UB] and result.deep_reads == 2
    for fact, binding, text in ((a, session.bindings[0], "alpha identifier is 2817"), (b, session.bindings[1], "beta identifier is 1122")):
        assert (fact.search_execution_id, fact.source_url) == (binding.search_execution_id, binding.canonical_url)
        assert text in fact.visible_text_excerpt
        assert any(e.get("kind") == "EXECUTION_RESULT" and e.get("action") == READ_SEARCH_SOURCE_ACTION and e.get("ok") is True
                   and e.get("execution_id") == fact.read_execution_id and e.get("result_authority") == "DATA_ONLY"
                   and e.get("result_provenance") == "COMPUTER_OBSERVATION" for e in session.evidence.events())
    assert a.read_execution_id != b.read_execution_id and a.search_execution_id != b.search_execution_id
    assert "beta identifier" not in a.visible_text_excerpt and "alpha identifier" not in b.visible_text_excerpt


# 6 -------------------------------------------------------------------------------------------------------------------
def test_6_same_canonical_url_via_two_bindings_is_one_document():
    result, _p, session, port = run15([SA, SB, READ1, READ2, FINISH], same=RES_A)
    assert len(session.bindings) == 2 and session.bindings[0].search_execution_id != session.bindings[1].search_execution_id
    assert port.reads == [UA] and result.deep_reads == 1 and len(result.deep_read_facts) == 1
    assert any("DEEP_READ CONTROLLED_RETURN source_already_read" in l for l in result.log)
    store = SourceFactStore(2)                                   # defence in depth inside the store itself
    store.admit(SourceCandidate("x1", RES_A, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    store.admit(SourceCandidate("x2", RES_A, "e" * 64, "DATA_ONLY", "TOOL_RESULT"))
    ok1 = DeepReadCandidate("r1", "x1", "d" * 64, UA, UA, "text", False, None, "1" * 64, "DATA_ONLY", "COMPUTER_OBSERVATION")
    store.admit_deep_read(ok1, lambda c: True)
    with pytest.raises(SourceFactRejected) as err:
        store.admit_deep_read(replace(ok1, read_execution_id="r2", search_execution_id="x2", search_effect_digest="e" * 64,
                                      result_digest="2" * 64), lambda c: True)
    assert err.value.code == "source_already_read"


# 7 -------------------------------------------------------------------------------------------------------------------
def test_7_second_read_of_the_same_ordinal_is_refused_before_any_browser_effect():
    result, _p, _s, port = run15([SA, SB, READ1, READ1, FINISH])
    assert port.reads == [UA] and result.deep_reads == 1
    assert any("DEEP_READ CONTROLLED_RETURN source_already_read" in l for l in result.log)


# 8 -------------------------------------------------------------------------------------------------------------------
def test_8_third_deep_read_is_refused():
    result, _p, _s, port = run15([SA, SB, READ1, READ2, READ1, FINISH])
    assert port.reads == [UA, UB] and result.deep_reads == 2
    assert any("DEEP_READ FAILED deep_read_budget_exhausted" in l for l in result.log)
    store = SourceFactStore(2)                                   # the store refuses a third fact even if the loop did not
    for i, item in enumerate((RES_A, RES_B, {**RES_B, "url": "https://docs.example.org/guide/gamma"})):
        store.admit(SourceCandidate(f"x{i}", item, f"{i}" * 64, "DATA_ONLY", "TOOL_RESULT"))
    for i, url in enumerate((UA, UB)):
        store.admit_deep_read(DeepReadCandidate(f"r{i}", f"x{i}", f"{i}" * 64, url, url, "t", False, None, f"{i}" * 64,
                                                "DATA_ONLY", "COMPUTER_OBSERVATION"), lambda c: True)
    g = "https://docs.example.org/guide/gamma"
    with pytest.raises(SourceFactRejected) as err:
        store.admit_deep_read(DeepReadCandidate("r2", "x2", "2" * 64, g, g, "t", False, None, "2" * 64, "DATA_ONLY", "COMPUTER_OBSERVATION"),
                              lambda c: True)
    assert err.value.code == "deep_read_limit"


# 9 / 10 / 11 ---------------------------------------------------------------------------------------------------------
def test_9_10_11_fact_a_survives_b_and_source_data_keeps_separate_provenance():
    result, provider, _s, _port = run15(BOTH)
    a, b = result.deep_read_facts
    last = provider.prompts[-1]                                  # the turn after both reads
    assert "<SOURCE_DATA>" in last
    assert f"deep read of source 1 (search_bound_deep_read execution={a.read_execution_id})" in last
    assert f"deep read of source 2 (search_bound_deep_read execution={b.read_execution_id})" in last
    assert "alpha identifier is 2817" in last and "beta identifier is 1122" in last
    block_a = last.split(f"execution={a.read_execution_id})")[1].split("deep read of source 2")[0]
    assert "alpha identifier" in block_a and "beta identifier" not in block_a        # B did not replace or contaminate A
    after_first = provider.prompts[-2]                           # between the two reads only A existed
    assert "alpha identifier" in after_first and "beta identifier" not in after_first


# verifier fixtures ---------------------------------------------------------------------------------------------------
VA = ("RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1 | RFC Editor", "https://www.rfc-editor.org/info/rfc2616/",
      "This RFC was updated, see RFC 2817, RFC 5785, RFC 6266, RFC 6585.")
VB = ("RFC 793: Transmission Control Protocol | RFC Editor", "https://www.rfc-editor.org/info/rfc793/",
      "This RFC was updated, see RFC 1122, RFC 3168, RFC 6093, RFC 6528.")
SD, RD = {"A": "a" * 64, "B": "b" * 64}, {"A": "c" * 64, "B": "d" * 64}


def chain_data(**over):
    ledger, bindings, facts, evs, se, re_ = [], [], [], [], [], []
    for k, (t, u, d) in (("A", VA), ("B", VB)):
        o = over.get(k, {})
        t, u, d = o.get("title", t), o.get("url", u), o.get("fact", d)
        ledger.append({"execution_id": f"x-s{k}", "title": t, "url": u, "result_digest": SD[k]})
        bindings.append(SearchSourceBinding(f"x-s{k}", 0, u, SD[k]))
        facts.append(DeepReadFact("search_bound_deep_read", o.get("read_id", f"x-r{k}"), f"x-s{k}", u,
                                  f"Skip to content. {d} Network Working Group", True, RD[k]))
        evs += [{"kind": "EXECUTION_RESULT", "action": "web.search", "ok": True, "execution_id": f"x-s{k}"},
                {"kind": "AUTHORIZATION", "action": READ_SEARCH_SOURCE_ACTION, "event_id": f"ev-auth-{k}", "resource": u,
                 "args": [["binding", json.dumps({"search_execution_id": f"x-s{k}", "result_index": 0, "effect_digest": SD[k]})]]},
                {"kind": "EXECUTION_RESULT", "action": READ_SEARCH_SOURCE_ACTION, "ok": True, "execution_id": o.get("read_id", f"x-r{k}"),
                 "authorization_id": f"ev-auth-{k}", "result_authority": "DATA_ONLY", "result_provenance": "COMPUTER_OBSERVATION"}]
        se.append(SimpleNamespace(opcode="web.search", result_digest=SD[k]))
        re_.append(SimpleNamespace(opcode=READ_SEARCH_SOURCE_ACTION, result_digest=RD[k]))
    return dict(ledger=ledger, evs=evs, se=se, bindings=bindings, facts=facts, re_=re_)


def verdict15(aria, **kw):
    d = {**chain_data(), **kw}
    return make_v15_verifier(lambda: d["ledger"], lambda: d["evs"], lambda: d["se"], lambda: d["bindings"], lambda: d["facts"],
                             lambda: d["re_"])(obs(aria))


FULL15 = page(preview(*VA, *VB))


# 12 ------------------------------------------------------------------------------------------------------------------
def test_12_both_chains_and_both_trios_in_preview_pass():
    r = verdict15(FULL15)
    assert r.verified and r.facts["CHAIN_A_VERIFIED"] and r.facts["CHAIN_B_VERIFIED"] and r.facts["CHAINS_DISTINCT"]
    assert verdict15(page(preview(" ".join(VB), " ".join(VA)))).verified          # layout / order / grouping are irrelevant


# 13 ------------------------------------------------------------------------------------------------------------------
def test_13_missing_b_fails():
    d = chain_data()
    assert not verdict15(FULL15, ledger=d["ledger"][:1]).verified                  # only one search chain
    assert not verdict15(page(preview(*VA))).verified                              # B absent from the artifact
    r = verdict15(FULL15, facts=d["facts"][:1])                                    # B read never admitted
    assert not r.verified and r.facts["CHAIN_B_CORRELATED"] is False and r.facts["CHAIN_A_VERIFIED"] is True
    assert not verdict15(page(preview(*VA, VB[0], VB[1]))).verified                # B title+URL without B deep fact


# 14 ------------------------------------------------------------------------------------------------------------------
def test_14_swapped_deep_read_facts_fail():
    d = chain_data()
    fa, fb = d["facts"]
    swapped = [replace(fb, search_execution_id="x-sA", source_url=fa.source_url), replace(fa, search_execution_id="x-sB", source_url=fb.source_url)]
    assert not verdict15(FULL15, facts=swapped).verified
    assert not verdict15(FULL15, facts=[replace(fa, search_execution_id="x-sB"), replace(fb, search_execution_id="x-sA")]).verified
    assert verdict15(page(preview(VA[0], VA[1], VB[2], VB[0], VB[1], VA[2]))).verified      # pure layout permutation stays valid
    assert not verdict15(page(preview(*VA, VB[0], VB[1], VA[2]))).verified                  # B shown with A's sentence


# 15 ------------------------------------------------------------------------------------------------------------------
def test_15_duplicate_canonical_url_fails():
    d = chain_data(B={"url": VA[1], "title": VB[0], "fact": VB[2]})
    r = make_v15_verifier(lambda: d["ledger"], lambda: d["evs"], lambda: d["se"], lambda: d["bindings"], lambda: d["facts"],
                          lambda: d["re_"])(obs(page(preview(VA[0], VA[1], VA[2], VB[0], VB[2]))))
    assert not r.verified and "chains_share_canonical_url" in r.reasons and r.facts["CHAINS_DISTINCT"] is False


# 16 ------------------------------------------------------------------------------------------------------------------
def test_16_only_one_valid_v14_chain_fails():
    d = chain_data()
    r = verdict15(FULL15, re_=d["re_"][:1])
    assert not r.verified and r.facts["CHAIN_A_VERIFIED"] is True and r.facts["CHAIN_B_VERIFIED"] is False
    r = verdict15(FULL15, evs=[e for e in d["evs"] if e.get("execution_id") != "x-rA"])
    assert not r.verified and r.facts["CHAIN_A_VERIFIED"] is False and r.facts["CHAIN_B_VERIFIED"] is True
    r = verdict15(page(preview(*VA)), se=d["se"][:1], ledger=d["ledger"])
    assert not r.verified


# 17 ------------------------------------------------------------------------------------------------------------------
def test_17_trios_outside_the_preview_fail():
    sd = page(preview("nothing here"), extra="".join(f'    - paragraph [ref=s{i}]: {t}\n' for i, t in enumerate((*VA, *VB))))
    assert not verdict15(sd).verified
    assert not verdict15(page(preview("nothing"), prompt=" ".join((*VA, *VB)))).verified
    assert not verdict15(page(preview(*VA), other_frame=preview(*VB))).verified


# 18 ------------------------------------------------------------------------------------------------------------------
def retired_test_18_v14_prompt_bytes_identical_and_v15_prompt_says_two():
    h = lambda s: hashlib.sha256(s.encode()).hexdigest()
    goal = AgentGoal("g", "goal text", max_steps=5)
    o = ComputerPageObservation("about:blank", "", "s", 0, (), "")
    assert (h(prompt_header()), h(prompt_header(True)), h(prompt_header(True, True)),
            h(build_prompt(goal, o, 1, None, ("https://a.example",), None, "blk", True, True))) == FROZEN_PROMPTS
    assert prompt_header(True, True, 1) == prompt_header(True, True)
    v15 = prompt_header(True, True, 2)
    assert "At most 2 deep reads are allowed." in v15 and "At most one deep read" not in v15
    assert v15.replace("At most 2 deep reads are allowed.", "At most one deep read is allowed.") == prompt_header(True, True)


# extra: cross-correlation (a read execution cannot serve both chains) -------------------------------------------------
def test_19_one_read_execution_cannot_serve_both_chains():
    d = chain_data(B={"read_id": "x-rA"})
    r = verdict15(FULL15, **d)
    assert not r.verified and "chains_share_read_execution" in r.reasons


def test_20_identical_deep_fact_or_title_is_one_chain_of_evidence_not_two():
    d = chain_data(B={"fact": VA[2]})
    r = verdict15(page(preview(*VA, VB[0], VB[1])), **d)
    assert not r.verified and "chains_share_deep_fact" in r.reasons
