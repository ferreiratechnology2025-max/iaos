"""The ONLY place in the product that touches the real filesystem.

Closed registry: action -> executor instance, fixed at construction. Nothing
here is chosen by a Proposal: cognition picks an admissible action, never the
implementation that runs it. No dynamic import, no eval/exec, no subprocess,
no shell/network, no fallback.

Phase 1 scope: ``fs.read`` (UTF-8 text) and ``fs.read_bytes`` (the same authority, raw bytes intact);
no fs.write, shell.exec, net.fetch, spawn. Neither decodes, parses, encodes or interprets the content beyond UTF-8 for fs.read.

RESOURCE BINDING (open-first): the file was OPENED during authorization and
its handle sits in the runtime BindingTable under ``resource_binding_id``.
``RealFsReadExecutor`` receives that ``Binding``, checks that the identity
queried again from the handle and its final path equal the authorized ones, and
reads from THAT handle. It never opens, resolves or stats a path: there is no
``open`` in this module on purpose.

Certified scope: Windows / NTFS / fs.read. Not covered: content changes of the
same file, ReFS, ext4 (inode reuse), cross-adapter replay, durable evidence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import base64
from email.message import EmailMessage
from dataclasses import field
from urllib.parse import urlparse
from urllib.parse import quote
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .handle_binding import Binding, BindingError
from .origin import canonical_allowed_origins, canonical_origin, host_matches_allowed_domain
import httpx

_LOGGER = logging.getLogger("iaos.governance.executors")

from .opendots import CREATE_ACTION, CREATE_ARGS, READ_ACTION, READ_ARGS, RESOURCE, _identity, _text


class UnknownExecutor(Exception):
    """No executor registered for the action. Fail closed."""


class ExecutorError(Exception):
    pass


@dataclass(frozen=True)
class FsReadResult:
    content: str
    identity: str  # identity of the handle that was actually read


@dataclass(frozen=True)
class FsReadBytesResult:
    content: bytes  # exactly the bytes read from the bound handle; no decoding, no re-encoding
    identity: str  # identity of the handle that was actually read


@dataclass(frozen=True)
class EffectRecord:
    opcode: str
    resource: str
    args: Tuple[Tuple[str, str], ...]
    result_digest: str
    resource_identity: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "opcode": self.opcode,
            "resource": self.resource,
            "args": [list(p) for p in self.args],
            "result_digest": self.result_digest,
            "resource_identity": self.resource_identity,
        }


class Executor:
    """Base type. The registry accepts only subclasses of this."""

    action: str = ""
    opcode: str = ""
    arg_names: Tuple[str, ...] = ()
    binds_identity: bool = False

    def begin_execution(self, execution_id: str) -> None:
        """Optional per-envelope context hook for bounded multi-request plans."""
        return None

    def end_execution(self) -> None:
        return None

    def run(self, resource: str, args: Tuple[Tuple[str, str], ...], identity: Optional[str],
            binding: Optional[Binding] = None) -> Any:
        raise NotImplementedError

    def effects(self) -> Tuple[EffectRecord, ...]:
        raise NotImplementedError


class ComputerNavigateAndReadExecutor(Executor):
    """Typed read-only bridge to a ComputerRuntimePort; never a generic computer call."""

    action = opcode = "browser.navigate_and_read"
    arg_names: Tuple[str, ...] = ()
    binds_identity = False

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._effects: List[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        if tuple(args) != ():
            raise ExecutorError("browser.navigate_and_read takes no extra args")
        observation = self._runtime.navigate_and_read(resource)
        content = observation.as_dict()
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, (), digest, observation.browser_session_id))
        return ExternalReadResult(
            content,
            identity=observation.browser_session_id,
            result_authority="DATA_ONLY",
            result_provenance="COMPUTER_OBSERVATION",
        )

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class ComputerObservationExecutor(Executor):
    """Dispatches one authorized observation of the current page; never navigates."""

    action = opcode = "browser.observe"
    arg_names: Tuple[str, ...] = ()
    binds_identity = False
    RESOURCE = "browser:current-page"  # must equal governance.computer_observation.OBSERVE_RESOURCE

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._effects: List[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        if tuple(args) != () or resource != self.RESOURCE:
            raise ExecutorError("browser.observe takes no args")
        observation = self._runtime.observe()
        content = observation.as_dict()
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, (), digest, observation.browser_session_id))
        return ExternalReadResult(
            content,
            identity=observation.browser_session_id,
            result_authority="DATA_ONLY",
            result_provenance="COMPUTER_OBSERVATION",
        )

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class ComputerHumanRequestExecutor(Executor):
    """Dispatches one authorized request for a person. Closed reason codes only; never takes control."""

    action = opcode = "browser.request_human"
    arg_names: Tuple[str, ...] = ("reason_code", "tool_call_id")
    binds_identity = False
    RESOURCE = "browser:human-handoff"  # must equal governance.computer_request_human.REQUEST_HUMAN_RESOURCE
    REASONS = {
        "authentication_required": "The assistant reached a page that requires signing in and needs a person to continue.",
        "challenge_required": "The assistant reached a human verification challenge and needs a person to continue.",
    }  # keys must equal governance.computer_request_human.REASON_CODES

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._effects: List[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        values = dict(args)
        if resource != self.RESOURCE or set(values) != {"reason_code", "tool_call_id"}:
            raise ExecutorError("browser.request_human args")
        reason = self.REASONS.get(values["reason_code"])
        if reason is None:
            raise ExecutorError("browser.request_human reason_code")
        state = self._runtime.request_help(reason, values["tool_call_id"])
        content = {
            "request_id": state.request_id, "request_status": state.request_status,
            "holder": state.holder, "reason_code": values["reason_code"],
        }
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest, None))
        return ExternalWriteResult(content, None, "EFFECT", "COMPUTER_HUMAN_REQUEST")

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class ComputerNavigationExecutor(Executor):
    """Navigates once to an authorized URL, then re-checks the FINAL origin against the same
    trusted allow-list (a redirect out of scope is a failed, reported effect)."""

    action = opcode = "browser.navigate"
    arg_names: Tuple[str, ...] = ()
    binds_identity = False

    def __init__(self, runtime, allowed_origins) -> None:
        self._runtime = runtime
        self._allowed = canonical_allowed_origins(allowed_origins)
        self._effects: List[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        if tuple(args) != ():
            raise ExecutorError("browser.navigate takes no extra args")
        try:
            requested = canonical_origin(resource)
        except ValueError:
            raise ExecutorError("browser.navigate url_invalid")
        if requested not in self._allowed:
            raise ExecutorError("browser.navigate origin_not_allowed")
        final_url = self._runtime.navigate(resource)
        try:
            final = canonical_origin(final_url)
        except ValueError:
            final = None
        if final not in self._allowed:
            raise ExecutorError("browser.navigate redirect_out_of_scope")
        content = {"requested_origin": requested, "final_origin": final}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, (), digest, None))
        return ExternalWriteResult(content, None, "EFFECT", "COMPUTER_NAVIGATION")

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class ComputerInteractionExecutor(Executor):
    """Dispatches one already-authorized typed browser effect."""

    # The browser session identity is carried in the authorization record and
    # checked by the executor; it is not a filesystem-style BindingTable handle.
    binds_identity = False

    def __init__(self, action: str, runtime) -> None:
        if action not in {"browser.click", "browser.type", "browser.key", "browser.select_option"}:
            raise ValueError("unsupported_computer_interaction")
        self.action = self.opcode = action
        self.arg_names = {
            "browser.click": ("browser_session_id", "ref", "snapshot_id"),
            "browser.type": ("browser_session_id", "ref", "snapshot_id", "text"),
            "browser.key": ("browser_session_id", "key", "ref", "snapshot_id"),
            "browser.select_option": ("browser_session_id", "option", "ref", "ref_role", "snapshot_id"),
        }[action]
        self._runtime, self._effects = runtime, []

    def run(self, resource, args, identity, binding=None):
        values = dict(args)
        if values.get("browser_session_id") != identity:
            raise ExecutorError("browser_session_mismatch")
        if self.action == "browser.click":
            result = self._runtime.click(resource, identity, int(values["snapshot_id"]), values["ref"])
        elif self.action == "browser.type":
            result = self._runtime.type(resource, identity, int(values["snapshot_id"]), values["ref"], values["text"])
        elif self.action == "browser.select_option":
            result = self._runtime.select_option(resource, identity, int(values["snapshot_id"]), values["ref"], values["option"])
        else:
            result = self._runtime.key(resource, identity, values["key"], values.get("ref") or None, int(values["snapshot_id"]))
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, args, digest, identity))
        return ExternalWriteResult(result, identity, "EFFECT", "COMPUTER_INTERACTION")

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


@dataclass(frozen=True)
class ExternalReadResult:
    content: Mapping[str, Any]
    identity: Optional[str] = None
    result_authority: str = "DATA_ONLY"
    result_provenance: str = "TOOL_RESULT"


@dataclass(frozen=True)
class ExternalWriteResult:
    content: Mapping[str, Any]
    identity: Optional[str] = None
    result_authority: str = "EFFECT"
    result_provenance: str = "PROVIDER_ACCEPTED"


class _OpenDotsExecutor(Executor):
    """Closed transport for the two typed OpenDots page capabilities."""

    def __init__(self, base_url: str, owner_token: str, transport=None) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("opendots_base_url_invalid")
        if not isinstance(owner_token, str) or not owner_token:
            raise ValueError("opendots_owner_token_required")
        self._base_url = base_url.rstrip("/")
        self._owner_token = owner_token
        self._transport = transport
        self._effects: List[EffectRecord] = []

    def _request(self, method: str, path: str, body: Optional[Mapping[str, str]] = None):
        headers = {"authorization": "Bearer " + self._owner_token, "accept": "application/json"}
        with httpx.Client(transport=self._transport, timeout=10.0, follow_redirects=False,
                          headers=headers) as client:
            if method == "POST":
                return client.post(self._base_url + path, json=dict(body or {}),
                                   headers={"content-type": "application/json"})
            if method == "GET":
                return client.get(self._base_url + path)
        raise ExecutorError("opendots_method_invalid")

    @staticmethod
    def _json(response, expected_status: int) -> Mapping[str, Any]:
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            raise ExecutorError("opendots_redirect_rejected")
        if response.status_code != expected_status:
            raise ExecutorError("opendots_status_unexpected")
        try:
            value = response.json()
        except ValueError as exc:
            raise ExecutorError("opendots_response_invalid") from exc
        if not isinstance(value, dict):
            raise ExecutorError("opendots_response_invalid")
        return value

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class OpenDotsPageCreateExecutor(_OpenDotsExecutor):
    action = opcode = CREATE_ACTION
    arg_names = CREATE_ARGS
    binds_identity = False

    def run(self, resource, args, identity, binding=None):
        if resource != RESOURCE or tuple(k for k, _ in args) != self.arg_names:
            raise ExecutorError("opendots_create_scope")
        values = dict(args)
        space_id = _identity(values.get("space_id"), "space_id")
        title = _text(values.get("title"), "title")
        content = _text(values.get("content"), "content", allow_empty=True)
        path = "/api/spaces/" + quote(space_id, safe="") + "/pages"
        body = {"title": title, "content": content}
        response = self._request("POST", path, body)
        payload = self._json(response, 201)
        if payload.get("spaceId") != space_id or payload.get("title") != title or payload.get("content") != content:
            raise ExecutorError("opendots_create_identity_mismatch")
        page_id = _identity(payload.get("id"), "page_id")
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise ExecutorError("opendots_revision_invalid")
        receipt = {"space_id": space_id, "page_id": page_id, "revision": revision,
                   "title": title, "content": content}
        digest = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest, page_id))
        return ExternalWriteResult(receipt, page_id, "EFFECT", "OPENDOTS_PAGE_CREATED")


class OpenDotsPageReadExecutor(_OpenDotsExecutor):
    action = opcode = READ_ACTION
    arg_names = READ_ARGS
    binds_identity = False

    def run(self, resource, args, identity, binding=None):
        if resource != RESOURCE or tuple(k for k, _ in args) != self.arg_names:
            raise ExecutorError("opendots_read_scope")
        values = dict(args)
        space_id = _identity(values.get("space_id"), "space_id")
        page_id = _identity(values.get("page_id"), "page_id")
        expected_revision = values.get("expected_revision")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, str) or not expected_revision.isdigit() or int(expected_revision) <= 0:
            raise ExecutorError("expected_revision_invalid")
        path = "/api/spaces/" + quote(space_id, safe="") + "/pages/" + quote(page_id, safe="")
        payload = self._json(self._request("GET", path), 200)
        if (payload.get("id") != page_id or payload.get("spaceId") != space_id
                or payload.get("revision") != int(expected_revision)
                or not isinstance(payload.get("title"), str)
                or not isinstance(payload.get("content"), str)):
            raise ExecutorError("opendots_read_identity_mismatch")
        observation = {"space_id": space_id, "page_id": page_id,
                       "revision": int(expected_revision), "title": payload["title"],
                       "content": payload["content"]}
        digest = hashlib.sha256(json.dumps(observation, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest, page_id))
        return ExternalReadResult(observation, page_id, "DATA_ONLY", "OPENDOTS_PAGE_READBACK")


def opendots_executors(base_url: str, owner_token: str, transport=None) -> Mapping[str, Executor]:
    """Construct the closed OpenDots registry extension with runtime-only secrets."""
    return {
        CREATE_ACTION: OpenDotsPageCreateExecutor(base_url, owner_token, transport),
        READ_ACTION: OpenDotsPageReadExecutor(base_url, owner_token, transport),
    }


class _FakeExternalExecutor(Executor):
    arg_names = ("request",)
    binds_identity = False

    def __init__(self) -> None:
        self._effects: List[EffectRecord] = []

    def run(self, resource, args, identity, binding=None):
        if tuple(k for k, _ in args) != self.arg_names:
            raise ExecutorError("args_shape")
        descriptor = json.loads(resource)
        request = json.loads(dict(args)["request"])
        if self.action == "web.search":
            domains = descriptor.get("allowed_domains")
            requested = request.get("requested_domains")
            if not domains or not requested or not set(requested).issubset(set(domains)):
                raise ExecutorError("web_domain_scope_exceeded")
        content = self.fixture(descriptor, request)
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), json.dumps(content, sort_keys=True)))
        return ExternalReadResult(content)

    def effects(self):
        return tuple(self._effects)


class FakeEmailReadExecutor(_FakeExternalExecutor):
    action = opcode = "email.read"
    def fixture(self, resource, request):
        return {"messages": [{"id": "fixture-email-1", "subject": "Daily brief input", "body": "data only"}]}


class FakeCalendarReadExecutor(_FakeExternalExecutor):
    action = opcode = "calendar.read"
    def fixture(self, resource, request):
        return {"events": [{"id": "fixture-event-1", "title": "Daily brief input", "start": "09:00"}]}


class FakeWebSearchExecutor(_FakeExternalExecutor):
    action = opcode = "web.search"
    def fixture(self, resource, request):
        return {"results": [{"url": "https://" + request["requested_domains"][0] + "/fixture", "title": "Daily brief input"}]}


class BraveConnectorError(Exception):
    """Typed, secret-free failure at the Brave connector boundary."""


class BraveSearchExecutor(Executor):
    """The only real network executor in this slice; provider and host are fixed."""

    action = opcode = "web.search"
    arg_names = ("request",)
    binds_identity = False
    _endpoint = "https://api.search.brave.com/res/v1/web/search"
    _host = "api.search.brave.com"

    def __init__(self, transport=None, api_key_getter=None, timeout=10.0) -> None:
        self._transport = transport
        self._api_key_getter = api_key_getter or (lambda: os.environ.get("BRAVE_SEARCH_API_KEY"))
        self._timeout = httpx.Timeout(timeout)
        self._effects: List[EffectRecord] = []
        self.last_domain_filter: Optional[Dict[str, Any]] = None

    def _request_query(self, request, domains):
        query = request.get("query")
        limit = request.get("result_limit")
        freshness = request.get("freshness")
        if not isinstance(query, str) or not query.strip():
            raise BraveConnectorError("query_required")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
            raise BraveConnectorError("result_limit_invalid")
        if freshness is not None and not isinstance(freshness, str):
            raise BraveConnectorError("freshness_invalid")
        if not domains or not isinstance(domains, list) or any(not isinstance(d, str) or not d for d in domains):
            raise BraveConnectorError("authorized_domains_required")
        requested = request.get("requested_domains", domains)
        if not isinstance(requested, list) or not requested or not set(requested).issubset(set(domains)):
            raise BraveConnectorError("domain_scope_exceeded")
        site_filter = " OR ".join("site:" + d for d in requested)
        return f"({query.strip()}) ({site_filter})"

    def run(self, resource, args, identity, binding=None):
        if tuple(k for k, _ in args) != self.arg_names:
            raise BraveConnectorError("args_shape")
        try:
            descriptor = json.loads(resource)
            request = json.loads(dict(args)["request"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise BraveConnectorError("descriptor_invalid") from exc
        if not isinstance(descriptor, dict) or not isinstance(request, dict):
            raise BraveConnectorError("descriptor_invalid")
        query = self._request_query(request, descriptor.get("allowed_domains"))
        parsed_endpoint = urlparse(self._endpoint)
        if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != self._host:
            raise BraveConnectorError("network_destination_rejected")
        api_key = self._api_key_getter()
        if not api_key:
            raise BraveConnectorError("credential_absent")
        params = {"q": query, "count": request["result_limit"]}
        if request.get("freshness") is not None:
            params["freshness"] = request["freshness"]
        requested = request.get("requested_domains", descriptor["allowed_domains"])
        _LOGGER.info(
            "brave_request endpoint=%s method=GET content_type=NONE "
            "token_header_present=YES token_nonempty=%s q_length=%d q_word_count=%d "
            "count=%d freshness_present=%s domain_filter_present=%s requested_domain_count=%d",
            self._endpoint, "YES" if bool(api_key) else "NO", len(query), len(query.split()),
            request["result_limit"], "YES" if request.get("freshness") is not None else "NO",
            "YES" if "site:" in query else "NO", len(requested),
        )
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
                headers={"x-subscription-token": api_key, "accept": "application/json"},
            ) as client:
                response = client.get(self._endpoint, params=params)
        except httpx.HTTPError as exc:
            _LOGGER.info(
                "brave_response transport_error=%s response_received=NO "
                "response_body_logged=NO secret_logged=NO", type(exc).__name__,
            )
            raise BraveConnectorError("network_error") from exc
        error_status = error_code = None
        error_detail_present = "NO"
        if response.status_code >= 400:
            try:
                error_body = response.json()
                error = error_body.get("error") if isinstance(error_body, dict) else None
                if isinstance(error, dict):
                    error_status = error.get("status") if isinstance(error.get("status"), (str, int)) else None
                    error_code = error.get("code") if isinstance(error.get("code"), (str, int)) else None
                    error_detail_present = "YES" if "detail" in error else "NO"
            except ValueError:
                pass
        _LOGGER.info(
            "brave_response http_status=%d response_content_type=%s error_response_parsed=%s "
            "brave_error_status=%s brave_error_code=%s brave_error_detail_present=%s "
            "response_body_logged=NO secret_logged=NO",
            response.status_code, response.headers.get("content-type", "NONE"),
            "YES" if error_status is not None or error_code is not None else "NO",
            error_status or "NONE", error_code if error_code is not None else "NONE", error_detail_present,
        )
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            raise BraveConnectorError("redirect_rejected")
        if response.status_code == 401 or response.status_code == 403:
            raise BraveConnectorError("provider_credential_or_scope")
        if response.status_code == 429:
            raise BraveConnectorError("provider_rate_limit")
        if response.status_code >= 500:
            raise BraveConnectorError("provider_unavailable")
        if response.status_code >= 400:
            raise BraveConnectorError("provider_request_rejected")
        try:
            body = response.json()
        except ValueError as exc:
            raise BraveConnectorError("provider_invalid_json") from exc
        if not isinstance(body, dict):
            raise BraveConnectorError("provider_response_invalid")
        web = body.get("web")
        if web is None:
            raw_results = []
        elif not isinstance(web, dict):
            raise BraveConnectorError("provider_response_invalid")
        else:
            raw_results = web.get("results")
            if raw_results is None:
                raw_results = []
            elif not isinstance(raw_results, list):
                raise BraveConnectorError("provider_response_invalid")
        allowed = tuple(descriptor["allowed_domains"])
        results = []
        returned_hosts = []
        accepted_hosts = []
        rejected_hosts = []
        for item in raw_results:
            if not isinstance(item, dict):
                raise BraveConnectorError("provider_response_invalid")
            if not isinstance(item.get("url"), str):
                continue
            hostname = (urlparse(item["url"]).hostname or "").lower()
            returned_hosts.append(hostname)
            if not any(host_matches_allowed_domain(hostname, domain) for domain in allowed):
                rejected_hosts.append(hostname)
                continue
            accepted_hosts.append(hostname)
            results.append({
                "title": item.get("title"),
                "url": item["url"],
                "description": item.get("description"),
                "age": item.get("age"),
            })
        self.last_domain_filter = {
            "expected_domains": sorted(set(allowed)),
            "returned_hosts": returned_hosts,
            "accepted_hosts": accepted_hosts,
            "rejected_hosts": rejected_hosts,
            "result_count": len(raw_results),
            "in_scope_count": len(results),
        }
        content = {"results": results, "truncated": False}
        _LOGGER.info(
            "brave_domain_postfilter provider_results_count=%d in_scope_results_count=%d "
            "out_of_scope_results_dropped=%d",
            len(raw_results), len(results), len(rejected_hosts),
        )
        if not results and raw_results:
            raise BraveConnectorError("result_domain_out_of_scope")
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest))
        return ExternalReadResult(content)


@dataclass(frozen=True, repr=False)
class CredentialProfile:
    """Provisioned, provider-bound credential metadata; never an authority field."""

    provider_ref: str
    account_ref: str
    provider_account_ref: str
    access_token: str = field(repr=False)
    expires_at: Optional[float] = None
    scopes: Tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            "CredentialProfile(provider_ref=%r, account_ref=%r, "
            "provider_account_ref=%r, access_token=<redacted>, expires_at=%r)"
            % (self.provider_ref, self.account_ref, self.provider_account_ref, self.expires_at)
        )


class CredentialResolver:
    """Explicit resolver boundary. It does not discover files or refresh tokens."""

    def lookup(self, account_ref: str, provider_ref: str) -> Optional[CredentialProfile]:
        raise NotImplementedError


class MappingCredentialResolver(CredentialResolver):
    def __init__(self, profiles: Mapping[Tuple[str, str], CredentialProfile]) -> None:
        self._profiles = dict(profiles)

    def lookup(self, account_ref: str, provider_ref: str) -> Optional[CredentialProfile]:
        return self._profiles.get((account_ref, provider_ref))


class GoogleCalendarConnectorError(Exception):
    """Typed, secret-free Calendar connector failure."""


def _recover_rejected_google_credential(resolver: CredentialResolver, resource_obj: Mapping[str, Any]) -> bool:
    recover = getattr(resolver, "refresh_after_provider_rejection", None)
    if not callable(recover):
        return False
    return bool(recover(resource_obj["account_ref"], resource_obj["provider_ref"]))


class GoogleCalendarReadExecutor(Executor):
    action = opcode = "calendar.read"
    arg_names = ("request",)
    binds_identity = False
    _endpoint_base = "https://www.googleapis.com/calendar/v3/calendars/"
    _host = "www.googleapis.com"
    _provider_ref = "google.calendar.v3"

    def __init__(self, credential_resolver: CredentialResolver, transport=None, clock=None, timeout=10.0) -> None:
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._clock = clock or time.time
        self._timeout = httpx.Timeout(timeout)
        self._effects: List[EffectRecord] = []

    def _parse(self, resource, args):
        if tuple(k for k, _ in args) != self.arg_names:
            raise GoogleCalendarConnectorError("args_shape")
        try:
            resource_obj = json.loads(resource)
            request = json.loads(dict(args)["request"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise GoogleCalendarConnectorError("descriptor_invalid") from exc
        if not isinstance(resource_obj, dict) or not isinstance(request, dict):
            raise GoogleCalendarConnectorError("descriptor_invalid")
        if set(resource_obj) != {"provider_ref", "account_ref", "calendar_ref"}:
            raise GoogleCalendarConnectorError("resource_shape")
        if any(not isinstance(resource_obj[key], str) or not resource_obj[key] for key in resource_obj):
            raise GoogleCalendarConnectorError("resource_shape")
        if set(request) != {"start", "end", "limit", "fields"}:
            raise GoogleCalendarConnectorError("args_shape")
        if not isinstance(request["start"], str) or not isinstance(request["end"], str):
            raise GoogleCalendarConnectorError("time_window_invalid")
        if not isinstance(request["limit"], int) or isinstance(request["limit"], bool) or not 1 <= request["limit"] <= 2500:
            raise GoogleCalendarConnectorError("limit_invalid")
        if not isinstance(request["fields"], str) or not request["fields"]:
            raise GoogleCalendarConnectorError("fields_invalid")
        if resource_obj["provider_ref"] != self._provider_ref:
            raise GoogleCalendarConnectorError("provider_mismatch")
        return resource_obj, request

    def run(self, resource, args, identity, binding=None):
        return self._run_once(resource, args, identity, binding, recovery_attempted=False)

    def _run_once(self, resource, args, identity, binding=None, recovery_attempted=False):
        resource_obj, request = self._parse(resource, args)
        endpoint = self._endpoint_base + quote(resource_obj["calendar_ref"], safe="") + "/events"
        parsed_endpoint = urlparse(endpoint)
        if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != self._host:
            raise GoogleCalendarConnectorError("network_destination_rejected")
        params = {
            "timeMin": request["start"], "timeMax": request["end"],
            "maxResults": request["limit"], "fields": request["fields"],
            "singleEvents": "true",
        }
        while True:
            profile = self._credential_resolver.lookup(resource_obj["account_ref"], resource_obj["provider_ref"])
            if profile is None or not profile.access_token:
                raise GoogleCalendarConnectorError("credential_missing")
            if profile.provider_ref != resource_obj["provider_ref"] or profile.account_ref != resource_obj["account_ref"]:
                raise GoogleCalendarConnectorError("credential_binding_mismatch")
            if profile.expires_at is not None and profile.expires_at <= self._clock():
                raise GoogleCalendarConnectorError("credential_expired")
            try:
                with httpx.Client(
                    transport=self._transport, timeout=self._timeout, follow_redirects=False,
                    headers={"authorization": "Bearer " + profile.access_token, "accept": "application/json"},
                ) as client:
                    response = client.get(endpoint, params=params)
            except httpx.HTTPError as exc:
                raise GoogleCalendarConnectorError("network_error") from exc
            if response.status_code == 401:
                if recovery_attempted:
                    raise GoogleCalendarConnectorError("credential_rejected")
                try:
                    recovered = _recover_rejected_google_credential(self._credential_resolver, resource_obj)
                except Exception as refresh_exc:
                    raise GoogleCalendarConnectorError("credential_refresh_failed") from refresh_exc
                if not recovered:
                    raise GoogleCalendarConnectorError("credential_rejected")
                recovery_attempted = True
                continue
            break
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            raise GoogleCalendarConnectorError("redirect_rejected")
        if response.status_code == 403:
            raise GoogleCalendarConnectorError("provider_scope_or_access_denied")
        if response.status_code == 404:
            raise GoogleCalendarConnectorError("calendar_not_found")
        if response.status_code == 429:
            raise GoogleCalendarConnectorError("provider_rate_limit")
        if response.status_code >= 500:
            raise GoogleCalendarConnectorError("provider_unavailable")
        if response.status_code >= 400:
            raise GoogleCalendarConnectorError("provider_request_rejected")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleCalendarConnectorError("provider_response_invalid") from exc
        if not isinstance(body, dict) or not isinstance(body.get("items", []), list):
            raise GoogleCalendarConnectorError("provider_response_invalid")
        events = []
        for item in body.get("items", []):
            if not isinstance(item, dict):
                raise GoogleCalendarConnectorError("provider_response_invalid")
            start = item.get("start") if isinstance(item.get("start"), dict) else {}
            end = item.get("end") if isinstance(item.get("end"), dict) else {}
            events.append({
                "event_ref": item.get("id"), "start": start.get("dateTime", start.get("date")),
                "end": end.get("dateTime", end.get("date")),
                "title": item.get("summary"), "location": item.get("location"),
            })
        content = {"events": events, "truncated": False}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest))
        return ExternalReadResult(content)


class GoogleGmailConnectorError(Exception):
    """Typed, secret-free Gmail bounded-plan failure."""


class GoogleGmailSendConnectorError(Exception):
    """Typed, secret-free Gmail write failure. Never retries or refreshes."""


class GoogleGmailSendExecutor(Executor):
    """One governed Gmail send. The provider request is deliberately singular."""

    action = "email.send"
    opcode = "EMAIL_SEND"
    arg_names = ("request",)
    binds_identity = False
    _endpoint = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    _host = "gmail.googleapis.com"
    _provider_ref = "google.gmail.v1"
    _required_scope = "https://www.googleapis.com/auth/gmail.send"

    def __init__(self, credential_resolver: CredentialResolver, transport=None, clock=None, timeout=10.0) -> None:
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._clock = clock or time.time
        self._timeout = httpx.Timeout(timeout)
        self._effects: List[EffectRecord] = []

    @staticmethod
    def _parse(resource, args):
        if tuple(k for k, _ in args) != ("request",):
            raise GoogleGmailSendConnectorError("args_shape")
        try:
            resource_obj = json.loads(resource)
            request = json.loads(dict(args)["request"])
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise GoogleGmailSendConnectorError("descriptor_invalid") from exc
        if not isinstance(resource_obj, dict) or not isinstance(request, dict):
            raise GoogleGmailSendConnectorError("descriptor_invalid")
        if set(resource_obj) != {"provider_ref", "account_ref", "recipient"}:
            raise GoogleGmailSendConnectorError("resource_shape")
        if resource_obj.get("provider_ref") != GoogleGmailSendExecutor._provider_ref:
            raise GoogleGmailSendConnectorError("provider_mismatch")
        if any(not isinstance(resource_obj[key], str) or not resource_obj[key] for key in resource_obj):
            raise GoogleGmailSendConnectorError("resource_shape")
        if set(request) != {"to", "subject", "body_text", "daily_brief_run_id"}:
            raise GoogleGmailSendConnectorError("args_shape")
        if request["to"] != resource_obj["recipient"] or any(
            not isinstance(request[key], str) or not request[key] for key in request
        ):
            raise GoogleGmailSendConnectorError("request_scope_mismatch")
        if any("\r" in request[key] or "\n" in request[key] for key in ("to", "subject")):
            raise GoogleGmailSendConnectorError("header_injection_rejected")
        if "@" not in request["to"]:
            raise GoogleGmailSendConnectorError("recipient_invalid")
        return resource_obj, request

    @staticmethod
    def _mime(request) -> bytes:
        message = EmailMessage()
        message["To"] = request["to"]
        message["Subject"] = request["subject"]
        message.set_content(request["body_text"])
        return message.as_bytes()

    def run(self, resource, args, identity, binding=None):
        resource_obj, request = self._parse(resource, args)
        profile = self._credential_resolver.lookup(resource_obj["account_ref"], self._provider_ref)
        if profile is None or not profile.access_token:
            raise GoogleGmailSendConnectorError("credential_missing")
        if profile.provider_ref != self._provider_ref or profile.account_ref != resource_obj["account_ref"]:
            raise GoogleGmailSendConnectorError("credential_binding_mismatch")
        if self._required_scope not in set(profile.scopes):
            raise GoogleGmailSendConnectorError("scope_missing")
        if profile.expires_at is not None and profile.expires_at <= self._clock():
            raise GoogleGmailSendConnectorError("credential_expired")
        mime = self._mime(request)
        raw = base64.urlsafe_b64encode(mime).rstrip(b"=").decode("ascii")
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout, follow_redirects=False,
                headers={"authorization": "Bearer " + profile.access_token, "accept": "application/json"},
            ) as client:
                response = client.post(self._endpoint, json={"raw": raw})
        except httpx.TimeoutException as exc:
            raise GoogleGmailSendConnectorError("provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise GoogleGmailSendConnectorError("network_error") from exc
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            raise GoogleGmailSendConnectorError("redirect_rejected")
        if response.status_code == 401:
            raise GoogleGmailSendConnectorError("credential_rejected")
        if response.status_code == 403:
            raise GoogleGmailSendConnectorError("provider_scope_or_access_denied")
        if response.status_code == 429:
            raise GoogleGmailSendConnectorError("provider_rate_limit")
        if response.status_code >= 500:
            raise GoogleGmailSendConnectorError("provider_unavailable")
        if response.status_code >= 400:
            raise GoogleGmailSendConnectorError("provider_request_rejected")
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleGmailSendConnectorError("provider_response_invalid") from exc
        message_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise GoogleGmailSendConnectorError("provider_response_invalid")
        digest = hashlib.sha256(mime).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest))
        return ExternalWriteResult({"provider_message_id": message_id})

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class GoogleGmailReadExecutor(Executor):
    """Execute one bounded Gmail logical read as list-then-detail requests."""

    action = opcode = "email.read"
    arg_names = ("request",)
    binds_identity = False
    _endpoint_base = "https://gmail.googleapis.com/gmail/v1/users/"
    _host = "gmail.googleapis.com"
    _provider_ref = "google.gmail.v1"
    _allowed_fields = frozenset({"message_ref", "received_at", "sender", "subject", "snippet"})

    def __init__(self, credential_resolver: CredentialResolver, transport=None, clock=None, timeout=10.0) -> None:
        self._credential_resolver = credential_resolver
        self._transport = transport
        self._clock = clock or time.time
        self._timeout = httpx.Timeout(timeout)
        self._effects: List[EffectRecord] = []
        self._execution_id: Optional[str] = None
        self.last_execution_id: Optional[str] = None
        self._derived_refs: set[str] = set()
        self.last_execution_metrics: Mapping[str, int] = MappingProxyType({})

    def begin_execution(self, execution_id: str) -> None:
        self._execution_id = execution_id
        self.last_execution_id = execution_id
        self._derived_refs = set()
        self.last_execution_metrics = MappingProxyType({
            "list_request_count": 0, "detail_request_count": 0,
            "derived_ref_count": 0, "completed_steps": 0,
        })

    def end_execution(self) -> None:
        self._execution_id = None
        self._derived_refs = set()

    def _parse(self, resource, args):
        if tuple(k for k, _ in args) != self.arg_names:
            raise GoogleGmailConnectorError("args_shape")
        try:
            resource_obj = json.loads(resource)
            request = json.loads(dict(args)["request"])
        except (TypeError, json.JSONDecodeError, KeyError) as exc:
            raise GoogleGmailConnectorError("descriptor_invalid") from exc
        if not isinstance(resource_obj, dict) or not isinstance(request, dict):
            raise GoogleGmailConnectorError("descriptor_invalid")
        if set(resource_obj) != {"provider_ref", "account_ref", "mailbox_ref"}:
            raise GoogleGmailConnectorError("resource_shape")
        if any(not isinstance(resource_obj[key], str) or not resource_obj[key] for key in resource_obj):
            raise GoogleGmailConnectorError("resource_shape")
        if resource_obj["provider_ref"] != self._provider_ref:
            raise GoogleGmailConnectorError("provider_mismatch")
        if set(request) != {"query", "time_window", "limit", "fields"}:
            raise GoogleGmailConnectorError("args_shape")
        if not isinstance(request["query"], str):
            raise GoogleGmailConnectorError("query_invalid")
        window = request["time_window"]
        if not isinstance(window, dict) or set(window) != {"after", "before"}:
            raise GoogleGmailConnectorError("time_window_invalid")
        if any(not isinstance(window[key], str) or not window[key] for key in window):
            raise GoogleGmailConnectorError("time_window_invalid")
        if not isinstance(request["limit"], int) or isinstance(request["limit"], bool) or not 1 <= request["limit"] <= 500:
            raise GoogleGmailConnectorError("limit_invalid")
        fields = request["fields"]
        if not isinstance(fields, list) or not fields or any(not isinstance(field, str) for field in fields):
            raise GoogleGmailConnectorError("fields_invalid")
        if len(set(fields)) != len(fields) or not set(fields).issubset(self._allowed_fields):
            raise GoogleGmailConnectorError("fields_invalid")
        return resource_obj, request

    @staticmethod
    def _query(request) -> str:
        terms = []
        if request["query"].strip():
            terms.append(request["query"].strip())
        terms.extend((f"after:{request['time_window']['after']}", f"before:{request['time_window']['before']}"))
        return " ".join(terms)

    @staticmethod
    def _headers(payload) -> dict[str, str]:
        headers = payload.get("headers", []) if isinstance(payload, dict) else []
        if not isinstance(headers, list):
            raise GoogleGmailConnectorError("provider_response_invalid")
        selected = {}
        for header in headers:
            if not isinstance(header, dict):
                raise GoogleGmailConnectorError("provider_response_invalid")
            name, value = header.get("name"), header.get("value")
            if isinstance(name, str) and isinstance(value, str) and name.lower() in {"from", "subject"}:
                selected[name.lower()] = value
        return selected

    @staticmethod
    def _check_response(response) -> None:
        if response.is_redirect or response.status_code in {301, 302, 303, 307, 308}:
            raise GoogleGmailConnectorError("redirect_rejected")
        if response.status_code == 401:
            raise GoogleGmailConnectorError("credential_rejected")
        if response.status_code == 403:
            raise GoogleGmailConnectorError("provider_scope_or_access_denied")
        if response.status_code == 404:
            raise GoogleGmailConnectorError("mailbox_or_message_not_found")
        if response.status_code == 429:
            raise GoogleGmailConnectorError("provider_rate_limit")
        if response.status_code >= 500:
            raise GoogleGmailConnectorError("provider_unavailable")
        if response.status_code >= 400:
            raise GoogleGmailConnectorError("provider_request_rejected")

    @staticmethod
    def _json_object(response) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleGmailConnectorError("provider_response_invalid") from exc
        if not isinstance(body, dict):
            raise GoogleGmailConnectorError("provider_response_invalid")
        return body

    def _detail_projection(self, message, fields) -> dict:
        forbidden = {"body", "parts", "raw", "attachments"}
        if forbidden.intersection(message):
            raise GoogleGmailConnectorError("body_or_attachment_returned")
        payload = message.get("payload", {})
        if isinstance(payload, dict) and forbidden.intersection(payload):
            raise GoogleGmailConnectorError("body_or_attachment_returned")
        if not isinstance(message.get("id"), str) or not message["id"]:
            raise GoogleGmailConnectorError("provider_response_invalid")
        headers = self._headers(payload)
        item = {}
        if "message_ref" in fields:
            item["message_ref"] = message["id"]
        if "received_at" in fields:
            item["received_at"] = message.get("internalDate")
        if "sender" in fields:
            item["sender"] = headers.get("from")
        if "subject" in fields:
            item["subject"] = headers.get("subject")
        if "snippet" in fields:
            item["snippet"] = message.get("snippet")
        return item

    def run(self, resource, args, identity, binding=None):
        return self._run_once(resource, args, identity, binding, recovery_attempted=False)

    def _run_once(self, resource, args, identity, binding=None, recovery_attempted=False):
        resource_obj, request = self._parse(resource, args)
        profile = self._credential_resolver.lookup(resource_obj["account_ref"], resource_obj["provider_ref"])
        if profile is None or not profile.access_token:
            raise GoogleGmailConnectorError("credential_missing")
        if profile.provider_ref != resource_obj["provider_ref"] or profile.account_ref != resource_obj["account_ref"]:
            raise GoogleGmailConnectorError("credential_binding_mismatch")
        if profile.expires_at is not None and profile.expires_at <= self._clock():
            raise GoogleGmailConnectorError("credential_expired")
        if self._execution_id is None:
            raise GoogleGmailConnectorError("execution_context_missing")
        endpoint = self._endpoint_base + quote(resource_obj["mailbox_ref"], safe="") + "/messages"
        parsed_endpoint = urlparse(endpoint)
        if parsed_endpoint.scheme != "https" or parsed_endpoint.hostname != self._host:
            raise GoogleGmailConnectorError("network_destination_rejected")
        headers = {"authorization": "Bearer " + profile.access_token, "accept": "application/json"}
        list_params = {
            "q": self._query(request), "maxResults": request["limit"], "includeSpamTrash": "false",
        }
        fields = tuple(request["fields"])
        items = []
        list_count = detail_count = completed_steps = 0
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False, headers=headers) as client:
                response = client.get(endpoint, params=list_params)
                list_count = 1
                self.last_execution_metrics = MappingProxyType({
                    "list_request_count": list_count, "detail_request_count": 0,
                    "derived_ref_count": 0, "completed_steps": 0,
                })
                try:
                    self._check_response(response)
                except GoogleGmailConnectorError as exc:
                    if str(exc) == "credential_rejected" and not recovery_attempted:
                        try:
                            recovered = _recover_rejected_google_credential(self._credential_resolver, resource_obj)
                        except Exception as refresh_exc:
                            raise GoogleGmailConnectorError("credential_refresh_failed") from refresh_exc
                        if recovered:
                            return self._run_once(resource, args, identity, binding, recovery_attempted=True)
                    raise
                body = self._json_object(response)
                messages = body.get("messages", [])
                if not isinstance(messages, list):
                    raise GoogleGmailConnectorError("provider_response_invalid")
                refs = []
                for message in messages[:request["limit"]]:
                    if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]:
                        raise GoogleGmailConnectorError("provider_response_invalid")
                    refs.append(message["id"])
                self._derived_refs = set(refs)
                self.last_execution_metrics = MappingProxyType({
                    "list_request_count": list_count, "detail_request_count": 0,
                    "derived_ref_count": len(refs), "completed_steps": 1,
                })
                for message_ref in refs:
                    if message_ref not in self._derived_refs:
                        raise GoogleGmailConnectorError("derived_message_ref_denied")
                    detail = client.get(
                        endpoint + "/" + quote(message_ref, safe=""),
                        params={"format": "full", "fields": "id,internalDate,snippet,payload/headers"},
                    )
                    detail_count += 1
                    self.last_execution_metrics = MappingProxyType({
                        "list_request_count": list_count, "detail_request_count": detail_count,
                        "derived_ref_count": len(refs), "completed_steps": 1 + detail_count,
                    })
                    try:
                        self._check_response(detail)
                    except GoogleGmailConnectorError as exc:
                        if str(exc) == "credential_rejected" and not recovery_attempted:
                            try:
                                recovered = _recover_rejected_google_credential(self._credential_resolver, resource_obj)
                            except Exception as refresh_exc:
                                raise GoogleGmailConnectorError("credential_refresh_failed") from refresh_exc
                            if recovered:
                                return self._run_once(resource, args, identity, binding, recovery_attempted=True)
                        raise
                    items.append(self._detail_projection(self._json_object(detail), fields))
                    completed_steps += 1
        except httpx.HTTPError as exc:
            raise GoogleGmailConnectorError("network_error") from exc
        finally:
            self._derived_refs = set()
        content = {"items": items, "truncated": bool(body.get("nextPageToken"))}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, tuple(args), digest))
        self.last_execution_metrics = MappingProxyType({
            "list_request_count": list_count, "detail_request_count": detail_count,
            "derived_ref_count": len(items), "completed_steps": 1 + completed_steps,
        })
        return ExternalReadResult(content)

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class RealFsReadExecutor(Executor):
    action = "fs.read"
    opcode = "FS_READ"
    arg_names: Tuple[str, ...] = ()
    binds_identity = True

    def __init__(self) -> None:
        self._effects: List[EffectRecord] = []

    def run(self, resource: str, args: Tuple[Tuple[str, str], ...], identity: Optional[str],
            binding: Optional[Binding] = None) -> FsReadResult:
        if tuple(args) != ():
            raise ExecutorError("fs.read takes no extra args")
        if not identity:
            raise ExecutorError("identity_required")
        if binding is None:
            raise ExecutorError("binding_required")
        try:
            if binding.final_path != resource:
                raise ExecutorError("resource_identity_mismatch")
            executed = binding.current_identity()  # asked to the handle, not to a path
            if executed != identity:
                raise ExecutorError("resource_identity_mismatch")
            data = binding.read_all()  # the SAME handle opened during authorization
        except BindingError as exc:
            raise ExecutorError(str(exc))
        finally:
            binding.close()
        content = data.decode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        self._effects.append(EffectRecord(self.opcode, resource, (), digest, executed))
        return FsReadResult(content, executed)

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class RealFsReadBytesExecutor(Executor):
    """Same authority as ``RealFsReadExecutor`` (same bound handle, same identity check, no path), but the
    result is the raw bytes. Governance does not know what the bytes are."""

    action = "fs.read_bytes"
    opcode = "FS_READ_BYTES"
    arg_names: Tuple[str, ...] = ()
    binds_identity = True

    def __init__(self) -> None:
        self._effects: List[EffectRecord] = []

    def run(self, resource: str, args: Tuple[Tuple[str, str], ...], identity: Optional[str],
            binding: Optional[Binding] = None) -> FsReadBytesResult:
        if tuple(args) != ():
            raise ExecutorError("fs.read_bytes takes no extra args")
        if not identity:
            raise ExecutorError("identity_required")
        if binding is None:
            raise ExecutorError("binding_required")
        try:
            if binding.final_path != resource:
                raise ExecutorError("resource_identity_mismatch")
            executed = binding.current_identity()  # asked to the handle, not to a path
            if executed != identity:
                raise ExecutorError("resource_identity_mismatch")
            data = binding.read_all()  # the SAME handle opened during authorization
        except BindingError as exc:
            raise ExecutorError(str(exc))
        finally:
            binding.close()
        digest = hashlib.sha256(data).hexdigest()  # over the raw bytes
        self._effects.append(EffectRecord(self.opcode, resource, (), digest, executed))
        return FsReadBytesResult(data, executed)

    def effects(self) -> Tuple[EffectRecord, ...]:
        return tuple(self._effects)


class AEPExecutorRegistry:
    """Immutable after construction; lookup of an unknown action fails closed."""

    def __init__(self, entries: Mapping[str, Executor]) -> None:
        checked: Dict[str, Executor] = {}
        for action, executor in entries.items():
            if not isinstance(executor, Executor):
                raise TypeError("registry entries must be Executor instances")
            if executor.action != action:
                raise ValueError("registry key does not match executor.action")
            checked[action] = executor
        self._entries = MappingProxyType(checked)

    def lookup(self, action: str) -> Executor:
        entry = self._entries.get(action)
        if entry is None:
            raise UnknownExecutor(action)
        return entry

    def actions(self) -> Tuple[str, ...]:
        return tuple(sorted(self._entries))

    def effects(self) -> Tuple[EffectRecord, ...]:
        out: List[EffectRecord] = []
        for executor in self._entries.values():
            out.extend(executor.effects())
        return tuple(out)


def default_registry() -> AEPExecutorRegistry:
    return AEPExecutorRegistry({"fs.read": RealFsReadExecutor(), "fs.read_bytes": RealFsReadBytesExecutor()})
