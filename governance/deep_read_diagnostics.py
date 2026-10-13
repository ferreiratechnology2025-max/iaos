"""Synthetic deep-read phase diagnostics, isolated from frozen V1.4 modules."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class DeepReadDiagnosticFailure(Exception):
    def __init__(self, stage: str, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.stage, self.code, self.detail = stage, code, detail


@dataclass(frozen=True)
class DeepReadDiagnosticResult:
    success: bool
    value: Any = None
    failure_stage: str | None = None
    failure_code: str | None = None


def _digest_content(content: Any) -> str | None:
    if content is None:
        return None
    raw = content if isinstance(content, bytes) else str(content).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()


def run_deep_read_diagnostics(*, source_id: str, expected_canonical_url: str,
                              attempted_url: str, navigate: Callable[[str], Mapping[str, Any]],
                              observe: Callable[[Mapping[str, Any]], Any],
                              bind: Callable[[str, str], bool], extract: Callable[[Any], Any],
                              restore: Callable[[str], Any], emit: Callable[[Mapping[str, Any]], None]) -> DeepReadDiagnosticResult:
    """Execute each read phase once against injected synthetic operations."""
    def event(kind: str, **fields: Any) -> None:
        emit({"event_type": kind, **fields})

    event("DEEP_READ_BEGIN", source_id=source_id, expected_canonical_url=expected_canonical_url)
    navigation = None
    page = None
    value = None
    failure: DeepReadDiagnosticFailure | None = None
    try:
        event("DEEP_READ_NAVIGATION", attempted_url=attempted_url, navigation_started=True, navigation_result="START")
        navigation = navigate(attempted_url)
        event("DEEP_READ_NAVIGATION", attempted_url=attempted_url, navigation_started=True,
              navigation_result="PASS", actual_final_url=str(navigation.get("url", "")))
    except DeepReadDiagnosticFailure as exc:
        failure = exc
    except Exception as exc:
        failure = DeepReadDiagnosticFailure("NAVIGATION", "navigation_failed", type(exc).__name__)

    if failure is None:
        try:
            event("DEEP_READ_OBSERVATION", actual_final_url=str(navigation.get("url", "")),
                  observation_started=True, observation_result="START")
            page = observe(navigation)
            text = page.get("text", "") if isinstance(page, Mapping) else page
            event("DEEP_READ_OBSERVATION", actual_final_url=str(navigation.get("url", "")),
                  observation_started=True, observation_result="PASS", content_present=bool(text),
                  content_length=len(text) if text is not None else 0, content_digest=_digest_content(text))
        except DeepReadDiagnosticFailure as exc:
            failure = exc
        except Exception as exc:
            failure = DeepReadDiagnosticFailure("OBSERVATION", "observation_failed", type(exc).__name__)

    if failure is None:
        actual_url = str(navigation.get("url", ""))
        try:
            matched = bool(bind(expected_canonical_url, actual_url))
        except Exception as exc:
            matched = False
            failure = DeepReadDiagnosticFailure("BINDING", "binding_failed", type(exc).__name__)
        event("DEEP_READ_BINDING", canonical_match=matched, source_binding_valid=matched)
        if failure is None and not matched:
            failure = DeepReadDiagnosticFailure("BINDING", "canonical_url_mismatch")

    if failure is None:
        try:
            value = extract(page)
        except DeepReadDiagnosticFailure as exc:
            failure = exc
        except Exception as exc:
            failure = DeepReadDiagnosticFailure("EXTRACTION", "deep_fact_extraction_failed", type(exc).__name__)

    event("DEEP_READ_RESTORE", restore_required=True, restore_attempted=True,
          restore_target=expected_canonical_url, restore_result="START")
    try:
        restore(expected_canonical_url)
        event("DEEP_READ_RESTORE", restore_required=True, restore_attempted=True,
              restore_target=expected_canonical_url, restore_result="PASS")
    except DeepReadDiagnosticFailure as exc:
        if failure is None or failure.stage == "RESTORE":
            failure = exc
        event("DEEP_READ_RESTORE", restore_required=True, restore_attempted=True,
              restore_target=expected_canonical_url, restore_result="FAIL",
              restore_exception_class=type(exc).__name__, restore_exception_code=exc.code)
    except Exception as exc:
        if failure is None:
            failure = DeepReadDiagnosticFailure("RESTORE", "restore_failed", type(exc).__name__)
        event("DEEP_READ_RESTORE", restore_required=True, restore_attempted=True,
              restore_target=expected_canonical_url, restore_result="FAIL",
              restore_exception_class=type(exc).__name__, restore_exception_code="restore_failed")

    if failure is not None:
        event("DEEP_READ_END", success=False, failure_stage=failure.stage, failure_code=failure.code)
        return DeepReadDiagnosticResult(False, failure_stage=failure.stage, failure_code=failure.code)
    event("DEEP_READ_END", success=True, failure_stage=None, failure_code=None)
    return DeepReadDiagnosticResult(True, value=value)
