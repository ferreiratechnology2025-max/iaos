"""V1_4_SEARCH_BOUND_DEEP_READ_SLICE_1 micro-gate: governed web.search -> binding -> read_source(n) -> one deep read -> DeepReadFact.

Real governance chain (ExternalReadAuthorizer, SearchBoundReadAuthorizer, AEPAdapter, executors); mock HTTP only. No live run."""
import hashlib
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from application.agent_loop import VerificationResult, build_prompt, prompt_header
from application.agent_runtime import build_agent_loop
from application.computer_runtime import (ComputerElement, ComputerPageObservation, ComputerRuntimeError, OpenBotComputerAdapter,
                                          PageRead, PageRestore)
from application.source_facts import (MAX_DEEP_READS, DeepReadCandidate, SearchConfig, SourceCandidate, SourceFactRejected,
                                      SourceFactStore)
from cognitive.agent_contracts import AgentGoal, DecisionParseError, ReadSourceDecision, parse_decision
from cognitive.provider import LLMResponse
from governance.aep_executors import BraveSearchExecutor
from governance.bound_read import READ_SEARCH_SOURCE_ACTION, SearchSourceBinding, canonical_source_url
from governance.contract import Proposal, Provenance

TITLE = "Zeta Quokka Handbook 7431"
URL = "https://docs.example.org/guide/quokka"
GOOD = {"title": TITLE, "url": URL, "description": "A short guide to quokkas.", "age": "1 day ago"}
CONFIG = SearchConfig(("docs.example.org",))
WOMBAT = "https://wombat.example/build"
GOAL_TEXT = "Look up the topic with a web search, read the source, then build a page that presents it."
SEARCH = json.dumps({"kind": "search", "query": "quokka handbook"})
READ = json.dumps({"kind": "read_source", "source": 1})
FINISH = '{"kind":"finish","claim":"done"}'
NAVIGATE = json.dumps({"kind": "navigate", "url": WOMBAT})
TYPE = json.dumps({"kind": "act", "action": "browser.type", "args": {"ref": "e1", "text": "x"}})
DEEP_TEXT = "Section 9\nThe quokka identifier is 2817 and the code is 5785.\nEnd."


class Port:
    def __init__(self, text=DEEP_TEXT, final_url=None, challenge=None, truncated=False, fail_restore=False, restore_final=None,
                 restore_challenge=None):
        self.snapshot, self.url, self.typed, self.reads, self.restores = 0, WOMBAT, [], [], []
        self.text, self.final_url, self.challenge, self.truncated = text, final_url, challenge, truncated
        self.fail_restore, self.restore_final, self.restore_challenge = fail_restore, restore_final, restore_challenge

    def observe(self):
        self.snapshot += 1
        els = (ComputerElement("e1", "textbox", "Title"), ComputerElement("e2", "button", "Go"))
        return ComputerPageObservation(self.url, "Page", "sess-1", self.snapshot, els,
                                       f'- textbox "Title" [ref=e1]\n- button "Go" [ref=e2]\n- text: typed={len(self.typed)}')

    def navigate(self, url):
        self.url = url
        return url

    def type(self, page_url, session_id, snapshot_id, ref, text):
        self.typed.append(text)
        return {"url": self.url}

    def read_page(self, requested_url):
        self.reads.append(requested_url)
        self.url = self.final_url or requested_url              # the shared browser really leaves the working page
        return PageRead(requested_url, self.final_url or requested_url, "Doc", self.text, self.truncated, self.challenge, "sess-r",
                        "2026-01-01T00:00:00+00:00")

    def restore_page(self, return_url):
        self.restores.append(return_url)
        if self.fail_restore:
            raise ComputerRuntimeError("computer_runtime_request_failed")
        self.url = self.restore_final or return_url
        return PageRestore(return_url, self.url, self.url, self.restore_challenge, "sess-r")


class Script:
    provider_id = "script"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "script")


def brave(results=None):
    return BraveSearchExecutor(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"web": {"results": [GOOD] if results is None else results}})), api_key_getter=lambda: "k")


def run(replies, port=None, allow_read=True, results=None, **opts):
    port = port or Port()
    executor = brave(results)
    provider = Script(replies)
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=CONFIG, search_executor=executor, allow_read=allow_read, sleep=lambda s: None, **opts)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=14))
    return result, provider, session, port, executor


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# 1 -------------------------------------------------------------------------------------------------------------------
def test_1_search_result_creates_exact_binding():
    result, _p, session, port, executor = run([SEARCH, FINISH])
    assert len(session.bindings) == 1
    b = session.bindings[0]
    ledger = session.search_ledger[0]
    assert (b.search_execution_id, b.result_index, b.canonical_url, b.effect_digest) == (
        ledger["execution_id"], 0, URL, ledger["result_digest"])
    assert any(r.result_digest == b.effect_digest for r in executor._effects)       # correlated with the executor effect record
    assert port.reads == []                                                          # a binding alone reads nothing


# 2 -------------------------------------------------------------------------------------------------------------------
def test_2_read_uses_binding_url_never_a_model_url():
    for extra in ({"url": "https://evil.example/x"}, {"origin": "https://evil.example"}, {"search_execution_id": "x-1"}):
        with pytest.raises(DecisionParseError, match="unexpected_fields"):
            parse_decision(json.dumps({"kind": "read_source", "source": 1, **extra}), None, allow_read=True)
    result, _p, _s, port, _e = run([SEARCH, READ, FINISH])
    assert port.reads == [URL] and result.deep_reads == 1 and len(result.deep_read_facts) == 1


def test_2b_trusted_observation_url_is_extracted_before_canonicalization():
    result, _p, session, port, _e = run([SEARCH, READ, FINISH])
    assert result.deep_reads == 1 and port.reads == [URL]
    assert session._trusted_page.page_url == WOMBAT


@pytest.mark.parametrize("trusted_page", [SimpleNamespace(), SimpleNamespace(page_url="about:blank")])
def test_2c_missing_or_malformed_trusted_observation_url_fails_closed(trusted_page):
    _result, _p, session, port, _e = run([SEARCH, FINISH])
    session._trusted_page = trusted_page
    outcome = session.read_source("manual-read", session.bindings[0].search_execution_id)
    assert not outcome.ok and outcome.code == "return_url_invalid"
    assert port.reads == []


# 3 -------------------------------------------------------------------------------------------------------------------
def test_3_out_of_range_source_fails_closed():
    for bad in (0, -1, 100, True, "1"):
        with pytest.raises(DecisionParseError, match="source_invalid"):
            parse_decision(json.dumps({"kind": "read_source", "source": bad}), None, allow_read=True)
    result, _p, _s, port, _e = run([SEARCH, json.dumps({"kind": "read_source", "source": 2}), FINISH])
    assert port.reads == [] and result.deep_read_facts == [] and any("DEEP_READ FAILED source_out_of_range" in l for l in result.log)
    result, _p, _s, port, _e = run([json.dumps({"kind": "read_source", "source": 1}), FINISH])   # no search yet
    assert port.reads == [] and any("DEEP_READ FAILED source_out_of_range" in l for l in result.log)


# 4 -------------------------------------------------------------------------------------------------------------------
def _proposal(session, resource, request):
    request = {"return_url": WOMBAT, **request}
    return Proposal("i1", READ_SEARCH_SOURCE_ACTION, {"resource": resource, "request": json.dumps(request)}, Provenance("agent", "i1"))


def test_4_tampered_url_after_search_is_denied():
    _r, _p, session, port, _e = run([SEARCH, FINISH])
    b = session.bindings[0]
    for resource in ("https://docs.example.org/guide/other", URL + "/", "https://evil.example/guide/quokka", "http://docs.example.org/guide/quokka",
                     "https://docs.example.org/guide/quokka?x=1", "https://docs.example.org"):
        with pytest.raises(ValueError, match="url_not_bound|https_public_url_required"):
            session._read_auth.authorize(_proposal(session, resource, b.request()))
    with pytest.raises(ValueError, match="non_authoritative_provenance"):
        session._read_auth.authorize(Proposal("i2", READ_SEARCH_SOURCE_ACTION, {"resource": URL, "request": json.dumps(b.request())},
                                              Provenance("tool", "i2")))
    assert port.reads == []
    assert session._read_auth.canonicalize(_proposal(session, URL, b.request())).resource == URL     # control: the exact URL canonicalises


# 5 -------------------------------------------------------------------------------------------------------------------
def test_5_search_execution_or_effect_digest_mismatch_is_denied():
    _r, _p, session, port, executor = run([SEARCH, FINISH])
    b = session.bindings[0]
    with pytest.raises(ValueError, match="binding_unknown"):
        session._read_auth.authorize(_proposal(session, URL, {**b.request(), "search_execution_id": "x-forged"}))
    with pytest.raises(ValueError, match="binding_effect_digest_mismatch"):
        session._read_auth.authorize(_proposal(session, URL, {**b.request(), "effect_digest": "0" * 64}))
    executor._effects.clear()                                      # the executor no longer proves the search effect
    with pytest.raises(ValueError, match="binding_unknown"):
        session._read_auth.authorize(_proposal(session, URL, b.request()))
    assert port.reads == []


# 6 -------------------------------------------------------------------------------------------------------------------
def _adapter(nav_payload, log):
    def handler(request):
        body = json.loads(request.content) if request.content else None
        log.append((request.method, request.url.path, body))
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "sess-9"})
        if request.url.path == "/navigate":
            return httpx.Response(200, json=nav_payload)
        return httpx.Response(200, json={})
    return OpenBotComputerAdapter("https://openbot.test", "bot", "tok", transport=httpx.MockTransport(handler))


def test_6_request_help_false_in_snapshot_and_navigate():
    log = []
    page = _adapter({"url": URL, "title": "T", "text": "hello", "truncated": False, "challenge": None}, log).read_page(URL)
    posts = [(p, b) for m, p, b in log if m == "POST"]
    assert posts == [("/snapshot", {"requestHelp": False}), ("/navigate", {"url": URL, "requestHelp": False})]
    assert page.final_url == URL and page.text == "hello" and page.truncated is False and page.challenge is None
    assert not any(p.startswith("/control") for _m, p, _b in log)


# 7 -------------------------------------------------------------------------------------------------------------------
def test_7_challenge_means_no_takeover_and_no_fact():
    log = []
    page = _adapter({"url": URL, "title": "T", "text": "x", "truncated": False, "challenge": {"kind": "cloudflare"}}, log).read_page(URL)
    assert page.challenge == "cloudflare" and not any(p.startswith("/control") for _m, p, _b in log)
    with pytest.raises(ComputerRuntimeError, match="computer_challenge_shape"):
        _adapter({"url": URL, "title": "T", "text": "x", "truncated": False, "challenge": {"kind": "weird"}}, []).read_page(URL)
    result, _p, session, port, _e = run([SEARCH, READ, FINISH], port=Port(challenge="cloudflare"))
    assert result.deep_read_facts == [] and any("DEEP_READ FAILED execution_failed:challenge_detected" in l for l in result.log)
    assert not any(e.get("action") == "browser.request_human" for e in session.evidence.events())


# 8 -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("final", ["https://other.example/guide/quokka", "https://docs.example.org/guide/quokka/",
                                   "https://docs.example.org/login", "http://docs.example.org/guide/quokka"])
def test_8_final_url_different_from_bound_url_discards_content(final):
    result, provider, _s, port, _e = run([SEARCH, READ, FINISH], port=Port(final_url=final))
    assert result.deep_read_facts == [] and any("DEEP_READ FAILED execution_failed:final_url" in l for l in result.log)
    assert "quokka identifier is 2817" not in provider.prompts[-1]


def test_8b_fragment_and_default_port_are_equivalent():
    result, *_ = run([SEARCH, READ, FINISH], port=Port(final_url="https://DOCS.example.org:443/guide/quokka#sec"))
    assert len(result.deep_read_facts) == 1


# 9 -------------------------------------------------------------------------------------------------------------------
def test_9_correlated_deep_fact_is_admitted_to_source_data():
    result, provider, session, _port, _e = run([SEARCH, READ, FINISH])
    fact = result.deep_read_facts[0]
    assert fact.source_kind == "search_bound_deep_read" and fact.source_url == URL and fact.search_execution_id == session.bindings[0].search_execution_id
    assert fact.read_execution_id.startswith("x-") and "2817" in fact.visible_text_excerpt and fact.truncated is False
    prompt = provider.prompts[-1]
    assert "deep read of source 1 (search_bound_deep_read" in prompt and "| The quokka identifier is 2817" in prompt
    done = [e for e in session.evidence.events() if e["kind"] == "EXECUTION_RESULT" and e["action"] == READ_SEARCH_SOURCE_ACTION]
    assert len(done) == 1 and (done[0]["result_authority"], done[0]["result_provenance"]) == ("DATA_ONLY", "COMPUTER_OBSERVATION")


# 10 ------------------------------------------------------------------------------------------------------------------
def test_10_generic_computer_observation_is_still_rejected_by_the_store():
    store = SourceFactStore()
    with pytest.raises(SourceFactRejected) as err:
        store.admit(SourceCandidate("x1", GOOD, "d" * 64, "DATA_ONLY", "COMPUTER_OBSERVATION"))
    assert err.value.code == "result_boundary_failed" and store.facts() == ()
    store.admit(SourceCandidate("x1", GOOD, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    ok = DeepReadCandidate("r1", "x1", "d" * 64, URL, URL, "text", False, None, "e" * 64, "DATA_ONLY", "COMPUTER_OBSERVATION")
    for bad in (replace(ok, result_provenance='TOOL_RESULT'), replace(ok, result_authority='EFFECT')):
        with pytest.raises(SourceFactRejected):
            store.admit_deep_read(bad, lambda c: True)
    with pytest.raises(SourceFactRejected) as err:
        store.admit_deep_read(ok, lambda c: False)
    assert err.value.code == "read_not_correlated" and store.deep_facts() == ()
    assert store.admit_deep_read(ok, lambda c: True).read_execution_id == "r1"


# 11 ------------------------------------------------------------------------------------------------------------------
def test_11_deep_fact_survives_navigation_to_wombat():
    result, provider, _s, port, _e = run([SEARCH, READ, NAVIGATE, TYPE, TYPE, FINISH])
    assert result.status == "VERIFIED" and port.url == WOMBAT
    after_nav = provider.prompts[3:]
    assert after_nav and all("| The quokka identifier is 2817" in p for p in after_nav)
    assert "| The quokka identifier is 2817" not in provider.prompts[0]
    assert "The system returned the browser to the page it was on" in provider.prompts[2]


# 12 ------------------------------------------------------------------------------------------------------------------
def retired_test_12_without_allow_read_v13_behaviour_is_preserved():
    with pytest.raises(DecisionParseError, match="unknown_kind"):
        parse_decision(READ, None, allow_search=True)
    result, provider, session, port, _e = run([SEARCH, READ, FINISH], allow_read=False)
    assert port.reads == [] and any("decision REJECTED unknown_kind" in l for l in result.log) and result.deep_read_facts == []
    assert session.bindings == [] and all("READ SOURCE:" not in p and "read_source" not in p for p in provider.prompts)
    h = prompt_header(True)
    assert hashlib.sha256(h.encode()).hexdigest() == "4350440ff29a00e6c52e457a36687e64fe6e75c2d5d4fab3e6d3896f1cd2b501"
    assert prompt_header(False) == prompt_header(False, False) and "read_source" in prompt_header(True, True)
    with pytest.raises(ValueError, match="read_requires_search"):
        build_agent_loop(Script([]), Port(), lambda o: VerificationResult(True, {}), allow_read=True)


# 13 ------------------------------------------------------------------------------------------------------------------
def test_13_second_deep_read_is_refused():
    assert MAX_DEEP_READS == 1
    result, _p, _s, port, _e = run([SEARCH, READ, READ, FINISH])
    assert len(port.reads) == 1 and result.deep_reads == 1 and len(result.deep_read_facts) == 1
    assert any("DEEP_READ FAILED deep_read_budget_exhausted" in l for l in result.log)
    store = SourceFactStore()                                   # defence in depth: the store itself also refuses a second deep fact
    store.admit(SourceCandidate("x1", GOOD, "d" * 64, "DATA_ONLY", "TOOL_RESULT"))
    cand = DeepReadCandidate("r1", "x1", "d" * 64, URL, URL, "text", False, None, "e" * 64, "DATA_ONLY", "COMPUTER_OBSERVATION")
    store.admit_deep_read(cand, lambda c: True)
    with pytest.raises(SourceFactRejected) as err:
        store.admit_deep_read(cand, lambda c: True)
    assert err.value.code == "deep_read_limit"


# 14 ------------------------------------------------------------------------------------------------------------------
def test_14_truncated_is_preserved_end_to_end():
    log = []
    page = _adapter({"url": URL, "title": "T", "text": "x" * 10, "truncated": True, "challenge": None}, log).read_page(URL)
    assert page.truncated is True
    with pytest.raises(ComputerRuntimeError, match="computer_truncated_shape"):
        _adapter({"url": URL, "title": "T", "text": "x", "challenge": None}, []).read_page(URL)
    result, provider, *_ = run([SEARCH, READ, FINISH], port=Port(text="a" * 7000, truncated=True))
    fact = result.deep_read_facts[0]
    assert fact.truncated is True and len(fact.visible_text_excerpt) == 6000
    assert "text_truncated: yes" in provider.prompts[-1]


# canonicalisation ----------------------------------------------------------------------------------------------------
def test_canonical_source_url_rules():
    assert canonical_source_url("https://Host.example") == canonical_source_url("https://host.example/") == "https://host.example/"
    assert canonical_source_url("https://h.example/a") != canonical_source_url("https://h.example/a/")
    assert canonical_source_url("https://h.example:443/a#frag") == "https://h.example/a"
    assert canonical_source_url("https://h.example/a?q=1#f") == "https://h.example/a?q=1"
    for bad in ("http://h.example/a", "https://localhost/a", "https://127.0.0.1/a", "https://u:p@h.example/a"):
        with pytest.raises(ValueError):
            canonical_source_url(bad)


def test_15_loop_exposes_admitted_deep_facts_read_only():
    provider = Script([SEARCH, READ, FINISH])
    loop, _session = build_agent_loop(provider, Port(), lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                      search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda s: None)
    assert loop.deep_read_facts == ()
    loop.run(AgentGoal("g", GOAL_TEXT, max_steps=14))
    assert [f.source_url for f in loop.deep_read_facts] == [URL]

# ---------------------------------------------------------------------------------------------------------------------
# V1_4_DEEP_READ_CONTROLLED_RETURN_V1: the runtime restores the trusted working page after the temporary source read
def test_c1_wombat_read_exact_return_fact_admitted():
    result, provider, session, port, _e = run([SEARCH, READ, FINISH])
    assert port.reads == [URL] and port.restores == [WOMBAT] and port.url == WOMBAT
    assert len(result.deep_read_facts) == 1 and result.status == "VERIFIED"
    t = session._read_executor.trace[0]
    assert (t["pre_read_url"], t["bound_source_url"], t["source_final_url"], t["return_url"], t["return_nav_final_url"],
            t["return_observed_url"]) == (WOMBAT, URL, URL, WOMBAT, WOMBAT, WOMBAT)
    assert t["restore_attempted"] and t["restore_succeeded"] and t["temporary_source_network_effect_occurred"] and not t["rollback_of_network_effect"]
    auth = [e for e in session.evidence.events() if e["kind"] == "AUTHORIZATION" and e["action"] == READ_SEARCH_SOURCE_ACTION][0]
    assert json.loads(dict(auth["args"])["binding"])["return_url"] == WOMBAT          # the return is part of the authorized contract


def test_c2_model_cannot_supply_return_url_and_runtime_value_wins():
    with pytest.raises(DecisionParseError, match="unexpected_fields"):
        parse_decision(json.dumps({"kind": "read_source", "source": 1, "return_url": "https://evil.example/"}), None, allow_read=True)
    _r, _p, session, port, _e = run([SEARCH, FINISH])
    b = session.bindings[0]
    for forged in ("https://wombat.example/other", "https://evil.example/build"):
        with pytest.raises(ValueError, match="return_url_not_trusted_page|return_origin_not_allowed"):
            session._read_auth.canonicalize(_proposal(session, URL, {**b.request(), "return_url": forged}))
    with pytest.raises(ValueError, match="return_url_invalid"):
        session._read_auth.canonicalize(_proposal(session, URL, {**b.request(), "return_url": WOMBAT + "#frag"}))
    result, *_ = run([SEARCH, READ, FINISH])
    assert result.deep_reads == 1


def test_c3_return_url_outside_allowed_origins_is_denied():
    port = Port()
    port.url = "https://other.example/page"
    result, _p, _s, port, _e = run([SEARCH, READ, FINISH], port=port)
    assert port.reads == [] and port.restores == [] and result.deep_read_facts == []
    assert any("DEEP_READ FAILED denied_by_governance:return_origin_not_allowed" in l for l in result.log)


def test_c4_source_final_url_mismatch_restores_and_admits_nothing():
    result, _p, session, port, _e = run([SEARCH, READ, FINISH], port=Port(final_url="https://other.example/x"))
    assert result.deep_read_facts == [] and port.restores == [WOMBAT] and port.url == WOMBAT
    assert any("execution_failed:final_url_mismatch" in l for l in result.log)
    assert session._read_executor.trace[0]["restore_succeeded"] is True


def test_c5_challenge_no_takeover_restores_and_admits_nothing():
    result, _p, session, port, _e = run([SEARCH, READ, FINISH], port=Port(challenge="cloudflare"))
    assert result.deep_read_facts == [] and port.restores == [WOMBAT]
    assert any("execution_failed:challenge_detected" in l for l in result.log)
    assert not any(e.get("action") == "browser.request_human" for e in session.evidence.events())


def test_c6_read_ok_but_restore_fails_action_fails_fact_not_admitted():
    result, _p, session, port, _e = run([SEARCH, READ, FINISH], port=Port(fail_restore=True))
    assert result.deep_read_facts == [] and any("execution_failed:restore_failed" in l for l in result.log)
    t = session._read_executor.trace[0]
    assert t["restore_attempted"] and not t["restore_succeeded"] and t["source_final_url"] == URL      # the network effect is still evidenced
    assert session._read_executor.effects() == ()


@pytest.mark.parametrize("kw", [dict(restore_final="https://wombat.example/elsewhere"), dict(restore_challenge="cloudflare")])
def test_c7_restore_redirect_or_challenge_fails_the_action(kw):
    result, _p, _s, _port, _e = run([SEARCH, READ, FINISH], port=Port(**kw))
    assert result.deep_read_facts == [] and any("execution_failed:restore_failed" in l for l in result.log)


def _adapter_log():
    log = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        log.append((request.method, request.url.path, body))
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "sess-9"})
        if request.url.path == "/navigate":
            return httpx.Response(200, json={"url": body["url"], "title": "T", "text": "t", "truncated": False, "challenge": None})
        if request.url.path == "/snapshot":
            return httpx.Response(200, json={"url": WOMBAT, "title": "W", "snapshotId": 1, "elements": [], "ariaSnapshot": "- generic"})
        return httpx.Response(200, json={})
    return OpenBotComputerAdapter("https://openbot.test", "bot", "tok", transport=httpx.MockTransport(handler)), log


def test_c8_request_help_false_on_every_relevant_call():
    adapter, log = _adapter_log()
    adapter.read_page(URL)
    restore = adapter.restore_page(WOMBAT)
    assert restore.nav_final_url == WOMBAT and restore.observed_url == WOMBAT and restore.challenge is None
    posts = [(p, b) for m, p, b in log if m == "POST"]
    assert [p for p, _ in posts] == ["/snapshot", "/navigate", "/navigate", "/snapshot"]
    assert all(b.get("requestHelp") is False for _p, b in posts)
    assert not any(p.startswith("/control") for _m, p, _b in log)


def test_c9_next_ordinary_observe_and_act_work_after_the_return():
    result, provider, _s, port, _e = run([SEARCH, READ, TYPE, FINISH])
    assert result.status == "VERIFIED" and port.typed == ["x"] and port.url == WOMBAT
    assert "page_title: Page" in provider.prompts[2]


def test_c10_deep_fact_present_in_the_next_cognition_after_the_return():
    result, provider, _s, _port, _e = run([SEARCH, READ, TYPE, FINISH])
    assert all("| The quokka identifier is 2817" in p for p in provider.prompts[2:])


def _restore_adapter(*, observed_url=WOMBAT, navigation_error=None, diagnostic_sink=None):
    def handler(request):
        body = json.loads(request.content) if request.content else None
        if request.url.path == "/navigate":
            if navigation_error is not None:
                raise navigation_error
            return httpx.Response(200, json={"url": body["url"], "challenge": None})
        if request.url.path == "/snapshot":
            return httpx.Response(200, json={"url": observed_url, "title": "W", "snapshotId": 1,
                                             "elements": [], "ariaSnapshot": "- generic"})
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "sess-9"})
        return httpx.Response(200, json={})

    return OpenBotComputerAdapter("https://openbot.test", "bot", "tok",
                                  transport=httpx.MockTransport(handler),
                                  restore_diagnostic_sink=diagnostic_sink)


def test_c11_restore_diagnostic_sink_is_optional_and_success_is_equivalent():
    plain = _restore_adapter().restore_page(WOMBAT)
    events = []
    traced = _restore_adapter(diagnostic_sink=events.append).restore_page(WOMBAT)
    assert traced == plain
    assert events == [{
        "requested_return_url": WOMBAT,
        "navigation_attempted": True,
        "navigation_succeeded": True,
        "navigation_final_url": WOMBAT,
        "observation_attempted": True,
        "observation_succeeded": True,
        "observed_url": WOMBAT,
        "nav_url_matches_return": True,
        "observed_url_matches_return": True,
        "failure_substage": None,
    }]


def test_c12_restore_navigation_failure_diagnostic_preserves_exception():
    def get_failure(sink=None):
        with pytest.raises(ComputerRuntimeError) as error:
            _restore_adapter(navigation_error=httpx.ReadError("transport"), diagnostic_sink=sink).restore_page(WOMBAT)
        return error.value

    plain = get_failure()
    events = []
    traced = get_failure(events.append)
    assert (type(traced), str(traced)) == (type(plain), str(plain))
    assert events[0]["navigation_attempted"] is True
    assert events[0]["navigation_succeeded"] is False
    assert events[0]["observation_attempted"] is False
    assert events[0]["failure_substage"] == "navigation"


def test_c13_restore_observation_failure_and_sink_failure_cannot_change_result():
    def raising_sink(_event):
        raise RuntimeError("diagnostics must not affect restore")

    def handler(request):
        body = json.loads(request.content) if request.content else None
        if request.url.path == "/navigate":
            return httpx.Response(200, json={"url": body["url"], "challenge": None})
        if request.url.path == "/snapshot":
            raise httpx.ReadError("observation transport")
        return httpx.Response(200, json={"run": "sess-9"})

    def invoke(sink=None):
        with pytest.raises(ComputerRuntimeError) as error:
            OpenBotComputerAdapter("https://openbot.test", "bot", "tok",
                                   transport=httpx.MockTransport(handler),
                                   restore_diagnostic_sink=sink).restore_page(WOMBAT)
        return error.value

    plain = invoke()
    events = []
    traced = invoke(lambda event: (events.append(event), raising_sink(event))[1])
    assert (type(traced), str(traced)) == (type(plain), str(plain))
    assert events[0]["navigation_succeeded"] is True
    assert events[0]["observation_attempted"] is True
    assert events[0]["observation_succeeded"] is False
    assert events[0]["failure_substage"] == "observation"


def test_c14_restore_wrong_observed_url_is_reported_without_changing_restore_result():
    events = []
    restore = _restore_adapter(observed_url="https://wombat.example/other", diagnostic_sink=events.append).restore_page(WOMBAT)
    assert restore.nav_final_url == WOMBAT and restore.observed_url == "https://wombat.example/other"
    assert events[0]["navigation_succeeded"] is True
    assert events[0]["observation_succeeded"] is True
    assert events[0]["nav_url_matches_return"] is True
    assert events[0]["observed_url_matches_return"] is False
    assert events[0]["failure_substage"] is None
