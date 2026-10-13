"""Synthetic V1.6 independent-publisher benchmark.

This module is deliberately a harness/verifier slice.  It does not change the
agent loop, governance, runtime, or the frozen V1.5 research primitives.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from application.agent_loop import VerificationResult
from application.artifact_surface import ArtifactSurface, resolve_artifact_surface
from application.research_completion import RequiredDocumentSpec
from governance.contract import Proposal, Provenance
from governance.opendots import ArtifactReceipt, read_proposal_from_receipt


@dataclass(frozen=True)
class PublisherFixture:
    publisher_id: str
    registered_domain: str
    canonical_document: str
    mission: str
    fact: str


NASA = PublisherFixture("NASA", "nasa.gov", "https://www.nasa.gov/mission/artemis-i/", "Artemis I", "Nov. 16, 2022")
ESA = PublisherFixture("ESA", "esa.int", "https://www.esa.int/Science_Exploration/Space_Science/Rosetta", "Rosetta", "2 March 2004")
V16_FIXTURE: tuple[PublisherFixture, PublisherFixture] = (NASA, ESA)
REQUIRED_DOCUMENTS = tuple(
    RequiredDocumentSpec(p.publisher_id, p.registered_domain, p.canonical_document)
    for p in V16_FIXTURE
)


@dataclass(frozen=True)
class PublisherChain:
    publisher: PublisherFixture
    search_execution_id: str
    read_execution_id: str
    search_url: str
    read_url: str
    read_text: str
    admitted_fact: Optional[str]


@dataclass(frozen=True)
class V16SyntheticRun:
    chain_a: PublisherChain
    chain_b: PublisherChain
    artifact_preview: str
    final_observation_id: str = "v16-final-observation"


@dataclass(frozen=True)
class ArtifactObservation:
    """Structured, read-back observation consumed by the V1.6 verifier."""

    space_id: str
    page_id: str
    revision: int
    title: str
    content: str


@dataclass(frozen=True)
class V16OpenDotsCompositionResult:
    completed: bool
    create_attempted: bool
    read_attempted: bool
    receipt: Optional[ArtifactReceipt]
    observation: Optional[ArtifactObservation]
    verification: Optional[VerificationResult]
    failure_reason: Optional[str] = None


class V16OpenDotsArtifactComposition:
    """Synthetic composition of the V1.6 research gate and typed OpenDots effects.

    ``create_effect`` and ``read_effect`` are the already-governed effect ports;
    this class owns only the V1.6 sequencing and completion policy.  The model
    can supply title/content, while the Space is pre-bound and the read action
    is private, receipt-derived follow-up state.
    """

    def __init__(self, research_run: V16SyntheticRun, authorized_space_id: str,
                 create_effect: Callable[[Proposal], Any], read_effect: Callable[[Proposal], Any]) -> None:
        if not isinstance(authorized_space_id, str) or not authorized_space_id:
            raise ValueError("authorized_space_id_required")
        self._research_run = research_run
        self._authorized_space_id = authorized_space_id
        self._create_effect = create_effect
        self._read_effect = read_effect

    @property
    def research_complete(self) -> bool:
        return research_assurance(self._research_run)

    @property
    def available_actions(self) -> tuple[str, ...]:
        return ("opendots.page.create",) if self.research_complete else ()

    def propose_and_verify(self, *, title: str, content: str) -> V16OpenDotsCompositionResult:
        """Run the fixed create -> receipt -> read -> structured verify sequence."""
        if not self.research_complete:
            return V16OpenDotsCompositionResult(False, False, False, None, None, None,
                                                "research_incomplete")
        if not isinstance(title, str) or not isinstance(content, str):
            return V16OpenDotsCompositionResult(False, False, False, None, None, None,
                                                "artifact_proposal_invalid")
        create_proposal = Proposal(
            "v16-opendots-create", "opendots.page.create",
            {"space_id": self._authorized_space_id, "title": title, "content": content},
            Provenance("agent", "v16-opendots-create"),
        )
        try:
            receipt = _receipt_from_effect(self._create_effect(create_proposal))
        except Exception as exc:
            return V16OpenDotsCompositionResult(False, True, False, None, None, None,
                                                "create_failed:" + type(exc).__name__)
        read_proposal = read_proposal_from_receipt(receipt, "v16-opendots-read")
        try:
            observation = _observation_from_effect(self._read_effect(read_proposal))
        except Exception as exc:
            return V16OpenDotsCompositionResult(False, True, True, receipt, None, None,
                                                "read_failed:" + type(exc).__name__)
        if (
            observation.space_id != receipt.space_id
            or observation.page_id != receipt.page_id
            or observation.revision != receipt.revision
        ):
            return V16OpenDotsCompositionResult(False, True, True, receipt, observation, None,
                                                "receipt_observation_identity_mismatch")
        verification = verify_v16(self._research_run, observation)
        if not verification.verified:
            return V16OpenDotsCompositionResult(False, True, True, receipt, observation, verification,
                                                "structured_verifier_failed")
        return V16OpenDotsCompositionResult(True, True, True, receipt, observation, verification)


def _receipt_from_effect(value: Any) -> ArtifactReceipt:
    if isinstance(value, ArtifactReceipt):
        return value
    if getattr(value, "ok", False) and isinstance(getattr(value, "structured_output", None), dict):
        return ArtifactReceipt(**value.structured_output)
    raise ValueError("create_effect_did_not_return_receipt")


def _observation_from_effect(value: Any) -> ArtifactObservation:
    if isinstance(value, ArtifactObservation):
        return value
    if getattr(value, "ok", False) and isinstance(getattr(value, "structured_output", None), dict):
        return ArtifactObservation(**value.structured_output)
    raise ValueError("read_effect_did_not_return_observation")


def _chain_diagnostic(chain: PublisherChain) -> dict[str, object]:
    required = chain.publisher.canonical_document
    publisher_match = chain.publisher.publisher_id in {NASA.publisher_id, ESA.publisher_id}
    document_match = chain.read_url == required
    if not chain.search_execution_id or not chain.read_execution_id:
        reason = "missing_execution_id"
    elif chain.search_execution_id == chain.read_execution_id:
        reason = "search_read_execution_id_not_distinct"
    elif chain.search_url != required:
        reason = "search_canonical_url_mismatch"
    elif not document_match:
        reason = "required_canonical_url_mismatch"
    elif not publisher_match:
        reason = "publisher_mismatch"
    elif not isinstance(chain.admitted_fact, str) or not chain.admitted_fact.strip():
        reason = "admitted_fact_missing"
    elif chain.admitted_fact != chain.publisher.fact:
        reason = "admitted_fact_mismatch"
    else:
        reason = None
    return {
        "publisher": chain.publisher.publisher_id,
        "required_canonical_url": required,
        "search_source_id": chain.search_execution_id,
        "search_canonical_url": chain.search_url,
        "read_execution_id": chain.read_execution_id,
        "read_url": chain.read_url,
        "admitted_fact_digest": hashlib.sha256((chain.admitted_fact or "").encode()).hexdigest(),
        "admitted_fact_length": len(chain.admitted_fact or ""),
        "publisher_match": publisher_match,
        "required_document_match": document_match,
        "chain_validation_result": "PASS" if reason is None else "FAIL",
        "chain_validation_failure_reason": reason,
    }


def _valid_research_chain(chain: PublisherChain) -> bool:
    p = chain.publisher
    return (
        bool(chain.search_execution_id) and bool(chain.read_execution_id)
        and chain.search_execution_id != chain.read_execution_id
        and chain.search_url == p.canonical_document
        and chain.read_url == p.canonical_document
        and _host(chain.read_url) == p.registered_domain
        and isinstance(chain.admitted_fact, str)
        and bool(chain.admitted_fact.strip())
        and chain.admitted_fact == p.fact
    )


def research_assurance(run: V16SyntheticRun) -> bool:
    """Return only the research-side gate; no artifact observation is involved."""
    a, b = run.chain_a, run.chain_b
    return (
        _valid_research_chain(a) and _valid_research_chain(b)
        and a.publisher.publisher_id != b.publisher.publisher_id
        and a.publisher.registered_domain != b.publisher.registered_domain
        and a.publisher.canonical_document != b.publisher.canonical_document
        and a.search_execution_id != b.search_execution_id
        and a.read_execution_id != b.read_execution_id
        and a.read_url != b.read_url
        and {a.publisher.publisher_id, b.publisher.publisher_id} == {NASA.publisher_id, ESA.publisher_id}
    )


def extract_benchmark_fact(publisher: PublisherFixture, read_text: str) -> Optional[str]:
    """Extract only the two frozen benchmark facts from the text actually read."""
    if not isinstance(read_text, str):
        return None
    if publisher is NASA and "Artemis I" in read_text and NASA.fact in read_text:
        return NASA.fact
    if publisher is ESA and "Rosetta" in read_text and ESA.fact in read_text:
        return ESA.fact
    return None


def extract_v16_fact(read_text: str) -> Optional[str]:
    """AgentLoop-compatible trusted extractor for this benchmark only.

    Source ownership remains a verifier responsibility; this function merely
    extracts one of the two exact facts from admitted read text.
    """
    if not isinstance(read_text, str):
        return None
    hits = [fact for fact in (NASA.fact, ESA.fact) if fact in read_text]
    return hits[0] if len(hits) == 1 else None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def make_synthetic_run(*, artifact_preview: Optional[str] = None,
                       chain_a: Optional[PublisherChain] = None,
                       chain_b: Optional[PublisherChain] = None) -> V16SyntheticRun:
    """Build the fixture-backed synthetic run; no network or browser is used."""
    a = chain_a or PublisherChain(NASA, "search-A", "read-A", NASA.canonical_document,
                                  NASA.canonical_document,
                                  "NASA mission page: Artemis I. Launch: Nov. 16, 2022.",
                                  NASA.fact)
    b = chain_b or PublisherChain(ESA, "search-B", "read-B", ESA.canonical_document,
                                  ESA.canonical_document,
                                  "ESA mission page: Rosetta. Launch: 2 March 2004.",
                                  ESA.fact)
    preview = artifact_preview if artifact_preview is not None else (
        "V1_6_PREVIEW\nSpace Mission Launch Comparison\n"
        "Artemis I — Nov. 16, 2022\nRosetta — 2 March 2004"
    )
    return V16SyntheticRun(a, b, preview)


def verify_v16(run: V16SyntheticRun, observation: ArtifactObservation) -> VerificationResult:
    """Deterministically verify both provenance chains and the final artifact."""
    chains = (run.chain_a, run.chain_b)
    facts: dict[str, object] = {
        "CHAIN_A_VALID": False, "CHAIN_B_VALID": False,
        "PUBLISHERS_DISTINCT": False,
        "ARTIFACT_OBSERVATION_VALID": False, "ARTIFACT_TITLE_VALID": False,
        "ARTIFACT_CONTAINS_FACT_A": False, "ARTIFACT_CONTAINS_FACT_B": False,
        "FRESH_FINAL_OBSERVATION": False,
    }
    reasons: list[str] = []

    valid_a, valid_b = _valid_research_chain(run.chain_a), _valid_research_chain(run.chain_b)
    diagnostics = {
        "chain_a": _chain_diagnostic(run.chain_a),
        "chain_b": _chain_diagnostic(run.chain_b),
    }
    facts["CHAIN_A_VALID"], facts["CHAIN_B_VALID"] = valid_a, valid_b
    if not valid_a:
        reasons.append("chain_a_invalid")
    if not valid_b:
        reasons.append("chain_b_invalid")

    distinct = (
        run.chain_a.publisher.publisher_id != run.chain_b.publisher.publisher_id
        and run.chain_a.publisher.registered_domain != run.chain_b.publisher.registered_domain
        and run.chain_a.publisher.canonical_document != run.chain_b.publisher.canonical_document
        and run.chain_a.search_execution_id != run.chain_b.search_execution_id
        and run.chain_a.read_execution_id != run.chain_b.read_execution_id
        and run.chain_a.read_url != run.chain_b.read_url
    )
    facts["PUBLISHERS_DISTINCT"] = distinct
    if not distinct:
        reasons.append("publishers_or_executions_not_distinct")

    observation_valid = (
        isinstance(observation, ArtifactObservation)
        and isinstance(observation.space_id, str) and bool(observation.space_id)
        and isinstance(observation.page_id, str) and bool(observation.page_id)
        and isinstance(observation.revision, int) and not isinstance(observation.revision, bool)
        and observation.revision > 0
        and isinstance(observation.title, str)
        and isinstance(observation.content, str)
    )
    facts["ARTIFACT_OBSERVATION_VALID"] = observation_valid
    facts["ARTIFACT_TITLE_VALID"] = observation_valid and observation.title == "Space Mission Launch Comparison"
    content = observation.content if observation_valid else ""
    facts["ARTIFACT_CONTAINS_FACT_A"] = (
        observation_valid and run.chain_a.publisher.mission in content and run.chain_a.publisher.fact in content
    )
    facts["ARTIFACT_CONTAINS_FACT_B"] = (
        observation_valid and run.chain_b.publisher.mission in content and run.chain_b.publisher.fact in content
    )
    facts["FRESH_FINAL_OBSERVATION"] = observation_valid
    for key in ("ARTIFACT_OBSERVATION_VALID", "ARTIFACT_TITLE_VALID", "ARTIFACT_CONTAINS_FACT_A",
                "ARTIFACT_CONTAINS_FACT_B", "FRESH_FINAL_OBSERVATION"):
        if not facts[key]:
            reasons.append(key.lower())

    verified = all(bool(facts[k]) for k in facts) and not reasons
    satisfied = tuple(k for k, value in facts.items() if value)
    missing = tuple(k for k, value in facts.items() if not value)
    return VerificationResult(verified, facts, tuple(reasons), satisfied, missing, diagnostics)


def synthetic_observation(run: V16SyntheticRun) -> ArtifactObservation:
    return ArtifactObservation("synthetic-space", "synthetic-page", 1,
                               "Space Mission Launch Comparison", run.artifact_preview)


def v16_loop_options() -> dict[str, object]:
    """Trusted opt-ins kept with the V1.6 capability, not the V1.5 harness."""
    return {"enable_source_value": True, "deep_fact_extractor": extract_v16_fact,
            "enable_blocker_recovery": True,
            "require_research_complete_before_artifact": True,
            "artifact_surface_resolver": resolve_v16_artifact_surface}


def resolve_v16_artifact_surface(observation, facts, deep_facts) -> ArtifactSurface:
    return resolve_artifact_surface(
        observation, facts, deep_facts,
        lambda url: next((document.publisher for document in REQUIRED_DOCUMENTS
                          if url == document.canonical_url or url.startswith(document.canonical_url + "/")), None),
    )


__all__ = ["NASA", "ESA", "V16_FIXTURE", "REQUIRED_DOCUMENTS", "PublisherFixture", "PublisherChain", "V16SyntheticRun",
           "ArtifactObservation", "V16OpenDotsCompositionResult", "V16OpenDotsArtifactComposition", "research_assurance",
           "extract_benchmark_fact", "extract_v16_fact", "make_synthetic_run", "verify_v16", "synthetic_observation",
           "v16_loop_options", "resolve_v16_artifact_surface"]
