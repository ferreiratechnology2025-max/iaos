"""Assembly gate: a scripted brain drives a simulated TodoMVC through real Governance."""
import json

from application.agent_runtime import build_agent_loop
from application.computer_runtime import ComputerElement, ComputerPageObservation, ComputerRuntimeError
from application.todomvc_verifier import verify_todomvc
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

URL = "https://todomvc.example/"


class FakeTodoMVC:
    """Simulated world: textbox e1; items get refs by position; implements the port the executors call."""

    def __init__(self):
        self.todos, self.snapshot, self.calls, self.fail_next_click = [], 0, [], False

    def _aria(self):
        rows = []
        for i, (text, done) in enumerate(self.todos):
            rows.append(f'    - listitem:\n      - checkbox "Toggle Todo"{" [checked]" if done else ""} [ref=c{i}]\n'
                        f'      - text: {text}\n      - button "Delete"')
        left = sum(1 for _, d in self.todos if not d)
        return ('- textbox "What needs to be done?" [ref=e1]\n- list:\n' + ("\n".join(rows) + "\n" if rows else "") +
                f'- contentinfo:\n  - strong: "{left}"\n  - text: item left!\n')

    def observe(self):
        self.snapshot += 1
        els = [ComputerElement("e1", "textbox", "What needs to be done?")]
        els += [ComputerElement(f"c{i}", "checkbox", "Toggle Todo", done) for i, (_, done) in enumerate(self.todos)]
        return ComputerPageObservation(URL, "TodoMVC", "sess-1", self.snapshot, tuple(els), self._aria())

    def _check(self, page_url, session_id, snapshot_id):
        assert page_url == URL and session_id == "sess-1"
        if snapshot_id != self.snapshot:
            raise ComputerRuntimeError("stale_snapshot")

    def type(self, page_url, session_id, snapshot_id, ref, text):
        self._check(page_url, session_id, snapshot_id)
        self.calls.append(("type", ref, text))
        self.pending = text
        return {"url": URL}

    def key(self, page_url, session_id, key, ref=None, snapshot_id=None):
        self._check(page_url, session_id, snapshot_id)
        self.calls.append(("key", key))
        if key == "Enter" and getattr(self, "pending", ""):
            self.todos.append((self.pending, False))
            self.pending = ""
        return {"url": URL}

    def click(self, page_url, session_id, snapshot_id, ref):
        self._check(page_url, session_id, snapshot_id)
        self.calls.append(("click", ref))
        if self.fail_next_click:
            self.fail_next_click = False
            raise ComputerRuntimeError("element_not_clickable")
        i = int(ref[1:])
        text, done = self.todos[i]
        self.todos[i] = (text, not done)
        return {"url": URL}


class Script:
    provider_id = "script"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "script")


def act(action, **args):
    return json.dumps({"kind": "act", "action": action, "args": args})


GOAL = AgentGoal("todo", 'Create "comprar café" and "ligar para Maria", check only "comprar café", finish when 1 item is left.')
FULL = [
    act("browser.type", ref="e1", text="comprar café"), act("browser.key", key="Enter"),
    act("browser.type", ref="e1", text="ligar para Maria"), act("browser.key", key="Enter"),
    act("browser.click", ref="c0"), '{"kind":"finish","claim":"done"}',
]


def test_scripted_brain_completes_todomvc_through_real_governance():
    world = FakeTodoMVC()
    loop, session = build_agent_loop(Script(FULL), world, verify_todomvc)
    result = loop.run(GOAL)
    assert result.status == "VERIFIED", result
    assert result.verification.facts == {"TOTAL_TODOS": 2, "CAFE_CHECKED": True, "MARIA_CHECKED": False, "ITEMS_LEFT": 1}
    kinds = [e["action"] for e in session.evidence.events() if e["kind"] == "AUTHORIZATION"]
    assert kinds.count("browser.observe") == 7  # 6 turns + 1 fresh final
    assert kinds.count("browser.type") == 2 and kinds.count("browser.key") == 2 and kinds.count("browser.click") == 1


def test_model_never_controls_authority_fields_and_runtime_is_snapshot_bound():
    world = FakeTodoMVC()
    # Model tries to inject authority fields: rejected by the parser, nothing executes.
    bad = act("browser.click", ref="e1", snapshot_id=99)
    loop, _ = build_agent_loop(Script([bad] * 4), world, verify_todomvc)
    assert loop.run(GOAL).status == "TOO_MANY_DECISION_FAILURES"
    assert world.calls == []


def test_action_failure_is_reported_back_then_world_state_decides():
    world = FakeTodoMVC()
    world.todos = [("comprar café", False), ("ligar para Maria", False)]
    world.fail_next_click = True
    script = Script([act("browser.click", ref="c0"), act("browser.click", ref="c0"), '{"kind":"finish","claim":"done"}'])
    loop, _ = build_agent_loop(script, world, verify_todomvc)
    result = loop.run(GOAL)
    assert result.status == "VERIFIED"
    assert "FAILED: execution_failed:" in script.prompts[1]
    assert [c for c in world.calls if c[0] == "click"] == [("click", "c0"), ("click", "c0")]


def test_false_claim_is_not_verified():
    world = FakeTodoMVC()
    loop, _ = build_agent_loop(Script(['{"kind":"finish","claim":"all done"}'] * 4), world, verify_todomvc)
    result = loop.run(GOAL)
    assert result.status == "TOO_MANY_FAILED_FINISHES_WITHOUT_PROGRESS" and result.claim == "all done"
