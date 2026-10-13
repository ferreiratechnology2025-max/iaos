"""Provider abstraction with an injected Agnes adapter and no network default."""

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: str


@dataclass(frozen=True)
class AgnesTransportResponse:
    """Sanitized provider response metadata used at the synthesis boundary."""

    text: str
    model: str
    finish_reason: str | None = None
    refusal_present: bool = False
    tool_calls_present: bool = False
    content_type: str | None = None


class AgnesResponseContractError(RuntimeError):
    """The provider returned no usable text or an unsupported response shape."""


class LLMProvider(Protocol):
    provider_id: str

    def complete(self, prompt: str) -> LLMResponse:
        """Return a model response; planning is not part of this interface."""


class AgnesProvider:
    """Structural Agnes adapter. Transport is injected; no external call is made by construction."""

    provider_id = "agnes"

    def __init__(self, complete_fn: Callable[[str], LLMResponse | AgnesTransportResponse] | None = None, model: str = "agnes-2.5-flash") -> None:
        self._complete_fn = complete_fn
        self.model = model

    def complete(self, prompt: str) -> LLMResponse:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if self._complete_fn is None:
            raise RuntimeError("Agnes transport is not configured")
        for attempt in range(2):
            response = self._complete_fn(prompt)
            if isinstance(response, LLMResponse):
                transport = AgnesTransportResponse(response.text, response.model)
            elif isinstance(response, AgnesTransportResponse):
                transport = response
            else:
                raise TypeError("provider returned an unsupported response")
            if transport.tool_calls_present or transport.refusal_present or transport.finish_reason == "tool_calls":
                raise AgnesResponseContractError("synthesis_provider_invalid")
            if transport.text.strip():
                return LLMResponse(transport.text, transport.model, self.provider_id)
            if attempt == 1:
                raise AgnesResponseContractError("synthesis_provider_invalid")
        raise AgnesResponseContractError("synthesis_provider_invalid")
