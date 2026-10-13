from __future__ import annotations

import pytest

from cognitive.provider import AgnesProvider, AgnesResponseContractError, AgnesTransportResponse, LLMResponse


def test_empty_content_retries_once_then_succeeds():
    responses = iter([
        AgnesTransportResponse("", "agnes"),
        AgnesTransportResponse("síntese pronta", "agnes"),
    ])
    calls = []
    provider = AgnesProvider(lambda prompt: (calls.append(prompt) or next(responses)))
    result = provider.complete("dados")
    assert result.text == "síntese pronta"
    assert len(calls) == 2 and calls[0] == calls[1] == "dados"


def test_empty_content_twice_fails_closed_after_one_retry():
    calls = []
    provider = AgnesProvider(lambda prompt: (calls.append(prompt) or LLMResponse("", "agnes", "agnes")))
    with pytest.raises(AgnesResponseContractError, match="synthesis_provider_invalid"):
        provider.complete("dados")
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    AgnesTransportResponse("", "agnes", refusal_present=True),
    AgnesTransportResponse("", "agnes", tool_calls_present=True),
    AgnesTransportResponse("", "agnes", finish_reason="tool_calls"),
])
def test_refusal_or_tool_call_fails_without_retry(response):
    calls = []
    provider = AgnesProvider(lambda prompt: (calls.append(prompt) or response))
    with pytest.raises(AgnesResponseContractError):
        provider.complete("dados")
    assert len(calls) == 1


def test_normal_content_uses_one_request():
    calls = []
    provider = AgnesProvider(lambda prompt: (calls.append(prompt) or AgnesTransportResponse("ok", "agnes")))
    assert provider.complete("dados").text == "ok"
    assert len(calls) == 1
