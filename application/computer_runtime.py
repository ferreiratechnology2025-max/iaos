"""Small experimental port for a governed, read-only computer runtime.

The port exposes one typed operation only. It is deliberately independent of
OpenBot so the experimental adapter can later be replaced without changing
cognition or governance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote, urlparse

import httpx

from .browser_key_contract import is_valid_browser_key


class ComputerRuntimeError(Exception):
    """The computer runtime refused or could not complete a read-only action."""


@dataclass(frozen=True)
class ComputerObservation:
    requested_url: str
    final_url: str
    page_title: str
    visible_text: str
    browser_session_id: str
    observed_at: str

    def as_dict(self) -> Mapping[str, str]:
        return {
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "page_title": self.page_title,
            "visible_text": self.visible_text,
            "browser_session_id": self.browser_session_id,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class PageRead:
    """Result of the V1.4 read path: the page text the server returned plus the facts a caller must fail closed on."""
    requested_url: str
    final_url: str
    title: str
    text: str
    truncated: bool
    challenge: str | None
    session_id: str
    observed_at: str


@dataclass(frozen=True)
class PageRestore:
    """Result of the controlled return: where the navigation ended and where a fresh ordinary observation says the page is."""
    requested_url: str
    nav_final_url: str
    observed_url: str
    challenge: str | None
    session_id: str


class ComputerRuntimePort(Protocol):
    def navigate_and_read(self, requested_url: str) -> ComputerObservation:
        """Navigate once and return read-only page observation."""


@dataclass(frozen=True)
class ComputerElement:
    ref: str
    role: str
    name: str
    checked: bool | None = None
    value: str | None = None

    def as_dict(self) -> Mapping[str, Any]:
        out: dict[str, Any] = {"ref": self.ref, "role": self.role, "name": self.name}
        if self.checked is not None:
            out["checked"] = self.checked
        if self.value is not None:
            out["value"] = self.value
        return out


@dataclass(frozen=True)
class ComputerPageObservation:
    """One coherent capture of the current page: elements and aria share one snapshot_id."""
    page_url: str
    page_title: str
    browser_session_id: str
    snapshot_id: int
    elements: tuple[ComputerElement, ...]
    aria_snapshot: str
    # Closed set reported by OpenBot ("cloudflare" | "visible-challenge"); None when no challenge.
    challenge: str | None = None

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "page_url": self.page_url,
            "page_title": self.page_title,
            "browser_session_id": self.browser_session_id,
            "snapshot_id": self.snapshot_id,
            "elements": [e.as_dict() for e in self.elements],
            "aria_snapshot": self.aria_snapshot,
            "challenge": self.challenge,
        }


class ComputerObservationPort(Protocol):
    def observe(self) -> ComputerPageObservation:
        """Observe the current bound page. Never navigates and never opens a handoff request."""


@dataclass(frozen=True)
class ComputerControlState:
    """Typed runtime telemetry about who holds the wheel. Not world data."""
    holder: str  # "bot" | "human"
    requested: bool
    request_id: str | None
    request_status: str | None  # waiting | taken | completed | cancelled | expired | interrupted
    resume_snapshot_required: bool
    transitioning: bool = False


class ComputerControlStatePort(Protocol):
    def read_control_state(self, request_id: str | None = None) -> ComputerControlState:
        """Read control telemetry, for one request when an id is given."""


class ComputerNavigationPort(Protocol):
    def navigate(self, requested_url: str) -> str:
        """Navigate once; return the FINAL url. Does not read the page and never opens a handoff request."""


class ComputerHumanRequestPort(Protocol):
    def request_help(self, reason: str, tool_call_id: str) -> ComputerControlState:
        """Ask for a person. Idempotent per tool_call_id. Does not take control."""


@dataclass(frozen=True)
class ComputerSnapshot:
    page_url: str
    page_title: str
    snapshot_id: int
    browser_session_id: str
    elements: tuple[ComputerElement, ...]


class ComputerInteractionPort(Protocol):
    def snapshot(self) -> ComputerSnapshot:
        """Return the current page's actionable elements and snapshot identity."""

    def click(self, page_url: str, session_id: str, snapshot_id: int, ref: str) -> Mapping[str, Any]:
        """Click one element from one current snapshot."""

    def type(self, page_url: str, session_id: str, snapshot_id: int, ref: str, text: str) -> Mapping[str, Any]:
        """Fill one element from one current snapshot."""

    def key(self, page_url: str, session_id: str, key: str, ref: str | None = None,
            snapshot_id: int | None = None) -> Mapping[str, Any]:
        """Press one explicit key, optionally against one current element."""


class ComputerControlPort(Protocol):
    def read_control(self) -> Mapping[str, Any]:
        """Read the current Bot/human control state."""

    def take_control(self, request_id: str) -> Mapping[str, Any]:
        """Transfer the browser wheel to the human."""

    def release_control(self, request_id: str) -> Mapping[str, Any]:
        """Return the browser wheel to the Bot."""


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


class OpenBotComputerAdapter:
    """Disposable V0 adapter for the OpenBot ``agent-computer`` HTTP API."""

    def __init__(self, endpoint: str, bot_id: str, computer_token: str, timeout: float = 20.0, transport=None,
                 restore_diagnostic_sink: Callable[[Mapping[str, Any]], None] | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bot_id = bot_id
        self._token = computer_token
        self._timeout = timeout
        self._transport = transport
        self._restore_diagnostic_sink = restore_diagnostic_sink

    def _emit_restore_diagnostic(self, fields: Mapping[str, Any]) -> None:
        sink = self._restore_diagnostic_sink
        if sink is None:
            return
        try:
            sink(dict(fields))
        except Exception:
            # Diagnostics are strictly observational and must never affect the
            # restore result or replace the original exception.
            return

    def navigate_and_read(self, requested_url: str) -> ComputerObservation:
        _validate_https_public_url(requested_url)
        headers = {
            "x-openbot-computer-token": self._token,
            "x-openbot-bot-id": self._bot_id,
        }
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False, transport=self._transport) as client:
                # The computer requires a fresh snapshot before acting. This is
                # observation state, not an additional browser navigation.
                snapshot = client.post(self._endpoint + "/snapshot", headers=headers, json={})
                _raise_http(snapshot, "snapshot")
                response = client.post(
                    self._endpoint + "/navigate",
                    headers=headers,
                    json={"url": requested_url},
                )
                _raise_http(response, "navigate")
                payload = response.json()
                run = client.get(self._endpoint + "/run", headers=headers)
                _raise_http(run, "run")
                run_payload = run.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ComputerRuntimeError("computer_runtime_request_failed") from exc

        final_url = payload.get("url")
        title = payload.get("title")
        text = payload.get("text")
        session_id = run_payload.get("run")
        if not all(isinstance(value, str) and value for value in (final_url, title, session_id)):
            raise ComputerRuntimeError("computer_observation_shape")
        if not isinstance(text, str):
            raise ComputerRuntimeError("computer_visible_text_shape")
        if _origin(final_url) != _origin(requested_url):
            raise ComputerRuntimeError("cross_origin_redirect")
        observed_at = payload.get("observedAt")
        if not isinstance(observed_at, str) or not observed_at:
            # agent-computer V0 returns elapsedMs rather than a timestamp; the
            # adapter stamps evidence at the boundary instead of guessing from it.
            from datetime import datetime, timezone

            observed_at = datetime.now(timezone.utc).isoformat()
        return ComputerObservation(requested_url, final_url, title, text, session_id, observed_at)

    def read_control(self) -> Mapping[str, Any]:
        payload, _ = self._request_json("GET", "/control")
        return payload

    def take_control(self, request_id: str) -> Mapping[str, Any]:
        return self._control_action("/control/take", request_id)

    def take_control_on_demand(self, reason: str = "Human requested takeover from IAOS") -> Mapping[str, Any]:
        """Take the wheel for the human, creating the handoff request first when none is waiting.

        An already-waiting request (the Bot asked for help earlier) is reused, never duplicated.
        The decision is made here, from upstream state, and never from client-supplied ids.
        """
        state = self.read_control()
        if state.get("holder") == "human":
            return state
        request = state.get("request") if state.get("requested") else None
        if not (isinstance(request, dict) and isinstance(request.get("id"), str) and request["id"]):
            state, _ = self._request_json("POST", "/control/request", {"reason": reason})
            request = state.get("request")
        request_id = request.get("id") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not request_id:
            raise ComputerRuntimeError("computer_request_id_missing")
        return self.take_control(request_id)

    def release_control(self, request_id: str) -> Mapping[str, Any]:
        return self._control_action("/control/release", request_id)

    def _control_action(self, path: str, request_id: str) -> Mapping[str, Any]:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ComputerRuntimeError("computer_request_id_invalid")
        payload, _ = self._request_json("POST", path, {"requestId": request_id})
        return payload

    def snapshot(self) -> ComputerSnapshot:
        payload, session_id = self._request_json("POST", "/snapshot", {})
        page_url, title, snapshot_id, elements = self._parse_snapshot(payload)
        self._session_id = session_id
        self._page_url = page_url
        self._snapshot_id = snapshot_id
        return ComputerSnapshot(page_url, title, snapshot_id, session_id, elements)

    def observe(self) -> ComputerPageObservation:
        """One /snapshot call with includeAria; elements and aria share one snapshot_id."""
        payload, session_id = self._request_json("POST", "/snapshot", {"includeAria": True, "requestHelp": False})
        page_url, title, snapshot_id, elements = self._parse_snapshot(payload)
        aria = payload.get("ariaSnapshot")
        if not isinstance(aria, str):
            raise ComputerRuntimeError("computer_aria_snapshot_shape")
        challenge = payload.get("challenge")
        kind = None
        if isinstance(challenge, dict):
            kind = challenge.get("kind")
            if kind not in ("cloudflare", "visible-challenge"):
                raise ComputerRuntimeError("computer_challenge_shape")
        self._session_id = session_id
        self._page_url = page_url
        self._snapshot_id = snapshot_id
        return ComputerPageObservation(page_url, title, session_id, snapshot_id, elements, aria, kind)

    def navigate(self, requested_url: str) -> str:
        _validate_https_public_url(requested_url)
        payload, _ = self._request_json("POST", "/navigate", {"url": requested_url, "requestHelp": False}, timeout=60.0)
        final_url = payload.get("url")
        if not isinstance(final_url, str) or not final_url:
            raise ComputerRuntimeError("computer_navigate_shape")
        return final_url

    def read_page(self, requested_url: str) -> "PageRead":
        """V1.4 read path (the legacy ``navigate_and_read`` is untouched). One fresh snapshot, then one navigation, BOTH with
        ``requestHelp: false``: a challenge is reported in the result, never turned into a takeover request. The
        server-side text cut (``truncated``) and the ``challenge`` are kept so the caller can fail closed."""
        _validate_https_public_url(requested_url)
        self._request_json("POST", "/snapshot", {"requestHelp": False})
        payload, session_id = self._request_json("POST", "/navigate", {"url": requested_url, "requestHelp": False}, timeout=60.0)
        final_url, title, text = payload.get("url"), payload.get("title"), payload.get("text")
        if not isinstance(final_url, str) or not final_url or not isinstance(title, str):
            raise ComputerRuntimeError("computer_navigate_shape")
        if not isinstance(text, str):
            raise ComputerRuntimeError("computer_visible_text_shape")
        truncated = payload.get("truncated")
        if not isinstance(truncated, bool):
            raise ComputerRuntimeError("computer_truncated_shape")
        challenge = payload.get("challenge")
        kind = None
        if isinstance(challenge, dict):
            kind = challenge.get("kind")
            if kind not in ("cloudflare", "visible-challenge"):
                raise ComputerRuntimeError("computer_challenge_shape")
        elif challenge is not None:
            raise ComputerRuntimeError("computer_challenge_shape")
        from datetime import datetime, timezone
        return PageRead(requested_url, final_url, title, text, truncated, kind, session_id,
                        datetime.now(timezone.utc).isoformat())

    def restore_page(self, return_url: str) -> "PageRestore":
        """V1.4 controlled return: navigate back to the trusted page (requestHelp=false), then take a fresh ORDINARY observation
        (also requestHelp=false). A challenge is reported, never turned into a takeover request."""
        diagnostic: dict[str, Any] = {
            "requested_return_url": return_url,
            "navigation_attempted": False,
            "navigation_succeeded": False,
            "navigation_final_url": None,
            "observation_attempted": False,
            "observation_succeeded": False,
            "observed_url": None,
            "nav_url_matches_return": None,
            "observed_url_matches_return": None,
            "failure_substage": None,
        }
        try:
            _validate_https_public_url(return_url)
            diagnostic["navigation_attempted"] = True
            payload, session_id = self._request_json(
                "POST", "/navigate", {"url": return_url, "requestHelp": False}, timeout=60.0
            )
            nav_final = payload.get("url")
            if not isinstance(nav_final, str) or not nav_final:
                raise ComputerRuntimeError("computer_navigate_shape")
            diagnostic["navigation_final_url"] = nav_final
            diagnostic["nav_url_matches_return"] = nav_final == return_url
            challenge = payload.get("challenge")
            kind = None
            if isinstance(challenge, dict):
                kind = challenge.get("kind")
                if kind not in ("cloudflare", "visible-challenge"):
                    raise ComputerRuntimeError("computer_challenge_shape")
            elif challenge is not None:
                raise ComputerRuntimeError("computer_challenge_shape")
            diagnostic["navigation_succeeded"] = True
        except Exception:
            diagnostic["failure_substage"] = "navigation"
            self._emit_restore_diagnostic(diagnostic)
            raise

        try:
            diagnostic["observation_attempted"] = True
            observation = self.observe()
            diagnostic["observed_url"] = observation.page_url
            diagnostic["observed_url_matches_return"] = observation.page_url == return_url
            diagnostic["observation_succeeded"] = True
        except Exception:
            diagnostic["failure_substage"] = "observation"
            self._emit_restore_diagnostic(diagnostic)
            raise

        self._emit_restore_diagnostic(diagnostic)
        return PageRestore(return_url, nav_final, observation.page_url, kind or (observation.challenge or None), session_id)

    def read_control_state(self, request_id: str | None = None) -> ComputerControlState:
        path = "/control" if request_id is None else "/control?requestId=" + quote(request_id, safe="")
        payload, _ = self._request_json("GET", path)
        return _parse_control_state(payload)

    def request_help(self, reason: str, tool_call_id: str) -> ComputerControlState:
        if not isinstance(reason, str) or not reason or len(reason) > 500:
            raise ComputerRuntimeError("computer_help_reason_invalid")
        if not isinstance(tool_call_id, str) or not tool_call_id or len(tool_call_id) > 200:
            raise ComputerRuntimeError("computer_tool_call_id_invalid")
        payload, _ = self._request_json("POST", "/control/request", {"reason": reason, "toolCallId": tool_call_id})
        return _parse_control_state(payload)

    @staticmethod
    def _parse_snapshot(payload: Mapping[str, Any]) -> tuple[str, str, int, tuple[ComputerElement, ...]]:
        page_url = payload.get("url")
        title = payload.get("title")
        snapshot_id = payload.get("snapshotId")
        raw_elements = payload.get("elements")
        if (not isinstance(page_url, str) or not page_url or
                not isinstance(title, str) or not isinstance(snapshot_id, int) or
                isinstance(snapshot_id, bool) or not isinstance(raw_elements, list)):
            raise ComputerRuntimeError("computer_snapshot_shape")
        elements = []
        for item in raw_elements:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("ref", "role", "name")):
                raise ComputerRuntimeError("computer_snapshot_element_shape")
            checked = item.get("checked")
            value = item.get("value")
            elements.append(ComputerElement(
                item["ref"], item["role"], item["name"],
                checked if isinstance(checked, bool) else None,
                value if isinstance(value, str) else None,
            ))
        return page_url, title, snapshot_id, tuple(elements)

    def click(self, page_url: str, session_id: str, snapshot_id: int, ref: str) -> Mapping[str, Any]:
        return self._act(page_url, session_id, "/click", {"ref": ref, "snapshotId": snapshot_id})

    def type(self, page_url: str, session_id: str, snapshot_id: int, ref: str, text: str) -> Mapping[str, Any]:
        if not isinstance(text, str) or len(text) > 4096:
            raise ComputerRuntimeError("computer_type_text_invalid")
        return self._act(page_url, session_id, "/type", {"ref": ref, "snapshotId": snapshot_id, "text": text})

    def select_option(self, page_url: str, session_id: str, snapshot_id: int, ref: str, option: str) -> Mapping[str, Any]:
        if not isinstance(option, str) or not option.strip() or len(option) > 256:
            raise ComputerRuntimeError("computer_select_option_invalid")
        return self._act(page_url, session_id, "/select", {"ref": ref, "snapshotId": snapshot_id, "option": option})

    def key(self, page_url: str, session_id: str, key: str, ref: str | None = None,
            snapshot_id: int | None = None) -> Mapping[str, Any]:
        if not is_valid_browser_key(key):
            raise ComputerRuntimeError("invalid_browser_key")
        body: dict[str, Any] = {"key": key}
        if ref is not None:
            body["ref"] = ref
        if snapshot_id is not None:
            body["snapshotId"] = snapshot_id
        return self._act(page_url, session_id, "/key", body)

    def _request_json(self, method: str, path: str, body: Mapping[str, Any] | None = None,
                      timeout: float | None = None) -> tuple[Mapping[str, Any], str]:
        headers = {
            "x-openbot-computer-token": self._token,
            "x-openbot-bot-id": self._bot_id,
        }
        try:
            with httpx.Client(timeout=timeout or self._timeout, transport=self._transport) as client:
                response = client.request(method, self._endpoint + path, headers=headers, json=body)
                _raise_http(response, path.lstrip("/"))
                payload = response.json()
                run = client.get(self._endpoint + "/run", headers=headers)
                _raise_http(run, "run")
                run_payload = run.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ComputerRuntimeError("computer_runtime_request_failed") from exc
        session_id = run_payload.get("run")
        if not isinstance(payload, dict) or not isinstance(session_id, str) or not session_id:
            raise ComputerRuntimeError("computer_runtime_response_shape")
        return payload, session_id

    def _act(self, page_url: str, session_id: str, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        if getattr(self, "_session_id", None) != session_id:
            raise ComputerRuntimeError("browser_session_mismatch")
        if getattr(self, "_page_url", None) != page_url:
            raise ComputerRuntimeError("browser_page_mismatch")
        payload, observed_session = self._request_json("POST", path, body)
        if observed_session != session_id:
            raise ComputerRuntimeError("browser_session_changed")
        if payload.get("url") != page_url:
            raise ComputerRuntimeError("browser_page_changed")
        return payload


def _parse_control_state(payload: Mapping[str, Any]) -> ComputerControlState:
    holder = payload.get("holder")
    request = payload.get("request")
    resume = payload.get("resumeSnapshotRequired")
    requested = payload.get("requested")
    if holder not in ("bot", "human") or not isinstance(resume, bool) or not isinstance(requested, bool):
        raise ComputerRuntimeError("computer_control_shape")
    request_id = status = None
    if request is not None:
        if not isinstance(request, dict) or not isinstance(request.get("id"), str) or not isinstance(request.get("status"), str):
            raise ComputerRuntimeError("computer_control_shape")
        request_id, status = request["id"], request["status"]
    return ComputerControlState(holder, requested, request_id, status, resume, bool(payload.get("transitioning", False)))


ACTION_FAILURE_CODES = ("covered_by_dialog", "stale_ref", "target_not_actionable", "option_not_found", "not_a_select", "timeout", "unknown")
_ACTION_OPERATIONS = ("click", "type", "key", "select")


@dataclass(frozen=True)
class ComputerActionFailure:
    """Sanitized, closed-vocabulary reason an interaction was rejected. Never carries raw runtime text."""
    code: str
    action: str


def classify_action_failure(action: str, status: int, body_text: str) -> ComputerActionFailure:
    """Map a runtime rejection to a closed code. The raw text (it may hold DOM snippets) is discarded."""
    text = (body_text or "").lower()
    if status == 409 or "out of date" in text:
        code = "stale_ref"
    elif "intercepts pointer events" in text:
        code = "covered_by_dialog"
    elif "did not find some options" in text:
        code = "option_not_found"
    elif "not a <select>" in text:
        code = "not_a_select"
    elif re.search(r"element is not (visible|enabled|stable|attached)|detached from the dom|outside of the viewport", text):
        code = "target_not_actionable"
    elif "timeout" in text:
        code = "timeout"
    else:
        code = "unknown"
    return ComputerActionFailure(code, action)


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    value = payload.get("error") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else ""


def _raise_http(response: httpx.Response, operation: str) -> None:
    if response.status_code >= 400:
        suffix = ""
        if operation in _ACTION_OPERATIONS:
            suffix = ":" + classify_action_failure(operation, response.status_code, _error_text(response)).code
        raise ComputerRuntimeError(f"computer_{operation}_rejected:{response.status_code}{suffix}")


def _validate_https_public_url(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ComputerRuntimeError("requested_url_invalid")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ComputerRuntimeError("https_public_url_required")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ComputerRuntimeError("public_network_only")
    try:
        import ipaddress

        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ComputerRuntimeError("public_network_only")

