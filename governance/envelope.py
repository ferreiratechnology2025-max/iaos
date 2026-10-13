"""ExecutionEnvelope & authorization correspondence (Phase 0 contract).

Three separate properties, never collapsed into one predicate:

  1. ``authorizes_static(A, E)``  pure, decidable BEFORE the effect  (this module)
  2. ``consume_once(A)``           atomic, stateful                   (consumption.py)
  3. ``effect_conforms(E, A, R)``  audit of what really happened      (this module)

``E`` (ExecutionEnvelope) is the ONLY execution representation the IAOS adapter
hands to the AEP. Its schema is CLOSED: any unknown field is INVALID_ENVELOPE.

Authority-bearing fields (Phase 0):   opcode, authorized_args, resource_binding_id
Correlation-only fields (grant none): execution_id, authorization_id, intent_id,
                                      authorization_digest, schema_version

PHASE 0 DECISIONS (frozen here):
  * authorization records are immutable; no TTL / expiration; no revocation
    after issue. ``static_mismatch`` therefore reads no clock and no state.
  * ``authorization_digest`` = SHA-256(JCS(projection(A))). It detects
    divergence between what the adapter sent and what the registry holds. It is
    NOT authentication: hash != signature.
  * ONE canonical serialization (RFC 8785 / JCS, restricted domain) is used for
    both the digest and the args equality. There is no second serializer.

Canonical domain: object, array, string, integer (|n| <= 2**53-1), boolean,
null. Floats are forbidden (``1`` vs ``1.0`` ambiguity); lone surrogates and
duplicate object keys are forbidden.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

SCHEMA_VERSION = 1
CARDINALITY_SINGLE_USE = "SINGLE_USE"

Args = Tuple[Tuple[str, str], ...]


class EnvelopeRefused(Exception):
    """Pre-effect refusal. ``code`` is a stable reason. No effect happened and,
    unless the code is ``replay_denied``, nothing was consumed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JCSDomainError(ValueError):
    """Value outside the supported canonical JSON domain."""


# ---- canonical serialization (RFC 8785 / JCS, restricted domain) -----------------

_MAX_SAFE_INT = 2 ** 53 - 1
_ESC = {0x22: '\\"', 0x5C: "\\\\", 0x08: "\\b", 0x0C: "\\f", 0x0A: "\\n", 0x0D: "\\r", 0x09: "\\t"}


def _enc_str(s: str) -> str:
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise JCSDomainError("lone_surrogate")
        if cp in _ESC:
            out.append(_ESC[cp])
        elif cp < 0x20:
            out.append("\\u%04x" % cp)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _enc(o: Any) -> str:
    if o is None:
        return "null"
    if o is True:
        return "true"
    if o is False:
        return "false"
    if isinstance(o, int):
        if abs(o) > _MAX_SAFE_INT:
            raise JCSDomainError("integer_out_of_range")
        return str(int(o))
    if isinstance(o, float):
        raise JCSDomainError("float_forbidden")
    if isinstance(o, str):
        return _enc_str(o)
    if isinstance(o, (list, tuple)):
        return "[" + ",".join(_enc(x) for x in o) + "]"
    if isinstance(o, Mapping):
        for k in o:
            if not isinstance(k, str):
                raise JCSDomainError("non_string_key")
        keys = sorted(o, key=lambda k: k.encode("utf-16-be", "surrogatepass"))  # UTF-16 code units
        return "{" + ",".join(_enc_str(k) + ":" + _enc(o[k]) for k in keys) + "}"
    raise JCSDomainError("unsupported_type:%s" % type(o).__name__)


def canonical_bytes(obj: Any) -> bytes:
    """The single canonical serialization. UTF-8 JCS bytes."""
    return _enc(obj).encode("utf-8")


def loads_canonical_domain(text: str) -> Any:
    """Parse JSON text, rejecting duplicate keys, floats and non-finite numbers."""

    def pairs(items):
        d: Dict[str, Any] = {}
        for k, v in items:
            if k in d:
                raise JCSDomainError("duplicate_key")
            d[k] = v
        return d

    def bad_const(name):
        raise JCSDomainError("non_finite_number")

    def no_float(text_):
        raise JCSDomainError("float_forbidden")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_const, parse_float=no_float)


# ---- authority digest ------------------------------------------------------------

def _pairs(args: Any) -> list:
    return [list(p) for p in args]


def authority_digest(
    authorization_id: str,
    intent_id: str,
    action: str,
    canonical_args: Any,
    resource_binding_id: Optional[str],
    schema_version: int = SCHEMA_VERSION,
    execution_cardinality: str = CARDINALITY_SINGLE_USE,
) -> str:
    """SHA-256(JCS(authority_digest_projection(A))). The ONLY digest code path:
    the AuthorizationRecord and the adapter both call this function."""
    projection = {
        "schema_version": schema_version,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "action": action,
        "canonical_args": _pairs(canonical_args),
        "resource_binding_id": resource_binding_id,
        "execution_cardinality": execution_cardinality,
    }
    return hashlib.sha256(canonical_bytes(projection)).hexdigest()


# ---- ExecutionEnvelope (closed schema) --------------------------------------------

@dataclass(frozen=True)
class ExecutionEnvelope:
    schema_version: int
    execution_id: str
    authorization_id: str
    intent_id: str
    opcode: str
    authorized_args: Args
    resource_binding_id: Optional[str]
    authorization_digest: str

    def to_wire(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "authorization_id": self.authorization_id,
            "intent_id": self.intent_id,
            "opcode": self.opcode,
            "authorized_args": _pairs(self.authorized_args),
            "resource_binding_id": self.resource_binding_id,
            "authorization_digest": self.authorization_digest,
        }


_FIELDS = (
    "schema_version",
    "execution_id",
    "authorization_id",
    "intent_id",
    "opcode",
    "authorized_args",
    "resource_binding_id",
    "authorization_digest",
)
_FIELD_SET = frozenset(_FIELDS)


def _str(v: Any) -> str:
    if type(v) is not str or not v:
        raise EnvelopeRefused("invalid_envelope")
    return v


def validate_envelope(candidate: Any) -> ExecutionEnvelope:
    """Closed-schema validation (pipeline step 2). Accepts an ExecutionEnvelope
    or its wire mapping; anything with an unknown / missing field is refused.
    Nothing here reads registry, ledger or filesystem."""
    if isinstance(candidate, ExecutionEnvelope):
        raw = dict(vars(candidate))
    elif isinstance(candidate, Mapping):
        raw = dict(candidate)
    else:
        raise EnvelopeRefused("invalid_envelope")
    if set(raw) != _FIELD_SET:
        raise EnvelopeRefused("invalid_envelope")
    version = raw["schema_version"]
    if type(version) is not int:
        raise EnvelopeRefused("invalid_envelope")
    if version != SCHEMA_VERSION:
        raise EnvelopeRefused("unsupported_schema")
    args_in = raw["authorized_args"]
    if not isinstance(args_in, (list, tuple)):
        raise EnvelopeRefused("invalid_envelope")
    pairs = []
    for p in args_in:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise EnvelopeRefused("invalid_envelope")  # unrecognized nested shape
        if type(p[0]) is not str or type(p[1]) is not str:
            raise EnvelopeRefused("invalid_envelope")
        pairs.append((p[0], p[1]))
    binding = raw["resource_binding_id"]
    if binding is not None and (type(binding) is not str or not binding):
        raise EnvelopeRefused("invalid_envelope")
    return ExecutionEnvelope(
        schema_version=version,
        execution_id=_str(raw["execution_id"]),
        authorization_id=_str(raw["authorization_id"]),
        intent_id=_str(raw["intent_id"]),
        opcode=_str(raw["opcode"]),
        authorized_args=tuple(pairs),
        resource_binding_id=binding,
        authorization_digest=_str(raw["authorization_digest"]),
    )


# ---- authorizes_static: PURE ------------------------------------------------------

def static_mismatch(record: Any, envelope: ExecutionEnvelope) -> Optional[str]:
    """First failing condition, or None. PURE: both arguments are immutable
    values already retrieved; no lookup, no mutation, no consumption, no
    filesystem, no clock, no external call. No partial match is authorization."""
    if envelope.schema_version != SCHEMA_VERSION or record.schema_version != SCHEMA_VERSION:
        return "unsupported_schema"
    if record.execution_cardinality != CARDINALITY_SINGLE_USE:
        return "cardinality_unsupported"
    if envelope.authorization_id != record.authorization_id:
        return "authorization_id_mismatch"
    if envelope.intent_id != record.intent_id:
        return "intent_mismatch"
    if envelope.opcode != record.action:
        return "opcode_mismatch"
    if canonical_bytes(_pairs(envelope.authorized_args)) != canonical_bytes(_pairs(record.canonical_args)):
        return "args_mismatch"
    if envelope.resource_binding_id != record.resource_binding_id:
        return "binding_mismatch"
    if envelope.authorization_digest != record.authority_digest():
        return "digest_mismatch"
    return None


def authorizes_static(record: Any, envelope: ExecutionEnvelope) -> bool:
    return static_mismatch(record, envelope) is None


# ---- effect_conforms: audit predicate (NOT the control) ---------------------------

def effect_conforms(envelope: ExecutionEnvelope, record: Any, observation: Any) -> bool:
    """True iff the observed effect used exactly the bound resource.

    This is an AUDIT predicate. Safety is guaranteed by construction (the
    executor reads the handle bound during authorization and never re-opens a
    path); this proves what happened. It is evaluated on observables only.
    """
    if observation is None:
        return False
    return (
        observation.execution_id == envelope.execution_id
        and observation.resource_binding_id == envelope.resource_binding_id
        and observation.path_open_calls_during_exec == 0
        and observation.bound_handle_reads == 1
        and observation.observed_resource_identity is not None
        and observation.observed_resource_identity == record.resource_identity
    )
