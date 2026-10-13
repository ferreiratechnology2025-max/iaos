"""Explicit Phase 1B product-to-governance composition."""
import json

from cognitive.contracts import Proposal as ProductProposal
from governance.contract import Proposal as GovernanceProposal, Provenance
from governance.external_read import ExternalReadDescriptor


def product_to_governance_proposal(product: ProductProposal, descriptor: ExternalReadDescriptor):
    """Translate an untrusted product proposal; this function grants no authority."""
    return GovernanceProposal(
        intent_id=product.proposal_id,
        action=descriptor.action,
        args={
            "resource": json.dumps(dict(descriptor.resource), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            "request": json.dumps(dict(descriptor.args), sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        },
        provenance=Provenance("agent", product.provenance.source_revision),
    )
