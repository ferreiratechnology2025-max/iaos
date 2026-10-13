"""Policy: decides on a *canonical* action. Never sees raw paths."""
from __future__ import annotations

from pathlib import Path

from .contract import CanonicalAction, Decision, PolicyDecision

AUTHORITATIVE_SOURCES = frozenset({"user", "agent"})
DATA_SOURCES = frozenset({"file_content", "tool_output"})


def _within(root: str, path: str) -> bool:
    return Path(path) == Path(root) or Path(root) in Path(path).parents


class Policy:
    def __init__(self, root: str) -> None:
        self._root = root

    def evaluate(self, canon: CanonicalAction) -> PolicyDecision:
        if not isinstance(canon, CanonicalAction):
            raise TypeError("Policy only accepts CanonicalAction")
        prov = canon.provenance
        if prov is None:
            return PolicyDecision(Decision.DENY, "provenance_missing")
        if prov.source in DATA_SOURCES:
            return PolicyDecision(Decision.DENY, "data_has_no_authority")
        if prov.source not in AUTHORITATIVE_SOURCES:
            return PolicyDecision(Decision.DENY, "provenance_unknown_source")
        if prov.intent_id != canon.intent_id:
            return PolicyDecision(Decision.DENY, "provenance_intent_mismatch")
        if not _within(self._root, canon.resource):
            return PolicyDecision(Decision.DENY, "resource_outside_root")
        return PolicyDecision(Decision.ALLOW, "policy_ok")
