"""V1.1 synthetic gate: governed navigation inside a trusted allow-list of exact origins."""
import json

import pytest

from application.agent_loop import AgentLoop, VerificationResult
from application.agent_runtime import GovernedComputerSession
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import AgentGoal, DecisionParseError, NavigateDecision, parse_decision
from cognitive.provider import LLMResponse
from governance.origin import canonical_allowed_origins, canonical_origin

ALLOWED = ("https://ai.8b.com",)


class Port:
    def __init__(self, redirects=None):
        self.navigations, self.redirects, self.snapshot = [], redirects or {}, 0

    def navigate(self, url):
        self.navigations.append(url)
        return self.redirects.get(url, url)

    def observe(self):
        self.snapshot += 1
        return ComputerPageObservation("https://ai.8b.com/", "8b", "s", self.snapshot,
                                       (ComputerElement("e1", "button", "Go"),), "- x", None)

    def read_control_state(self, request_id=None):
        return ComputerControlState("bot", False, None, None, False)


def session(port=None, allowed=ALLOWED):
    port = port or Port()
    return port, GovernedComputerSession(port, allowed_origins=allowed)


# ---- authority
def test_allowed_origin_is_navigated_and_audited():
    port, s = session()
    out = s.navigate("n1", "https://ai.8b.com/login?x=1")
    assert out.ok and port.navigations == ["https://ai.8b.com/login?x=1"]
    assert [e["action"] for e in s.evidence.events() if e["kind"] == "AUTHORIZATION"] == ["browser.navigate"]


@pytest.mark.parametrize("url", [
    "https://evil.example/",                 # other origin
    "https://sub.ai.8b.com/",                # unauthorized subdomain
    "https://8b.com/",                       # parent domain
    "https://ai.8b.com:8443/",               # other effective port
    "http://ai.8b.com/",                     # not https
    "https://user:pw@ai.8b.com/",            # userinfo
    "https://localhost/", "https://127.0.0.1/", "https://10.0.0.5/", "https://[::1]/",
    "https://ai.8b.com.evil.example/", "https://ai.8b.com@evil.example/",
])
def test_everything_else_is_denied_before_any_effect(url):
    port, s = session()
    out = s.navigate("n1", url)
    assert not out.ok and port.navigations == []
    assert [e for e in s.evidence.events() if e["kind"] == "AUTHORIZATION"] == []


def test_effective_port_and_case_are_canonical():
    port, s = session()
    assert s.navigate("n1", "https://AI.8B.com:443/x").ok
    assert canonical_origin("https://ai.8b.com.") == "https://ai.8b.com"


def test_empty_allow_list_denies_everything():
    port, s = session(allowed=())
    assert not s.navigate("n1", "https://ai.8b.com/").ok and port.navigations == []


@pytest.mark.parametrize("bad", ["https://*.8b.com", "https://ai.8b.com/path", "http://ai.8b.com", "ai.8b.com", "https://localhost"])
def test_allow_list_itself_is_exact_origins_only(bad):
    with pytest.raises(ValueError):
        canonical_allowed_origins([bad])
    with pytest.raises(ValueError):
        GovernedComputerSession(Port(), allowed_origins=[bad])


def test_redirect_out_of_scope_fails_and_is_reported():
    port, s = session(Port({"https://ai.8b.com/go": "https://evil.example/landing"}))
    out = s.navigate("n1", "https://ai.8b.com/go")
    assert not out.ok and out.code.endswith("redirect_out_of_scope")


def test_redirect_inside_scope_is_fine():
    port, s = session(Port({"https://ai.8b.com/a": "https://ai.8b.com/b"}))
    assert s.navigate("n1", "https://ai.8b.com/a").ok


def test_navigate_decision_carries_no_authority_fields():
    assert parse_decision('{"kind":"navigate","url":"https://ai.8b.com/"}') == NavigateDecision("https://ai.8b.com/")
    for bad in ('{"kind":"navigate","url":"https://a.b/","allowed_origins":["*"]}', '{"kind":"navigate"}', '{"kind":"navigate","url":""}'):
        with pytest.raises(DecisionParseError):
            parse_decision(bad)


# ---- loop
class Provider:
    provider_id = "scripted"

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return LLMResponse(self.replies.pop(0), "m", "scripted")


NAV = lambda url: json.dumps({"kind": "navigate", "url": url})
FINISH = '{"kind":"finish","claim":"on 8b"}'
GOAL = AgentGoal("g", "open 8B", max_steps=8)
OK = lambda o: VerificationResult(True, {})


def make_loop(replies, port=None, allowed=ALLOWED):
    port = port or Port()
    s = GovernedComputerSession(port, allowed_origins=allowed)
    provider = Provider(replies)
    return port, s, provider, AgentLoop(provider, s, OK, control=s, sleep=lambda x: None)


def test_navigation_is_followed_by_a_fresh_observation_before_the_next_decision():
    port, s, provider, loop = make_loop([NAV("https://ai.8b.com/"), FINISH])
    result = loop.run(GOAL)
    assert result.status == "VERIFIED"
    obs = [e["action"] for e in s.evidence.events() if e["kind"] == "AUTHORIZATION"]
    assert obs == ["browser.observe", "browser.navigate", "browser.observe", "browser.observe"]  # obs, nav, obs, final
    assert "navigation was executed" in provider.prompts[1] and port.snapshot == 3


def test_denied_navigation_has_no_browser_effect_and_is_reported_to_agnes():
    port, s, provider, loop = make_loop([NAV("https://evil.example/"), FINISH])
    result = loop.run(GOAL)
    assert port.navigations == []
    assert "navigation FAILED: denied:origin_not_allowed" in provider.prompts[1]
    assert result.status == "VERIFIED"


def test_repeated_denials_fail_closed():
    port, s, provider, loop = make_loop([NAV("https://evil.example/")] * 4)
    assert loop.run(GOAL).status == "TOO_MANY_ACTION_FAILURES" and port.navigations == []


def test_redirect_out_of_scope_ends_the_run_without_observing_the_foreign_page():
    port, s, provider, loop = make_loop([NAV("https://ai.8b.com/go"), FINISH], Port({"https://ai.8b.com/go": "https://evil.example/"}))
    result = loop.run(GOAL)
    assert result.status == "NAVIGATION_SCOPE_VIOLATION"
    assert len(provider.prompts) == 1 and port.snapshot == 1  # the foreign page was never observed or shown


def test_allowed_origins_are_exposed_to_cognition_from_the_trusted_config_without_a_route():
    from application.agent_runtime import build_agent_loop
    provider = Provider([FINISH])
    loop, session = build_agent_loop(provider, Port(), OK, allowed_origins=["https://AI.8b.com:443/"])
    loop.run(GOAL)
    prompt = provider.prompts[0]
    assert "You may navigate only to these exact allowed origins:\n- https://ai.8b.com\n" in prompt
    assert "Do not invent alternative domains." in prompt
    assert "navigate to https" not in prompt.lower() and "go to https" not in prompt.lower()


def test_no_navigation_section_without_configured_origins():
    from application.agent_loop import build_prompt
    assert "NAVIGATION CONSTRAINTS" not in build_prompt(GOAL, Port().observe(), 1, None)


def test_navigation_log_carries_only_the_canonical_origin():
    port, s, provider, loop = make_loop([NAV("https://evil.example/secret/path?token=abc#frag"), FINISH])
    result = loop.run(GOAL)
    line = next(l for l in result.log if "NAVIGATION_DENIED" in l)
    assert "REQUESTED_ORIGIN=https://evil.example DENIAL_REASON=origin_not_allowed AUTHORIZATION=DENY" in line
    blob = "\n".join(result.log)
    assert "secret" not in blob and "token=abc" not in blob and "frag" not in blob


def test_allowed_navigation_logs_pass_with_origin():
    port, s, provider, loop = make_loop([NAV("https://ai.8b.com/x?y=1"), FINISH])
    result = loop.run(GOAL)
    assert any("NAVIGATE REQUESTED_ORIGIN=https://ai.8b.com AUTHORIZATION=PASS" in l for l in result.log)
    assert not any("y=1" in l for l in result.log)
