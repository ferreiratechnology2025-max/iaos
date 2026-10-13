from dataclasses import replace

from application.computer_runtime import ComputerPageObservation
from application.v16_independent_publisher import ArtifactObservation, make_synthetic_run, synthetic_observation, verify_v16


def _good():
    return synthetic_observation(make_synthetic_run())


def test_structured_artifact_happy_path_passes_without_preview_semantics():
    result = verify_v16(make_synthetic_run(), _good())
    assert result.verified
    assert result.facts["ARTIFACT_OBSERVATION_VALID"]
    assert result.facts["ARTIFACT_TITLE_VALID"]


def test_structured_identity_and_title_are_required():
    base = _good()
    for bad in (
        replace(base, space_id=""),
        replace(base, page_id=""),
        replace(base, revision=0),
        replace(base, title="Wrong title"),
    ):
        assert not verify_v16(make_synthetic_run(), bad).verified


def test_each_required_fact_name_and_date_is_required():
    base = _good()
    for text in ("Artemis I", "Nov. 16, 2022", "Rosetta", "2 March 2004"):
        assert not verify_v16(make_synthetic_run(), replace(base, content=base.content.replace(text, ""))).verified


def test_fact_a_only_and_fact_b_only_fail():
    base = _good()
    assert not verify_v16(make_synthetic_run(), replace(base, content="Artemis I Nov. 16, 2022")).verified
    assert not verify_v16(make_synthetic_run(), replace(base, content="Rosetta 2 March 2004")).verified


def test_legacy_preview_observation_is_not_a_v16_artifact_observation():
    legacy = ComputerPageObservation(
        "https://websitewombat.app/build", "Build", "v16", 1, (),
        "V1_6_PREVIEW Space Mission Launch Comparison Artemis I Nov. 16, 2022 Rosetta 2 March 2004",
    )
    assert not verify_v16(make_synthetic_run(), legacy).verified
