"""The Wombat harness must abort BEFORE Agnes when the clean-world gate fails, and record the profile when it passes."""
import os

import pytest

import scripts.agent_wombat_e2e as h
from application.computer_runtime import ComputerControlState, ComputerPageObservation
from application.eightb_verifier import SUBTITLE, TITLE

VOL = "openbot-profile-wombat-clean-20261008-141500"
CHROME = '''- generic [ref=f1e1]:
    - generic [ref=f1e2]:
        - button "Edit link" [ref=f1e16]
        - button "Copy link" [ref=f1e17]
        - button "Download" [ref=f1e18]
    - generic [ref=f1e19]:
        - iframe [ref=f1e153]:
%s
'''
EMPTY = "            - generic:\n                - heading [level=1]"
DIRTY = f"            - generic:\n                - heading \"{TITLE}\" [level=1]\n                - paragraph: {SUBTITLE}"
SURFACE = '    - generic [ref=f1e155]:\n        - heading "Start a new site" [level=2]\n'


class FakePort:
    def __init__(self, aria):
        self.aria = aria

    def read_control_state(self, request_id=None):
        return ComputerControlState("bot", False, None, None, False)

    def navigate(self, url):
        return url

    def observe(self):
        return ComputerPageObservation(h.TARGET, "w", "s", 1, (), self.aria)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    state = {"agnes_reached": False, "mounted": VOL}
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(h, "read_env", lambda path, keys: {k: "x" for k in keys})
    monkeypatch.setattr(h, "mounted_profile_volume", lambda container: state["mounted"])
    monkeypatch.setattr(h.time, "sleep", lambda s: None)

    def agnes():
        state["agnes_reached"] = True
        raise RuntimeError("AGNES_REACHED")
    monkeypatch.setattr(h, "configured_agnes_provider", agnes)
    ledger = tmp_path / "used.txt"

    def go(aria, volume=VOL, ledger_text=None):
        monkeypatch.setattr(h, "OpenBotComputerAdapter", lambda *a, **k: FakePort(aria))
        if ledger_text is not None:
            ledger.write_text(ledger_text, encoding="utf-8")
        argv = ["--computer-env", "c", "--agnes-env", "a", "--profile-volume", volume, "--profile-ledger", str(ledger)]
        try:
            return h.main(argv)
        except RuntimeError as exc:
            assert str(exc) == "AGNES_REACHED"
            return "reached_agnes"
    return state, ledger, go


def test_clean_profile_and_pristine_builder_start_the_agent_and_burn_the_profile(rig, capsys):
    state, ledger, go = rig
    assert go(CHROME % EMPTY + SURFACE) == "reached_agnes" and state["agnes_reached"]
    assert ledger.read_text(encoding="utf-8").split() == [VOL]
    assert "CLEAN_WORLD_GATE=PASS" in capsys.readouterr().out


def test_dirty_page_aborts_before_agnes_and_does_not_record_the_profile(rig, capsys):
    state, ledger, go = rig
    assert go(CHROME % DIRTY + SURFACE) == 3 and not state["agnes_reached"] and not ledger.exists()
    out = capsys.readouterr().out
    assert "TARGET_TITLE_PRESENT=YES" in out and "CLEAN_WORLD_GATE=FAIL" in out


def test_reused_profile_aborts_before_agnes(rig, capsys):
    state, ledger, go = rig
    assert go(CHROME % EMPTY + SURFACE, ledger_text=VOL + "\n") == 3 and not state["agnes_reached"]
    assert "PROFILE_REUSED=YES" in capsys.readouterr().out


def test_profile_not_actually_mounted_aborts_before_agnes(rig):
    state, _ledger, go = rig
    state["mounted"] = "openbot-profile-takeover-gate"
    assert go(CHROME % EMPTY + SURFACE) == 3 and not state["agnes_reached"]


def test_missing_start_surface_aborts_before_agnes(rig):
    state, _ledger, go = rig
    assert go(CHROME % EMPTY) == 3 and not state["agnes_reached"]
