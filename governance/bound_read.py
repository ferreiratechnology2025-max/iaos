"""V1.4 slice 1: search-bound deep read. ONE narrow authority, additive; no kernel file is changed.

Chain: governed ``web.search`` result  ->  SearchSourceBinding (trusted, built by the runtime from the search ledger)
-> ``browser.read_search_source`` (exact canonical URL of THAT result only) -> one page read through the Computer.

* The model never supplies a URL, an origin or an execution id. It names a source ordinal; the runtime resolves it.
* The authorizer accepts only a URL that equals the canonical URL of a binding that the runtime can still prove
  (search execution evidence + executor effect digest). There is no same-origin fallback.
* The executor fails closed on a challenge and on a final URL that is not the bound URL: no content is returned.
  Limitation (documented, not hidden): the browser follows a redirect BEFORE the final URL is known, so a redirect target
  may have been visited before the content is rejected. What is blocked is admission of the content and cognition use.
* The result is DATA_ONLY with provenance COMPUTER_OBSERVATION. That provenance is NOT generally admissible anywhere:
  only the dedicated DeepReadFact path of the source store accepts it, and only when correlated.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from .aep_executors import EffectRecord, Executor, ExecutorError, ExternalReadResult
from .approval import ApprovalGate
from .auth_registry import AuthorizationIssuer
from .authorization import issue_authorized_action
from .contract import AuthorizedAction, CanonicalAction, Decision, PolicyDecision, RiskLevel
from .evidence import Event, EventKind
from .origin import canonical_origin

READ_SEARCH_SOURCE_ACTION = "browser.read_search_source"
DEEP_READ_AUTHORITY = "DATA_ONLY"
DEEP_READ_PROVENANCE = "COMPUTER_OBSERVATION"


def canonical_source_url(url: object) -> str:
    """Canonical form used for binding: scheme/host/default-port normalised and the fragment removed.

    Path and query are preserved exactly (``/a`` and ``/a/`` are different resources). An empty path is ``/``."""
    origin = canonical_origin(url)               # validates public https, no userinfo, no control characters
    parts = urlsplit(str(url))
    path = parts.path or "/"
    return origin + path + ("?" + parts.query if parts.query else "")


def _canon_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class SearchSourceBinding:
    search_execution_id: str
    result_index: int
    canonical_url: str
    effect_digest: str

    def request(self) -> dict:
        return {"search_execution_id": self.search_execution_id, "result_index": self.result_index,
                "effect_digest": self.effect_digest}


BindingResolver = Callable[[str, int], Optional[SearchSourceBinding]]


class SearchBoundReadAuthorizer:
    """Issues exactly one authority: read the exact canonical URL of a still-provable search result."""

    def __init__(self, evidence: Any, issuer: AuthorizationIssuer, approval: ApprovalGate, resolver: BindingResolver,
                 page_provider: Callable[[], Optional[str]], allowed_origins: Iterable[str]) -> None:
        self._evidence, self._issuer, self._approval, self._resolver = evidence, issuer, approval, resolver
        self._page_provider, self._allowed_origins = page_provider, frozenset(allowed_origins)

    def canonicalize(self, proposal: Any) -> CanonicalAction:
        if proposal.action != READ_SEARCH_SOURCE_ACTION:
            raise ValueError("unknown_read_action")
        if proposal.provenance is None or proposal.provenance.source != "agent":
            raise ValueError("non_authoritative_provenance")
        resource = proposal.args.get("resource")
        try:
            request = json.loads(str(proposal.args.get("request", "")))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid:request") from exc
        if not isinstance(request, dict) or set(request) != {"search_execution_id", "result_index", "effect_digest", "return_url"}:
            raise ValueError("binding_shape")
        sid, idx, digest = request["search_execution_id"], request["result_index"], request["effect_digest"]
        if (not isinstance(sid, str) or not sid or not isinstance(digest, str) or not digest
                or not isinstance(idx, int) or isinstance(idx, bool) or idx < 0):
            raise ValueError("binding_shape")
        bound = self._resolver(sid, idx)
        if bound is None:
            raise ValueError("binding_unknown")
        if bound.effect_digest != digest:
            raise ValueError("binding_effect_digest_mismatch")
        if not isinstance(resource, str) or canonical_source_url(resource) != resource or resource != bound.canonical_url:
            raise ValueError("url_not_bound")
        # The return target is runtime-injected: it must be the trusted current page and inside the browser allowlist.
        return_url = request["return_url"]
        try:
            return_c = canonical_source_url(return_url) if isinstance(return_url, str) else None
            current = self._page_provider()
            current_c = canonical_source_url(current) if isinstance(current, str) and current else None
            return_origin = canonical_origin(return_url) if return_c else None
        except ValueError:
            raise ValueError("return_url_invalid")
        if return_c is None or return_c != return_url:
            raise ValueError("return_url_invalid")
        if current_c is None or return_c != current_c:
            raise ValueError("return_url_not_trusted_page")
        if return_origin not in self._allowed_origins:
            raise ValueError("return_origin_not_allowed")
        return CanonicalAction(proposal.intent_id, READ_SEARCH_SOURCE_ACTION, READ_SEARCH_SOURCE_ACTION, resource,
                               (("binding", _canon_json(request)),), proposal.provenance)

    def authorize(self, proposal: Any) -> AuthorizedAction:
        canon = self.canonicalize(proposal)
        policy = PolicyDecision(Decision.ALLOW, "search_bound_exact_url")
        approval = self._approval.decide(canon, RiskLevel.MEDIUM, policy)
        if approval.decision is not Decision.ALLOW:
            raise PermissionError("approval_required")
        event_id = self._evidence.append(Event(EventKind.AUTHORIZATION, proposal.intent_id, {
            "action": canon.action, "opcode": canon.opcode, "resource": canon.resource,
            "args": [list(p) for p in canon.args], "digest": canon.digest(),
            "resource_identity": None, "resource_binding_id": None,
            "policy_decision": policy.decision.value, "risk": RiskLevel.MEDIUM.value,
            "approval_decision": approval.decision.value, "budget_decision": Decision.ALLOW.value,
            "final_decision": Decision.ALLOW.value,
        }))
        return issue_authorized_action(self._issuer, proposal.intent_id, canon, event_id)


class SearchBoundReadExecutor(Executor):
    """Reads one page through a Computer port that exposes ``read_page`` (requestHelp=false, challenge and truncation kept)."""

    action = opcode = READ_SEARCH_SOURCE_ACTION
    arg_names = ("binding",)
    binds_identity = False

    def __init__(self, port: Any) -> None:
        self._port = port
        self._effects: list = []
        self.trace: list = []   # every attempt, successful or not: the network effect happened even when the action fails

    def run(self, resource, args, identity, binding=None):
        if tuple(k for k, _ in args) != self.arg_names:
            raise ExecutorError("args_shape")
        return_url = json.loads(dict(args)["binding"])["return_url"]
        entry = {"pre_read_url": return_url, "bound_source_url": resource, "source_final_url": None, "return_url": return_url,
                 "return_nav_final_url": None, "return_observed_url": None, "source_error": None, "restore_attempted": False,
                 "restore_succeeded": False, "restore_error": None, "temporary_source_network_effect_occurred": True,
                 "rollback_of_network_effect": False}
        self.trace.append(entry)
        page = None
        try:
            page = self._port.read_page(resource)
            entry["source_final_url"] = page.final_url
            if page.challenge:
                entry["source_error"] = "challenge_detected"        # no takeover, no content
            else:
                try:
                    final = canonical_source_url(page.final_url)
                    if final != resource:
                        entry["source_error"] = "final_url_mismatch"    # content is discarded, never returned
                except ValueError:
                    entry["source_error"] = "final_url_invalid"
        except Exception:
            entry["source_error"] = "read_failed"
        # Controlled return: always attempted once the source navigation was attempted. It is NOT a model decision.
        entry["restore_attempted"] = True
        try:
            restored = self._port.restore_page(return_url)
            entry["return_nav_final_url"], entry["return_observed_url"] = restored.nav_final_url, restored.observed_url
            if restored.challenge:
                entry["restore_error"] = "restore_challenge"
            elif (canonical_source_url(restored.nav_final_url) != return_url or canonical_source_url(restored.observed_url) != return_url):
                entry["restore_error"] = "restore_final_url_mismatch"
        except Exception:
            entry["restore_error"] = "restore_navigation_failed"
        entry["restore_succeeded"] = entry["restore_error"] is None
        if entry["restore_error"]:
            raise ExecutorError("restore_failed")                # the workspace is in an invalid state: no tool success
        if entry["source_error"]:
            raise ExecutorError(entry["source_error"])
        content = {"requested_url": resource, "final_url": canonical_source_url(page.final_url), "page_title": page.title,
                   "visible_text": page.text, "truncated": bool(page.truncated), "browser_session_id": page.session_id,
                   "observed_at": page.observed_at, "return_url": return_url, "return_observed_url": entry["return_observed_url"]}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest, page.session_id))
        return ExternalReadResult(content, identity=page.session_id, result_authority=DEEP_READ_AUTHORITY,
                                  result_provenance=DEEP_READ_PROVENANCE)

    def effects(self):
        return tuple(self._effects)
