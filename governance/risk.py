"""Risk classification from the closed registry. Unknown -> HIGH (fail closed)."""
from __future__ import annotations

from typing import Mapping

from .contract import ACTION_REGISTRY, ActionSpec, CanonicalAction, RiskLevel


def assess(
    canon: CanonicalAction, registry: Mapping[str, ActionSpec] = ACTION_REGISTRY
) -> RiskLevel:
    spec = registry.get(canon.action)
    if spec is None:
        return RiskLevel.HIGH
    return spec.risk
