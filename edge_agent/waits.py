"""What EdgeAgent waits on, each woken by the thing it waits for (the owner,
2026-10-14: nothing in the product asks at a fixed interval). A wait has at
most one deadline, to give up; nothing here sleeps to look again.

- ProcessExit: a process's exit as a kernel object. Windows: a SYNCHRONIZE
  handle (OpenProcess), signaled once the process has exited. Linux: a pidfd
  (os.pidfd_open), readable once the process has exited.
- Wake: a wake-up any thread can give, waited on in the kernel together with
  a process's exit. Windows: an auto-reset event and WaitForMultipleObjects.
  Linux: an eventfd and a selector.
- OutputLines: a child's output read line by line on a thread (blocking
  reads), waking a waiter at the line it waits for or when the output ends.
- EventStream: a server-sent event stream on this machine, read from the
  socket itself so that closing it ends a blocked read on Windows too.
"""

from __future__ import annotations

import math
import os
import selectors
import socket
import threading
import time
from collections.abc import Callable, Iterator
from typing import IO, Self

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    # A library object of our own: its prototypes (64-bit handles) do not
    # change ctypes.windll.kernel32's for the rest of the process.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForMultipleObjects.argtypes = (
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD
    )
    _kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
    _kernel32.CreateEventW.argtypes = (
        ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR
    )
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

SYNCHRONIZE = 0x0010_0000
WAIT_OBJECT_0 = 0
WAIT_FAILED = 0xFFFF_FFFF
INFINITE = 0xFFFF_FFFF
ERROR_INVALID_PARAMETER = 87  # OpenProcess: no process has this id


def _milliseconds(timeout: float | None) -> int:
    if timeout is None:
        return INFINITE
    return min(max(0, math.ceil(timeout * 1000)), INFINITE - 1)


def _windows_error() -> OSError:
    return ctypes.WinError(ctypes.get_last_error())


class ProcessExit:
    """Process `pid`'s exit, to wait on in the kernel. `open()` gives None
    when there is no such process any more."""

    def __init__(self, pid: int, handle: int) -> None:
        self.pid = pid
        # Windows: the process handle; Linux: the pidfd.
        self.handle: int | None = handle

    @classmethod
    def open(cls, pid: int) -> ProcessExit | None:
        if pid <= 0:
            return None
        if os.name == "nt":
            handle = _kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == ERROR_INVALID_PARAMETER:
                    return None
                raise ctypes.WinError(error)
            return cls(pid, handle)
        try:
            return cls(pid, os.pidfd_open(pid))
        except ProcessLookupError:
            return None

    def wait(self, timeout: float | None = None) -> bool:
        """True once the process has exited; False at the timeout."""
        assert self.handle is not None, "closed"
        if os.name == "nt":
            result = _kernel32.WaitForSingleObject(self.handle, _milliseconds(timeout))
            if result == WAIT_FAILED:
                raise _windows_error()
            return result == WAIT_OBJECT_0
        with selectors.DefaultSelector() as selector:
            selector.register(self.handle, selectors.EVENT_READ)
            return bool(selector.select(timeout))

    def exited(self) -> bool:
        return self.wait(0)

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        if os.name == "nt":
            _kernel32.CloseHandle(handle)
        else:
            os.close(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        if getattr(self, "handle", None) is not None:
            self.close()


class Wake:
    """A wake-up any thread can give (`set()`), waited on in the kernel
    together with a process's exit. Given while nobody waits, it ends the next
    wait at once; the wait it ends takes it."""

    def __init__(self) -> None:
        if os.name == "nt":
            handle = _kernel32.CreateEventW(None, False, False, None)  # auto-reset
            if not handle:
                raise _windows_error()
            self._handle: int | None = handle
        else:
            self._handle = os.eventfd(0, os.EFD_NONBLOCK | os.EFD_CLOEXEC)

    def set(self) -> None:
        handle = self._handle
        if handle is None:
            return
        if os.name == "nt":
            _kernel32.SetEvent(handle)
        else:
            os.eventfd_write(handle, 1)

    def wait(
        self, process: ProcessExit | None = None, timeout: float | None = None
    ) -> None:
        """Until set(), `process` exits, or the timeout."""
        assert self._handle is not None, "closed"
        if os.name == "nt":
            handles = [self._handle]
            if process is not None and process.handle is not None:
                handles.append(process.handle)
            array = (wintypes.HANDLE * len(handles))(*handles)
            result = _kernel32.WaitForMultipleObjects(
                len(handles), array, False, _milliseconds(timeout)
            )
            if result == WAIT_FAILED:
                raise _windows_error()
            return
        with selectors.DefaultSelector() as selector:
            selector.register(self._handle, selectors.EVENT_READ)
            if process is not None and process.handle is not None:
                selector.register(process.handle, selectors.EVENT_READ)
            selector.select(timeout)
        try:
            os.eventfd_read(self._handle)  # take the wake-up, if one was given
        except BlockingIOError:
            pass

    def close(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle is None:
            return
        if os.name == "nt":
            _kernel32.CloseHandle(handle)
        else:
            os.close(handle)

    def __del__(self) -> None:
        self.close()


class OutputLines:
    """A child's output read line by line on a thread of its own, with
    blocking reads, to its end (so the child never stalls on a full pipe).
    wait() returns once a line `wanted` accepts has come, or the output ended
    (the child closed it or exited)."""

    def __init__(
        self, stream: IO[bytes], wanted: Callable[[bytes], bool], *, name: str
    ) -> None:
        self.seen = False
        self.ended = False
        self._wanted = wanted
        self._settled = threading.Event()
        threading.Thread(target=self._read, args=(stream,), name=name, daemon=True).start()

    def _read(self, stream: IO[bytes]) -> None:
        try:
            for line in iter(stream.readline, b""):
                if not self.seen and self._wanted(line):
                    self.seen = True
                    self._settled.set()
        except (OSError, ValueError):
            pass  # closed under the read
        finally:
            self.ended = True
            self._settled.set()
            try:
                stream.close()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> bool:
        """True once the wanted line has come; False when the output ended
        without it, or at the timeout (then `ended` is False)."""
        self._settled.wait(timeout)
        return self.seen


class EventStream:
    """A server-sent event stream (GET `path`) from a server on 127.0.0.1,
    read from the socket itself: a read blocked in http.client wakes on
    shutdown() on Linux but on Windows only once the socket is closed, and a
    socket inside a file object cannot be closed under it (SilentTalk's status
    stream, Windows CI 2026-10-14). close() from any thread ends a blocked
    read. Back from the constructor once the server has answered 200 (it has
    taken the client on by then), within `timeout`."""

    def __init__(self, port: int, path: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self._pending = b""
        try:
            self._socket.sendall(
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                "Accept: text/event-stream\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            head = (self._line(deadline) or b"").split()
            if len(head) < 2 or not head[0].startswith(b"HTTP/") or head[1] != b"200":
                raise ConnectionError(
                    f"{path} answered {b' '.join(head[:3]).decode('latin-1') or 'nothing'}"
                )
            while self._line(deadline):  # the headers, up to the blank line
                pass
        except BaseException:
            self.close()
            raise

    def _line(self, deadline: float | None) -> bytes | None:
        """The next line without its line end; None once the server closed
        the stream. TimeoutError at the deadline."""
        while b"\n" not in self._pending:
            if deadline is None:
                self._socket.settimeout(None)
            else:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("event stream: deadline passed")
                self._socket.settimeout(left)
            chunk = self._socket.recv(65536)
            if not chunk:
                return None
            self._pending += chunk
        line, self._pending = self._pending.split(b"\n", 1)
        return line.rstrip(b"\r")

    def events(self, deadline: float | None = None) -> Iterator[bytes]:
        """Each event's data (a `data:` line), as it comes, until the server
        ends the stream; TimeoutError at `deadline` (a time.monotonic()), and
        OSError once close() was called or the connection broke."""
        while True:
            line = self._line(deadline)
            if line is None:
                return
            if line.startswith(b"data:"):
                yield line[5:].strip()

    def close(self) -> None:
        sock = self._socket
        try:
            sock.shutdown(socket.SHUT_RDWR)  # wakes a blocked read on Linux
        except OSError:
            pass
        sock.close()  # and on Windows
