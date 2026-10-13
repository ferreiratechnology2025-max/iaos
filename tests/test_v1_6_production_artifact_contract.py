from application.artifact_surface import resolve_artifact_surface, verifier_observation
from application.computer_runtime import ComputerElement, ComputerPageObservation
from application.source_facts import DeepReadFact, SourceFact
from application.v16_independent_publisher import ESA, NASA, resolve_v16_artifact_surface


def _observation(aria="- region \"Preview\" [ref=p1]\n"):
    return ComputerPageObservation(
        "https://websitewombat.app/build", "Build", "v16", 1,
        (ComputerElement("t1", "textbox", "Page title"),
         ComputerElement("c1", "textbox", "Body content")),
        aria,
    )


def _evidence():
    facts = [
        SourceFact("web.search", "sn", "NASA", NASA.canonical_document, "", "n" * 64),
        SourceFact("web.search", "se", "ESA", ESA.canonical_document, "", "e" * 64),
    ]
    deep = [
        DeepReadFact("search_bound_deep_read", "rn", "sn", NASA.canonical_document,
                     "Artemis I Nov. 16, 2022", False, "a" * 64),
        DeepReadFact("search_bound_deep_read", "re", "se", ESA.canonical_document,
                     "Rosetta 2 March 2004", False, "b" * 64),
    ]
    return facts, deep


def test_surface_is_observation_bound_and_exposes_required_source_values():
    facts, deep = _evidence()
    surface = resolve_v16_artifact_surface(_observation(), facts, deep)
    assert surface.title_target.ref == "t1"
    assert surface.content_target.ref == "c1"
    assert surface.preview_region_ref == "p1"
    assert surface.complete
    assert {(item.publisher, item.source, item.field) for item in surface.source_values} == {
        ("NASA", 1, "deep_fact"), ("ESA", 2, "deep_fact")
    }
    assert surface.trusted_refs == frozenset({"t1", "c1", "p1"})
    assert "V1_6_PREVIEW" not in surface.prompt_projection()


def test_preview_surface_requires_exactly_one_real_region():
    assert not resolve_v16_artifact_surface(_observation(""), *_evidence()).uniquely_resolved_preview
    two = '- region "Preview" [ref=p1]\n- region "Artifact preview" [ref=p2]\n'
    surface = resolve_v16_artifact_surface(_observation(two), *_evidence())
    assert surface.preview_region_count == 2
    assert not surface.complete


def test_generic_textbox_without_semantic_name_is_not_a_target():
    observation = ComputerPageObservation(
        "https://websitewombat.app/build", "Build", "v16", 1,
        (ComputerElement("x1", "textbox", "Text field"),),
        '- region "Preview" [ref=p1]\n',
    )
    surface = resolve_v16_artifact_surface(observation, *_evidence())
    assert surface.title_target is None and surface.content_target is None
    assert not surface.complete


def test_real_preview_maps_to_internal_verifier_representation_only_at_adapter_boundary():
    observation = _observation("- region \"Preview\" [ref=p1]\n- text \"Artemis I\"\n")
    surface = resolve_v16_artifact_surface(observation, *_evidence())
    mapped = verifier_observation(observation, surface)
    assert "V1_6_PREVIEW" not in observation.aria_snapshot
    assert mapped.aria_snapshot.startswith("V1_6_PREVIEW\n")


def test_stale_or_untrusted_refs_cannot_become_surface_targets():
    surface = resolve_v16_artifact_surface(_observation(), *_evidence())
    assert "model-ref" not in surface.trusted_refs
