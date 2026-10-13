"""Open-first resource binding: the object is opened ONCE, during authorization.

Order of operations (AUTHORIZATION_RESOLUTION_GATE):
  1. the file is OPENED here (no earlier realpath / lstat / exists check);
  2. identity (FILE_ID_INFO on Windows) and final path (GetFinalPathNameByHandleW)
     are read FROM THE OPEN HANDLE;
  3. the canonical resource is that final path, so Policy judges the object that
     is actually open, not a path string that could be swapped meanwhile;
  4. the handle stays in a runtime ``BindingTable`` under an opaque
     ``resource_binding_id``; only that id travels in CanonicalAction /
     AuthorizedAction / evidence. The raw handle never leaves ``Binding``;
  5. EXEC takes the binding out of the table (single use) and reads from the
     same handle. Nothing re-resolves or re-opens the path.

This is the ONLY module allowed to use ctypes / msvcrt (see test_architecture).

Certified scope: Windows / NTFS / fs.read. The POSIX branch exists so the code
runs on a dev machine (Linux with /proc); it is not what the gate certifies
(inode reuse on ext4, no sequence number).

Not covered (deferred by design): content changes of the same file, ReFS,
ext4, cross-adapter replay, durable evidence.
"""
from __future__ import annotations

import os
import secrets
import stat
import sys
import threading
from typing import Callable, Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"


class BindingError(Exception):
    """The object could not be bound. ``args[0]`` is a stable reason code."""


if IS_WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
    ]
    _k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _k32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _k32.GetFileInformationByHandleEx.restype = wintypes.BOOL

    _GENERIC_READ = 0x80000000
    _SHARE_ALL = 0x1 | 0x2 | 0x4  # READ|WRITE|DELETE: others (and tests) may rename/swap
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x80
    _FILE_ID_INFO_CLASS = 18
    _INVALID = ctypes.c_void_p(-1).value

    class _FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_ulonglong), ("FileId", ctypes.c_ubyte * 16)]

    def open_native(path: str) -> int:
        """CreateFileW (follows links, like any ordinary open) -> CRT fd owning the handle."""
        h = _k32.CreateFileW(path, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING,
                             _FILE_ATTRIBUTE_NORMAL, None)
        if h is None or h == _INVALID:
            err = ctypes.get_last_error()
            raise OSError(None, "CreateFileW failed", path, err)
        try:
            return msvcrt.open_osfhandle(h, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except Exception:
            _k32.CloseHandle(h)
            raise

    def identity_of_fd(fd: int) -> str:
        info = _FILE_ID_INFO()
        h = msvcrt.get_osfhandle(fd)
        if not _k32.GetFileInformationByHandleEx(h, _FILE_ID_INFO_CLASS, ctypes.byref(info),
                                                 ctypes.sizeof(info)):
            raise BindingError("identity_unavailable")
        return "%08x:%s" % (info.VolumeSerialNumber, bytes(info.FileId).hex())

    def final_path_of_fd(fd: int) -> str:
        h = msvcrt.get_osfhandle(fd)
        size = 1024
        for _ in range(3):
            buf = ctypes.create_unicode_buffer(size)
            n = _k32.GetFinalPathNameByHandleW(h, buf, size, 0)  # NORMALIZED | VOLUME_NAME_DOS
            if n == 0:
                raise BindingError("final_path_unavailable")
            if n < size:
                return _strip_extended_prefix(buf.value)
            size = n + 1
        raise BindingError("final_path_unavailable")

    def _strip_extended_prefix(p: str) -> str:
        if p.startswith("\\\\?\\UNC\\"):
            return "\\\\" + p[8:]
        if p.startswith("\\\\?\\"):
            return p[4:]
        return p

else:  # POSIX dev branch (not the certified host)

    def open_native(path: str) -> int:
        return os.open(path, os.O_RDONLY)

    def identity_of_fd(fd: int) -> str:
        st = os.fstat(fd)
        if st.st_ino == 0:
            raise BindingError("identity_unavailable")
        return "%d:%d" % (st.st_dev, st.st_ino)

    def final_path_of_fd(fd: int) -> str:
        if not os.path.isdir("/proc/self/fd"):
            raise BindingError("final_path_unavailable")
        return os.readlink("/proc/self/fd/%d" % fd)


Opener = Callable[[str], int]


class Binding:
    """One open object, bound at authorization time. Single use."""

    __slots__ = ("_fd", "identity", "final_path", "_table", "_closed")

    def __init__(self, fd: int, identity: str, final_path: str, table: "BindingTable") -> None:
        self._fd = fd
        self.identity = identity
        self.final_path = final_path
        self._table = table
        self._closed = False

    def current_identity(self) -> str:
        """Identity queried again FROM THE HANDLE (never from a path)."""
        if self._closed:
            raise BindingError("binding_closed")
        return identity_of_fd(self._fd)

    def read_all(self) -> bytes:
        if self._closed:
            raise BindingError("binding_closed")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(self._fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        self._table._note_read(self.final_path, self.identity, total)
        return b"".join(chunks)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._fd)


class BindingTable:
    """Runtime table: ``resource_binding_id`` -> open Binding.

    ``opener`` is a test seam (path -> CRT fd). The default is the native open.
    """

    def __init__(self, opener: Optional[Opener] = None) -> None:
        self._opener: Opener = opener if opener is not None else open_native
        self._lock = threading.Lock()
        self._bindings: Dict[str, Binding] = {}
        self._reads: List[Tuple[str, str, int]] = []
        self._open_calls = 0

    def open(self, path: str) -> Tuple[str, str, str]:
        """Open ``path`` and bind it. Returns (binding_id, final_path, identity).

        Fails closed with BindingError. The caller must ``release`` the id if the
        authorization does not end in ALLOW.
        """
        with self._lock:
            self._open_calls += 1  # observable: every path open that goes through the product boundary
        try:
            fd = self._opener(path)
        except OSError as exc:
            missing = getattr(exc, "errno", None) == 2 or getattr(exc, "winerror", None) in (2, 3)
            raise BindingError("resource_not_found" if missing else "resource_open_failed")
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BindingError("resource_not_regular_file")
            identity = identity_of_fd(fd)
            final_path = final_path_of_fd(fd)
        except BaseException:
            os.close(fd)
            raise
        binding_id = "b-" + secrets.token_hex(8)
        with self._lock:
            self._bindings[binding_id] = Binding(fd, identity, final_path, self)
        return binding_id, final_path, identity

    def release(self, binding_id: Optional[str]) -> None:
        if binding_id is None:
            return
        with self._lock:
            binding = self._bindings.pop(binding_id, None)
        if binding is not None:
            binding.close()

    def take(self, binding_id: Optional[str]) -> Binding:
        """Remove and return the binding (single use). Fails closed if absent."""
        with self._lock:
            binding = self._bindings.pop(binding_id, None) if binding_id else None
        if binding is None:
            raise BindingError("binding_unavailable")
        return binding

    def open_calls(self) -> int:
        """How many times a PATH was opened through this table (observable for EXEC)."""
        with self._lock:
            return self._open_calls

    def pending(self) -> int:
        with self._lock:
            return len(self._bindings)

    def read_log(self) -> Tuple[Tuple[str, str, int], ...]:
        """(final_path, identity, bytes) for every read ever performed."""
        with self._lock:
            return tuple(self._reads)

    def _note_read(self, final_path: str, identity: str, nbytes: int) -> None:
        with self._lock:
            self._reads.append((final_path, identity, nbytes))

    def close_all(self) -> None:
        with self._lock:
            items = list(self._bindings.values())
            self._bindings.clear()
        for b in items:
            b.close()
