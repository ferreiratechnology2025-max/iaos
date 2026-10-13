"""Observation of what really happens during EXEC.

``path_open_calls_during_exec`` must observe the REAL boundary that would open a
path, not a spy placed in the adapter. Two independent sources are summed:

  * the interpreter audit hook: every ``open`` event (builtins.open, io.open,
    os.open) raised by the thread that is inside an exec window;
  * ``BindingTable.open_calls``: the product's own open boundary (on Windows the
    native CreateFileW does not raise an ``open`` audit event).

The audit hook is installed once at import and cannot be removed; it only
counts, and only for a thread that opened a window. It decides nothing.
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExecutedAuthorityObservation:
    """The authority that REACHED the effect boundary and was handed to the executor.

    Recorded by the runtime from the (validated) envelope and the executor it really ran: an
    observation only, never an input to any decision. It grants nothing and is never read back
    by the runtime. ``opcode`` is the executor opcode that actually ran."""

    authorization_id: str
    intent_id: str
    opcode: str
    executed_args: Tuple[Tuple[str, str], ...]
    resource_binding_id: Optional[str]
    authorization_digest: str


@dataclass(frozen=True)
class EffectExecutionObservation:
    execution_id: str
    resource_binding_id: Optional[str]
    bound_handle_reads: int
    path_open_calls_during_exec: int
    observed_resource_identity: Optional[str]
    executed: Optional[ExecutedAuthorityObservation] = None


_tls = threading.local()


def _hook(event: str, args: tuple) -> None:
    if event == "open":
        window = getattr(_tls, "window", None)
        if window is not None:
            window.audit_opens += 1


sys.addaudithook(_hook)


class ExecWindow:
    """Context manager: counts ``open`` audit events of the current thread."""

    def __init__(self) -> None:
        self.audit_opens = 0

    def __enter__(self) -> "ExecWindow":
        _tls.window = self
        return self

    def __exit__(self, *exc) -> None:
        _tls.window = None
