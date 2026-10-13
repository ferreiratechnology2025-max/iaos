from dataclasses import replace

import pytest

from application.v16_independent_publisher import (
    ArtifactObservation,
    V16OpenDotsArtifactComposition,
    make_synthetic_run,
)
from governance.opendots import ArtifactReceipt


TITLE = "Space Mission Launch Comparison"
CONTENT = "Artemis I — Nov. 16, 2022\nRosetta — 2 March 2004"


def _ports(*, create_value=None, read_value=None):
    calls = []
    receipt = create_value or ArtifactReceipt("space-a", "page-a", 1, TITLE, CONTENT)
    observation = read_value or ArtifactObservation("space-a", "page-a", 1, TITLE, CONTENT)

    def create(proposal):
        calls.append(("create", proposal))
        return receipt

    def read(proposal):
        calls.append(("read", proposal))
        return observation

    return calls, create, read


def test_create_is_unavailable_until_full_research_gate():
    calls, create, read = _ports()
    run = make_synthetic_run(chain_b=replace(make_synthetic_run().chain_b, admitted_fact=None))
    composition = V16OpenDotsArtifactComposition(run, "space-a", create, read)
    result = composition.propose_and_verify(title=TITLE, content=CONTENT)
    assert composition.available_actions == ()
    assert not result.completed and not result.create_attempted and calls == []


def test_full_research_exposes_create_but_never_read_to_cognition():
    calls, create, read = _ports()
    composition = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read)
    assert composition.available_actions == ("opendots.page.create",)
    assert "opendots.page.read" not in composition.available_actions


def test_model_can_propose_only_title_and_content():
    calls, create, read = _ports()
    composition = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read)
    with pytest.raises(TypeError):
        composition.propose_and_verify(title=TITLE, content=CONTENT, space_id="other")
    with pytest.raises(TypeError):
        composition.propose_and_verify(title=TITLE, content=CONTENT, base_url="https://evil")
    with pytest.raises(TypeError):
        composition.propose_and_verify(title=TITLE, content=CONTENT, token="secret")


def test_prebound_space_is_used_and_create_produces_receipt():
    calls, create, read = _ports()
    composition = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read)
    result = composition.propose_and_verify(title=TITLE, content=CONTENT)
    assert calls[0][0] == "create"
    assert calls[0][1].args == {"space_id": "space-a", "title": TITLE, "content": CONTENT}
    assert result.receipt is not None and result.receipt.page_id == "page-a"


def test_read_is_automatically_derived_from_receipt():
    calls, create, read = _ports()
    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content=CONTENT,
    )
    read_proposal = calls[1][1]
    assert result.completed
    assert read_proposal.args == {"space_id": "space-a", "page_id": "page-a", "expected_revision": 1}


def test_create_failure_prevents_read():
    calls = []

    def create(_proposal):
        calls.append("create")
        raise RuntimeError("provider_rejected")

    def read(_proposal):
        calls.append("read")
        raise AssertionError("read must not run")

    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content=CONTENT,
    )
    assert not result.completed and result.create_attempted and not result.read_attempted
    assert calls == ["create"]


def test_read_failure_prevents_completion():
    calls, create, _ = _ports()

    def read(_proposal):
        calls.append(("read-failed",))
        raise RuntimeError("readback_failed")

    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content=CONTENT,
    )
    assert not result.completed and result.create_attempted and result.read_attempted


def test_identity_mismatch_prevents_completion_after_readback():
    calls, create, read = _ports(read_value=ArtifactObservation("space-a", "page-b", 1, TITLE, CONTENT))
    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content=CONTENT,
    )
    assert not result.completed
    assert result.verification is None
    assert result.failure_reason == "receipt_observation_identity_mismatch"


def test_successful_write_with_invalid_content_still_fails_verifier():
    calls, create, read = _ports(
        create_value=ArtifactReceipt("space-a", "page-a", 1, TITLE, "Artemis I — Nov. 16, 2022"),
        read_value=ArtifactObservation("space-a", "page-a", 1, TITLE, "Artemis I — Nov. 16, 2022"),
    )
    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content="Artemis I — Nov. 16, 2022",
    )
    assert not result.completed
    assert result.receipt is not None and result.observation is not None
    assert result.verification is not None and not result.verification.verified


def test_structured_verifier_pass_allows_completion():
    calls, create, read = _ports()
    result = V16OpenDotsArtifactComposition(make_synthetic_run(), "space-a", create, read).propose_and_verify(
        title=TITLE, content=CONTENT,
    )
    assert result.completed and result.verification is not None and result.verification.verified
