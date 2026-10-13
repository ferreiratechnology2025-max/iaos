"""WOMBAT_TARGET_SWITCH_GATE: iframe-scoped verifier + Download/Copy link/Edit link guards (Website Wombat)."""
import json
from textwrap import indent

import pytest

from application.agent_loop import ActionOutcome, AgentLoop, HumanRequestOutcome, VerificationResult, build_prompt
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation
from application.eightb_verifier import SUBTITLE, TITLE
from application.wombat_verifier import verify_wombat_artifact
from cognitive.agent_contracts import AgentGoal
from cognitive.provider import LLMResponse
from scripts.agent_wombat_e2e import ALLOWED, FORBIDDEN, GOAL

GOOD = f'''- generic [ref=f2e2]:
    - banner:
        - heading "{TITLE}" [level=1] [ref=f2e3]
    - paragraph [ref=f2e4]: {SUBTITLE}
    - button "Testar" [ref=f2e5] [cursor=pointer]'''
EMPTY = '''- generic [ref=f2e2]:
    - banner:
        - heading [level=1]
    - generic:
        - paragraph'''
ALL_TEXT = f"{TITLE} {SUBTITLE} Testar"


def page(preview=EMPTY, prompt="", form="", modal_iframe=None, second_preview=None, chrome=True, chrome_iframe=None):
    """Mirrors the real /build aria: header chrome (buttons) and [editor panel + preview iframe] are siblings."""
    buttons = ('        - button "Edit link" [ref=f1e16]\n        - button "Copy link" [ref=f1e17]\n'
               '        - button "Download" [ref=f1e18]\n') if chrome else ""
    inchrome = ("        - iframe [ref=f1e300]:\n" + indent(chrome_iframe, " " * 12) + "\n") if chrome_iframe else ""
    second = ("        - iframe [ref=f1e200]:\n" + indent(second_preview, " " * 12) + "\n") if second_preview else ""
    modal_if = ("        - iframe [ref=f1e400]:\n" + indent(modal_iframe, " " * 12) + "\n") if modal_iframe else ""
    return f'''- generic [active] [ref=f1e1]:
    - generic [ref=f1e2]:
        - heading [level=1] [ref=f1e3]:
            - link "Website Wombat" [ref=f1e4]
        - button "My sites" [ref=f1e5]
        - button "New site" [ref=f1e6]
        - button "Edit with AI" [ref=f1e7]
        - button "Form" [ref=f1e9]
        - button "JSON" [ref=f1e10]
{buttons}{inchrome}    - generic [ref=f1e19]:
        - generic [ref=f1e20]:
            - textbox "Business name *" [ref=f1e29]: "{form}"
            - textbox "Manifest JSON" [ref=f1e30]: "{form}"
            - paragraph [ref=f1e31]: "{form}"
        - iframe [ref=f1e153]:
{indent(preview, " " * 12)}
{second}    - generic [ref=f1e155]:
        - heading "Start a new site" [level=2] [ref=f1e158]
        - textbox "e.g. A painting company in Philadelphia" [ref=f1e185]: "{prompt}"
        - button "Generate my site" [ref=f1e187]
{modal_if}'''


def obs(aria, n=1, elements=()):
    return ComputerPageObservation("https://websitewombat.app/build", "w", "s", n, tuple(elements), aria)


def verify(**kw):
    return verify_wombat_artifact(obs(page(**kw)))


# ---- verifier (cases 1-6) --------------------------------------------------------------------------------
def test_1_correct_preview_passes():
    r = verify(preview=GOOD)
    assert r.verified, r
    assert r.facts == {"PREVIEW_CANDIDATES": 1, "TITLE": True, "SUBTITLE": True, "BUTTON": True}


def test_2_expected_strings_only_in_the_prompt_fail():
    assert not verify(prompt=ALL_TEXT).verified


def test_3_expected_strings_only_in_form_or_json_fail():
    r = verify(form=ALL_TEXT)
    assert not r.verified and r.facts["TITLE"] is False and r.facts["SUBTITLE"] is False and r.facts["BUTTON"] is False


def test_4a_expected_strings_in_another_iframe_inside_the_page_fail_as_ambiguous():
    r = verify(preview=EMPTY, modal_iframe=GOOD)
    assert not r.verified and r.reasons == ("PREVIEW_REGION_AMBIGUOUS",) and r.facts["TITLE"] is False


def test_4b_expected_strings_in_an_iframe_outside_the_preview_region_fail():
    r = verify(preview=EMPTY, chrome_iframe=GOOD)       # an iframe in the header chrome is not a preview candidate
    assert not r.verified and r.facts["PREVIEW_CANDIDATES"] == 1 and r.facts["TITLE"] is False


def test_5_empty_preview_fails():
    r = verify()
    assert not r.verified and r.reasons == ("title_missing", "subtitle_missing", "button_missing")


def test_6_ambiguous_preview_fails_even_if_both_are_good():
    r = verify(preview=GOOD, second_preview=GOOD)
    assert not r.verified and r.reasons == ("PREVIEW_REGION_AMBIGUOUS",) and r.facts["PREVIEW_CANDIDATES"] == 2


def test_6b_missing_builder_chrome_means_missing_region_without_global_fallback():
    r = verify(preview=GOOD, chrome=False)
    assert not r.verified and r.reasons == ("PREVIEW_REGION_MISSING",)


def test_near_misses_fail():
    assert not verify(preview=GOOD.replace(TITLE, TITLE + "_X")).verified
    assert not verify(preview=GOOD.replace("governed browser", "governed Browser")).verified
    assert not verify(preview=GOOD.replace('"Testar"', '"Testar agora"')).verified
    assert not verify(preview=GOOD.replace("- heading", "- paragraph [ref=f2e3]: x\n- generic")).verified
    assert not verify_wombat_artifact(obs(": : [")).verified


def test_button_block_rendered_as_link_testar_passes_and_other_roles_with_the_same_text_do_not():
    as_link = GOOD.replace('- button "Testar" [ref=f2e5] [cursor=pointer]',
                           '- link "Testar" [ref=f2e5] [cursor=pointer]:\n        - /url: tel:(555) 123-4567')
    r = verify(preview=as_link)
    assert r.verified and r.facts["BUTTON"] is True
    for other in ('- paragraph [ref=f2e5]: Testar', '- heading "Testar" [level=2] [ref=f2e5]', '- generic "Testar" [ref=f2e5]',
                  '- textbox "Testar" [ref=f2e5]', '- link "Testar agora" [ref=f2e5]'):
        assert not verify(preview=GOOD.replace('- button "Testar" [ref=f2e5] [cursor=pointer]', other)).facts["BUTTON"], other


def test_link_testar_in_the_editor_or_another_iframe_still_never_counts():
    link = '- link "Testar" [ref=f9e9]'
    assert not verify(form=ALL_TEXT, preview=EMPTY).facts["BUTTON"]
    aria = page(preview=EMPTY).replace("    - generic [ref=f1e155]:", f"    {link}\n    - generic [ref=f1e155]:", 1)
    assert not verify_wombat_artifact(obs(aria)).facts["BUTTON"]
    assert not verify(preview=EMPTY, modal_iframe="- generic:\n    " + link).facts["BUTTON"]


def test_page_text_outside_every_iframe_never_counts():
    aria = page(preview=EMPTY).replace("    - generic [ref=f1e155]:", f'    - heading "{TITLE}" [level=1]\n    - paragraph: {SUBTITLE}\n    - button "Testar"\n    - generic [ref=f1e155]:', 1)
    assert not verify_wombat_artifact(obs(aria)).verified


# ---- guards (cases 7-10) ---------------------------------------------------------------------------------
FINISH = '{"kind":"finish","claim":"done"}'
OK = lambda o: VerificationResult(True, {})
G = AgentGoal("g", "make a page", max_steps=20)
NAMES = {"e1": "Edit with AI", "e2": "Download", "e3": "Copy link", "e4": "Edit link", "e5": "Generate my site"}


def act(action, **args):
    return json.dumps({"kind": "act", "action": action, "args": args})


def click(ref):
    return act("browser.click", ref=ref)


def key(k, ref=None):
    return act("browser.key", key=k, **({"ref": ref} if ref else {}))


class Computer:
    def __init__(self, renames=None):
        self.n, self.acted, self.renames = 0, [], renames or {}

    def observe(self, step_id):
        self.n += 1
        els = [ComputerElement(r, "button", self.renames.get((self.n, r), name)) for r, name in NAMES.items()]
        els.append(ComputerElement("e6", "textbox", "Prompt"))
        return ComputerPageObservation("https://websitewombat.app/build", "w", "s", self.n, tuple(els), "- x")

    def act(self, step_id, decision, observation):
        self.acted.append((decision.action.split(".")[1], dict(decision.args)))
        return ActionOutcome(True, "executed")

    def request_human(self, step_id, reason_code, tool_call_id):
        return HumanRequestOutcome(True, "req-1", "requested")

    def read_control_state(self, request_id=None):
        return ComputerControlState("bot", False, "req-1", "completed", False)


class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, prompt):
        return LLMResponse(self.replies.pop(0), "m", "scripted")


def run(replies, computer=None):
    computer = computer or Computer()
    result = AgentLoop(Provider(replies), computer, OK, control=computer, sleep=lambda s: None,
                       forbidden_controls=FORBIDDEN).run(G)
    return computer, result


@pytest.mark.parametrize("ref", ["e2", "e3", "e4"])          # 7 Download, 8 Copy link, 9 Edit link
def test_7_8_9_click_type_and_key_on_download_copy_edit_link_are_blocked_before_any_effect(ref):
    for reply in (click(ref), act("browser.type", ref=ref, text="x"), key("Enter", ref), key("Space", ref)):
        computer, result = run([reply, FINISH])
        assert result.status == "GOAL_CONSTRAINT_VIOLATION" and computer.acted == []


@pytest.mark.parametrize("renamed", ["Download", "Copy link", "Edit link"])
def test_10_bare_enter_on_focus_that_becomes_a_forbidden_control_is_blocked(renamed):
    computer, result = run([click("e5"), key("Enter"), FINISH], Computer({(2, "e5"): renamed}))
    assert result.status == "GOAL_CONSTRAINT_VIOLATION" and computer.acted == [("click", {"ref": "e5"})]


def test_legacy_guards_are_kept_and_edit_with_ai_and_generate_are_not_forbidden():
    assert {"Publish", "Publicar", "Upgrade"} <= set(FORBIDDEN)
    computer, result = run([click("e1"), click("e5"), FINISH])
    assert result.status == "VERIFIED" and [a[1]["ref"] for a in computer.acted] == ["e1", "e5"]


# ---- case 11 + harness contract ----------------------------------------------------------------------------
def test_11_full_aria_stays_visible_to_the_agent_and_nothing_is_hidden_or_hinted():
    aria = page(preview=GOOD)
    prompt = build_prompt(GOAL, obs(aria), 1, None, ALLOWED)
    for needle in ("Download", "Copy link", "Edit link", "Generate my site", "Start a new site", "Edit with AI", TITLE, "Testar"):
        assert needle in prompt
    assert "forbidden" not in prompt.lower()


def test_goal_is_authorized_text_only_and_gives_no_ui_hints():
    obj = GOAL.objective
    assert obj.startswith("Crie no Website Wombat uma landing page simples de uma página.")
    assert obj.endswith("Não faça download nem crie links de compartilhamento.")
    for hint in ("textbox", "generate my site", "template", "json", "iframe", "preview", "edit with ai", "form"):
        assert hint not in obj.lower()
    assert ALLOWED == ("https://websitewombat.app",) and GOAL.max_steps == 60
