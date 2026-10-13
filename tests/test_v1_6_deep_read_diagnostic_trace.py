import pytest

from governance.deep_read_diagnostics import DeepReadDiagnosticFailure, run_deep_read_diagnostics

URL = "https://www.nasa.gov/source"


def _run(*, failure=None, actual_url=URL):
    events = []

    def navigate(url):
        if failure == "navigation":
            raise DeepReadDiagnosticFailure("NAVIGATION", "navigation_failed")
        return {"url": actual_url}

    def observe(_navigation):
        if failure == "observation":
            raise DeepReadDiagnosticFailure("OBSERVATION", "observation_failed")
        return {"text": "Artemis I Nov. 16, 2022"}

    def extract(page):
        if failure == "extraction":
            raise DeepReadDiagnosticFailure("EXTRACTION", "deep_fact_extraction_failed")
        return page["text"]

    def restore(_url):
        if failure == "restore":
            raise DeepReadDiagnosticFailure("RESTORE", "restore_failed")

    result = run_deep_read_diagnostics(
        source_id="search-1", expected_canonical_url=URL, attempted_url=URL,
        navigate=navigate, observe=observe, bind=lambda expected, actual: expected == actual,
        extract=extract, restore=restore, emit=events.append,
    )
    return result, events


@pytest.mark.parametrize("failure,stage", [
    ("navigation", "NAVIGATION"), ("observation", "OBSERVATION"),
    ("extraction", "EXTRACTION"), ("restore", "RESTORE"),
])
def test_each_failure_has_an_attributable_phase(failure, stage):
    result, events = _run(failure=failure)
    assert not result.success and result.failure_stage == stage
    assert events[0]["event_type"] == "DEEP_READ_BEGIN"
    assert events[-1]["event_type"] == "DEEP_READ_END"
    assert events[-1]["failure_stage"] == stage


def test_binding_mismatch_is_attributable():
    result, events = _run(actual_url="https://example.invalid/source")
    assert not result.success and result.failure_stage == "BINDING"
    binding = [e for e in events if e["event_type"] == "DEEP_READ_BINDING"][-1]
    assert binding["canonical_match"] is False
    assert binding["source_binding_valid"] is False


def test_successful_read_has_complete_trace_without_page_content():
    result, events = _run()
    assert result.success and result.value == "Artemis I Nov. 16, 2022"
    assert events[-1] == {"event_type": "DEEP_READ_END", "success": True,
                          "failure_stage": None, "failure_code": None}
    observation = [e for e in events if e["event_type"] == "DEEP_READ_OBSERVATION"
                   and e.get("observation_result") == "PASS"][0]
    assert observation["content_length"] > 0 and observation["content_digest"]
    assert "text" not in observation


def test_trace_is_incremental_before_observation_failure_completes():
    events = []
    checkpoint = []

    def navigate(url):
        checkpoint.extend(events)
        return {"url": url}

    def observe(_navigation):
        raise DeepReadDiagnosticFailure("OBSERVATION", "observation_failed")

    result = run_deep_read_diagnostics(
        source_id="search-1", expected_canonical_url=URL, attempted_url=URL,
        navigate=navigate, observe=observe, bind=lambda _a, _b: True,
        extract=lambda page: page, restore=lambda _url: None, emit=events.append,
    )
    assert not result.success
    assert any(event["event_type"] == "DEEP_READ_BEGIN" for event in checkpoint)
    assert any(event["event_type"] == "DEEP_READ_NAVIGATION" and event.get("navigation_result") == "START"
               for event in checkpoint)


def test_default_v14_modules_are_not_the_diagnostic_implementation():
    from governance.bound_read import SearchBoundReadExecutor
    assert not hasattr(SearchBoundReadExecutor, "set_trace_sink")
