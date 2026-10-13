"""Windows-protected persistence for the single Google connection.

This is intentionally a narrow store, not a general vault. DPAPI binds the
encrypted blob to the current Windows user profile; unavailable protection is
an error and never falls back to plaintext.
"""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from application.google_connection import GoogleCredentialRecord, GoogleCredentialStore


class SecureCredentialStoreError(Exception):
    """Credential persistence is unavailable or corrupt."""


class WindowsDPAPIGoogleCredentialStore(GoogleCredentialStore):
    def __init__(self, path: Optional[Path] = None) -> None:
        if os.name != "nt":
            raise SecureCredentialStoreError("windows_dpapi_unavailable")
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise SecureCredentialStoreError("local_app_data_unavailable")
        self._path = path or (Path(root) / "IAOS" / "google.connection.dpapi")

    def load(self) -> Optional[GoogleCredentialRecord]:
        if not self._path.exists():
            return None
        try:
            raw = _unprotect(self._path.read_bytes())
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("record_shape")
            return GoogleCredentialRecord(
                account_ref=value["account_ref"], email=value["email"],
                access_token=value["access_token"], refresh_token=value.get("refresh_token"),
                expires_at=value.get("expires_at"), scopes=tuple(value["scopes"]),
            )
        except Exception as exc:
            raise SecureCredentialStoreError("secure_credential_unreadable") from exc

    def save(self, record: GoogleCredentialRecord) -> None:
        value = json.dumps({
            "account_ref": record.account_ref, "email": record.email,
            "access_token": record.access_token, "refresh_token": record.refresh_token,
            "expires_at": record.expires_at, "scopes": list(record.scopes),
        }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_bytes(_protect(value))
            os.replace(tmp, self._path)
        except Exception as exc:
            raise SecureCredentialStoreError("secure_credential_save_failed") from exc

    def delete(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except Exception as exc:
            raise SecureCredentialStoreError("secure_credential_delete_failed") from exc


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(fn_name: str, data: bytes) -> bytes:
    if os.name != "nt":
        raise SecureCredentialStoreError("windows_dpapi_unavailable")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = getattr(crypt32, fn_name)
    fn.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    fn.restype = wintypes.BOOL
    source_buf = ctypes.create_string_buffer(data)
    source = _DATA_BLOB(len(data), ctypes.cast(source_buf, ctypes.POINTER(ctypes.c_byte)))
    target = _DATA_BLOB()
    if fn_name == "CryptProtectData":
        ok = fn(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    else:
        ok = fn(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target))
    if not ok:
        raise SecureCredentialStoreError("dpapi_operation_failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _protect(data: bytes) -> bytes:
    return _crypt("CryptProtectData", data)


def _unprotect(data: bytes) -> bytes:
    return _crypt("CryptUnprotectData", data)
