from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import ApplicationSetting
from app.schemas import NetworkEnforcementPublic

KNOWN_INTEGRATIONS = ("metadata", "artwork", "portal", "telemetry")
_WINDOWS_PROGRAMS = {
    "python": "runtime/python/python.exe",
    "node": "runtime/node/node.exe",
    "ffmpeg": "runtime/ffmpeg/ffmpeg.exe",
    "ffprobe": "runtime/ffmpeg/ffprobe.exe",
    "caddy": "runtime/caddy/caddy.exe",
}
_BLOCKED_REMOTE_RANGES = (
    "0.0.0.0-126.255.255.255", "128.0.0.0-255.255.255.255",
    "::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
)
_firewall_cache: dict[tuple[str, str], tuple[float, NetworkEnforcementPublic]] = {}
_firewall_lock = threading.Lock()
_native_guard_installed = False

# Values come from the process environment, never interpolated into PowerShell.
# Read only ActiveStore (the merged effective policy), not an installer marker.
_FIREWALL_QUERY = r"""
$ErrorActionPreference = 'Stop'
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object { $_.Enabled.ToString() })
$group = $env:BLUEREEL_FIREWALL_GROUP
$rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Group $group -ErrorAction SilentlyContinue |
ForEach-Object {
    $rule = $_
    $application = $rule | Get-NetFirewallApplicationFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $port = $rule | Get-NetFirewallPortFilter
    $service = $rule | Get-NetFirewallServiceFilter
    $interface = $rule | Get-NetFirewallInterfaceFilter
    $interfaceType = $rule | Get-NetFirewallInterfaceTypeFilter
    [PSCustomObject]@{
        name = $rule.Name; enabled = $rule.Enabled.ToString(); direction = $rule.Direction.ToString()
        action = $rule.Action.ToString(); profile = $rule.Profile.ToString(); program = $application.Program
        status = $rule.PrimaryStatus.ToString(); remote = @($address.RemoteAddress); local = @($address.LocalAddress)
        protocol = $port.Protocol.ToString(); remote_port = @($port.RemotePort); local_port = @($port.LocalPort)
        service = $service.Service; package = $application.Package; interface = @($interface.InterfaceAlias)
        interface_type = $interfaceType.InterfaceType.ToString()
    }
})
[PSCustomObject]@{profiles = $profiles; rules = $rules} | ConvertTo-Json -Depth 6 -Compress
"""


class OutboundConnectionDisabled(PermissionError):
    pass


def _loopback_host(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value.casefold().rstrip(".") == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_loopback


def native_network_audit(event: str, arguments: tuple[object, ...]) -> None:
    """Refuse DNS before Windows can delegate it to the shared DNS Client service."""
    if event in {"socket.gethostbyaddr", "socket.getnameinfo"}:
        # getnameinfo's audit event omits flags, so an audit hook cannot tell
        # numeric formatting from PTR lookup. Deny the raw resolver even for
        # 127/8 and ::1: missing hosts entries could otherwise trigger DNS.
        raise OutboundConnectionDisabled("Strict-local mode blocks reverse name resolution")
    if event in {"socket.getaddrinfo", "socket.gethostbyname"}:
        if not arguments or not _loopback_host(arguments[0]):
            raise OutboundConnectionDisabled("Strict-local mode blocks external name resolution")
    elif event in {"socket.bind", "socket.connect", "socket.sendto", "socket.sendmsg"}:
        address = arguments[-1] if arguments else None
        if not isinstance(address, tuple) or not address or not _loopback_host(address[0]):
            raise OutboundConnectionDisabled("Strict-local mode blocks non-loopback connections")


def _numeric_loopback(host: object, family: int = socket.AF_UNSPEC) -> str:
    if not isinstance(host, str):
        raise OutboundConnectionDisabled("Strict-local mode requires a loopback address")
    if host.casefold().rstrip(".") == "localhost":
        return "::1" if family == socket.AF_INET6 else "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise OutboundConnectionDisabled("Strict-local mode blocks external name resolution") from error
    if not address.is_loopback:
        raise OutboundConnectionDisabled("Strict-local mode blocks non-loopback connections")
    return str(address)


def _guarded_address(connection: socket.socket, address: object) -> tuple[object, ...]:
    if not isinstance(address, tuple) or len(address) < 2:
        raise OutboundConnectionDisabled("Strict-local mode requires a loopback socket address")
    return (_numeric_loopback(address[0], connection.family), *address[1:])


def _guard_socket_method(method: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(connection: socket.socket, address: object, *args: Any, **kwargs: Any) -> Any:
        return method(connection, _guarded_address(connection, address), *args, **kwargs)

    return guarded


def _numeric_nameinfo(address: tuple[Any, ...], flags: int) -> tuple[str, str]:
    required = socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
    if flags != required or len(address) not in {2, 4} or not isinstance(address[0], str):
        raise OutboundConnectionDisabled("Strict-local mode permits only numeric loopback name formatting")
    # This compatibility path never invokes getnameinfo/Winsock or any resolver.
    try:
        host = ipaddress.ip_address(address[0])
    except ValueError as error:
        raise OutboundConnectionDisabled("Strict-local mode requires a numeric loopback address") from error
    port = address[1]
    if not host.is_loopback or not isinstance(port, int) or not 0 <= port <= 65535:
        raise OutboundConnectionDisabled("Strict-local mode requires a numeric loopback address")
    if len(address) == 4 and (host.version != 6 or address[2:] != (0, 0)):
        raise OutboundConnectionDisabled("Strict-local mode does not resolve scoped names")
    return str(host), str(port)


def _install_native_socket_wrappers() -> None:
    # CPython resolves a hostname in getsockaddrarg() BEFORE its bind/connect/
    # connect_ex/sendto audit event. Guard the standard-library entry points
    # before that C conversion; the audit hook remains a second gate.
    # gethostbyname_ex shares socket.gethostbyname's audit event. getfqdn uses
    # gethostbyaddr, whose raw reverse resolver is denied above.
    for name in ("bind", "connect", "connect_ex"):
        setattr(socket.socket, name, _guard_socket_method(getattr(socket.socket, name)))
    original_sendto = socket.socket.sendto

    def sendto(connection: socket.socket, data: Any, *args: Any) -> int:
        if len(args) not in {1, 2}:
            raise OutboundConnectionDisabled("Strict-local mode requires a loopback datagram destination")
        address = _guarded_address(connection, args[-1])
        if len(args) == 1:
            return original_sendto(connection, data, address)
        return original_sendto(connection, data, args[0], address)

    socket.socket.sendto = sendto  # type: ignore[assignment]
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0) -> Any:
        return original_getaddrinfo(
            _numeric_loopback(host, family), port, family, type, proto, flags | socket.AI_NUMERICHOST,
        )

    def gethostbyname(host: str) -> str:
        address = _numeric_loopback(host, socket.AF_INET)
        if ipaddress.ip_address(address).version != 4:
            raise OutboundConnectionDisabled("Strict-local IPv4 lookup requires an IPv4 loopback address")
        return address

    def gethostbyname_ex(host: str) -> tuple[str, list[str], list[str]]:
        address = gethostbyname(host)
        return address, [], [address]

    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = gethostbyname
    socket.gethostbyname_ex = gethostbyname_ex
    socket.getnameinfo = _numeric_nameinfo


def install_native_network_guard(config: AppConfig) -> None:
    global _native_guard_installed
    if (
        config.deployment_mode == "native_windows"
        and not config.outbound_integrations_enabled and not _native_guard_installed
    ):
        sys.addaudithook(native_network_audit)
        _install_native_socket_wrappers()
        _native_guard_installed = True


def _query_windows_firewall(group: str) -> dict[str, Any]:
    executable = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _FIREWALL_QUERY],
        stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=12,
        encoding="utf-8", errors="replace",
        env={**os.environ, "BLUEREEL_FIREWALL_GROUP": group},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Firewall verification returned an invalid result")
    return payload


def _ranges(value: object) -> frozenset[tuple[int, int, int]]:
    if not isinstance(value, list):
        raise ValueError("Firewall address ranges are unavailable")
    ranges: set[tuple[int, int, int]] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Firewall address ranges are invalid")
        first, last = item.split("-", 1)
        begin, end = ipaddress.ip_address(first), ipaddress.ip_address(last)
        if begin.version != end.version:
            raise ValueError("Firewall address families differ")
        ranges.add((begin.version, int(begin), int(end)))
    return frozenset(ranges)


def _verified_firewall(payload: dict[str, Any], group: str, program_dir: Path) -> bool:
    profiles = payload.get("profiles")
    rules = payload.get("rules")
    if not isinstance(profiles, list) or len(profiles) != 3 or any(item != "True" for item in profiles):
        return False
    if not isinstance(rules, list):
        return False
    for name, relative in _WINDOWS_PROGRAMS.items():
        matching = [rule for rule in rules if isinstance(rule, dict) and rule.get("name") == f"{group}-Outbound-{name}"]
        if len(matching) != 1:
            return False
        rule = matching[0]
        expected = {
            "enabled": "True", "direction": "Outbound", "action": "Block", "profile": "Any", "status": "OK",
            "protocol": "Any", "local": ["Any"], "remote_port": ["Any"], "local_port": ["Any"],
            "service": "Any", "interface": ["Any"], "interface_type": "Any",
        }
        if (
            any(rule.get(key) != value for key, value in expected.items())
            or rule.get("package") not in {None, "Any", ""}
        ):
            return False
        program = rule.get("program")
        if not isinstance(program, str) or Path(program) != program_dir / relative:
            return False
        try:
            if _ranges(rule.get("remote")) != _ranges(list(_BLOCKED_REMOTE_RANGES)):
                return False
        except ValueError:
            return False
    return True


def network_enforcement(config: AppConfig) -> NetworkEnforcementPublic:
    if config.deployment_mode != "native_windows":
        return NetworkEnforcementPublic(
            platform="docker", status="not_managed",
            detail="Application gate is separate from container network isolation; verify the reference deployment.",
        )
    group, program_dir = config.windows_service_prefix, config.native_program_dir
    if os.name != "nt" or not group or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", group) or program_dir is None:
        return NetworkEnforcementPublic(
            platform="windows", status="unknown", detail="Native firewall verification is not configured.",
        )
    key = (group, str(program_dir))
    with _firewall_lock:
        cached = _firewall_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < 15:
            return cached[1]
        try:
            verified = _verified_firewall(_query_windows_firewall(group), group, program_dir)
            status = NetworkEnforcementPublic(
                platform="windows", status="enforced" if verified and _native_guard_installed else "not_enforced",
                checked_at=datetime.now(UTC),
                detail=(
                    "Active Windows Firewall rules block non-loopback traffic for every bundled runtime; "
                    "the Python DNS guard is active."
                    if verified and _native_guard_installed else
                    "Required effective firewall rules, enabled profiles, "
                    "or the runtime DNS guard could not be verified."
                ),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            status = NetworkEnforcementPublic(
                platform="windows", status="unknown", checked_at=datetime.now(UTC),
                detail="Windows Firewall status could not be read; no enforcement claim can be made.",
            )
        _firewall_cache[key] = (time.monotonic(), status)
        return status


def outbound_enabled(db: Session, config: AppConfig, integration: str) -> bool:
    if integration not in KNOWN_INTEGRATIONS or not config.outbound_integrations_enabled:
        return False
    setting = db.get(ApplicationSetting, f"outbound.{integration}.enabled")
    return bool(setting and setting.value is True)


def require_outbound_permission(db: Session, config: AppConfig, integration: str) -> None:
    if not outbound_enabled(db, config, integration):
        raise OutboundConnectionDisabled(f"Outbound integration '{integration}' is disabled")
