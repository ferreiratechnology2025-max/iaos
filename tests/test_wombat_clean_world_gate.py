"""CLEAN_WORLD_GATE: a Wombat gate may only start on a never-used disposable profile showing a pristine builder."""
import pytest

from application.eightb_verifier import SUBTITLE, TITLE
from application.wombat_clean_world import check_clean_world

VOL = "openbot-profile-wombat-clean-20261008-141500"
FRESH_FACTS = {"PREVIEW_CANDIDATES": 1, "TITLE": False, "SUBTITLE": False, "BUTTON": False}
FRESH_ARIA = '- heading "Start a new site" [level=2]\n- button "Generate my site"\n'


def check(**kw):
    args = dict(requested_volume=VOL, mounted_volume=VOL, used_volumes=("openbot-profile-takeover-gate",),
                verification_facts=FRESH_FACTS, aria=FRESH_ARIA, holder="bot", requested=False, title=TITLE, subtitle=SUBTITLE)
    args.update(kw)
    return check_clean_world(**args)


def test_fresh_profile_and_pristine_builder_pass():
    r = check()
    assert r.ok and r.facts["CLEAN_PROFILE"] == "YES" and r.facts["START_NEW_SITE_SURFACE_PRESENT"] == "YES"
    assert r.facts["TARGET_TITLE_PRESENT"] == r.facts["TARGET_SUBTITLE_PRESENT"] == r.facts["TARGET_BUTTON_PRESENT"] == "NO"


@pytest.mark.parametrize("kw,fact", [
    ({"used_volumes": (VOL,)}, "PROFILE_REUSED"),                                      # recreating on a used volume is not a reset
    ({"used_volumes": (" " + VOL + "\n",)}, "PROFILE_REUSED"),
    ({"mounted_volume": "openbot-profile-takeover-gate"}, "PROFILE_MOUNTED_IS_REQUESTED"),
    ({"mounted_volume": None}, "PROFILE_MOUNTED_IS_REQUESTED"),
    ({"requested_volume": "openbot-profile-takeover-gate", "mounted_volume": "openbot-profile-takeover-gate"}, "PROFILE_VOLUME_NAME_VALID"),
])
def test_profile_identity_failures_abort(kw, fact):
    r = check(**kw)
    assert not r.ok and r.facts["CLEAN_PROFILE"] == "NO"
    assert r.facts[fact] == ("YES" if fact == "PROFILE_REUSED" else "NO")


@pytest.mark.parametrize("facts,aria,fact", [
    ({**FRESH_FACTS, "TITLE": True}, FRESH_ARIA, "TARGET_TITLE_PRESENT"),
    ({**FRESH_FACTS, "SUBTITLE": True}, FRESH_ARIA, "TARGET_SUBTITLE_PRESENT"),
    ({**FRESH_FACTS, "BUTTON": True}, FRESH_ARIA, "TARGET_BUTTON_PRESENT"),
    (FRESH_FACTS, FRESH_ARIA + f"- generic: {TITLE}\n", "TARGET_TITLE_PRESENT"),          # brand text is not a heading, still contamination
    (FRESH_FACTS, FRESH_ARIA + f"- paragraph: {SUBTITLE}\n", "TARGET_SUBTITLE_PRESENT"),
])
def test_any_trace_of_the_target_aborts_even_when_the_profile_looks_fresh(facts, aria, fact):
    r = check(verification_facts=facts, aria=aria)
    assert not r.ok and r.facts[fact] == "YES" and r.facts["CLEAN_PROFILE"] == "YES"


def test_missing_start_surface_aborts():
    r = check(aria='- heading "Home"\n')
    assert not r.ok and r.facts["START_NEW_SITE_SURFACE_PRESENT"] == "NO"


@pytest.mark.parametrize("kw", [
    {"verification_facts": {**FRESH_FACTS, "PREVIEW_CANDIDATES": 2}},
    {"verification_facts": {**FRESH_FACTS, "PREVIEW_CANDIDATES": 0}},
    {"holder": "human"}, {"requested": True},
])
def test_unique_preview_and_bot_control_are_required(kw):
    assert not check(**kw).ok


def test_full_artifact_present_reports_the_artifact_fact():
    r = check(verification_facts={"PREVIEW_CANDIDATES": 1, "TITLE": True, "SUBTITLE": True, "BUTTON": True})
    assert not r.ok and r.facts["PREVIEW_TARGET_ARTIFACT_PRESENT"] == "YES"
