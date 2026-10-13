from textwrap import indent

from application.computer_runtime import ComputerPageObservation
from application.eightb_verifier import SUBTITLE, TITLE, parse_aria_tree, verify_eightb_artifact

GOOD = f'''- heading "{TITLE}" [level=1] [ref=f1e3]
- paragraph [ref=f1e4]: {SUBTITLE}
- button "Testar" [ref=f1e5] [cursor=pointer]'''
EMPTY = '''- heading "Hello World" [level=1] [ref=f1e3]
- paragraph [ref=f1e4]: Start chatting to build your website'''


def editor(preview=EMPTY, log="x", composer="", dialog_children=EMPTY, publish=True, second_preview=None, sidebar_upgrade=True):
    """Mirrors the real 8B editor: the chrome <generic> (group + buttons) and the preview iframe are siblings."""
    sidebar = "    - button: Upgrade\n" if sidebar_upgrade else ""
    chrome = "                - button: Upgrade\n" + ("                - button: Publish\n" if publish else "")
    extra = ""
    if second_preview is not None:
        extra = "            - iframe [ref=e200]:\n" + indent(second_preview, " " * 16) + "\n"
    return f'''- generic:
{sidebar}    - main:
        - log:
            - generic: {log}
        - textbox:
            - /placeholder: What to change?
            - text: "{composer}"
    - generic:
        - generic:
            - generic:
                - group:
                    - button
{chrome}            - iframe [ref=e114]:
{indent(preview, " " * 16)}
{extra}- dialog [ref=e117]:
    - heading "Choose Design" [level=2] [ref=e118]
    - iframe [ref=e126]:
{indent(dialog_children, " " * 8)}
'''


def obs(aria):
    return ComputerPageObservation("https://ai.8b.com/projects/p", "8b", "s", 1, (), aria)


def verify(**kw):
    return verify_eightb_artifact(obs(editor(**kw)))


def test_8_exact_content_in_the_unique_preview_passes():
    r = verify(preview=GOOD)
    assert r.verified, r
    assert r.facts == {"PREVIEW_CANDIDATES": 1, "TITLE": True, "SUBTITLE": True, "BUTTON": True}


def test_initial_hello_world_fails():
    assert not verify().verified


def test_5_expected_strings_only_in_chat_log_fail():
    r = verify(log=f"{TITLE} {SUBTITLE} Testar", composer="")
    assert not r.verified and r.facts["TITLE"] is False


def test_6_expected_strings_only_in_composer_fail():
    r = verify(composer=f"{TITLE} {SUBTITLE} Testar")
    assert not r.verified


def test_prompt_text_in_both_chat_and_composer_still_fails():
    assert not verify(log=f"Crie {TITLE}", composer=f"{TITLE}\n{SUBTITLE}").verified


def test_7_expected_strings_in_the_choose_design_iframe_fail():
    r = verify(dialog_children=GOOD)
    assert not r.verified and r.facts["PREVIEW_CANDIDATES"] == 1 and r.facts["TITLE"] is False


def test_text_globally_outside_the_preview_fails():
    aria = editor().replace("- generic:\n    - main:", f'- generic:\n    - heading "{TITLE}" [level=1]\n    - paragraph: {SUBTITLE}\n    - button "Testar"\n    - main:', 1)
    assert not verify_eightb_artifact(obs(aria)).verified


def test_9_ambiguous_preview_candidates_fail_even_if_both_are_good():
    r = verify(preview=GOOD, second_preview=GOOD)
    assert not r.verified and r.reasons == ("PREVIEW_REGION_AMBIGUOUS",) and r.facts["PREVIEW_CANDIDATES"] == 2


def test_missing_preview_region_fails_without_global_fallback():
    r = verify(preview=GOOD, publish=False)
    assert not r.verified and r.reasons == ("PREVIEW_REGION_MISSING",)


def test_near_misses_fail():
    assert not verify(preview=GOOD.replace(TITLE, TITLE + "_X")).verified
    assert not verify(preview=GOOD.replace("governed browser", "governed Browser")).verified
    assert not verify(preview=GOOD.replace('"Testar"', '"Testar agora"')).verified
    assert not verify(preview=GOOD.replace("- heading", "- paragraph [ref=f1e3]: x\n- generic")).verified  # title not a heading


def test_title_must_be_a_heading_and_unreadable_aria_fails():
    assert not verify_eightb_artifact(obs(": : [")).verified


def test_real_aria_that_is_not_valid_yaml_still_parses_and_verifies():
    # real 8B pages contain lines such as "- generic [ref=f2e187]: =" inside unrelated iframes (invalid YAML)
    junk = '- generic [ref=f2e187]: =\n- generic [ref=f2e188]: @\n- link "a: b" [ref=f2e189]: "x: y"'
    aria = editor(preview=GOOD, dialog_children=junk)
    assert verify_eightb_artifact(obs(aria)).verified
    tree = parse_aria_tree('- a [ref=1]: =\n- text: "q \\" z"')
    assert tree[0].scalar == "=" and tree[1].scalar == 'q " z'
