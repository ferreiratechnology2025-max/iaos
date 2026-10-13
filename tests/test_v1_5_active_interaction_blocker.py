"""V1_5_ACTIVE_INTERACTION_BLOCKER_RECOVERY_V1 (synthetic): replay of live attempt 6 turns 7-12.

A runtime `covered_by_dialog` is the trusted fact; the accessibility tree may show no role=dialog at all.
Operational feedback (overlay + action stagnation) must survive decision failures and be consumed only by a valid decision.
"""
import json

from application.agent_loop import (ACTION_STAGNATION_NOTE, BLOCKER_CONTEXT_LINES, ActionOutcome, AgentLoop,
                                    VerificationResult, action_failure_note, build_prompt)
from application.computer_runtime import ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

COVERED = "execution_failed:computer_click_rejected:502:covered_by_dialog"
CLICK = json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": "e2"}})
CLICK_E1 = json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": "e1"}})
FINISH = '{"kind":"finish","claim":"done"}'
OK = lambda o: VerificationResult(True, {"x": 1})
GOAL = AgentGoal("g", "do the thing", max_steps=30)
ARIA_PLAIN = '- generic [ref=e0]:\n  - button "New site" [ref=e2]\n  - textbox "t" [ref=e1]\n'
ARIA_FOREGROUND = ARIA_PLAIN + '  - generic [ref=e9]:\n    - button "Close" [ref=e10]\n    - heading "Start a new site" [ref=e11]\n'
ARIA_ROLE_DIALOG = ARIA_PLAIN + '- dialog [ref=e20]:\n  - button "Pick A" [ref=e21]\n'
COVERED_TEXT = "another dialog or overlay covers the target"
NO_DIALOG_CLAIM = "no dialog is present"
ELEMENTS = (ComputerElement("e1", "textbox", "t"), ComputerElement("e2", "button", "New site"))


class Computer:
    def __init__(self, outcomes, aria_after=ARIA_FOREGROUND):
        self.outcomes, self.aria_after, self.n = list(outcomes), aria_after, 0

    def observe(self, step_id):
        self.n += 1
        aria = ARIA_PLAIN if self.n == 1 else self.aria_after
        return ComputerPageObservation("https://t.example/", "T", "sess", self.n, ELEMENTS, aria)

    def act(self, step_id, decision, observation):
        ok, code = self.outcomes.pop(0) if self.outcomes else (True, "ok")
        return ActionOutcome(ok, code)


class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(replies, outcomes, enabled=True, aria_after=ARIA_FOREGROUND, log=None):
    provider = Provider(replies)
    loop = AgentLoop(provider, Computer(outcomes, aria_after), OK, enable_blocker_recovery=enabled,
                     log=(log.append if log is not None else None))
    return loop.run(GOAL), provider.prompts


OKS = (True, "ok")
BAD = (False, COVERED)
# attempt 6, turns 7..12: success, covered x3 (stagnation at the third), invalid_json, ...
REPLAY_REPLIES = [CLICK, CLICK, CLICK, CLICK, "junk", "junk", CLICK_E1, FINISH]
REPLAY_OUTCOMES = [OKS, BAD, BAD, BAD, OKS]


def head(prompt):
    return prompt.split("\n<observation>\n")[0]


def test_01_replay_attempt_6_blocker_and_both_feedbacks_survive_invalid_json():
    result, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    t5, t6, t7 = prompts[4], prompts[5], prompts[6]      # t5: right after covered #3 (stagnation); t6/t7 follow invalid_json
    for p in (t5, t6, t7):
        assert "RUNTIME_FOREGROUND_BLOCKER=YES" in p and COVERED_TEXT in p and ACTION_STAGNATION_NOTE in p
    assert NO_DIALOG_CLAIM not in t6 and NO_DIALOG_CLAIM not in t7
    assert t5.count(COVERED_TEXT) == 1 and t5.count(ACTION_STAGNATION_NOTE) == 1      # no duplicate between the note and the pending block
    assert result.status not in ("TOO_MANY_ACTION_FAILURES", "TOO_MANY_DECISION_FAILURES")      # attempt 6 died here; now it recovers


def test_02_covered_by_dialog_creates_blocker_and_prompt_does_not_deny_it():
    _, prompts = run([CLICK, CLICK, FINISH], [BAD, OKS])
    p = prompts[1]
    assert "\n".join(BLOCKER_CONTEXT_LINES) in p and "RUNTIME_FOREGROUND_BLOCKER=YES" in p
    assert NO_DIALOG_CLAIM not in p and "aria dialog count is 0" in p       # aria says 0, runtime says blocker: not contradictory


def test_03_role_dialog_plus_blocker_keeps_existing_dialog_context():
    _, prompts = run([CLICK, CLICK, FINISH], [BAD, OKS], aria_after=ARIA_ROLE_DIALOG)
    p = prompts[1]
    assert "a dialog is present" in p and "ref=e21 role=button name='Pick A'" in p
    assert "RUNTIME_FOREGROUND_BLOCKER=YES" in p and NO_DIALOG_CLAIM not in p


def test_04_blocker_persists_across_invalid_json():
    _, prompts = run([CLICK, "junk", "junk", CLICK_E1, FINISH], [BAD, OKS])
    assert "RUNTIME_FOREGROUND_BLOCKER=YES" in prompts[2] and "RUNTIME_FOREGROUND_BLOCKER=YES" in prompts[3]


def test_05_pending_overlay_feedback_survives_invalid_json():
    _, prompts = run([CLICK, "junk", "junk", CLICK_E1, FINISH], [BAD, OKS])
    assert "PENDING OPERATIONAL FEEDBACK:" in prompts[2] and COVERED_TEXT in prompts[2] and COVERED_TEXT in prompts[3]


def test_06_pending_stagnation_feedback_survives_invalid_json():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    assert ACTION_STAGNATION_NOTE in prompts[5] and ACTION_STAGNATION_NOTE in prompts[6]


def test_07_next_valid_decision_receives_both_pending_items():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    p = prompts[6]      # the prompt that produced the first valid decision after the invalid replies
    assert COVERED_TEXT in p and ACTION_STAGNATION_NOTE in p and "PENDING OPERATIONAL FEEDBACK:" in p


def test_08_valid_decision_consumes_pending_no_endless_replay():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    last = prompts[7]       # after click on e1 succeeded
    assert "PENDING OPERATIONAL FEEDBACK:" not in last and ACTION_STAGNATION_NOTE not in last and COVERED_TEXT not in last


def test_09_successful_browser_action_and_fresh_observation_clear_the_blocker():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    last = prompts[7]
    assert "RUNTIME_FOREGROUND_BLOCKER" not in last and "A foreground interaction blocker is active" not in last


def test_10_another_covered_by_dialog_reactivates_the_blocker():
    _, prompts = run([CLICK, CLICK_E1, CLICK, FINISH], [BAD, OKS, BAD])
    assert "RUNTIME_FOREGROUND_BLOCKER=YES" in prompts[1]
    assert "RUNTIME_FOREGROUND_BLOCKER" not in prompts[2]      # cleared by the successful click
    assert "RUNTIME_FOREGROUND_BLOCKER=YES" in prompts[3]      # re-activated by the next covered failure


def test_11_repeated_feedback_is_deduplicated_and_prompt_growth_is_bounded():
    _, prompts = run([CLICK, CLICK, CLICK, CLICK, "junk", "junk", "junk", "junk", CLICK_E1, FINISH], [OKS, BAD, BAD, BAD, OKS])
    assert prompts[7].count(ACTION_STAGNATION_NOTE) == 1 and prompts[7].count(COVERED_TEXT) == 1
    assert prompts[7].count("RUNTIME_FOREGROUND_BLOCKER=YES") == 1
    assert abs(len(prompts[7]) - len(prompts[6])) < 200


def test_12_no_raw_dom_selector_or_coordinates_enter_the_blocker_context():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES)
    h = head(prompts[5])
    assert "ref=" not in h and "e2" not in h and "New site" not in h and "x=" not in h and "px" not in h


def test_13_runs_without_covered_by_dialog_have_identical_prompts_enabled_or_not():
    replies = [CLICK, "junk", CLICK_E1, FINISH]
    outs = [(False, "execution_failed:computer_click_timeout:timeout"), OKS]
    _, on = run(replies, outs, enabled=True)
    _, off = run(replies, outs, enabled=False)
    assert on == off


def test_14_default_behavior_unchanged_when_the_flag_is_off():
    _, prompts = run(REPLAY_REPLIES, REPLAY_OUTCOMES, enabled=False)
    joined = "\n".join(prompts)
    assert "RUNTIME_FOREGROUND_BLOCKER" not in joined and "PENDING OPERATIONAL FEEDBACK" not in joined
    assert NO_DIALOG_CLAIM in prompts[5] or "a dialog is present" in prompts[5]
    assert COVERED_TEXT not in prompts[5]      # legacy behavior: the invalid_json reply replaced the useful feedback


def test_15_build_prompt_default_arguments_are_byte_identical_to_before():
    obs = ComputerPageObservation("https://t.example/", "T", "s", 1, ELEMENTS, ARIA_PLAIN)
    base = build_prompt(GOAL, obs, 2, "n", (), "covered_by_dialog")
    assert "INTERACTION CONTEXT NOTE:" in base and "RUNTIME_FOREGROUND_BLOCKER" not in base and NO_DIALOG_CLAIM in base
    assert "PENDING OPERATIONAL FEEDBACK" not in base and "A foreground interaction blocker" not in base


def test_16_wiring_harness_enables_blocker_recovery_only_in_v15():
    import os
    from scripts.agent_wombat_v15_e2e import blocker_recovery_options
    assert blocker_recovery_options() == {"enable_blocker_recovery": True}
    root = os.path.join(os.path.dirname(__file__), "..", "scripts")
    src = open(os.path.join(root, "agent_wombat_v15_e2e.py"), encoding="utf-8").read()
    assert "**blocker_recovery_options()" in src and src.count("build_agent_loop(") == 1
    for name in os.listdir(root):
        if name.endswith(".py") and name != "agent_wombat_v15_e2e.py":
            assert "enable_blocker_recovery" not in open(os.path.join(root, name), encoding="utf-8").read(), name
