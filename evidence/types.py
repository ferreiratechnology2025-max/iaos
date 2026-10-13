"""Neutral evidence DTOs. Standard library only; no decision logic.

Shared by the exporter (producer side) and the auditor (consumer side) so that neither
has to import the other. ``None`` means "not observed" and must never be replaced by a
safe-looking default.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class ResourceBindingEvidence:
    """Facts about one fs.read execution. ``None`` means "not observed"."""

    execution_id: Optional[str] = None
    authorization_id: Optional[str] = None
    resource_binding_id: Optional[str] = None
    authorized_resource_identity: Optional[str] = None
    observed_resource_identity: Optional[str] = None
    path_open_calls_during_exec: Optional[int] = None
    bound_handle_reads: Optional[int] = None
    effect_succeeded: Optional[bool] = None
    # where the facts were observed (needed to decide whether a metric applies)
    platform: Optional[str] = None
    filesystem: Optional[str] = None
    action: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourceBindingEvidence":
        names = {f.name for f in dataclasses.fields(cls)}
        extra = set(data) - names
        if extra:
            raise ValueError(f"unknown evidence fields: {sorted(extra)}")
        return cls(**dict(data))


# ---- authorization correspondence ------------------------------------------------------

CORRESPONDENCE_SCHEMA_VERSION = 1


def _pairs_or_none(value: Any) -> Any:
    """Normalize args to a tuple of 2-tuples; anything else is kept as-is so a metric can call it malformed."""
    if isinstance(value, (list, tuple)):
        return tuple(tuple(p) if isinstance(p, (list, tuple)) else p for p in value)
    return value


@dataclass(frozen=True)
class AuthorizedAuthority:
    """What Governance authorized (read from the immutable authorization record). ``None`` = not observed."""

    authorization_id: Optional[str] = None
    intent_id: Optional[str] = None
    opcode: Optional[str] = None
    authorized_args: Any = None
    resource_binding_id: Optional[str] = None
    authorization_digest: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorized_args", _pairs_or_none(self.authorized_args))


@dataclass(frozen=True)
class ExecutedAuthority:
    """What reached the effect boundary and was executed (observed by the runtime). ``None`` = not observed."""

    authorization_id: Optional[str] = None
    intent_id: Optional[str] = None
    opcode: Optional[str] = None
    executed_args: Any = None
    resource_binding_id: Optional[str] = None
    authorization_digest: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "executed_args", _pairs_or_none(self.executed_args))


@dataclass(frozen=True)
class AuthorizationCorrespondenceEvidence:
    """Both sides of one execution. It is evidence only: no decision, no claim, no authority."""

    schema_version: Optional[int] = None
    execution_id: Optional[str] = None
    authorized: Optional[AuthorizedAuthority] = None
    executed: Optional[ExecutedAuthority] = None

    @property
    def authorization_id(self) -> Optional[str]:
        """Subject of the evidence (used only for correlation with the claim)."""
        return None if self.authorized is None else self.authorized.authorization_id

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AuthorizationCorrespondenceEvidence":
        names = {f.name for f in dataclasses.fields(cls)}
        extra = set(data) - names
        if extra:
            raise ValueError(f"unknown evidence fields: {sorted(extra)}")
        raw = dict(data)
        for key, kind in (("authorized", AuthorizedAuthority), ("executed", ExecutedAuthority)):
            side = raw.get(key)
            if side is not None:
                side_names = {f.name for f in dataclasses.fields(kind)}
                bad = set(side) - side_names
                if bad:
                    raise ValueError(f"unknown {key} fields: {sorted(bad)}")
                raw[key] = kind(**dict(side))
        return cls(**raw)
