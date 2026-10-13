"""V1 automated gate: the loop must stay SUSPENDED while a person holds the wheel."""
import json

import pytest

from application.agent_loop import ActionOutcome, AgentLoop, HumanRequestOutcome, VerificationResult
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

GOAL = AgentGoal("g", "reach a signed-in page", max_steps=12)
REQUEST = '{"kind":"request_human","reason_code":"authentication_required"}'
FINISH = '{"kind":"finish","claim":"signed in"}'
CLICK = '{"kind":"act","action":"browser.click","args":{"ref":"e1"}}'
OK = lambda o: VerificationResult(True, {})


class World:
    """Stateful stand-in for OpenBot control semantics (release => snapshot owed)."""

    def __init__(self, polls=("waiting", "taken", "completed"), stuck_resume=False, stale_after_handback=0):
        self.snapshot, self.holder, self.requested, self.status = 0, "bot", False, None
        self.resume, self.script = False, list(polls)
        self.stuck_resume, self.stale_after_handback = stuck_resume, stale_after_handback
        self.request_calls, self.cognition_log, self.act_log, self.observe_log, self.reads = [], [], [], [], 0

    # --- computer
    def observe(self, step_id):
        if self.holder == "bot" and not self.requested and self.status != "completed_pending":
            pass
        if self.stale_after_handback and self.resume_done_once and self.stale_after_handback > 0:
            self.stale_after_handback -= 1
            snap = self.snapshot  # same id again: not newer than what was seen
        else:
            self.snapshot += 1
            snap = self.snapshot
        if self.holder == "bot" and not self.requested and not self.stuck_resume:
            self.resume = False
        self.observe_log.append((step_id, snap, self.holder, self.requested))
        return ComputerPageObservation(
            "https://t.example/", f"page-{snap}", "sess", snap,
            (ComputerElement("e1", "button", "Go"),), "- text: login", "cloudflare" if snap == 1 else None,
        )

    resume_done_once = False

    def request_human(self, step_id, reason_code, tool_call_id):
        self.request_calls.append((reason_code, tool_call_id))
        self.requested, self.status = True, "waiting"
        return HumanRequestOutcome(True, "req-1", "requested")

    def act(self, step_id, decision, observation):
        self.act_log.append((self.holder, self.status, observation.snapshot_id))
        return ActionOutcome(True, "executed")

    # --- control telemetry
    def read_control_state(self, request_id=None):
        self.reads += 1
        if request_id is not None and self.script:
            step = self.script.pop(0)
            self.status = step
            if step == "taken":
                self.holder = "human"
            elif step in ("completed", "cancelled", "expired", "interrupted"):
                self.holder, self.requested, self.resume = "bot", False, True
                if step == "completed":
                    World.resume_done_once = True
        return ComputerControlState(self.holder, self.requested, request_id, self.status, self.resume)


class Provider:
    provider_id = "scripted"

    def __init__(self, world, replies):
        self.world, self.replies, self.prompts = world, list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        self.world.cognition_log.append((self.world.holder, self.world.status, self.world.resume))
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(world, replies, **kw):
    World.resume_done_once = False
    provider = Provider(world, replies)
    loop = AgentLoop(provider, world, OK, control=world, sleep=lambda s: None, **kw)
    return loop.run(GOAL), provider


def test_1_request_human_creates_exactly_one_request():
    world = World()
    result, _ = run(world, [REQUEST, FINISH])
    assert len(world.request_calls) == 1
    code, tool_call_id = world.request_calls[0]
    assert code == "authentication_required" and tool_call_id == "g-s1-human"
    assert len(result.boundaries) == 1 and result.boundaries[0].request_id == "req-1"


def test_2_and_3_waiting_and_taken_mean_zero_cognition_zero_actions():
    world = World(polls=("waiting", "waiting", "taken", "taken", "completed"))
    result, _ = run(world, [REQUEST, FINISH])
    record = result.boundaries[0]
    assert record.polls == 5
    assert record.cognition_calls_during == 0 and record.actions_during == 0
    assert world.act_log == []
    # cognition only ever ran while the bot held the wheel and nothing was requested
    assert all(holder == "bot" and status != "waiting" and status != "taken" for holder, status, _ in world.cognition_log)


def test_4_completed_requires_fresh_governed_observation():
    world = World()
    result, provider = run(world, [REQUEST, FINISH])
    assert result.status == "VERIFIED"
    assert [o[1] for o in world.observe_log][:2] == [1, 2]  # a new observation after the handback
    assert result.boundaries[0].resumed_snapshot_id == 2 > result.boundaries[0].snapshot_before == 1


def test_5_completed_but_snapshot_still_owed_means_no_cognition():
    world = World(stuck_resume=True)
    result, provider = run(world, [REQUEST, FINISH])
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED"
    assert len(provider.prompts) == 1  # only the pre-request decision


def test_6_fresh_observe_with_nothing_owed_returns_cognition():
    world = World()
    result, provider = run(world, [REQUEST, CLICK, FINISH])
    assert result.status == "VERIFIED"
    assert len(provider.prompts) == 3
    assert "A person handled the page" in provider.prompts[1]
    assert world.act_log == [("bot", "completed", 2)]


@pytest.mark.parametrize("end", ["cancelled", "expired", "interrupted"])
def test_7_unresolved_requests_end_the_run(end):
    world = World(polls=("waiting", end))
    result, provider = run(world, [REQUEST, FINISH])
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED"
    assert len(provider.prompts) == 1 and world.act_log == []
    assert result.boundaries[0].final_request_status == end


def test_8_pre_takeover_snapshot_never_returns_to_cognition():
    world = World(stale_after_handback=1)
    result, provider = run(world, [REQUEST, FINISH])
    assert result.status == "VERIFIED"
    # prompts[0] saw page-1; the stale re-observation (same id) was never given to Agnes
    assert len(provider.prompts) == 2
    assert "page-1" not in provider.prompts[1]
    assert result.boundaries[0].resume_observes == 2


def test_8b_endlessly_stale_observation_fails_closed():
    world = World(stale_after_handback=99)
    result, provider = run(world, [REQUEST, FINISH])
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED" and len(provider.prompts) == 1


def test_suspension_times_out_fail_closed():
    ticks = iter(range(0, 10_000, 400))
    world = World(polls=["waiting"] * 50)
    World.resume_done_once = False
    provider = Provider(world, [REQUEST, FINISH])
    loop = AgentLoop(provider, world, OK, control=world, sleep=lambda s: None, clock=lambda: next(ticks), suspend_timeout=900)
    result = loop.run(GOAL)
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED" and len(provider.prompts) == 1


def test_request_without_control_reader_is_refused():
    world = World()
    World.resume_done_once = False
    result = AgentLoop(Provider(world, [REQUEST]), world, OK).run(GOAL)
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED" and world.request_calls == []


def test_request_cap():
    world = World()
    result, _ = run(world, [REQUEST, REQUEST, FINISH], max_human_requests=1)
    assert result.status == "HUMAN_BOUNDARY_UNRESOLVED" and len(world.request_calls) == 1


def test_prompt_carries_challenge_and_request_option():
    world = World()
    _, provider = run(world, [FINISH])
    assert "challenge: cloudflare" in provider.prompts[0]
    assert '"kind":"request_human"' in provider.prompts[0]
