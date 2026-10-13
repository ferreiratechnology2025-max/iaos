import httpx
import pytest

from application.browser_key_contract import BROWSER_KEY_CONTRACT_DESCRIPTION, canonical_browser_key_tokens, is_valid_browser_key
from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter
from application.agent_loop import prompt_header


@pytest.mark.parametrize("key", ["Enter", "Tab", "ArrowRight", "F12", "Control+o", "ControlOrMeta+o", "a", "+"])
def test_pinned_playwright_keys_are_accepted(key):
    assert is_valid_browser_key(key)


@pytest.mark.parametrize("key", ["Click", "f75e10", "F30", ""])
def test_non_contract_keys_are_rejected_without_normalization(key):
    assert not is_valid_browser_key(key)


def test_invalid_key_is_rejected_before_openbot_request():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(500)

    adapter = OpenBotComputerAdapter("http://computer", "bot-1", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(ComputerRuntimeError, match="invalid_browser_key"):
        adapter.key("https://example.test", "session-1", "Click")
    assert calls == []


def test_valid_key_reaches_openbot_unchanged():
    payloads = []

    def handler(request):
        import json
        payloads.append((request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/key":
            return httpx.Response(200, json={"action": "key", "key": "Tab", "url": "https://example.test"})
        if request.url.path == "/run":
            return httpx.Response(200, json={"run": "session-1"})
        return httpx.Response(404)

    adapter = OpenBotComputerAdapter("http://computer", "bot-1", "token", transport=httpx.MockTransport(handler))
    adapter._session_id = "session-1"
    adapter._page_url = "https://example.test"
    adapter.key("https://example.test", "session-1", "Tab")
    assert ("/key", {"key": "Tab"}) in payloads


def test_model_facing_contract_uses_the_same_pinned_contract_description():
    header = prompt_header()
    assert "pinned Playwright key name/code" in header
    assert "never a mouse action" in header
    assert "Enter" in header and "Tab" in header and "ArrowDown" in header
    assert "never an element ref" in header


def test_model_facing_key_tokens_are_the_runtime_contract():
    tokens = canonical_browser_key_tokens()
    assert all(is_valid_browser_key(token) for token in tokens)
    assert "f96e9" not in tokens
    assert BROWSER_KEY_CONTRACT_DESCRIPTION in prompt_header()
