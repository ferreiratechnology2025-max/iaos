"""Pure helpers of the V1.5 preflight / harness (document identity must be decided from the LIVE result, never from an ordinal)."""
import importlib

from scripts.agent_wombat_v15_preflight import GOAL, WANTED, fake_wombat_page, identify_rfc


def test_identify_rfc_requires_url_and_title_to_agree():
    ok = {"title": "RFC 793: Transmission Control Protocol | RFC Editor", "url": "https://www.rfc-editor.org/info/rfc793/"}
    assert identify_rfc(ok) == 793 and identify_rfc({**ok, "url": "https://www.rfc-editor.org/info/rfc793"}) == 793
    assert identify_rfc({**ok, "title": "RFC 9293: Transmission Control Protocol (TCP)"}) is None
    assert identify_rfc({**ok, "url": "https://www.rfc-editor.org/info/rfc9293/"}) is None
    assert identify_rfc({**ok, "url": "https://www.rfc-editor.org/rfc/rfc793.txt"}) is None
    assert identify_rfc({"title": "x", "url": "not a url"}) is None
    assert WANTED == (2616, 793)


def test_goal_carries_topics_only_and_no_deep_fact():
    assert "This RFC was updated" not in GOAL.objective and "RFC 2817" not in GOAL.objective and "RFC 1122" not in GOAL.objective
    assert "RFC 2616" in GOAL.objective and "RFC 793" in GOAL.objective


def test_fake_page_is_parsed_by_the_frozen_preview_logic():
    from application.wombat_verifier import preview_regions, _chrome_parents
    from application.eightb_verifier import parse_aria_tree
    tree = parse_aria_tree(fake_wombat_page("t1", "u1"))
    assert len(preview_regions(tree)) == 1 and sum(1 for _ in _chrome_parents(tree)) == 1


def test_live_harness_imports_without_running():
    importlib.import_module("scripts.agent_wombat_v15_e2e")


def test_attempt_timeline_reconstructs_the_observed_attempt_1_log():
    import os
    from scripts.agent_wombat_v15_e2e import attempt_timeline
    log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence", "v1-5-live-run.log")
    lines = [l.rstrip("\n") for l in open(log, encoding="utf-8", errors="replace")]
    # the log stores the first title across two physical lines; re-join them as the live harness sees one logical line
    joined, buf = [], ""
    for l in lines:
        if buf:
            l = buf + " " + l
            buf = ""
        if l.startswith("STEP 1 SEARCH_EXECUTED") and "url=" not in l:
            buf = l
            continue
        joined.append(l)
    tl = attempt_timeline(joined)
    assert [a["outcome"] for a in tl["searches"]][:2] == ["searched", "no_results"] and tl["searches"][1]["result_count"] == 0
    assert tl["valid_sources"] == 1 and tl["reads"][0]["admitted_source"] == "YES"      # attempt 1 admitted a fact BEFORE budget v2 had a validator
