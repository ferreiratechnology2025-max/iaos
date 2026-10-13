"""Explicit assembly of the V0 agent slice (pattern of the Daily Brief assembly).

Every observation and every action crosses Governance
(Proposal -> Authorizer -> AuthorizedAction -> AEPAdapter -> executor). The
AgentLoop sees only the ``GovernedComputer`` below; it never touches OpenBot, a
token, an authorizer or the AEP registry. The model never supplies page_url,
browser_session_id or snapshot_id: they are injected here from the trusted
last observation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from application.agent_loop import ActionOutcome, AgentLoop, GovernedComputer, HumanRequestOutcome, Verifier
from application.computer_runtime import ComputerControlState, ComputerElement, ComputerPageObservation
from cognitive.agent_contracts import ActDecision, CognitionProvider
from governance.aep_adapter import AEPAdapter
from application.source_facts import (DeepReadCandidate, ReadOutcome, SearchConfig, SearchOutcome, SourceCandidate, hostname_of)
from governance.bound_read import (READ_SEARCH_SOURCE_ACTION, SearchBoundReadAuthorizer, SearchBoundReadExecutor,
                                   SearchSourceBinding, canonical_source_url)
from governance.aep_executors import AEPExecutorRegistry, BraveSearchExecutor, ComputerHumanRequestExecutor, ComputerInteractionExecutor, ComputerNavigationExecutor, ComputerObservationExecutor, default_registry
from governance.aep_runtime import AEPRuntime
from governance.approval import ApprovalGate
from governance.auth_registry import create_authorization_registry
from governance.computer_interaction import ComputerInteractionAuthorizer
from governance.computer_navigation import ComputerNavigationAuthorizer
from governance.computer_observation import ComputerObservationAuthorizer
from governance.computer_request_human import ComputerHumanRequestAuthorizer
from governance.consumption import create_consumption_ledger
from governance.contract import Proposal, Provenance
from governance.evidence import InMemoryEvidenceStore
from governance.external_read import ExternalReadAuthorizer, ExternalReadDescriptor
from governance.handle_binding import BindingTable
from governance.origin import canonical_allowed_origins, canonical_origin
from governance.origin import host_matches_allowed_domain
from application.deep_read_transport_trace import classify_read_failure, deep_read_operation

_CODE = re.compile(r"'([A-Za-z0-9_:.\-]{1,80})'")


@dataclass(frozen=True)
class ResearchReturnContext:
    """Runner-owned context used exclusively by governed research return."""

    page_url: str
    provenance: str


class GovernedComputerSession:
    """One agent run's governed access to the Computer. Holds no OpenBot token."""

    def __init__(self, port: Any, source: str = "agent", allowed_origins: Iterable[str] = (),
                 search: SearchConfig | None = None, search_executor: Any = None, allow_read: bool = False,
                 research_return_context: ResearchReturnContext | None = None) -> None:
        if allow_read and search is None:
            raise ValueError("read_requires_search")
        self._source = source
        self._allow_read = allow_read
        self._trusted_page: ComputerPageObservation | None = None   # last governed observation; its page_url is the only return target a read may use
        self._research_return_context = research_return_context
        self._allowed_origins = tuple(allowed_origins)
        self.bindings: list = []   # SearchSourceBinding per successful search, built here from the search result (trusted)
        self._search = search
        self.search_ledger: list = []
        self._port = port
        self._trace_sink = None
        self.evidence = InMemoryEvidenceStore()
        issuer, view = create_authorization_registry()
        _ledger, consume = create_consumption_ledger()
        entries = {name: default_registry().lookup(name) for name in default_registry().actions()}
        entries["browser.observe"] = ComputerObservationExecutor(port)
        entries["browser.request_human"] = ComputerHumanRequestExecutor(port)
        entries["browser.navigate"] = ComputerNavigationExecutor(port, tuple(allowed_origins))
        for action in ("browser.click", "browser.type", "browser.key", "browser.select_option"):
            entries[action] = ComputerInteractionExecutor(action, port)
        if search is not None:   # opt-in: a run without search configuration has no web.search executor or authorizer wired
            entries["web.search"] = search_executor if search_executor is not None else BraveSearchExecutor()
        self._search_executor = entries.get("web.search")
        self._read_executor = None
        if allow_read:   # opt-in V1.4 capability: one narrow search-bound read authority, nothing else
            self._read_executor = SearchBoundReadExecutor(port)
            entries[READ_SEARCH_SOURCE_ACTION] = self._read_executor
        self._adapter = AEPAdapter(AEPRuntime(AEPExecutorRegistry(entries), BindingTable(), view, consume))
        self._issuer = issuer
        self._approval = ApprovalGate()
        self._observe_auth = ComputerObservationAuthorizer(self.evidence, issuer, self._approval)
        self._act_auth = ComputerInteractionAuthorizer(self.evidence, issuer, self._approval)
        self._human_auth = ComputerHumanRequestAuthorizer(self.evidence, issuer, self._approval)
        self._nav_auth = ComputerNavigationAuthorizer(self.evidence, issuer, self._approval, tuple(allowed_origins))
        self._search_auth = ExternalReadAuthorizer(self.evidence, issuer, self._approval) if search is not None else None
        self._read_auth = (SearchBoundReadAuthorizer(self.evidence, issuer, self._approval, self._resolve_binding,
                                                     self._research_page_url, self._research_allowed_origins()) if allow_read else None)

    def set_research_return_context(self, context: ResearchReturnContext) -> None:
        """Install runner-owned return context; model observations never call this."""
        self._research_return_context = context
        if self._read_executor is not None:
            self._read_auth = SearchBoundReadAuthorizer(
                self.evidence, self._issuer, self._approval, self._resolve_binding,
                self._research_page_url, self._research_allowed_origins(),
            )

    def set_trace_sink(self, trace_sink) -> None:
        self._trace_sink = trace_sink
        if self._read_executor is not None and hasattr(self._read_executor, "set_trace_sink"):
            self._read_executor.set_trace_sink(trace_sink)

    def _execute(self, authorizer, proposal: Proposal):
        canonical = authorizer.canonicalize(proposal)
        self._approval.grant("agent-runtime", proposal.intent_id, canonical.digest())
        return self._adapter.execute(authorizer.authorize(proposal), self.evidence)

    def search(self, step_id: str, query: str) -> SearchOutcome:
        """Governed web.search. The model supplied only ``query``; domains, limit and provider are trusted config."""
        if self._search is None or self._search_auth is None:
            return SearchOutcome(False, "search_not_configured")
        cfg = self._search
        domains = list(cfg.allowed_domains)
        if hasattr(self._search_executor, "last_domain_filter"):
            self._search_executor.last_domain_filter = None
        try:
            descriptor = ExternalReadDescriptor(
                "web.search", {"provider_ref": cfg.provider_ref, "allowed_domains": domains},
                {"requested_domains": domains, "query": query, "result_limit": cfg.result_limit})
        except ValueError:
            return SearchOutcome(False, "search_descriptor_invalid")
        dump = lambda value: json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        proposal = Proposal(step_id, "web.search", {"resource": dump(descriptor.resource), "request": dump(descriptor.args)},
                            Provenance(self._source, step_id))
        try:
            result = self._execute(self._search_auth, proposal)
        except PermissionError:
            return SearchOutcome(False, "denied_by_governance")
        except Exception as exc:
            return SearchOutcome(False, "refused:" + (getattr(exc, "code", None) or type(exc).__name__ + ":" + _code(str(exc))),
                                domain_filter=getattr(self._search_executor, "last_domain_filter", None))
        if not result.ok:
            return SearchOutcome(False, "execution_failed:" + _code(result.error),
                                domain_filter=getattr(self._search_executor, "last_domain_filter", None))
        content = result.structured_output
        results = content.get("results") if isinstance(content, Mapping) else None
        if not isinstance(results, list):
            return SearchOutcome(False, "result_shape")
        results = [r for r in results if isinstance(r, Mapping)][: cfg.result_limit]
        if not results:
            return SearchOutcome(False, "no_results")
        item = results[0]
        if not any(host_matches_allowed_domain(hostname_of(str(item.get("url", ""))), domain)
                   for domain in domains):   # defence in depth: the executor already post-filters
            return SearchOutcome(False, "result_domain_out_of_scope",
                                domain_filter=getattr(self._search_executor, "last_domain_filter", None))
        digest = _content_digest(content)
        # Harness-side ledger of governed search results (not shown to the model); the V1.3 verifier correlates it with evidence.
        self.search_ledger.append({"execution_id": result.execution_id, "title": str(item.get("title", "")),
                                   "url": str(item.get("url", "")), "result_digest": digest})
        if self._allow_read:
            try:
                bound_url = canonical_source_url(str(item.get("url", "")))
            except ValueError:
                bound_url = ""   # not readable: the binding exists but can never authorize a read
            self.bindings.append(SearchSourceBinding(result.execution_id, 0, bound_url, digest))
        return SearchOutcome(True, "searched", SourceCandidate(
            result.execution_id, dict(item), digest, result.result_authority, result.result_provenance),
            domain_filter=getattr(self._search_executor, "last_domain_filter", None))

    def _resolve_binding(self, search_execution_id: str, result_index: int) -> SearchSourceBinding | None:
        """Trusted lookup used by the authorizer: a binding is valid only while the search execution is provable in the
        evidence AND the executor's own effect record carries the digest the binding was built from."""
        bound = next((b for b in self.bindings if b.search_execution_id == search_execution_id and b.result_index == result_index), None)
        if bound is None or not bound.canonical_url:
            return None
        executed = any(e.get("kind") == "EXECUTION_RESULT" and e.get("action") == "web.search" and e.get("ok") is True
                       and e.get("execution_id") == search_execution_id for e in self.evidence.events())
        effect = any(getattr(r, "opcode", None) == "web.search" and getattr(r, "result_digest", None) == bound.effect_digest
                     for r in _effects_of(self._search_executor))
        return bound if executed and effect else None

    def read_source(self, step_id: str, search_execution_id: str) -> ReadOutcome:
        """Governed deep read of the exact URL bound to a search result. The model supplied only an ordinal (resolved by
        the loop to ``search_execution_id``): the URL comes from the trusted binding."""
        if self._read_auth is None:
            return ReadOutcome(False, "read_not_configured")
        bound = next((b for b in self.bindings if b.search_execution_id == search_execution_id), None)
        if bound is None or not bound.canonical_url:
            return ReadOutcome(False, "source_not_bound")
        if self._research_return_context is None and self._trusted_page is None:
            return ReadOutcome(False, "no_trusted_page")
        trusted_url = self._research_page_url()
        if not trusted_url:
            return ReadOutcome(False, "return_url_invalid")
        try:
            return_url = canonical_source_url(trusted_url)
        except ValueError:
            return ReadOutcome(False, "return_url_invalid")
        dump = lambda value: json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        proposal = Proposal(step_id, READ_SEARCH_SOURCE_ACTION,
                            {"resource": bound.canonical_url, "request": dump({**bound.request(), "return_url": return_url})},
                            Provenance(self._source, step_id))
        operation = deep_read_operation(step_id, "deep_read")
        try:
            with operation:
                result = self._execute(self._read_auth, proposal)
        except (PermissionError, ValueError) as exc:
            return ReadOutcome(False, ("denied_by_governance:" + (str(exc) if re.fullmatch(r"[a-z0-9_]+", str(exc)) else _code(str(exc))))
                               if isinstance(exc, ValueError) else "denied_by_governance")
        except Exception as exc:
            return ReadOutcome(False, "refused:" + (getattr(exc, "code", None) or type(exc).__name__ + ":" + _code(str(exc))))
        if not result.ok:
            if self._trace_sink is not None:
                try:
                    self._trace_sink.emit({"event_type": "DEEP_READ_END", "operation_id": step_id,
                                           "success": False,
                                           "failure_stage": classify_read_failure(operation, str(result.error)),
                                           "failure_code": _code(result.error)})
                except Exception:
                    pass
            return ReadOutcome(False, "execution_failed:" + _code(result.error))
        content = result.structured_output
        if not isinstance(content, Mapping) or not all(k in content for k in ("final_url", "visible_text", "truncated")):
            return ReadOutcome(False, "result_shape")
        return ReadOutcome(True, "read", DeepReadCandidate(
            result.execution_id, bound.search_execution_id, bound.effect_digest, bound.canonical_url, str(content["final_url"]),
            str(content["visible_text"]), bool(content["truncated"]), None, _content_digest(content),
            result.result_authority, result.result_provenance))

    def deep_read_is_correlated(self, candidate: DeepReadCandidate) -> bool:
        """The read execution exists in the evidence AND in the executor's effect record with the same content digest."""
        executed = any(e.get("kind") == "EXECUTION_RESULT" and e.get("action") == READ_SEARCH_SOURCE_ACTION and e.get("ok") is True
                       and e.get("execution_id") == candidate.read_execution_id for e in self.evidence.events())
        effect = any(getattr(r, "opcode", None) == READ_SEARCH_SOURCE_ACTION and getattr(r, "result_digest", None) == candidate.result_digest
                     for r in _effects_of(self._read_executor))
        return executed and effect

    def observe(self, step_id: str) -> ComputerPageObservation:
        proposal = Proposal(step_id, "browser.observe", {}, Provenance(self._source, step_id))
        result = self._execute(self._observe_auth, proposal)
        if not result.ok:
            raise RuntimeError("observation_failed:" + _code(result.error))
        observation = _observation(result.structured_output)
        self._trusted_page = observation
        return observation

    def _trusted_page_url(self) -> str | None:
        """Extract the URL from the last trusted observation without inventing a fallback."""
        page = self._trusted_page
        url = getattr(page, "page_url", None) if page is not None else None
        return url if isinstance(url, str) and url else None

    def _research_page_url(self) -> str | None:
        context = self._research_return_context
        if context is not None:
            return context.page_url if isinstance(context.page_url, str) and context.page_url else None
        # Preserve legacy V1.4/V1.5 behavior for callers without explicit V1.6 context.
        return self._trusted_page_url()

    def _research_allowed_origins(self) -> tuple[str, ...]:
        context = self._research_return_context
        if context is None:
            return self._allowed_origins
        try:
            return (canonical_origin(context.page_url),)
        except ValueError:
            return ()

    def navigate(self, step_id: str, url: str) -> ActionOutcome:
        """Governed navigation. It only changes the page; the loop observes afresh next turn."""
        proposal = Proposal(step_id, "browser.navigate", {"url": url}, Provenance(self._source, step_id))
        try:
            result = self._execute(self._nav_auth, proposal)
        except PermissionError:
            return ActionOutcome(False, "denied_by_governance")
        except Exception as exc:
            return ActionOutcome(False, "denied:" + _nav_reason(str(exc)))
        if not result.ok:
            return ActionOutcome(False, "execution_failed:" + _nav_reason(str(result.error)))
        return ActionOutcome(True, "navigated")

    def request_human(self, step_id: str, reason_code: str, tool_call_id: str) -> HumanRequestOutcome:
        proposal = Proposal(
            step_id, "browser.request_human",
            {"reason_code": reason_code, "tool_call_id": tool_call_id},
            Provenance(self._source, step_id),
        )
        try:
            result = self._execute(self._human_auth, proposal)
        except PermissionError:
            return HumanRequestOutcome(False, None, "denied_by_governance")
        except Exception as exc:
            return HumanRequestOutcome(False, None, "refused:" + (getattr(exc, "code", None) or type(exc).__name__ + ":" + _code(str(exc))))
        if not result.ok:
            return HumanRequestOutcome(False, None, "execution_failed:" + _code(result.error))
        data = result.structured_output
        request_id = data.get("request_id") if isinstance(data, Mapping) else None
        if not isinstance(request_id, str) or not request_id:
            return HumanRequestOutcome(False, None, "request_shape")
        return HumanRequestOutcome(True, request_id, "requested")

    def read_control_state(self, request_id: str | None = None) -> ComputerControlState:
        """Runtime telemetry (holder, request status). Deliberately not governed."""
        return self._port.read_control_state(request_id)

    def act(self, step_id: str, decision: ActDecision, observation: ComputerPageObservation) -> ActionOutcome:
        args: dict[str, Any] = {
            "page_url": observation.page_url,
            "browser_session_id": observation.browser_session_id,
            "snapshot_id": observation.snapshot_id,
            **dict(decision.args),
        }
        if decision.action == "browser.select_option":
            # The role comes from the trusted observation, never from the model; the authorizer requires select-like.
            element = next((e for e in observation.elements if e.ref == decision.args.get("ref")), None)
            args["ref_role"] = element.role if element is not None else ""
        proposal = Proposal(step_id, decision.action, args, Provenance(self._source, step_id))
        try:
            result = self._execute(self._act_auth, proposal)
        except PermissionError:
            return ActionOutcome(False, "denied_by_governance")
        except Exception as exc:
            return ActionOutcome(False, "refused:" + (getattr(exc, "code", None) or type(exc).__name__ + ":" + _code(str(exc))))
        if not result.ok:
            return ActionOutcome(False, "execution_failed:" + _code(result.error))
        return ActionOutcome(True, "executed")


_NAV_REASONS = ("redirect_out_of_scope", "origin_not_allowed", "url_invalid", "navigate_args",
                "public_network_only", "https_public_url_required", "requested_url_invalid", "url_port_invalid")


def _nav_reason(text: str) -> str:
    for reason in _NAV_REASONS:
        if reason in text:
            return reason
    return "unspecified"


def _content_digest(content: Any) -> str:
    # same formula as the executor's EffectRecord, so the stored fact correlates with the executor's own record
    import hashlib
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()


def _code(text: Any) -> str:
    match = _CODE.search(str(text or ""))
    return match.group(1) if match else "unspecified"


def _effects_of(executor: Any) -> tuple:
    """Executor effect records; BraveSearchExecutor records them but exposes no public accessor."""
    if executor is None:
        return ()
    try:
        return tuple(executor.effects())
    except NotImplementedError:
        return tuple(getattr(executor, "_effects", ()))


def _observation(data: Any) -> ComputerPageObservation:
    if not isinstance(data, Mapping):
        raise RuntimeError("observation_shape")
    try:
        elements = tuple(
            ComputerElement(e["ref"], e["role"], e["name"], e.get("checked"), e.get("value"))
            for e in data["elements"]
        )
        return ComputerPageObservation(
            data["page_url"], data["page_title"], data["browser_session_id"], data["snapshot_id"],
            elements, data["aria_snapshot"], data.get("challenge"),
        )
    except (KeyError, TypeError) as exc:
        raise RuntimeError("observation_shape") from exc


def build_agent_loop(provider: CognitionProvider, port: Any, verifier: Verifier,
                     log: Callable[[str], None] | None = None,
                     summarize: Callable[[ComputerPageObservation], str] | None = None,
                     allowed_origins: Iterable[str] = (),
                     search: SearchConfig | None = None, search_executor: Any = None, allow_read: bool = False,
                     research_return_context: ResearchReturnContext | None = None,
                     **loop_options: Any) -> tuple[AgentLoop, GovernedComputerSession]:
    origins = tuple(sorted(canonical_allowed_origins(allowed_origins)))  # one trusted source for both layers
    session = GovernedComputerSession(port, allowed_origins=origins, search=search, search_executor=search_executor,
                                      allow_read=allow_read, research_return_context=research_return_context)
    if search is not None:   # opt-in V1.3 capability; without it the loop is exactly the V1.2 loop
        loop_options.setdefault("enable_search", True)
        loop_options.setdefault("max_searches", search.max_searches)
    if allow_read:   # opt-in V1.4 capability (requires search; enforced by the session)
        loop_options.setdefault("enable_read", True)
    return AgentLoop(provider, session, verifier, log=log, summarize=summarize, control=session,
                     allowed_origins=origins, **loop_options), session
