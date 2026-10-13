"""V1_5_TERMINAL_SOURCE_REJECTION_V1 micro-gate (synthetic): a document that was read soundly and deterministically found unusable is never read
again in the same run (keyed by canonical URL, decided before any browser effect, consuming no read attempt). Transient or structural read
failures never mark a source. Real governance chain, mock HTTP, scripted port. No live run.

Replays live attempt 5: combined search -> source X; read X (no usable fact) x4 would have burned the whole read budget."""
import json

import httpx

from application.agent_loop import VerificationResult
from application.agent_runtime import build_agent_loop
from application.source_facts import ResearchBudget, SearchConfig
from cognitive.agent_contracts import AgentGoal
from governance.aep_executors import BraveSearchExecutor

from test_v1_4_deep_read_micro_gate import GOAL_TEXT, Port, Script

BUDGET = ResearchBudget(2, 4, 4)
CONFIG = SearchConfig(("docs.example.org",), result_limit=1, max_searches=4)
D = "https://docs.example.org/"
ITEMS = {"xagain": ("X Variant", D + "rfc/x.html"), "combined": ("X Variant", D + "rfc/x.html"), "ypage": ("Y Doc", D + "info/y/"),
         "zpage": ("Z Doc", D + "info/z/"), "other": ("O Doc", D + "info/o/")}
GOOD, BAD = "Header\nFACT: identifier {n}\nEnd.", "Header\nno usable sentence here\nEnd."
TEXTS = {D + "rfc/x.html": BAD, D + "info/y/": GOOD.format(n=1), D + "info/z/": GOOD.format(n=2), D + "info/o/": GOOD.format(n=3)}
FINISH = '{"kind":"finish","claim":"done"}'
GENERIC = "This source was already read in this run and did not yield a usable deep fact. Choose a different source or search result."


def validator(fact):
    return "FACT:" in fact.visible_text_excerpt


class PortX(Port):
    """Port whose first read of selected URLs misbehaves once: {url: 'raise' | 'restore' | 'challenge' | 'mismatch'}."""
    def __init__(self, faults=None):
        super().__init__()
        self.faults = dict(faults or {})

    def read_page(self, requested_url):
        self.text = TEXTS[requested_url]
        self.challenge, self.final_url, self.fail_restore = None, None, False
        mode = self.faults.pop(requested_url, None)
        if mode == "raise":
            self.reads.append(requested_url)
            raise RuntimeError("boom")
        if mode == "challenge":
            self.challenge = "captcha"
        elif mode == "mismatch":
            self.final_url = D + "elsewhere/"
        elif mode == "restore":
            self.fail_restore = True
        return super().read_page(requested_url)


def brave():
    def handler(request):
        q = request.url.params["q"]
        title, url = ITEMS[next(k for k in ITEMS if k in q)]
        return httpx.Response(200, json={"web": {"results": [{"title": title, "url": url, "description": "d"}]}})
    return BraveSearchExecutor(transport=httpx.MockTransport(handler), api_key_getter=lambda: "k")


def s(q):
    return json.dumps({"kind": "search", "query": q})


def r(n):
    return json.dumps({"kind": "read_source", "source": n})


def run(replies, faults=None, research=BUDGET, **opts):
    port, provider = PortX(faults), Script(replies)
    opts.setdefault("max_consecutive_failures", 20)
    loop, session = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                     search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda x: None,
                                     research=research, deep_fact_validator=validator, **opts)
    return loop.run(AgentGoal("g", GOAL_TEXT, max_steps=40)), provider, port


def failed(result):
    return [l.split("DEEP_READ FAILED ")[1] for l in result.log if "DEEP_READ FAILED " in l]


X = D + "rfc/x.html"


# 1 + 2 + 5 -----------------------------------------------------------------------------------------------------------------
def test_1_2_5_extraction_failure_rejects_the_url_and_a_reread_is_blocked_with_no_effect_and_no_attempt():
    result, _p, port = run([s("combined"), r(1), r(1), r(1), FINISH])
    assert [u.canonical_url for u in result.rejected_sources] == [X] and result.rejected_sources[0].reason == "deep_fact_extraction_failed"
    assert failed(result) == ["deep_fact_extraction_failed", "source_previously_rejected", "source_previously_rejected"]
    assert port.reads == [X]                                    # ZERO browser effect for the blocked rereads
    assert result.deep_reads == 1                               # blocked rereads consume no read attempt


# 3 -------------------------------------------------------------------------------------------------------------------------
def test_3_a_different_search_binding_to_the_same_canonical_url_is_blocked():
    result, _p, port = run([s("combined"), r(1), s("xagain"), r(2), FINISH])
    assert len(result.source_facts) == 2 and result.source_facts[0].source_id != result.source_facts[1].source_id
    assert failed(result) == ["deep_fact_extraction_failed", "source_previously_rejected"] and port.reads == [X] and result.deep_reads == 1


# 4 + 6 ---------------------------------------------------------------------------------------------------------------------
def test_4_6_a_different_url_is_allowed_and_admitted_sources_stay_governed():
    result, _p, port = run([s("combined"), r(1), s("ypage"), r(2), r(2), r(1), FINISH])
    assert port.reads == [X, D + "info/y/"] and [f.source_url for f in result.deep_read_facts] == [D + "info/y/"]
    assert failed(result) == ["deep_fact_extraction_failed", "source_previously_rejected"]    # idempotent reread is a controlled return


# 7 / 8 / 9 / 10 -- non-terminal failures: a SOUND page (Y) that fails once for a structural reason is never marked ---------------------
Y = D + "info/y/"


def _nonterminal(fault):
    result, _p, port = run([s("ypage"), r(1), r(1), FINISH], faults={Y: fault})
    assert result.rejected_sources == [] and "source_previously_rejected" not in failed(result)
    return result, port


def test_7_transient_runtime_failure_does_not_reject():
    result, port = _nonterminal("raise")
    assert failed(result)[0].startswith(("execution_failed", "refused")) and port.reads == [Y, Y]
    assert [f.source_url for f in result.deep_read_facts] == [Y]                  # the source stayed readable and was admitted on the retry


def test_8_restore_failure_does_not_reject():
    result, _port = _nonterminal("restore")
    assert "restore_failed" in failed(result)[0]


def test_9_challenge_does_not_reject():
    result, port = _nonterminal("challenge")
    assert "challenge" in failed(result)[0] and port.reads == [Y, Y]


def test_10_final_url_mismatch_does_not_reject():
    result, port = _nonterminal("mismatch")
    assert "final_url_mismatch" in failed(result)[0] and port.reads == [Y, Y]


def test_10b_only_a_sound_read_with_no_usable_fact_rejects():
    """Contrast: the same retry on an unusable page DOES reject (read sound, content unusable)."""
    result, _p, port = run([s("combined"), r(1), r(1), FINISH], faults={X: "raise"})
    assert failed(result)[0].startswith(("execution_failed", "refused")) and failed(result)[1] == "deep_fact_extraction_failed"
    assert [u.canonical_url for u in result.rejected_sources] == [X] and port.reads == [X, X]


# 11 / 12 / 13 -----------------------------------------------------------------------------------------------------------------
def test_11_generic_feedback_reaches_the_next_cognition_call_and_no_page_content():
    result, provider, _port = run([s("combined"), r(1), r(1), FINISH])
    assert GENERIC in provider.prompts[3] and "PREVIOUS TURN RESULT" in provider.prompts[3]          # prompt sent after the blocked reread
    for p in provider.prompts:
        assert "no usable sentence" not in p                                                          # rejected page text never reaches cognition
    for banned in ("RFC", "/info/", "Wombat"):
        assert banned not in GENERIC


def test_12_13_rejection_record_holds_identity_only_and_no_valid_source():
    result, _p, _port = run([s("combined"), r(1), FINISH])
    (rej,) = result.rejected_sources
    assert set(vars(rej)) == {"canonical_url", "reason", "search_execution_id", "read_execution_id"}
    assert "no usable sentence" not in repr(rej) and rej.search_execution_id == result.source_facts[0].source_id
    assert result.deep_read_facts == []                                                               # valid_sources unaffected


# 14 + attempt-5 replay -------------------------------------------------------------------------------------------------------
def test_14_attempt_5_replay_one_bad_source_cannot_monopolise_the_read_budget():
    replies = [s("combined"), r(1), r(1), r(1),               # X: read once, then two blocked rereads
               s("ypage"), r(2), s("zpage"), r(3), FINISH]    # research still reaches two valid sources
    result, _p, port = run(replies)
    assert [f.source_url for f in result.deep_read_facts] == [D + "info/y/", D + "info/z/"]
    assert port.reads == [X, D + "info/y/", D + "info/z/"] and result.deep_reads == 3 and result.deep_reads <= BUDGET.max_deep_read_attempts
    assert failed(result) == ["deep_fact_extraction_failed", "source_previously_rejected", "source_previously_rejected"]


def test_budget_still_binds_executed_reads():
    result, _p, _port = run([s("combined"), r(1), s("ypage"), r(2), FINISH])
    assert result.deep_reads == 2 and result.searches == 2


# 15 -- V1.4 / default unchanged ------------------------------------------------------------------------------------------------
def test_15_without_research_mode_nothing_is_rejected_and_v14_codes_are_unchanged():
    port, provider = PortX(), Script([s("combined"), r(1), r(1), FINISH])
    loop, _s = build_agent_loop(provider, port, lambda o: VerificationResult(True, {}), allowed_origins=["https://wombat.example"],
                                search=CONFIG, search_executor=brave(), allow_read=True, sleep=lambda x: None,
                                deep_fact_validator=validator, max_consecutive_failures=20)
    result = loop.run(AgentGoal("g", GOAL_TEXT, max_steps=20))
    assert result.rejected_sources == [] and failed(result) == ["deep_fact_extraction_failed", "deep_read_budget_exhausted"]
    assert port.reads == [X]
