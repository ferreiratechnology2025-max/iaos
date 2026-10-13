"""V1_3_VERIFIER_GATE: cross-tool verifier = frozen Wombat structure + exact source correspondence (preview only)."""
import hashlib
from pathlib import Path
from textwrap import indent
from types import SimpleNamespace

import pytest

from application.computer_runtime import ComputerPageObservation
from application.source_fact_checks import check_title_url_provenance
from application.wombat_v13_verifier import correlated_expected, make_v13_verifier
from application.wombat_verifier import verify_wombat_artifact

TITLE = "RFC 9110: HTTP Semantics | RFC Editor"
URL = "https://www.rfc-editor.org/info/rfc9110/"
DIGEST = "a" * 64
LEDGER = [{"execution_id": "ex-1", "title": TITLE, "url": URL, "result_digest": DIGEST}]
EVENTS = [{"kind": "EXECUTION_RESULT", "action": "web.search", "ok": True, "execution_id": "ex-1"}]
EFFECTS = [SimpleNamespace(opcode="web.search", result_digest=DIGEST)]


def preview(title=TITLE, url=URL):
    lines = ["- generic [ref=p1]:"]
    if title is not None:
        lines.append(f'    - heading "{title}" [level=1] [ref=p2]')
    if url is not None:
        lines.append(f'    - link "{url}" [ref=p3]')
    return "\n".join(lines)


def page(prev, prompt="", second_preview=None, chrome_iframe=None, chrome=True):
    buttons = ('        - button "Edit link" [ref=f1e16]\n        - button "Copy link" [ref=f1e17]\n'
               '        - button "Download" [ref=f1e18]\n') if chrome else ""
    inchrome = ("        - iframe [ref=f1e300]:\n" + indent(chrome_iframe, " " * 12) + "\n") if chrome_iframe else ""
    second = ("        - iframe [ref=f1e200]:\n" + indent(second_preview, " " * 12) + "\n") if second_preview else ""
    return f'''- generic [active] [ref=f1e1]:
    - generic [ref=f1e2]:
        - button "My sites" [ref=f1e5]
{buttons}{inchrome}    - generic [ref=f1e19]:
        - generic [ref=f1e20]:
            - textbox "Manifest JSON" [ref=f1e30]: "{prompt}"
        - iframe [ref=f1e153]:
{indent(prev, " " * 12)}
{second}    - generic [ref=f1e155]:
        - textbox "e.g. A painting company in Philadelphia" [ref=f1e185]: "{prompt}"
'''


def obs(aria):
    return ComputerPageObservation("https://websitewombat.app/build", "w", "s", 1, (), aria)


def verifier(ledger=LEDGER, events=EVENTS, effects=EFFECTS):
    return make_v13_verifier(lambda: ledger, lambda: events, lambda: effects)


def verdict(aria, **kw):
    return verifier(**kw)(obs(aria))


# 1-3 ----------------------------------------------------------------------------------------------------------------
def test_1_title_and_url_exact_in_preview_pass():
    r = verdict(page(preview()))
    assert r.verified and r.facts["SEARCH_CORRELATED"] and r.facts["PREVIEW_CANDIDATES"] == 1


def test_2_only_title_fails():
    r = verdict(page(preview(url=None)))
    assert not r.verified and r.facts["SEARCH_RESULT_TITLE_EXACT"] and not r.facts["SEARCH_RESULT_URL_EXACT"]


def test_3_only_url_fails():
    r = verdict(page(preview(title=None)))
    assert not r.verified and r.facts["SEARCH_RESULT_URL_EXACT"] and not r.facts["SEARCH_RESULT_TITLE_EXACT"]


def combined(*texts):
    return "- generic [ref=p1]:\n" + "\n".join(f'    - paragraph [ref=q{i}]: "{t}"' for i, t in enumerate(texts))


# semantic alignment with the goal (attempt 2): the values must OCCUR in the preview; they need not be separate nodes
def test_a1_title_and_url_in_separate_nodes_pass():
    assert verdict(page(preview())).verified


@pytest.mark.parametrize("text", [f"{TITLE} {URL}", f"{TITLE}   {URL}", f"{URL} - {TITLE}", f"See {TITLE} at {URL}."])
def test_a2_title_and_url_in_the_same_paragraph_pass(text):
    r = verdict(page(combined(text)))
    assert r.verified and r.facts["SEARCH_RESULT_TITLE_EXACT"] and r.facts["SEARCH_RESULT_URL_EXACT"]


def test_a3_exact_title_with_altered_url_fails():
    assert not verdict(page(combined(f"{TITLE} https://www.rfc-editor.org/info/rfc9111/"))).verified
    assert not verdict(page(preview(url="https://www.rfc-editor.org/rfc/rfc9110.html"))).verified


def test_a4_exact_url_with_altered_title_fails():
    assert not verdict(page(combined(f"RFC 9111: HTTP Caching {URL}"))).verified
    assert not verdict(page(preview(title="RFC 9110: HTTP Semantics"))).verified


def test_a5_only_a_title_prefix_fails():
    assert not verdict(page(combined(f"{TITLE[:20]} {URL}"))).verified


def test_a6_only_a_url_prefix_fails():
    assert not verdict(page(combined(f"{TITLE} {URL[:30]}"))).verified


def test_a7_a_value_cannot_be_assembled_from_fragments_across_nodes():
    assert not verdict(page(preview(title=None, url=None) + f'\n    - paragraph [ref=r1]: "{TITLE[:15]}"\n    - paragraph [ref=r2]: "{TITLE[15:]}"'
                            f'\n    - paragraph [ref=r3]: "{URL}"')).verified


def test_a9_duplicates_in_the_preview_pass():
    assert verdict(page(combined(TITLE, URL, f"{TITLE} {URL}", TITLE))).verified


# 4-5 ----------------------------------------------------------------------------------------------------------------
def test_4_values_only_in_prompt_or_source_context_fail():
    r = verdict(page(preview(title=None, url=None), prompt=f"{TITLE} {URL}"))
    assert not r.verified and not r.facts["SEARCH_RESULT_TITLE_EXACT"] and not r.facts["SEARCH_RESULT_URL_EXACT"]
    sd = page(preview(title=None, url=None)) + f'    - paragraph [ref=s1]: source 1 {TITLE}\n    - link "{URL}" [ref=s2]\n'
    r = verdict(sd)
    assert not r.verified and r.facts["OUTSIDE_PREVIEW_MATCHES_IGNORED"] is True


def test_5_values_in_a_frame_other_than_the_preview_fail():
    other = preview()
    r = verdict(page(preview(title=None, url=None), chrome_iframe=other))      # iframe inside the chrome node
    assert not r.verified and r.facts["PREVIEW_CANDIDATES"] == 1
    r = verdict(page(preview(title=None, url=None), second_preview=other))     # second preview-like iframe: ambiguous
    assert not r.verified and r.facts["PREVIEW_CANDIDATES"] == 2 and "PREVIEW_REGION_AMBIGUOUS" in r.reasons
    assert not verdict(page(preview(), chrome=False)).verified                 # no identifiable builder chrome


# 6 + 9 --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ledger,events,effects", [
    ([], EVENTS, EFFECTS),                                                                        # never searched
    (LEDGER, [], EFFECTS),                                                                        # no execution evidence
    (LEDGER, [{**EVENTS[0], "ok": False}], EFFECTS),                                              # failed execution
    (LEDGER, [{**EVENTS[0], "execution_id": "other"}], EFFECTS),                                  # evidence for another execution
    (LEDGER, EVENTS, []),                                                                         # no executor effect
    (LEDGER, EVENTS, [SimpleNamespace(opcode="web.search", result_digest="b" * 64)]),             # digest mismatch
    (LEDGER, EVENTS, [SimpleNamespace(opcode="browser.type", result_digest=DIGEST)]),             # wrong opcode
])
def test_6_expected_value_not_correlated_to_a_search_execution_fails(ledger, events, effects):
    r = verdict(page(preview()), ledger=ledger, events=events, effects=effects)
    assert not r.verified and r.facts["SEARCH_CORRELATED"] is False and "no_correlated_search_execution" in r.reasons


def test_9_a_page_showing_a_different_search_result_fails():
    r = verdict(page(preview("Some Other Page", "https://www.rfc-editor.org/info/rfc9999/")))
    assert not r.verified
    second = {"execution_id": "ex-2", "title": "Second", "url": "https://www.rfc-editor.org/info/rfc2/", "result_digest": "c" * 64}
    events = EVENTS + [{**EVENTS[0], "execution_id": "ex-2"}]
    effects = EFFECTS + [SimpleNamespace(opcode="web.search", result_digest="c" * 64)]
    assert correlated_expected(LEDGER + [second], events, effects)["title"] == "Second"          # latest correlated fact wins
    assert not verdict(page(preview()), ledger=LEDGER + [second], events=events, effects=effects).verified
    assert verdict(page(preview("Second", second["url"])), ledger=LEDGER + [second], events=events, effects=effects).verified


# 7-8 ----------------------------------------------------------------------------------------------------------------
def test_7_8_preflight_aborts_when_the_fact_pre_exists():
    ok = check_title_url_provenance(TITLE, URL, "Search the topic and build a page.", "prompt", "- generic: start", LEDGER, EVENTS, EFFECTS)
    assert ok["PREFLIGHT"] == "OK" and all(v == "YES" for k, v in ok.items() if k != "PREFLIGHT")
    for kwargs, key in (({"goal_text": f"Show {TITLE}"}, "EXPECTED_TITLE_NOT_IN_GOAL"),
                        ({"goal_text": f"Show {URL}"}, "EXPECTED_URL_NOT_IN_GOAL"),
                        ({"first_prompt": f"x {URL}"}, "EXPECTED_URL_NOT_IN_INITIAL_PROMPT"),
                        ({"first_observation_text": f'heading "{TITLE}"'}, "EXPECTED_TITLE_NOT_IN_INITIAL_OBSERVATION"),
                        ({"first_observation_text": URL}, "EXPECTED_URL_NOT_IN_INITIAL_OBSERVATION")):
        args = {"goal_text": "g", "first_prompt": "p", "first_observation_text": "o", **kwargs}
        r = check_title_url_provenance(TITLE, URL, args["goal_text"], args["first_prompt"], args["first_observation_text"], LEDGER, EVENTS, EFFECTS)
        assert r["PREFLIGHT"] == "ABORT" and r[key] == "NO"


def test_7b_expected_value_must_come_from_the_search_effect():
    r = check_title_url_provenance("Invented title", URL, "g", "p", "o", LEDGER, EVENTS, EFFECTS)
    assert r["EXPECTED_TITLE_FROM_GOVERNED_SEARCH_EFFECT"] == "NO" and r["EXPECTED_URL_FROM_GOVERNED_SEARCH_EFFECT"] == "YES"
    r = check_title_url_provenance(TITLE, URL, "g", "p", "o", LEDGER, [], EFFECTS)
    assert r["EXPECTED_TITLE_FROM_GOVERNED_SEARCH_EFFECT"] == "NO"


# 10 -----------------------------------------------------------------------------------------------------------------
V12_WOMBAT_VERIFIER_SHA = "dec906f3acdedbe0659c1f8eda87b76edb0791718930b6a7b531b80fc959ad27"


def test_10_v12_verifier_is_byte_equivalent_to_the_frozen_tag():
    src = (Path(__file__).resolve().parents[1] / "application" / "wombat_verifier.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(src).hexdigest() == V12_WOMBAT_VERIFIER_SHA
    # and it still judges the old criteria (not the V1.3 ones)
    assert not verify_wombat_artifact(obs(page(preview()))).verified
