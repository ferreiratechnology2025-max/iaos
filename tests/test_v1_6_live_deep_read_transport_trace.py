import httpx
import pytest

from application.computer_runtime import ComputerRuntimeError, OpenBotComputerAdapter
from application.deep_read_transport_trace import (
    TracingTransport, classify_read_failure, deep_read_operation,
)
from application.execution_trace import MemoryExecutionTrace


def _response(request, path, payload, status=200):
    return httpx.Response(status, json=payload, request=request)


def _adapter(handler, trace=None):
    transport = httpx.MockTransport(handler)
    if trace is not None:
        transport = TracingTransport(transport, trace)
    return OpenBotComputerAdapter("https://computer.invalid", "bot", "token", transport=transport)


def _handler(request):
    if request.url.path == "/snapshot":
        return _response(request, request.url.path, {})
    if request.url.path == "/navigate":
        return _response(request, request.url.path, {
            "url": "https://www.nasa.gov/source", "title": "NASA", "text": "Artemis I",
            "truncated": False, "challenge": None,
        })
    if request.url.path == "/run":
        return _response(request, request.url.path, {"run": "session-1"})
    return _response(request, request.url.path, {})


def test_traced_transport_preserves_adapter_result_and_records_phases():
    direct = _adapter(_handler).read_page("https://www.nasa.gov/source")
    trace = MemoryExecutionTrace("transport")
    with deep_read_operation("read-1"):
        traced = _adapter(_handler, trace).read_page("https://www.nasa.gov/source")
    assert (traced.final_url, traced.title, traced.text, traced.truncated) == \
           (direct.final_url, direct.title, direct.text, direct.truncated)
    requests = [e for e in trace.events if e["event_type"] == "COMPUTER_HTTP_REQUEST"]
    assert [e["endpoint"] for e in requests] == ["/snapshot", "/run", "/navigate", "/run"]
    assert all(e["operation_id"] == "read-1" for e in requests)


@pytest.mark.parametrize("failing_path,stage", [
    ("/snapshot", "TRANSPORT_SNAPSHOT"),
    ("/navigate", "TRANSPORT_NAVIGATE"),
    ("/run", "TRANSPORT_RUN"),
])
def test_transport_failures_are_endpoint_attributable(failing_path, stage):
    def handler(request):
        if request.url.path == failing_path:
            raise httpx.ConnectError("boom", request=request)
        return _handler(request)

    trace = MemoryExecutionTrace("transport-failure")
    with deep_read_operation("read-1"):
        with pytest.raises(ComputerRuntimeError):
            _adapter(handler, trace).read_page("https://www.nasa.gov/source")
    failure = [e for e in trace.events if e["event_type"] == "COMPUTER_HTTP_FAILURE"][-1]
    assert failure["endpoint"] == failing_path
    assert classify_read_failure(type("Op", (), {"failure_stage": stage})(), "read_failed") == stage


def test_all_transport_phases_pass_but_frozen_validation_is_opaque():
    def handler(request):
        if request.url.path == "/navigate":
            return _response(request, request.url.path, {
                "url": "https://www.nasa.gov/source", "title": "NASA",
                "truncated": False, "challenge": None,
            })
        return _handler(request)

    trace = MemoryExecutionTrace("opaque")
    with deep_read_operation("read-1"):
        with pytest.raises(ComputerRuntimeError):
            _adapter(handler, trace).read_page("https://www.nasa.gov/source")
    assert not any(e["event_type"] == "COMPUTER_HTTP_FAILURE" for e in trace.events)
    assert classify_read_failure(None, "computer_visible_text_shape") == "OPAQUE_READ_PAGE"


def test_trace_sink_failure_does_not_change_transport_result():
    class BrokenTrace:
        def emit(self, _event):
            raise OSError("trace unavailable")

    with deep_read_operation("read-1"):
        page = _adapter(_handler, BrokenTrace()).read_page("https://www.nasa.gov/source")
    assert page.text == "Artemis I"


def test_live_wiring_uses_existing_trace_and_transport_decorator():
    from scripts import agent_wombat_v16_live as live
    source = open(live.__file__, encoding="utf-8").read()
    assert "make_traced_transport(trace_sink)" in source
    assert "trace_sink=trace" in source
