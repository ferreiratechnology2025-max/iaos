import pytest

from application.computer_runtime import ComputerPageObservation
from application.todomvc_verifier import parse_todos, verify_todomvc


def obs(aria):
    return ComputerPageObservation("https://todomvc.example/", "TodoMVC", "s", 1, (), aria)


def page(items, left_text='- strong: "1"\n  - text: item left!'):
    rows = "\n".join(items)
    return f"""- heading "todos" [level=1] [ref=e2]
- textbox "What needs to be done?" [ref=e3]
- main [ref=e4]:
  - checkbox "Mark all as complete" [ref=e5]
  - list [ref=e6]:
{rows}
- contentinfo [ref=e20]:
  {left_text}
  - list:
    - listitem:
      - link "All" [ref=e21]
"""


CAFE_DONE = """    - listitem [ref=e8]:
      - checkbox "Toggle Todo" [checked] [ref=e9]
      - text: comprar café
      - button "Delete" [ref=e10]"""
MARIA_OPEN = """    - listitem [ref=e11]:
      - checkbox "Toggle Todo" [ref=e12]
      - text: ligar para Maria
      - button "Delete" [ref=e13]"""
CAFE_OPEN = CAFE_DONE.replace(" [checked]", "")
MARIA_DONE = MARIA_OPEN.replace('"Toggle Todo"', '"Toggle Todo" [checked]')


def test_success_state_verifies():
    r = verify_todomvc(obs(page([CAFE_DONE, MARIA_OPEN])))
    assert r.verified, r
    assert r.facts == {"TOTAL_TODOS": 2, "CAFE_CHECKED": True, "MARIA_CHECKED": False, "ITEMS_LEFT": 1}


def test_checked_equals_true_form_and_label_variants():
    variant = CAFE_DONE.replace("[checked]", "[checked=true]").replace("- text: comprar café", "- generic: comprar café")
    assert verify_todomvc(obs(page([variant, MARIA_OPEN]))).verified


def test_inline_footer_text():
    aria = page([CAFE_DONE, MARIA_OPEN], '- text: 1 item left!')
    assert verify_todomvc(obs(aria)).verified


@pytest.mark.parametrize("items,reason", [
    ([CAFE_OPEN, MARIA_OPEN], "wrong_checked_state:comprar café"),
    ([CAFE_DONE, MARIA_DONE], "wrong_checked_state:ligar para Maria"),
    ([CAFE_DONE], "total_todos"),
    ([CAFE_DONE, MARIA_OPEN, CAFE_DONE], "total_todos"),
    ([CAFE_DONE, CAFE_DONE], "not_exactly_once:ligar para Maria"),
    ([], "total_todos"),
])
def test_failures(items, reason):
    r = verify_todomvc(obs(page(items)))
    assert not r.verified
    assert reason in r.reasons


def test_extra_todo_fails_even_if_required_ones_correct():
    extra = MARIA_OPEN.replace("ligar para Maria", "outra coisa")
    r = verify_todomvc(obs(page([CAFE_DONE, MARIA_OPEN, extra])))
    assert not r.verified and "total_todos" in r.reasons


def test_items_left_must_be_one():
    r = verify_todomvc(obs(page([CAFE_DONE, MARIA_OPEN], '- strong: "2"\n  - text: items left')))
    assert not r.verified and "items_left" in r.reasons


def test_missing_items_left_fails():
    r = verify_todomvc(obs(page([CAFE_DONE, MARIA_OPEN], "- text: nothing")))
    assert not r.verified and "items_left" in r.reasons


def test_unreadable_aria_fails_closed():
    assert not verify_todomvc(obs("- : [unclosed\n  bad: {")).verified


def test_page_text_cannot_fake_a_todo_item():
    # A text node outside a listitem with a checkbox is not a todo.
    aria = page([CAFE_DONE], '- text: comprar café 1 item left!')
    todos, left = parse_todos(aria)
    assert todos == [("comprar café", True)] and left == 1


REAL_TEMPLATE = """- generic [ref=f1e1]:
    - generic [ref=f1e5]:
        - generic [ref=f1e6]:
            - heading "todos" [level=1] [ref=f1e7]
            - textbox "What needs to be done?" [active] [ref=f1e8]
        - generic [ref=f1e9]:
            - checkbox "❯Mark all as complete" [ref=f1e10]
            - generic [ref=f1e11]: ❯Mark all as complete
            - list [ref=f1e12]:
{items}
        - generic [ref=f1e17]:
            - generic [ref=f1e18]:
                - strong [ref=f1e19]: {left}
                - text: item left
            - list [ref=f1e20]:
                - listitem [ref=f1e21]:
                    - link "All" [ref=f1e22] [cursor=pointer]:
                        - /url: "#/"
"""


def real_item(ref, text, done):
    flag = " [checked]" if done else ""
    return f"""                - listitem [ref=f1e{ref}]:
                    - generic [ref=f1e{ref + 1}]:
                        - checkbox "Toggle Todo"{flag} [ref=f1e{ref + 2}]
                        - generic [ref=f1e{ref + 3}]: {text}
                        - text: ×"""


def test_real_playwright_demo_format_success_and_failure():
    ok = REAL_TEMPLATE.format(items="\n".join([real_item(30, "comprar café", True), real_item(40, "ligar para Maria", False)]), left=1)
    r = verify_todomvc(obs(ok))
    assert r.verified, r
    assert r.facts == {"TOTAL_TODOS": 2, "CAFE_CHECKED": True, "MARIA_CHECKED": False, "ITEMS_LEFT": 1}
    both = REAL_TEMPLATE.format(items="\n".join([real_item(30, "comprar café", True), real_item(40, "ligar para Maria", True)]), left=0)
    assert not verify_todomvc(obs(both)).verified
    only = REAL_TEMPLATE.format(items=real_item(30, "probe", False), left=1)
    assert parse_todos(only) == ([("probe", False)], 1)


def test_summarizer_reports_input_value_from_both_prompt_sources():
    from application.computer_runtime import ComputerElement
    from application.todomvc_verifier import summarize_todomvc
    aria = REAL_TEMPLATE.format(items=real_item(30, "comprar café", False), left=1).replace(
        '- textbox "What needs to be done?" [active] [ref=f1e8]', '- textbox "What needs to be done?" [active] [ref=f1e8]: ligar para Maria')
    o = ComputerPageObservation("https://t/", "T", "s", 1, (ComputerElement("f1e8", "textbox", "What needs to be done?", None, "ligar para Maria"),), aria)
    line = summarize_todomvc(o)
    assert "input_value(elements)='ligar para Maria'" in line and "input_value(aria)='ligar para Maria'" in line
    assert "comprar café[ ]" in line and "items_left=1" in line
