"""Production evidence exporter: observe -> normalize -> serialize. It never decides.

It does not import the runtime, governance or the auditor: inputs are read by attribute
(duck typing). It does not authorize, execute, evaluate a metric or declare a gate PASS,
and it never fills a missing observable with a safe value: absence stays ``None``.

EVIDENCE_SOURCE_INDEPENDENCE=PENDING: the exporter trusts the runtime's own observables.
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Any, Optional

from .types import (
    CORRESPONDENCE_SCHEMA_VERSION,
    AuthorizationCorrespondenceEvidence,
    AuthorizedAuthority,
    ExecutedAuthority,
    ResourceBindingEvidence,
)


def volume_filesystem(path: Optional[str]) -> Optional[str]:
    """Filesystem name of the volume holding ``path`` (Windows only); ``None`` if unknown."""
    if path is None:
        return None
    if not sys.platform.startswith("win"):
        return None
    root = os.path.splitdrive(os.path.realpath(path))[0] + "\\"
    buf = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, buf, 64)
    if not ok:
        return None
    return buf.value


def export_resource_binding_evidence(result: Any, action: Any, *,
                                     volume_path: Optional[str] = None) -> ResourceBindingEvidence:
    """Build the immutable DTO from an execution result and its AuthorizedAction.

    ``result`` is read for ``ok`` and ``observation``; ``action`` for ``authorization_id``,
    ``resource_identity`` and ``action``. Anything not present is exported as ``None``.
    """
    obs = getattr(result, "observation", None)

    def seen(name: str) -> Any:
        if obs is None:
            return None
        return getattr(obs, name, None)

    return ResourceBindingEvidence(
        execution_id=seen("execution_id"),
        authorization_id=getattr(action, "authorization_id", None),
        resource_binding_id=seen("resource_binding_id"),
        authorized_resource_identity=getattr(action, "resource_identity", None),
        observed_resource_identity=seen("observed_resource_identity"),
        path_open_calls_during_exec=seen("path_open_calls_during_exec"),
        bound_handle_reads=seen("bound_handle_reads"),
        effect_succeeded=getattr(result, "ok", None),
        platform=sys.platform,
        filesystem=volume_filesystem(volume_path),
        action=getattr(action, "action", None),
    )


def _read(obj: Any, name: str) -> Any:
    """Attribute of ``obj``; ``None`` when ``obj`` or the attribute is absent (never a safe default)."""
    if obj is None:
        return None
    return getattr(obj, name, None)


def _authority_digest(authorization: Any) -> Optional[str]:
    """The record's own authority digest. The exporter does not compute digests."""
    fn = _read(authorization, "authority_digest")
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def export_authorization_correspondence_evidence(result: Any,
                                                 authorization: Any) -> AuthorizationCorrespondenceEvidence:
    """Both sides of one execution, as plain immutable facts.

    * authorized side: read from ``authorization`` (the immutable AuthorizationRecord Governance issued:
      ``authorization_id``, ``intent_id``, ``opcode``, ``canonical_args``, ``resource_binding_id`` and its
      own ``authority_digest()``);
    * executed side: read from ``result.observation.executed`` (what the runtime recorded when the
      envelope reached the effect boundary).

    Neither side is derived from the other, from a claim or from a default: absence stays ``None``.
    EVIDENCE_SOURCE_INDEPENDENCE=PENDING: both sides still come from the system's own components.
    """
    obs = _read(result, "observation")
    ran = _read(obs, "executed")
    return AuthorizationCorrespondenceEvidence(
        schema_version=CORRESPONDENCE_SCHEMA_VERSION,
        execution_id=_read(obs, "execution_id"),
        authorized=AuthorizedAuthority(
            authorization_id=_read(authorization, "authorization_id"),
            intent_id=_read(authorization, "intent_id"),
            opcode=_read(authorization, "opcode"),
            authorized_args=_read(authorization, "canonical_args"),
            resource_binding_id=_read(authorization, "resource_binding_id"),
            authorization_digest=_authority_digest(authorization),
        ),
        executed=ExecutedAuthority(
            authorization_id=_read(ran, "authorization_id"),
            intent_id=_read(ran, "intent_id"),
            opcode=_read(ran, "opcode"),
            executed_args=_read(ran, "executed_args"),
            resource_binding_id=_read(ran, "resource_binding_id"),
            authorization_digest=_read(ran, "authorization_digest"),
        ),
    )
