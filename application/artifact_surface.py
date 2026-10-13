"""Trusted semantic projection of a product's artifact surface.

This is an opt-in V1.6 composition layer.  It resolves semantic targets only
from the current trusted accessibility observation; cognition never supplies
or invents refs, and the verifier marker is not exposed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, Mapping, Optional, Sequence

from application.aria_scope import analyze
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.source_facts import DeepReadFact, SourceFact


@dataclass(frozen=True)
class ArtifactTarget:
    semantic_role: str
    ref: str


@dataclass(frozen=True)
class ArtifactSourceValue:
    publisher: str
    source: int
    field: str = "deep_fact"


@dataclass(frozen=True)
class ArtifactSurface:
    title_target: Optional[ArtifactTarget]
    content_target: Optional[ArtifactTarget]
    preview_region_ref: Optional[str]
    preview_region_count: int
    source_values: tuple[ArtifactSourceValue, ...] = ()

    @property
    def uniquely_resolved_preview(self) -> bool:
        return self.preview_region_count == 1 and self.preview_region_ref is not None

    @property
    def complete(self) -> bool:
        return self.title_target is not None and self.content_target is not None and self.uniquely_resolved_preview

    @property
    def trusted_refs(self) -> FrozenSet[str]:
        refs = {target.ref for target in (self.title_target, self.content_target) if target is not None}
        if self.preview_region_ref is not None:
            refs.add(self.preview_region_ref)
        return frozenset(refs)

    def prompt_projection(self) -> str:
        """Cognition-facing projection; never includes the verifier marker."""
        lines = ["ARTIFACT SURFACE (trusted current observation):"]
        for label, target in (("title", self.title_target), ("content", self.content_target)):
            if target is None:
                lines.append(f"- {label}: UNRESOLVED")
            else:
                lines.append(f"- {label}: ref={target.ref} semantic_role={target.semantic_role}")
        if self.uniquely_resolved_preview:
            lines.append(f"- preview: ref={self.preview_region_ref} uniquely_resolved=YES")
        else:
            lines.append(f"- preview: uniquely_resolved=NO region_count={self.preview_region_count}")
        if self.source_values:
            lines.append("- available source values:")
            for value in self.source_values:
                lines.append(f"  - publisher={value.publisher} source={value.source} field={value.field}")
        else:
            lines.append("- available source values: none")
        return "\n".join(lines)


_TITLE_NAMES = frozenset(("title", "page title", "site title", "heading", "page heading"))
_CONTENT_NAMES = frozenset(("body", "content", "page content", "body content", "editor", "page editor"))
_PREVIEW_NAMES = frozenset(("preview", "artifact preview", "preview region", "site preview"))


def _norm(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _unique_target(elements: Sequence[ComputerElement], names: FrozenSet[str], semantic_role: str) -> Optional[ArtifactTarget]:
    matches = [element for element in elements if _norm(element.name) in names and element.role in {"textbox", "searchbox"}]
    return ArtifactTarget(semantic_role, matches[0].ref) if len(matches) == 1 else None


def _preview_refs(observation: ComputerPageObservation) -> list[str]:
    report = analyze(observation.aria_snapshot)
    return [ref for ref, scope in report.scopes.items()
            if scope.role in {"region", "iframe", "group"} and _norm(scope.name) in _PREVIEW_NAMES]


def resolve_artifact_surface(observation: ComputerPageObservation,
                             facts: Sequence[SourceFact] = (),
                             deep_facts: Sequence[DeepReadFact] = (),
                             publisher_resolver: Optional[Callable[[str], Optional[str]]] = None) -> ArtifactSurface:
    """Resolve title/content/preview and admitted deep-fact bindings from one observation."""
    preview_refs = _preview_refs(observation)
    source_values: list[ArtifactSourceValue] = []
    by_source_id = {fact.source_id: index for index, fact in enumerate(facts, 1)}
    for deep in deep_facts:
        source = by_source_id.get(deep.search_execution_id)
        publisher = publisher_resolver(deep.source_url) if publisher_resolver is not None else None
        if source is not None and publisher is not None and publisher not in {value.publisher for value in source_values}:
            source_values.append(ArtifactSourceValue(publisher, source))
    return ArtifactSurface(
        _unique_target(observation.elements, _TITLE_NAMES, "title"),
        _unique_target(observation.elements, _CONTENT_NAMES, "content"),
        preview_refs[0] if len(preview_refs) == 1 else None,
        len(preview_refs),
        tuple(source_values),
    )


def verifier_observation(observation: ComputerPageObservation, surface: ArtifactSurface) -> ComputerPageObservation:
    """Map one real preview region to the verifier representation deterministically."""
    marker = "V1_6_PREVIEW\n" if surface.uniquely_resolved_preview else ""
    return ComputerPageObservation(observation.page_url, observation.page_title, observation.browser_session_id,
                                   observation.snapshot_id, observation.elements,
                                   marker + observation.aria_snapshot, observation.challenge)


def artifact_surface_trace(surface: ArtifactSurface) -> Mapping[str, object]:
    return {
        "artifact_surface_complete": "YES" if surface.complete else "NO",
        "title_target_ref": surface.title_target.ref if surface.title_target else None,
        "content_target_ref": surface.content_target.ref if surface.content_target else None,
        "preview_region_ref": surface.preview_region_ref,
        "preview_region_count": surface.preview_region_count,
        "source_value_publishers": [value.publisher for value in surface.source_values],
    }


__all__ = ["ArtifactTarget", "ArtifactSourceValue", "ArtifactSurface", "resolve_artifact_surface",
           "verifier_observation", "artifact_surface_trace"]
