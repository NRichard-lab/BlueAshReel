"""Real user-scoped DPAPI must reject a different, anonymous security context."""
from __future__ import annotations

import ctypes
import os
import secrets
from ctypes import wintypes

import pytest

from app.remote.storage import protect_secret, unprotect_secret


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows user-scoped DPAPI")
def test_dpapi_denies_anonymous_impersonation_and_preserves_current_user() -> None:
    original = secrets.token_bytes(32)
    protected = protect_secret(original)
    assert protected.startswith("dpapi-user:")
    assert unprotect_secret(protected) == original
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentThread.restype = wintypes.HANDLE
    security.ImpersonateAnonymousToken.argtypes = [wintypes.HANDLE]
    security.ImpersonateAnonymousToken.restype = wintypes.BOOL
    security.RevertToSelf.restype = wintypes.BOOL
    if not security.ImpersonateAnonymousToken(kernel.GetCurrentThread()):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(OSError):
            unprotect_secret(protected)
    finally:
        if not security.RevertToSelf():
            raise ctypes.WinError(ctypes.get_last_error())
    assert unprotect_secret(protected) == original
