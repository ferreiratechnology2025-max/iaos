"""V1_4 verifier gate: frozen V1.3 title/url correspondence + deep fact derived from a correlated governed deep read (preview only)."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from textwrap import indent
from types import SimpleNamespace

import pytest

from application.computer_runtime import ComputerPageObservation
from application.source_facts import DeepReadFact
from application.wombat_v14_verifier import deep_correlation, deep_preflight, extract_deep_fact, make_v14_verifier
from governance.bound_read import READ_SEARCH_SOURCE_ACTION, SearchSourceBinding

TITLE = "RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1 | RFC Editor"
URL = "https://www.rfc-editor.org/info/rfc2616/"
DESC = "Network Working Group R. Fielding ... of this Memo This document specifies an Internet standards track protocol."
SDIG, RDIG = "a" * 64, "b" * 64
DEEP = "This RFC was updated, see RFC 2817, RFC 5785, RFC 6266, RFC 6585."
LIVE_TEXT = ("Skip to content RFC 2616 Info This RFC is now obsolete, see RFC 7230, RFC 7231.This RFC was updated, see "
             "RFC 2817, RFC 5785, RFC 6266, RFC 6585.Network Working Group R. Fielding")
LEDGER = [{"execution_id": "x-s1", "title": TITLE, "url": URL, "result_digest": SDIG}]
BINDINGS = [SearchSourceBinding("x-s1", 0, URL, SDIG)]
FACTS = [DeepReadFact("search_bound_deep_read", "x-r1", "x-s1", URL, LIVE_TEXT, True, RDIG)]
SEARCH_EFFECTS = [SimpleNamespace(opcode="web.search", result_digest=SDIG)]
READ_EFFECTS = [SimpleNamespace(opcode=READ_SEARCH_SOURCE_ACTION, result_digest=RDIG)]


def events(sid="x-s1", rid="x-r1"):
    return [{"kind": "EXECUTION_RESULT", "action": "web.search", "ok": True, "execution_id": sid},
            {"kind": "AUTHORIZATION", "action": READ_SEARCH_SOURCE_ACTION,
             "args": [["binding", json.dumps({"search_execution_id": sid, "result_index": 0, "effect_digest": SDIG})]]},
            {"kind": "EXECUTION_RESULT", "action": READ_SEARCH_SOURCE_ACTION, "ok": True, "execution_id": rid,
             "result_authority": "DATA_ONLY", "result_provenance": "COMPUTER_OBSERVATION"}]


def preview(*texts):
    return "- generic [ref=p1]:\n" + "\n".join(f'    - paragraph [ref=q{i}]: "{t}"' for i, t in enumerate(texts))


def page(prev, prompt="", other_frame=None, extra=""):
    chrome_iframe = ("        - iframe [ref=f1e300]:\n" + indent(other_frame, " " * 12) + "\n") if other_frame else ""
    return f'''- generic [active] [ref=f1e1]:
    - generic [ref=f1e2]:
        - button "My sites" [ref=f1e5]
        - button "Edit link" [ref=f1e16]
        - button "Copy link" [ref=f1e17]
        - button "Download" [ref=f1e18]
{chrome_iframe}    - generic [ref=f1e19]:
        - generic [ref=f1e20]:
            - textbox "Manifest JSON" [ref=f1e30]: "{prompt}"
        - iframe [ref=f1e153]:
{indent(prev, " " * 12)}
    - generic [ref=f1e155]:
        - textbox "e.g. A painting company in Philadelphia" [ref=f1e185]: "{prompt}"
{extra}'''


def obs(aria):
    return ComputerPageObservation("https://websitewombat.app/build", "w", "s", 1, (), aria)


def verdict(aria, ledger=LEDGER, evs=None, se=SEARCH_EFFECTS, bindings=BINDINGS, facts=FACTS, re_=READ_EFFECTS):
    evs = events() if evs is None else evs
    return make_v14_verifier(lambda: ledger, lambda: evs, lambda: se, lambda: bindings, lambda: facts, lambda: re_)(obs(aria))


FULL = preview(TITLE, URL, DEEP)


def test_1_title_url_and_deep_fact_in_preview_pass():
    r = verdict(page(FULL))
    assert r.verified and r.facts["DEEP_READ_CORRELATED"] and r.facts["DEEP_FACT_EXACT"]
    assert verdict(page(preview(f"{TITLE} {URL} {DEEP}"))).verified            # same paragraph is irrelevant
    assert extract_deep_fact(LIVE_TEXT) == DEEP                                  # NBSP-normalised sentence, from the live-shaped text


def test_2_title_and_url_without_deep_fact_fail():
    r = verdict(page(preview(TITLE, URL)))
    assert not r.verified and r.facts["SEARCH_RESULT_TITLE_EXACT"] and not r.facts["DEEP_FACT_EXACT"] and "deep_fact_exact_missing" in r.reasons


@pytest.mark.parametrize("alt", ["This RFC was updated, see RFC 2817, RFC 5785, RFC 6266.", "This RFC was updated, see RFC 2817, RFC 5785, RFC 6266, RFC 6586.",
                                 "This RFC was updated, see RFC 2817, RFC 5785, RFC 6266, RFC 6585"])
def test_3_altered_deep_fact_fails(alt):
    assert not verdict(page(preview(TITLE, URL, alt))).verified


def test_4_deep_fact_only_in_source_data_or_prompt_fails():
    sd = page(preview(TITLE, URL), extra=f'    - paragraph [ref=s1]: deep read of source 1 | {DEEP}\n')
    r = verdict(sd)
    assert not r.verified and r.facts["OUTSIDE_PREVIEW_MATCHES_IGNORED"] is True
    assert not verdict(page(preview(TITLE, URL), prompt=DEEP)).verified


def test_5_deep_fact_only_in_editor_or_other_frame_fails():
    assert not verdict(page(preview(TITLE, URL), other_frame=preview(DEEP))).verified
    assert not verdict(page(preview(TITLE, URL), prompt=f"{DEEP}")).verified


GOAL = "Pesquise sobre o tema, leia a fonte e crie uma pagina com titulo, URL e a frase que informa quais RFCs atualizaram o documento."
ITEM = {"title": TITLE, "url": URL, "description": DESC}
BASE = dict(result_count=1, search_url=URL, bound_url=URL, final_url=URL, challenge=None, takeover_requests=0, truncated=True,
            visible_text=LIVE_TEXT, read_admitted=True, fact_created=True, search_item=ITEM, goal_text=GOAL, first_prompt="prompt",
            first_observation_text="- generic: start")


def test_6_deep_fact_in_search_snippet_aborts_preflight():
    ok = deep_preflight(**BASE)
    assert ok["PREFLIGHT"] == "OK" and ok["DEEP_FACT"] == DEEP and ok["DEEP_FACT_IN_SEARCH_SNIPPET"] == "NO"
    r = deep_preflight(**{**BASE, "search_item": {**ITEM, "description": DESC + " " + DEEP}})
    assert r["PREFLIGHT"] == "ABORT" and r["DEEP_FACT_IN_SEARCH_SNIPPET"] == "YES" and "DEEP_FACT_NOT_IN_SEARCH_DESCRIPTION" in r["PREFLIGHT_FAILING"]


def test_7_deep_fact_not_correlated_to_the_read_execution_fails():
    for kw in (dict(re_=[]), dict(re_=[SimpleNamespace(opcode=READ_SEARCH_SOURCE_ACTION, result_digest="c" * 64)]),
               dict(re_=[SimpleNamespace(opcode="web.search", result_digest=RDIG)]), dict(evs=events()[:2]),
               dict(evs=events(rid="x-other")), dict(facts=[])):
        r = verdict(page(FULL), **kw)
        assert not r.verified and r.facts["DEEP_READ_CORRELATED"] is False
    bad_prov = events()
    bad_prov[2] = {**bad_prov[2], "result_provenance": "TOOL_RESULT"}
    assert not verdict(page(FULL), evs=bad_prov).verified


def test_8_read_correlated_to_another_search_binding_fails():
    assert not verdict(page(FULL), bindings=[]).verified
    assert not verdict(page(FULL), bindings=[SearchSourceBinding("x-other", 0, URL, SDIG)]).verified
    assert not verdict(page(FULL), bindings=[SearchSourceBinding("x-s1", 0, URL, "f" * 64)]).verified
    assert not verdict(page(FULL), facts=[replace(FACTS[0], search_execution_id="x-other")]).verified
    assert not verdict(page(FULL), evs=events(sid="x-other")).verified          # read authorization bound to a different search


def test_9_final_read_url_different_from_bound_url_fails():
    assert not verdict(page(FULL), facts=[replace(FACTS[0], source_url="https://www.rfc-editor.org/rfc/rfc2616.html")]).verified
    r = deep_preflight(**{**BASE, "final_url": "https://www.rfc-editor.org/rfc/rfc2616.html"})
    assert r["PREFLIGHT"] == "ABORT" and "FINAL_CANONICAL_URL_EQUALS_BOUND" in r["PREFLIGHT_FAILING"]


def test_10_challenge_or_fact_truncated_away_aborts_preflight():
    assert deep_preflight(**{**BASE, "challenge": "cloudflare"})["PREFLIGHT"] == "ABORT"
    assert deep_preflight(**{**BASE, "takeover_requests": 1})["PREFLIGHT"] == "ABORT"
    r = deep_preflight(**{**BASE, "visible_text": "x" * 6000, "truncated": True})
    assert r["PREFLIGHT"] == "ABORT" and r["DEEP_FACT_TRUNCATED_AWAY"] == "YES" and r["DEEP_FACT_EXTRACTION"] == "FAIL"
    assert deep_preflight(**{**BASE, "result_count": 2})["PREFLIGHT"] == "ABORT"
    assert deep_preflight(**{**BASE, "read_admitted": False})["PREFLIGHT"] == "ABORT"
    ambiguous = LIVE_TEXT + " This RFC was updated, see RFC 9999."
    assert deep_preflight(**{**BASE, "visible_text": ambiguous})["DEEP_FACT_EXTRACTION"] == "FAIL"


def test_11_expected_fact_in_goal_prompt_or_observation_aborts_preflight():
    for key, name in (("goal_text", "GOAL"), ("first_prompt", "INITIAL_PROMPT"), ("first_observation_text", "INITIAL_OBSERVATION")):
        r = deep_preflight(**{**BASE, key: f"x {DEEP}"})
        assert r["PREFLIGHT"] == "ABORT" and r[f"DEEP_FACT_NOT_IN_{name}"] == "NO"


def test_11b_expected_is_derived_from_the_live_read_never_a_constant():
    other = replace(FACTS[0], visible_text_excerpt=LIVE_TEXT.replace("2817", "1111"))
    assert not verdict(page(FULL), facts=[other]).verified                      # page shows the old numbers: no longer the live fact
    assert verdict(page(preview(TITLE, URL, DEEP.replace("2817", "1111"))), facts=[other]).verified
    assert deep_correlation(LEDGER, events(), SEARCH_EFFECTS, BINDINGS, [replace(FACTS[0], visible_text_excerpt="nothing")], READ_EFFECTS)[1] \
        == "deep_fact_extraction_failed"


V13_VERIFIER_SHA = "97f1179ac1deee95e3c083c1c85aed5539063fea1bdef390ac952dd95941a63f"
V12_WOMBAT_VERIFIER_SHA = "dec906f3acdedbe0659c1f8eda87b76edb0791718930b6a7b531b80fc959ad27"


def test_12_v13_and_v12_verifiers_remain_frozen():
    root = Path(__file__).resolve().parents[1] / "application"
    for name, sha in (("wombat_v13_verifier.py", V13_VERIFIER_SHA), ("wombat_verifier.py", V12_WOMBAT_VERIFIER_SHA)):
        assert hashlib.sha256((root / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == sha
