"""Canonicalization: Proposal -> CanonicalAction, before Policy ever runs.

Policy only sees the resolved path. ``ResourceResolver`` is explicit so the
symlink behaviour is a visible, testable component.

RESOURCE BINDING (open-first): for actions that bind identity (fs.read) the
resolver OPENS the file first (``handle_binding``), and the canonical resource,
identity and binding id all come from that open handle. There is no realpath /
lstat before the open, so there is no authorization-time window between
"resolve" and "open". Policy then judges the final path OF THE OPEN OBJECT.
Actions that bind no identity (fs.write, not executable in Phase 1) still use
the path-based ``resolve``.
"""
from __future__ import annotations

import os
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from .contract import ACTION_REGISTRY, ActionSpec, CanonicalAction, Proposal
from .handle_binding import BindingError, BindingTable


class CanonicalizationError(Exception):
    """Proposal cannot be reduced to a closed, well-formed action. Fail closed."""


class ResourceResolver:
    """Resolves a raw path against a root; binds identity-carrying resources by opening them."""

    def __init__(self, root: str, bindings: Optional[BindingTable] = None) -> None:
        self._root = os.path.realpath(root)
        self._bindings = bindings if bindings is not None else BindingTable()

    @property
    def root(self) -> str:
        return self._root

    @property
    def bindings(self) -> BindingTable:
        return self._bindings

    def resolve(self, raw: str) -> str:
        return os.path.realpath(os.path.join(self._root, raw))

    def bind(self, raw: str) -> Tuple[str, str, str]:
        """Open-first: (final_path, identity, binding_id) taken from the open handle."""
        try:
            binding_id, final_path, identity = self._bindings.open(os.path.join(self._root, raw))
        except BindingError as exc:
            raise CanonicalizationError(str(exc))
        return final_path, identity, binding_id

    def release(self, binding_id: Optional[str]) -> None:
        self._bindings.release(binding_id)


class Canonicalizer:
    def __init__(
        self,
        resolver: ResourceResolver,
        registry: Mapping[str, ActionSpec] = ACTION_REGISTRY,
    ) -> None:
        if not isinstance(registry, MappingProxyType):
            raise TypeError("registry must be a read-only mapping")
        self._resolver = resolver
        self._registry = registry

    def canonicalize(self, proposal: Proposal) -> CanonicalAction:
        if not isinstance(proposal, Proposal):
            raise CanonicalizationError("not_a_proposal")
        spec = self._registry.get(proposal.action)
        if spec is None:
            raise CanonicalizationError("unknown_action")
        expected = {spec.resource_arg, *spec.extra_args}
        got = set(proposal.args.keys())
        if got != expected:
            raise CanonicalizationError("args_schema_mismatch")
        for key, value in proposal.args.items():
            if not isinstance(value, str):
                raise CanonicalizationError("arg_not_str:" + key)
        raw = proposal.args[spec.resource_arg]
        if not raw or "\x00" in raw:
            raise CanonicalizationError("bad_resource")
        extra = tuple(sorted((k, proposal.args[k]) for k in spec.extra_args))
        if spec.binds_identity:
            resource, identity, binding_id = self._resolver.bind(raw)
        else:
            resource, identity, binding_id = self._resolver.resolve(raw), None, None
        return CanonicalAction(
            intent_id=proposal.intent_id,
            action=proposal.action,
            opcode=spec.opcode,
            resource=resource,
            args=extra,
            provenance=proposal.provenance,
            resource_identity=identity,
            resource_binding_id=binding_id,
        )

    def release(self, binding_id: Optional[str]) -> None:
        """Drop a binding that did not end in an AuthorizedAction."""
        self._resolver.release(binding_id)
