"""Exact-origin canonicalization shared by the navigation authority and its executor.

An origin is ``scheme + host + effective port``. No wildcards, no implied subdomains.
"""
from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

from .computer_read import _validate_requested_url


def host_matches_allowed_domain(host: str, allowed_domain: str) -> bool:
    """Accept an exact host or a genuine subdomain, never a substring match."""
    normalized_host = str(host or "").lower().rstrip(".")
    normalized_domain = str(allowed_domain or "").lower().lstrip(".").rstrip(".")
    if not normalized_host or not normalized_domain:
        return False
    return normalized_host == normalized_domain or normalized_host.endswith("." + normalized_domain)


def canonical_origin(url: object) -> str:
    """Return ``https://host[:port]`` for a public https URL, or raise ValueError."""
    _validate_requested_url(url)
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        raise ValueError("requested_url_invalid")
    parsed = urlparse(url)
    host = parsed.hostname.lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url_port_invalid") from exc
    port = 443 if port is None else port
    if not 1 <= port <= 65535 or not host:
        raise ValueError("url_port_invalid")
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


def canonical_allowed_origins(origins: Iterable[str]) -> frozenset:
    """Validate a trusted allow-list of exact origins (no wildcard, no path)."""
    if isinstance(origins, (str, bytes)):
        raise ValueError("allowed_origins_invalid")
    out = set()
    for origin in origins:
        if not isinstance(origin, str) or "*" in origin:
            raise ValueError("allowed_origin_invalid")
        parsed = urlparse(origin)
        if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("allowed_origin_invalid")
        out.add(canonical_origin(origin))
    return frozenset(out)
