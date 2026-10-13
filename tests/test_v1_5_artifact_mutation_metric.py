"""V1_5_ARTIFACT_MUTATION_METRIC_FIX: observational only. `+ Page` (and every structural/content edit) is an artifact mutation;
Close / Start blank / New site / page selection / navigation are not. Replays the logged live attempts without rewriting them."""
import json
import os

import pytest

from scripts.agent_wombat_v15_e2e import first_artifact_mutation

EV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "evidence")


def click(step, name, ref="e1"):
    return [f"STEP {step} Agnes decided browser.click args={{'ref': '{ref}'}}", f'STEP {step} TARGET ref={ref} role=button name="{name}" frame=f3 inside_dialog=NO']


def typ(step):
    return [f"STEP {step} Agnes decided browser.type args={{'ref': 'e2', 'text': 'x'}}", f'STEP {step} TARGET ref=e2 role=textbox name="Some text…" frame=f3 inside_dialog=NO']


@pytest.mark.parametrize("name", ["Close", "➕ Start blank", "Start blank", "New site", "Home", "Page 2", "Page 14", "My sites", "Start building →"])
def test_dismissal_and_navigation_are_not_artifact_mutations(name):
    assert first_artifact_mutation(click(3, name)) is None


@pytest.mark.parametrize("name", ["+ Page", "+ Add section", "+ Add block", "+ Add button", "Delete section", "Something unknown", "Hero"])
def test_structural_and_unknown_clicks_are_artifact_mutations(name):
    assert first_artifact_mutation(click(3, name)) == 0


def test_type_and_select_are_mutations_key_is_not():
    assert first_artifact_mutation(typ(4)) == 0
    sel = ["STEP 4 Agnes decided browser.select_option args={'ref': 'e3', 'option': 'Hero'}"]
    assert first_artifact_mutation(sel) == 0
    assert first_artifact_mutation(["STEP 4 Agnes decided browser.key args={'key': 'Enter'}"]) is None
    assert first_artifact_mutation(["STEP 4 Agnes decided WAIT (1/20)", "STEP 5 Agnes decided READ_SOURCE source=1"]) is None


def test_first_mutation_is_the_first_not_the_last():
    lines = click(3, "Close") + click(6, "➕ Start blank") + click(7, "+ Page") + typ(9)
    assert first_artifact_mutation(lines) == 4 and lines[4].startswith("STEP 7 ")


def _raw(attempt):
    path = os.path.join(EV, f"v1-5-live-attempt{attempt}-raw.json")
    if not os.path.exists(path):
        pytest.skip("evidence file not present")
    return json.load(open(path, encoding="utf-8"))["log"]


def _before(log):
    reads = [i for i, l in enumerate(log) if "DEEP_READ_EXECUTED" in l]
    art = first_artifact_mutation(log)
    return len(reads) == 2 and (art is None or reads[1] < art), art


def test_attempt_4_replay_page_before_second_read_is_ordering_fail():
    log = _raw(4)
    ok, art = _before(log)
    assert log[art].startswith("STEP 7 Agnes decided browser.click") and ok is False        # `+ Page` at turn 7, second read at turn 8


def test_attempt_2_replay_first_edit_was_after_both_reads():
    log = _raw(2)
    ok, art = _before(log)
    assert log[art].startswith("STEP 7 Agnes decided browser.click") and ok is True         # `+ Add section` at turn 7, second read at turn 6


def test_click_without_a_resolvable_target_name_counts_as_a_mutation():
    assert first_artifact_mutation(["STEP 3 Agnes decided browser.click args={'ref': 'e1'}"]) == 0
