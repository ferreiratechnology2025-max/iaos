import pytest

from application.daily_brief import (
    DailyBriefFailure,
    DailyBriefSynthesizer,
    DailyBriefToolResult,
    normalize_synthesis_summary,
    validate_synthesis_summary,
)
from cognitive.contracts import DailyBriefRequest
from cognitive.provider import AgnesProvider, LLMResponse


def _request():
    return DailyBriefRequest("America/Sao_Paulo", {"after": "a", "before": "b"}, {"after": "a", "before": "b"}, 1, ("q",), 1)


def _reads():
    return [DailyBriefToolResult(action, "x-" + action, "d", {}, "DATA_ONLY", "TOOL_RESULT") for action in ("calendar.read", "email.read", "web.search")]


def test_known_leading_title_and_date_are_removed_but_body_date_is_preserved():
    value = normalize_synthesis_summary("\n# Resumo Diário — 12 de junho de 2025\n\nAgenda: evento em 15 de outubro.")
    assert value == "Agenda: evento em 15 de outubro."
    validate_synthesis_summary(value)


def test_multiple_leading_presentation_lines_are_removed():
    value = normalize_synthesis_summary("## Daily Brief\nJune 12, 2025 (UTC)\n\nConteúdo em português.")
    assert value == "Conteúdo em português."


def test_unrecognized_leading_envelope_fails_without_guessing_content():
    with pytest.raises(ValueError, match="synthesis_contract_invalid"):
        validate_synthesis_summary("Morning Digest — June 12, 2025\nConteúdo")


def test_invalid_synthesis_is_failed_without_second_provider_request():
    calls = []
    provider = AgnesProvider(lambda prompt: (calls.append(prompt) or LLMResponse("# Resumo Diário — 12 de junho de 2025", "fake", "agnes")))
    with pytest.raises(DailyBriefFailure) as failure:
        DailyBriefSynthesizer(provider).synthesize(_request(), _reads())
    assert failure.value.failure_class == "synthesis_contract_invalid"
    assert len(calls) == 1
