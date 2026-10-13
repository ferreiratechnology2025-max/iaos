"""Scope facts from the aria text: logging-only metadata (role/name/frame/dialog containment), no behavior change."""
from application.agent_loop import ActionOutcome, AgentLoop, VerificationResult
from application.aria_scope import analyze, describe_target, frame_of
from application.computer_runtime import ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse

ARIA = '''- generic [ref=e1]:
  - button "Background" [ref=e2] [cursor=pointer]
  - iframe [ref=e3]:
    - button "Preview behind" [ref=f1e4]
- dialog [ref=e10]:
  - heading "Choose Design" [level=2] [ref=e11]
  - iframe [ref=e12]:
    - button "Monthly" [pressed] [ref=f2e20] [cursor=pointer]
  - button "Try this design" [ref=e13] [cursor=pointer]
'''


def test_dialog_containment_and_frames():
    report = analyze(ARIA)
    assert [(d.role, d.name, d.ref) for d in report.dialogs] == [("dialog", "", "e10")]
    assert not report.scopes["e2"].inside_dialog and not report.scopes["f1e4"].inside_dialog
    for ref in ("e13", "f2e20", "e11"):
        assert report.scopes[ref].inside_dialog
    assert report.scopes["f2e20"].frame == "f2" and report.scopes["e13"].frame == "main"
    assert sorted(c.ref for c in report.dialogs[0].controls) == ["e13", "f2e20"]
    assert report.controls_outside == 2


def test_describe_target_is_role_name_frame_only_and_never_raises():
    text = describe_target(ARIA, "f2e20")
    assert text == 'TARGET ref=f2e20 role=button name="Monthly" frame=f2 inside_dialog=YES nearest_dialog=""'
    assert describe_target(ARIA, "e2").endswith("frame=main inside_dialog=NO")
    assert describe_target(ARIA, "e999") == "TARGET ref=e999 frame=main scope=unknown"
    assert describe_target(None, "e1").startswith("TARGET ref=e1")
    assert frame_of("f12e3") == "f12"


class Computer:
    def __init__(self):
        self.n = 0

    def observe(self, step_id):
        self.n += 1
        return ComputerPageObservation("https://ai.8b.com/", "t", "s", self.n,
                                       (ComputerElement("e13", "button", "Try this design"),), ARIA)

    def act(self, step_id, decision, observation):
        return ActionOutcome(True, "executed")

    def navigate(self, step_id, url):  # pragma: no cover
        raise AssertionError


class Provider:
    provider_id = "s"

    def __init__(self):
        self.replies = ['{"kind":"act","action":"browser.click","args":{"ref":"e13"}}', '{"kind":"finish","claim":"d"}']

    def complete(self, prompt):
        return LLMResponse(self.replies.pop(0), "m", "s")


def test_loop_logs_target_metadata():
    result = AgentLoop(Provider(), Computer(), lambda o: VerificationResult(True, {})).run(AgentGoal("g", "x", max_steps=5))
    assert result.status == "VERIFIED"
    assert 'STEP 1 TARGET ref=e13 role=button name="Try this design" frame=main inside_dialog=YES nearest_dialog=""' in result.log
