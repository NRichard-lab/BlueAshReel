"""Windows AppContainer launcher; the parent uses the existing outbound-blocked Python.

The child has only internetClient, no private-network or filesystem capabilities.
Installation grants its specific package SID access to isolated code/state only.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
from ctypes import wintypes as w
from pathlib import Path
from typing import Any

from app.remote.native_desktop import allow_service_desktop_read


def appcontainer_sid(name: str) -> tuple[Any, str]:
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    derive = userenv.DeriveAppContainerSidFromAppContainerName
    derive.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    derive.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    if derive(name, ctypes.byref(pointer)) != 0:
        raise OSError("Cannot derive connector AppContainer identity")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    text = w.LPWSTR()
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return pointer, text.value or ""
    finally:
        ctypes.windll.kernel32.LocalFree(text)


def launch(name: str, executable: Path, policy: Path) -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    package, _ = appcontainer_sid(name)
    allow_service_desktop_read(package, name)
    internet = ctypes.c_void_p()
    advapi.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    if not advapi.ConvertStringSidToSidW("S-1-15-3-1", ctypes.byref(internet)):
        raise ctypes.WinError(ctypes.get_last_error())

    class SidAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", w.DWORD)]

    class Capabilities(ctypes.Structure):
        _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.POINTER(SidAttributes)),
                    ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD)]

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                    ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD), ("dwYSize", w.DWORD),
                    ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                    ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                    ("hStdError", w.HANDLE)]

    class StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE), ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

    class BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    capability = SidAttributes(internet, 4)
    security = Capabilities(package, ctypes.pointer(capability), 1, 0)
    # Profiles are scoped to the launcher service identity. Never use machine
    # scope or impersonate an interactive owner to make credential storage work.
    profile_sid = ctypes.c_void_p()
    create_profile = userenv.CreateAppContainerProfile
    create_profile.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.POINTER(SidAttributes), w.DWORD,
                               ctypes.POINTER(ctypes.c_void_p)]
    create_profile.restype = ctypes.c_long
    result = create_profile(name, name, "Isolated remote diagnostic connector", ctypes.pointer(capability), 1,
                            ctypes.byref(profile_sid))
    if result not in (0, -2147024713):  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
        raise OSError("Connector AppContainer profile could not be created")
    if profile_sid:
        advapi.FreeSid.argtypes = [ctypes.c_void_p]
        advapi.FreeSid(profile_sid)

    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                       ctypes.POINTER(ctypes.c_size_t)]
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                               ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x20009, ctypes.byref(security),
                                           ctypes.sizeof(security), None, None):
        raise ctypes.WinError(ctypes.get_last_error())
    startup = StartupInfoEx()
    startup.StartupInfo.cb = ctypes.sizeof(startup)
    startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
    process = ProcessInfo()
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    limits = ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x8
    limits.BasicLimitInformation.ActiveProcessLimit = 1
    limits.ProcessMemoryLimit = 128 * 1024 * 1024
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD,
                                    ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(StartupInfoEx),
                                    ctypes.POINTER(ProcessInfo)]
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([
        str(executable), "-I", "-B", "-m", "app.remote.native_policy", "--policy", str(policy),
    ]))
    try:
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.CreateProcessW(str(executable), command, None, None, False, 0x80000 | 0x8000000 | 0x4,
                                     None, str(executable.parent), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        if not kernel.AssignProcessToJobObject(job, process.hProcess):
            kernel.TerminateProcess(process.hProcess, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.ResumeThread.argtypes = [w.HANDLE]
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.ResumeThread(process.hThread)
        kernel.WaitForSingleObject(process.hProcess, 0xFFFFFFFF)
        exit_code = w.DWORD()
        if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value
    finally:
        if process.hThread:
            kernel.CloseHandle(process.hThread)
        if process.hProcess:
            kernel.CloseHandle(process.hProcess)
        if job:
            kernel.CloseHandle(job)
        kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel.DeleteProcThreadAttributeList(attributes)
        advapi.FreeSid.argtypes = [ctypes.c_void_p]
        advapi.FreeSid(package)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(internet)


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Native connector isolation requires Windows")
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--derive-sid", action="store_true")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args()
    if args.derive_sid:
        _, value = appcontainer_sid(args.name)
        print(value)
        return
    if args.executable is None or args.policy is None:
        parser.error("executable and policy are required")
    raise SystemExit(launch(args.name, args.executable, args.policy))


if __name__ == "__main__":
    main()
