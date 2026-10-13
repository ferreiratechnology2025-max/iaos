from application.daily_brief_history import DailyBriefRun
from application.delivery import format_daily_brief_email


def _run(summary="Resumo em português"):
    return DailyBriefRun(
        run_id="brief-new",
        requested_at="2026-10-07T03:30:34+00:00",
        completed_at="2026-10-07T03:30:41.952447+00:00",
        status="success",
        summary=summary,
        calendar_section='source=calendar.read:x-cal\n{"events": [], "truncated": false}',
        email_section='source=email.read:x-mail\n{"items": [{"subject": "privado"}]}',
        web_section='source=web.search:x-web\n{"results": [{"title": "raw"}]}',
        scout_section="No new Scout findings.",
        source_refs=("calendar.read:x-cal", "email.read:x-mail", "web.search:x-web"),
    )


def test_formatter_uses_completed_at_in_sao_paulo_and_hides_raw_tool_payloads():
    content = format_daily_brief_email(_run("**Resumo**\n\npróximas ações"))
    assert content.subject == "IAOS — Resumo diário — 07/10/2026"
    assert content.body_text.startswith("IAOS — Resumo diário\n7 de outubro de 2026\n")
    assert "RESUMO" in content.body_text
    assert "**" not in content.body_text
    assert "{\"events\"" not in content.body_text
    assert "source=calendar.read" not in content.body_text
    assert "- Agenda" in content.body_text
    assert "- E-mail" in content.body_text
    assert "- Web" in content.body_text


def test_formatter_preserves_unicode_and_does_not_guess_legacy_replacement_characters():
    content = format_daily_brief_email(_run("ação, informação, Goiânia, e-mails, próximas ações"))
    assert "ação" in content.body_text
    assert "Goiânia" in content.body_text
    assert "�" not in content.body_text
    legacy = format_daily_brief_email(_run("Goi�nia"))
    assert "Goi�nia" in legacy.body_text


def test_formatter_rejects_invalid_timestamp_instead_of_inventing_a_date():
    run = _run()
    invalid = run.__class__(**{**run.to_dict(), "completed_at": "not-a-date"})
    try:
        format_daily_brief_email(invalid)
        assert False, "invalid timestamp must fail closed"
    except ValueError as exc:
        assert str(exc) == "invalid_daily_brief_completed_at"
