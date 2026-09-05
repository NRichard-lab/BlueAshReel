"""Read access to this virtual service's private, noninteractive desktop only.

USER32 initializes transitively through Python's ctypes/CFFI dependencies. The
service desktop lacks the package SID that an interactive desktop already has.
Do not grant access to interactive stations, other services or media resources.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from typing import Any


def allow_service_desktop_read(package: Any, profile: str) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    user.GetProcessWindowStation.restype = w.HANDLE
    user.GetThreadDesktop.argtypes = [w.DWORD]
    user.GetThreadDesktop.restype = w.HANDLE
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                             ctypes.POINTER(w.DWORD)]

    def object_name(handle: Any) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        needed = w.DWORD()
        if not user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    station = user.GetProcessWindowStation()
    station_name = object_name(station)

    class ObjectFlags(ctypes.Structure):
        _fields_ = [("Inherit", w.BOOL), ("Reserved", w.BOOL), ("Flags", w.DWORD)]

    flags, flag_size = ObjectFlags(), w.DWORD()
    if station_name.lower() == "winsta0" or not user.GetUserObjectInformationW(
        station, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(flag_size)
    ) or flags.Flags & 1:
        raise RuntimeError("Connector window station must be noninteractive and invisible")
    service = {
        "bluereelremote.diagnostics": "BlueReelRemote",
        "bluereeldevelopmentremote.diagnostics": "BlueReelDevelopmentRemote",
    }.get(profile)
    if service is None:
        raise RuntimeError("Unknown native connector service identity")
    session = w.DWORD()
    kernel.ProcessIdToSessionId.argtypes = [w.DWORD, ctypes.POINTER(w.DWORD)]
    if not kernel.ProcessIdToSessionId(kernel.GetCurrentProcessId(), ctypes.byref(session)) or session.value != 0:
        raise RuntimeError("Connector desktop must belong to a noninteractive service session")

    class Luid(ctypes.Structure):
        _fields_ = [("LowPart", w.DWORD), ("HighPart", w.LONG)]

    class Statistics(ctypes.Structure):
        _fields_ = [("TokenId", Luid), ("AuthenticationId", Luid), ("ExpirationTime", ctypes.c_int64),
                    ("TokenType", ctypes.c_int), ("ImpersonationLevel", ctypes.c_int),
                    ("DynamicCharged", w.DWORD), ("DynamicAvailable", w.DWORD),
                    ("GroupCount", w.DWORD), ("PrivilegeCount", w.DWORD), ("ModifiedId", Luid)]

    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD,
                                         ctypes.POINTER(w.DWORD)]
    advapi.LookupAccountNameW.argtypes = [w.LPCWSTR, w.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(w.DWORD),
                                        w.LPWSTR, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD)]
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    token = w.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = w.DWORD()
        token_user = ctypes.create_string_buffer(512)
        statistics = Statistics()
        user_loaded = advapi.GetTokenInformation(token, 1, token_user, len(token_user), ctypes.byref(needed))
        statistics_loaded = advapi.GetTokenInformation(
            token, 10, ctypes.byref(statistics), ctypes.sizeof(statistics), ctypes.byref(needed))
        if not user_loaded or not statistics_loaded:
            raise ctypes.WinError(ctypes.get_last_error())
        current_sid = ctypes.cast(token_user, ctypes.POINTER(ctypes.c_void_p))[0]
        expected_sid = ctypes.create_string_buffer(512)
        sid_size, domain_size, sid_type = w.DWORD(512), w.DWORD(256), w.DWORD()
        domain = ctypes.create_unicode_buffer(256)
        service_found = advapi.LookupAccountNameW(
            None, "NT SERVICE\\" + service, expected_sid, ctypes.byref(sid_size), domain, ctypes.byref(domain_size),
            ctypes.byref(sid_type))
        if not service_found or not advapi.EqualSid(current_sid, expected_sid):
            raise RuntimeError("Connector launcher is not its dedicated virtual service account")
        authentication = statistics.AuthenticationId
        expected_station = f"Service-0x{authentication.HighPart & 0xffffffff:x}-{authentication.LowPart:x}$"
        if station_name.lower() != expected_station.lower():
            raise RuntimeError("Connector window station belongs to another logon session")
        desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
        if object_name(desktop).lower() != "default":
            raise RuntimeError("Unexpected noninteractive connector desktop")

        class Trustee(ctypes.Structure):
            _fields_ = [("MultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                        ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("Name", ctypes.c_void_p)]

        class Access(ctypes.Structure):
            _fields_ = [("Permissions", w.DWORD), ("Mode", ctypes.c_int), ("Inheritance", w.DWORD),
                        ("Trustee", Trustee)]

        advapi.GetSecurityInfo.argtypes = [w.HANDLE, ctypes.c_int, w.DWORD,
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
        advapi.SetEntriesInAclW.argtypes = [w.ULONG, ctypes.POINTER(Access), ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
        advapi.SetSecurityInfo.argtypes = [w.HANDLE, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p]
        # Read-only object rights: no hooks, clipboard, screen, window creation,
        # write access or ACL changes are granted to the child package SID.
        verified = []
        try:
            # Validate both objects before changing either. A NULL DACL must
            # never be replaced with a package-only ACL by SetEntriesInAclW.
            for handle, rights in ((station, 0x20103), (desktop, 0x20041)):
                owner, old_acl, descriptor = (ctypes.c_void_p() for _ in range(3))
                result = advapi.GetSecurityInfo(handle, 7, 1 | 4, ctypes.byref(owner), None,
                                                ctypes.byref(old_acl), None, ctypes.byref(descriptor))
                if result:
                    raise ctypes.WinError(result)
                verified.append((handle, rights, old_acl, descriptor))
                if not owner or not old_acl or not advapi.EqualSid(owner, current_sid):
                    raise RuntimeError("Connector desktop is not owned by its dedicated service account")
            for handle, rights, old_acl, _ in verified:
                new_acl = ctypes.c_void_p()
                entry = Access(rights, 1, 0, Trustee(None, 0, 0, 5, package))
                result = advapi.SetEntriesInAclW(1, ctypes.byref(entry), old_acl, ctypes.byref(new_acl))
                if result:
                    raise ctypes.WinError(result)
                try:
                    result = advapi.SetSecurityInfo(handle, 7, 4, None, None, new_acl, None)
                    if result:
                        raise ctypes.WinError(result)
                finally:
                    kernel.LocalFree(new_acl)
        finally:
            for _, _, _, descriptor in verified:
                kernel.LocalFree(descriptor)
    finally:
        kernel.CloseHandle(token)
