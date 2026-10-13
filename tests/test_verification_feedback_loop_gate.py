"""VERIFICATION_FEEDBACK_LOOP_GATE + VERIFICATION_PROGRESS_STAGNATION_GATE (V1.2).

FINISH is a claim: a rejected claim feeds back and the run continues. The FINISH budget is spent only when the
VERIFIER shows no improvement; a successful action is activity, not progress.
"""
import json
import re

from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult, stalled_feedback, verification_feedback
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.wombat_verifier import verify_wombat_artifact
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

GOAL = AgentGoal("g", "do the thing")
FINISH = '{"kind":"finish","claim":"done"}'
WAIT = '{"kind":"wait"}'
TOO_MANY = "TOO_MANY_FAILED_FINISHES_WITHOUT_PROGRESS"


def click(ref):
    return json.dumps({"kind": "act", "action": "browser.click", "args": {"ref": ref}})


B = click("e2")      # satisfies criterion B
C = click("e3")      # satisfies criterion C
NOOP = click("e1")   # successful action, verifiable state unchanged


class World:
    def __init__(self, crit="A", mutate=False, final_only=""):
        self.crit, self.mutate, self.counter, self.final_only = set(crit), mutate, 0, set(final_only)


class Computer:
    def __init__(self, world, fail_acts=False, third_name="Other"):
        self.world, self.observed, self.acted, self.fail_acts, self.third_name = world, [], [], fail_acts, third_name

    def observe(self, step_id):
        self.observed.append(step_id)
        n = len(self.observed)
        crit = self.world.crit | (self.world.final_only if step_id.endswith("-final") else set())
        aria = f"- button [ref=e{n}]: x\n- text: crit={''.join(sorted(crit))} n={self.world.counter}"
        els = (ComputerElement("e1", "button", "One"), ComputerElement("e2", "button", "Two"),
               ComputerElement("e3", "button", self.third_name))
        return ComputerPageObservation("https://t.example/", "T", "sess", n, els, aria)

    def act(self, step_id, decision, observation):
        self.acted.append((decision.action, decision.args.get("ref"), observation.snapshot_id))
        if self.fail_acts:
            return ActionOutcome(False, "stale")
        if self.world.mutate:
            self.world.counter += 1
        ref = decision.args.get("ref")
        if ref == "e2":
            self.world.crit.add("B")
        if ref == "e3":
            self.world.crit.add("C")
        return ActionOutcome(True, "ok")


class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "scripted")


LABEL = {"A": "criterion alpha", "B": "criterion beta", "C": "criterion gamma"}


def verifier(obs):
    have = set(re.search(r"crit=([A-C]*)", obs.aria_snapshot).group(1))
    ok = have == {"A", "B", "C"}
    return VerificationResult(ok, {"X": ok}, () if ok else ("x_missing",),
                              tuple(LABEL[k] for k in sorted(have)), tuple(LABEL[k] for k in "ABC" if k not in have))


def run(replies, world=None, fail_acts=False, **kw):
    world = world or World()
    computer = Computer(world, fail_acts)
    provider = Provider(replies)
    result = AgentLoop(provider, computer, verifier, sleep=lambda s: None, **kw).run(GOAL)
    return result, computer, provider


def feedback_block(prompt):
    return prompt.split("PREVIOUS TURN RESULT:")[1].split("<observation>")[0]


# --- verification feedback loop (carried over) -------------------------------------------------------------------
def test_finish_with_pass_succeeds_immediately():
    result, _c, _p = run([FINISH], world=World("ABC"))
    assert result.status == "VERIFIED" and result.turns == 1 and result.failed_finishes == 0


def test_finish_with_fail_does_not_end_the_run_and_feeds_back():
    result, _c, provider = run([FINISH, B, C, FINISH])
    assert result.status == "VERIFIED" and result.failed_finishes == 1 and result.feedback_delivered == 1
    assert "VERIFICATION FAILED" in provider.prompts[1] and "VERIFICATION FAILED" not in provider.prompts[0]


def test_feedback_contains_only_verifiable_state_no_ui_instruction():
    _r, _c, provider = run([FINISH, B, C, FINISH])
    fb = feedback_block(provider.prompts[1])
    assert "Satisfied:" in fb and "- criterion alpha" in fb and "Missing or incorrect:" in fb and "- criterion beta" in fb
    for forbidden in ("ref=", "e1", "click", "Heading", "destination", "x_missing"):
        assert forbidden not in fb
    obs = ComputerPageObservation("https://websitewombat.app/build", "T", "s", 1, (), "- text: nothing")
    for text in (verification_feedback(verify_wombat_artifact(obs)), stalled_feedback(verify_wombat_artifact(obs))):
        assert "single identifiable artifact preview" in text
        for forbidden in ("Heading block", "Hero", "Call", "Section name", "destination", "Add block", "select", "Link"):
            assert forbidden not in text


def test_verifier_judges_the_fresh_final_observation_not_the_one_before_finish():
    result, _c, _p = run([FINISH], world=World("AB", final_only="C"))
    assert result.status == "VERIFIED"


def test_next_cognition_gets_a_fresh_observation_after_the_rejected_finish():
    _r, computer, _p = run([FINISH, B, C, FINISH])
    assert computer.observed[:3] == ["g-s1-observe", "g-s1-final", "g-s2-observe"]
    assert computer.acted[0][2] == 3          # bound to the observation made AFTER the FINISH (final was snapshot 2)


# --- progress budget ---------------------------------------------------------------------------------------------
def test_1_verifier_fail_continues_and_improvement_is_progress():
    result, _c, _p = run([FINISH, B, FINISH, C, FINISH])
    assert result.status == "VERIFIED" and result.progress_events == 1 and result.failed_finishes == 2


def test_2_successful_action_without_verified_change_does_not_reset_the_budget():
    result, _c, _p = run([FINISH] * 3 + [NOOP, FINISH])
    assert result.status == TOO_MANY and result.post_failure_actions == 1


def test_3_identical_verifier_fail_increments_no_progress():
    result, _c, _p = run([FINISH, FINISH, B, C, FINISH])
    assert any("no_progress=1/3 VERIFIED_PROGRESS=NO" in l for l in result.log)
    assert any("no_progress=2/3 VERIFIED_PROGRESS=NO" in l for l in result.log)


def test_4_improved_criterion_is_progress_and_resets_the_budget():
    result, _c, _p = run([FINISH] * 3 + [B] + [FINISH] * 4 + [C, FINISH])
    assert result.status == "VERIFIED" and result.progress_events == 1
    assert any("no_progress=0/3 VERIFIED_PROGRESS=YES" in l for l in result.log)


def test_5_progress_is_judged_by_best_satisfied_so_far_not_by_oscillation():
    world = World()
    result = AgentLoop(Provider([FINISH, B, FINISH, FINISH, FINISH, FINISH]), Computer(world), verifier,
                       sleep=lambda s: None).run(GOAL)
    assert result.progress_events == 1


def test_7_wait_is_not_progress():
    result, _c, _p = run([FINISH] * 3 + [WAIT, FINISH])
    assert result.status == TOO_MANY


def test_8_invalid_json_is_not_progress():
    result, _c, _p = run([FINISH] * 3 + ["junk", FINISH])
    assert result.status == TOO_MANY


def test_9_three_failed_finishes_without_progress_still_continue():
    result, _c, _p = run([FINISH] * 3 + [B, C, FINISH])
    assert result.status == "VERIFIED" and result.failed_finishes == 3


def test_10_fourth_failed_finish_without_progress_ends_the_run():
    result, _c, provider = run([FINISH] * 4)
    assert result.status == TOO_MANY and result.failed_finishes == 4 and len(provider.prompts) == 4
    assert result.verification is not None and not result.verification.verified


def test_failed_external_action_is_not_progress_either():
    result, _c, _p = run([FINISH] * 3 + [B, FINISH], fail_acts=True)
    assert result.status == TOO_MANY


def test_step_limit_still_applies_after_rejected_finish():
    goal = AgentGoal("g", "do", max_steps=2)
    result = AgentLoop(Provider([FINISH, FINISH]), Computer(World()), verifier, sleep=lambda s: None).run(goal)
    assert result.status == "STEP_LIMIT" and result.failed_finishes == 2


# --- stall feedback ----------------------------------------------------------------------------------------------
def test_stall_feedback_after_two_verifications_without_progress_is_generic():
    _r, _c, provider = run([FINISH, FINISH, FINISH, B, C, FINISH])
    assert "VERIFICATION FAILED" in provider.prompts[1] and "STALLED" not in provider.prompts[1]
    stalled = feedback_block(provider.prompts[2])
    assert "VERIFIED PROGRESS HAS STALLED." in stalled and "Still missing:" in stalled and "- criterion beta" in stalled
    assert "Do not repeat the same strategy unchanged." in stalled
    assert "Inspect the current interface for additional required fields," in stalled
    for forbidden in ("ref=", "click", "destination", "Call", "Link", "Hero", "Heading"):
        assert forbidden not in stalled


# --- action stagnation -------------------------------------------------------------------------------------------
def test_5s_same_action_same_target_same_state_three_times_delivers_stagnation_feedback():
    result, _c, provider = run([NOOP, NOOP, NOOP, B, C, FINISH])
    assert result.action_stagnations == 1 and any("ACTION_STAGNATION=YES repeats=3" in l for l in result.log)
    assert "repeated without changing the observed state" not in provider.prompts[2]   # only after the 3rd repetition
    assert "repeated without changing the observed state" in provider.prompts[3]
    assert "Choose a different strategy." in feedback_block(provider.prompts[3])


def test_stagnation_does_not_block_the_action():
    _r, computer, _p = run([NOOP, NOOP, NOOP, NOOP, B, C, FINISH])
    assert [a[1] for a in computer.acted[:4]] == ["e1"] * 4


def test_6_similar_action_but_changed_state_is_not_stagnation():
    result, _c, provider = run([NOOP, NOOP, NOOP, NOOP, B, C, FINISH], world=World(mutate=True))
    assert result.action_stagnations == 0
    assert all("repeated without changing the observed state" not in p for p in provider.prompts)


def test_stagnation_counts_across_a_rejected_finish_with_stable_state():
    result, _c, _p = run([NOOP, FINISH, NOOP, FINISH, NOOP, B, C, FINISH])
    assert result.action_stagnations == 1


def test_stagnation_key_ignores_unstable_refs():
    result, _c, _p = run([NOOP, NOOP, NOOP, B, C, FINISH])
    assert result.action_stagnations == 1


# --- guard observability -----------------------------------------------------------------------------------------
def test_blocked_target_is_logged_sanitized_from_the_trusted_observation():
    world = World()
    provider = Provider([C])
    result = AgentLoop(provider, Computer(world, third_name='Down"load'), verifier, forbidden_controls=("Down",),
                       sleep=lambda s: None).run(GOAL)
    assert result.status == "GOAL_CONSTRAINT_VIOLATION"
    assert result.blocked_target == "BLOCKED_TARGET role=button name=\"Down'load\" frame=main guard_reason=click_forbidden_control"
    assert any(l.endswith(result.blocked_target) for l in result.log)
