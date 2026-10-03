"""Discord first-run credentials in Windows Credential Manager, never in logs."""

from __future__ import annotations

import ctypes
import getpass
import json
import os
from ctypes import wintypes

TARGET = "SilentTalk/Discord"


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if os.name != "nt":
        raise OSError("Windows Credential Manager is only available on Windows")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def read() -> dict[str, str]:
    if os.name != "nt":
        return {}
    api = _api()
    pointer = ctypes.POINTER(_Credential)()
    if not api.CredReadW(TARGET, 1, 0, ctypes.byref(pointer)):
        return {}
    try:
        item = pointer.contents
        payload = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        value = json.loads(payload.decode("utf-8"))
        if (
            isinstance(value, dict)
            and isinstance(value.get("token"), str)
            and isinstance(value.get("owner"), str)
        ):
            return {"token": value["token"], "owner": value["owner"]}
        return {}
    except (ValueError, UnicodeError):
        return {}
    finally:
        api.CredFree(pointer)


def save(token: str, owner: str) -> None:
    token, owner = token.strip(), owner.strip()
    if not token or not owner.isdecimal() or int(owner) <= 0:
        raise ValueError("A bot token and a positive Discord user ID are required")
    payload = json.dumps({"token": token, "owner": owner}).encode("utf-8")
    if len(payload) > 2560:
        raise ValueError("Credential input is too long")
    buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    item = _Credential()
    item.Type, item.TargetName, item.UserName = 1, TARGET, owner
    item.CredentialBlobSize = len(payload)
    item.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    item.Persist = 2
    try:
        if not _api().CredWriteW(ctypes.byref(item), 0):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        ctypes.memset(buffer, 0, len(payload))


def main() -> int:
    print(
        "Discord credentials are saved in Windows Credential Manager for this Windows account."
    )
    print(
        "Existing .env and settings files are not changed. The bot token is hidden while you type."
    )
    try:
        save(getpass.getpass("Discord bot token: "), input("Owner Discord user ID: "))
        print("[OK] Discord credentials saved")
        return 0
    except (OSError, ValueError, EOFError, KeyboardInterrupt):
        print("Credential setup was not completed. Run the launcher again to retry.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
