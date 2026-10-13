"""SELECT_OPTION_GATE: governed browser.select_option (snapshot-bound, select-like role, exact visible label)."""
import json

import httpx
import pytest

from application.agent_loop import VerificationResult, action_failure_note, build_prompt
from application.agent_runtime import build_agent_loop
from application.computer_runtime import (ComputerElement, ComputerPageObservation, ComputerRuntimeError,
                                          OpenBotComputerAdapter, classify_action_failure)
from cognitive.agent_contracts import ActDecision, AgentGoal, DecisionParseError, parse_decision
from cognitive.provider import LLMResponse
from governance.computer_interaction import ComputerInteractionAuthorizer
from governance.contract import Proposal, Provenance

URL = "https://websitewombat.app/build"
OPTIONS = ("Text", "Heading", "Button")
GOAL = AgentGoal("g", "pick the Button block type", max_steps=10)


class World:
    """s1 = block-type <select> (first option selected), t1 = textbox, b1 = button. Implements the executor's port."""

    def __init__(self):
        self.snapshot, self.calls, self.selected = 0, [], OPTIONS[0]

    def observe(self):
        self.snapshot += 1
        els = (ComputerElement("s1", "combobox", "", None, self.selected),
               ComputerElement("t1", "textbox", "Some text."), ComputerElement("b1", "button", "Download"))
        aria = f'- combobox [ref=s1]:\n  - option "{self.selected}" [selected]\n- textbox "Some text." [ref=t1]\n- button "Download" [ref=b1]\n'
        return ComputerPageObservation(URL, "w", "sess-1", self.snapshot, els, aria)

    def select_option(self, page_url, session_id, snapshot_id, ref, option):
        assert page_url == URL and session_id == "sess-1"
        if snapshot_id != self.snapshot:
            raise ComputerRuntimeError("computer_select_rejected:409:stale_ref")
        self.calls.append((page_url, session_id, snapshot_id, ref, option))
        if option not in OPTIONS:
            raise ComputerRuntimeError("computer_select_rejected:502:option_not_found")
        self.selected = option
        return {"url": URL}


class Script:
    provider_id = "script"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "script")


def select(ref, option):
    return json.dumps({"kind": "act", "action": "browser.select_option", "args": {"ref": ref, "option": option}})


FINISH = '{"kind":"finish","claim":"done"}'
verify_button = lambda o: VerificationResult(o.elements[0].value == "Button", {"SELECTED": o.elements[0].value})


def run(replies, world=None, **opts):
    world = world or World()
    script = Script(replies)
    loop, session = build_agent_loop(script, world, verify_button, **opts)
    return world, script, session, loop.run(GOAL)


# 1 + 5 + 6 -----------------------------------------------------------------------------------------------------
def test_1_valid_combobox_and_existing_option_is_authorized_selected_and_reflected_in_the_next_observation():
    world, script, _session, result = run([select("s1", "Button"), FINISH])
    assert result.status == "VERIFIED" and world.selected == "Button"
    assert "[selected]" in script.prompts[1] and 'option "Button"' in script.prompts[1]   # fresh observation reflects it


def test_5_agnes_supplies_ref_and_option_only_everything_else_comes_from_the_trusted_observation():
    world, _s, _session, result = run([select("s1", "Button"), FINISH])
    (page, sess, snap, ref, option), = world.calls
    assert (page, sess, snap, ref, option) == (URL, "sess-1", 1, "s1", "Button")
    for extra in ("page_url", "browser_session_id", "snapshot_id", "ref_role"):
        with pytest.raises(DecisionParseError):
            parse_decision(json.dumps({"kind": "act", "action": "browser.select_option",
                                       "args": {"ref": "s1", "option": "Button", extra: "x"}}))
    for bad in ("", "   ", "x" * 257):
        with pytest.raises(DecisionParseError):
            parse_decision(select("s1", bad))
    with pytest.raises(DecisionParseError):
        parse_decision(select("zz", "Button"), known_refs={"s1"})


def test_6_one_successful_selection_is_exactly_one_external_effect():
    world, _s, session, _r = run([select("s1", "Button"), FINISH])
    assert len(world.calls) == 1


# 2 stale ref ---------------------------------------------------------------------------------------------------
def test_2_stale_snapshot_is_rejected_and_nothing_is_selected():
    world = World()
    _w, _s, session, _r = run([FINISH], world)
    old = world.observe()
    world.observe()                                          # the page moved on
    outcome = session.act("s", ActDecision("browser.select_option", {"ref": "s1", "option": "Button"}), old)
    assert not outcome.ok and outcome.code.startswith("execution_failed") and world.selected == "Text"
    assert classify_action_failure("select", 409, "That list of elements is out of date").code == "stale_ref"


# 3 role --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ref", ["t1", "b1"])
def test_3_refs_that_are_not_select_like_are_rejected_before_any_effect(ref):
    world = World()
    _w, _s, session, _r = run([FINISH], world)
    outcome = session.act("s", ActDecision("browser.select_option", {"ref": ref, "option": "Button"}), world.observe())
    assert not outcome.ok and outcome.code.startswith("refused:") and world.calls == []


def test_3b_authorizer_requires_a_select_like_role_a_option_and_a_bounded_option():
    world = World()
    _w, _s, session, _r = run([FINISH], world)
    auth = session._act_auth
    base = {"page_url": URL, "browser_session_id": "sess-1", "snapshot_id": 1, "ref": "s1", "option": "Button", "ref_role": "combobox"}
    mk = lambda **kw: Proposal("i", "browser.select_option", {**base, **kw}, Provenance("agent", "i"))
    assert dict(auth.canonicalize(mk()).args)["ref_role"] == "combobox"
    assert dict(auth.canonicalize(mk(ref_role="listbox")).args)["option"] == "Button"
    for kw, msg in (({"ref_role": "textbox"}, "select_target_not_select_like"), ({"ref_role": None}, "select_target_not_select_like"),
                    ({"option": ""}, "select_option_required"), ({"option": "  "}, "select_option_required"),
                    ({"option": "x" * 257}, "select_option_too_large"), ({"snapshot_id": 0}, "snapshot_id_required"),
                    ({"ref": ""}, "element_ref_required")):
        with pytest.raises(ValueError, match=msg):
            auth.canonicalize(mk(**kw))


# 4 nonexistent option ------------------------------------------------------------------------------------------
def test_4_nonexistent_option_is_an_execution_failure_with_no_silent_fallback():
    world, script, _s, result = run([select("s1", "Buton")] + [FINISH] * 4)
    assert world.selected == "Text" and result.status == "TOO_MANY_FAILED_FINISHES_WITHOUT_PROGRESS"       # not the first option, not anything
    assert any("action FAILED execution_failed" in line for line in result.log)
    assert "could not find that option label" in script.prompts[1]


# OpenBot adapter ----------------------------------------------------------------------------------------------
def adapter(handler):
    a = OpenBotComputerAdapter("http://x", "bot", "tok", transport=httpx.MockTransport(handler))
    a._session_id, a._page_url = "s", URL
    return a


def test_adapter_posts_select_with_snapshot_binding_and_only_the_label():
    seen = []

    def handler(request):
        if request.url.path == "/select":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"action": "select", "url": URL})
        return httpx.Response(200, json={"run": "s"})
    adapter(handler).select_option(URL, "s", 7, "s1", "Button")
    assert seen == [{"ref": "s1", "snapshotId": 7, "option": "Button"}]


@pytest.mark.parametrize("body,code", [
    ("Timeout 5000ms exceeded.\nCall log:\n - did not find some options <div SECRET>", "option_not_found"),
    ("Error: Element is not a <select> element", "not_a_select"),
])
def test_adapter_classifies_select_failures_without_leaking_raw_text(body, code):
    a = adapter(lambda r: httpx.Response(502, json={"error": body}))
    with pytest.raises(ComputerRuntimeError) as info:
        a.select_option(URL, "s", 1, "s1", "Button")
    assert str(info.value) == f"computer_select_rejected:502:{code}" and "SECRET" not in str(info.value)
    assert "Previous action failed" in action_failure_note("browser.select_option", f"execution_failed:{code}")


def test_prompt_documents_the_semantics_and_the_action_shape():
    obs = World().observe()
    prompt = build_prompt(GOAL, obs, 1, None)
    assert '"action":"browser.select_option","args":{"ref":"<ref>","option":"<exact visible option label>"}' in prompt
    assert "use only on a select/combobox control that exposes selectable options" in prompt
    assert "exact visible label" in prompt and "observe the page again" in prompt


def test_select_on_a_forbidden_named_control_is_blocked_before_any_proposal():
    world = World()
    world.observe = lambda: ComputerPageObservation(URL, "w", "sess-1", 1, (ComputerElement("s1", "combobox", "Download"),), "- x")
    _w, _s, _session, result = run([select("s1", "Button"), FINISH], world, forbidden_controls=("Download",))
    assert result.status == "GOAL_CONSTRAINT_VIOLATION" and world.calls == []
