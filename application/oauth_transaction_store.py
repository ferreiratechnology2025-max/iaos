"""Ephemeral, one-shot persistence for backend OAuth transactions.

This store is deliberately separate from GoogleCredentialStore.  Transactions
contain only short-lived protocol material and are protected with Windows
DPAPI; the OAuth state itself is represented on disk only by its SHA-256
digest.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Iterator, Optional, Protocol, Sequence

from application.google_secure_store import (
    SecureCredentialStoreError,
    _protect,
    _unprotect,
)


class OAuthTransactionStoreError(Exception):
    """The protected OAuth transaction store is unavailable or corrupt."""


@dataclass(frozen=True)
class OAuthTransaction:
    state_digest: str
    pkce_verifier: str
    issued_at: float
    expires_at: float
    requested_capabilities: tuple[str, ...]
    redirect_uri: str


class OAuthTransactionStore(Protocol):
    def save(self, transaction: OAuthTransaction) -> None: ...
    def consume(self, state_digest: str, now: float) -> Optional[OAuthTransaction]: ...
    def purge_expired(self, now: float) -> int: ...
    def size(self) -> int: ...


class InMemoryOAuthTransactionStore:
    """Test double; production wiring uses WindowsDPAPIOAuthTransactionStore."""

    def __init__(self) -> None:
        self._records: dict[str, OAuthTransaction] = {}

    def save(self, transaction: OAuthTransaction) -> None:
        self._records[transaction.state_digest] = transaction

    def consume(self, state_digest: str, now: float) -> Optional[OAuthTransaction]:
        transaction = self._records.pop(state_digest, None)
        if transaction is None or transaction.expires_at <= now:
            return None
        return transaction

    def purge_expired(self, now: float) -> int:
        expired = [key for key, value in self._records.items() if value.expires_at <= now]
        for key in expired:
            self._records.pop(key, None)
        return len(expired)

    def size(self) -> int:
        return len(self._records)


class WindowsDPAPIOAuthTransactionStore:
    """One encrypted file per transaction with atomic consume semantics."""

    _DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

    def __init__(self, root: Optional[Path] = None) -> None:
        if os.name != "nt":
            raise OAuthTransactionStoreError("windows_dpapi_unavailable")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data and root is None:
            raise OAuthTransactionStoreError("local_app_data_unavailable")
        self._root = root or (Path(local_app_data) / "IAOS" / "oauth-transactions")
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, transaction: OAuthTransaction) -> None:
        self._validate_digest(transaction.state_digest)
        value = json.dumps({
            "state_digest": transaction.state_digest,
            "pkce_verifier": transaction.pkce_verifier,
            "issued_at": transaction.issued_at,
            "expires_at": transaction.expires_at,
            "requested_capabilities": list(transaction.requested_capabilities),
            "redirect_uri": transaction.redirect_uri,
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        target = self._record_path(transaction.state_digest)
        try:
            with _exclusive_lock(self._lock_path(transaction.state_digest)):
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(_protect(value))
                os.replace(temporary, target)
        except OAuthTransactionStoreError:
            raise
        except Exception as exc:
            raise OAuthTransactionStoreError("oauth_transaction_save_failed") from exc

    def consume(self, state_digest: str, now: float) -> Optional[OAuthTransaction]:
        self._validate_digest(state_digest)
        target = self._record_path(state_digest)
        try:
            with _exclusive_lock(self._lock_path(state_digest)):
                if not target.exists():
                    return None
                raw = _unprotect(target.read_bytes())
                transaction = _decode(raw)
                if transaction.state_digest != state_digest or transaction.expires_at <= now:
                    target.unlink(missing_ok=True)
                    return None
                # Delete while holding the same inter-process lock: one winner.
                target.unlink()
                return transaction
        except OAuthTransactionStoreError:
            raise
        except Exception as exc:
            raise OAuthTransactionStoreError("oauth_transaction_consume_failed") from exc

    def purge_expired(self, now: float) -> int:
        removed = 0
        for target in self._root.glob("*.dpapi"):
            digest = target.stem
            if not self._DIGEST_RE.fullmatch(digest):
                continue
            try:
                with _exclusive_lock(self._lock_path(digest)):
                    if not target.exists():
                        continue
                    transaction = _decode(_unprotect(target.read_bytes()))
                    if transaction.expires_at <= now:
                        target.unlink()
                        removed += 1
            except (FileNotFoundError, PermissionError):
                continue
            except Exception as exc:
                raise OAuthTransactionStoreError("oauth_transaction_cleanup_failed") from exc
        return removed

    def size(self) -> int:
        return sum(1 for path in self._root.glob("*.dpapi") if self._DIGEST_RE.fullmatch(path.stem))

    def _record_path(self, digest: str) -> Path:
        return self._root / f"{digest}.dpapi"

    def _lock_path(self, digest: str) -> Path:
        return self._root / f"{digest}.lock"

    def _validate_digest(self, digest: str) -> None:
        if not self._DIGEST_RE.fullmatch(digest):
            raise OAuthTransactionStoreError("oauth_transaction_digest_invalid")


def state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _decode(raw: bytes) -> OAuthTransaction:
    value = json.loads(raw.decode("utf-8"))
    transaction = OAuthTransaction(
        state_digest=value["state_digest"], pkce_verifier=value["pkce_verifier"],
        issued_at=float(value["issued_at"]), expires_at=float(value["expires_at"]),
        requested_capabilities=tuple(value["requested_capabilities"]),
        redirect_uri=value["redirect_uri"],
    )
    if not isinstance(transaction.pkce_verifier, str) or not transaction.pkce_verifier:
        raise ValueError("pkce_verifier_missing")
    return transaction


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    if os.name != "nt":
        raise OAuthTransactionStoreError("windows_dpapi_unavailable")
    import msvcrt

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()
