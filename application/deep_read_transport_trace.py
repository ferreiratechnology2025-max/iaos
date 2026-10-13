"""Endpoint-level diagnostics for the frozen Computer read primitive."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


@dataclass
class _Operation:
    operation_id: str
    operation_kind: str
    sequence: int = 0
    failure_stage: str | None = None


_current: ContextVar[_Operation | None] = ContextVar("deep_read_transport_operation", default=None)


class deep_read_operation:
    """Explicit operation context; no temporal inference is used for correlation."""
    def __init__(self, operation_id: str, operation_kind: str = "deep_read") -> None:
        self._operation = _Operation(operation_id, operation_kind)
        self._token: Token | None = None

    def __enter__(self):
        self._token = _current.set(self._operation)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _current.reset(self._token)
        return False


class TracingTransport(httpx.BaseTransport):
    """Transparent HTTP transport decorator.

    It delegates request handling and response objects unchanged.  It records
    endpoint/status metadata only and never reads or rewrites response bodies.
    """
    def __init__(self, wrapped: httpx.BaseTransport, trace_sink: Any) -> None:
        self._wrapped = wrapped
        self._trace_sink = trace_sink

    def _emit(self, event_type: str, operation: _Operation, **fields: Any) -> None:
        try:
            self._trace_sink.emit({"event_type": event_type, "operation_id": operation.operation_id,
                                   "operation_kind": operation.operation_kind, **fields})
        except Exception:
            # Existing runtime trace policy is non-authoritative during a run.
            return

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        operation = _current.get()
        if operation is None:
            return self._wrapped.handle_request(request)
        operation.sequence += 1
        endpoint = request.url.path
        operation._last_endpoint = endpoint
        self._emit("COMPUTER_HTTP_REQUEST", operation, endpoint=endpoint,
                   sequence=operation.sequence, method=request.method)
        try:
            response = self._wrapped.handle_request(request)
        except Exception as exc:
            operation.failure_stage = {
                "/snapshot": "TRANSPORT_SNAPSHOT",
                "/navigate": "TRANSPORT_NAVIGATE",
                "/run": "TRANSPORT_RUN",
            }.get(endpoint, "TRANSPORT_OTHER")
            self._emit("COMPUTER_HTTP_FAILURE", operation, endpoint=endpoint,
                       sequence=operation.sequence, exception_class=type(exc).__name__,
                       failure_code="transport_request_failed")
            raise
        self._emit("COMPUTER_HTTP_RESULT", operation, endpoint=endpoint,
                   sequence=operation.sequence, success=True, http_status=response.status_code,
                   response_shape_valid=(200 <= response.status_code < 400))
        return response

    def close(self) -> None:
        self._wrapped.close()


def make_traced_transport(trace_sink: Any) -> TracingTransport:
    """Use the same httpx default transport class with additive decoration."""
    return TracingTransport(httpx.HTTPTransport(), trace_sink)


def classify_read_failure(operation: _Operation | None, read_error: str) -> str:
    """Prefer an observed transport phase; otherwise admit the frozen boundary."""
    if operation is not None and operation.failure_stage:
        return operation.failure_stage
    return "OPAQUE_READ_PAGE"
