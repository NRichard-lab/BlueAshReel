from __future__ import annotations

import json
import socket
import subprocess
import sys

import pytest

from app.services import outbound


@pytest.mark.parametrize("configured", [False, True])
def test_metadata_dns_exception_requires_configuration_and_exact_https_hosts(
    configured: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbound, "_metadata_allowed", configured)
    monkeypatch.setattr(outbound, "_portal_allowed", False)
    for host in ("api.themoviedb.org", "image.tmdb.org"):
        if configured:
            outbound.native_network_audit("socket.getaddrinfo", (host, 443))
        else:
            with pytest.raises(outbound.OutboundConnectionDisabled):
                outbound.native_network_audit("socket.getaddrinfo", (host, 443))
        for event, arguments in (
            ("socket.getaddrinfo", (host, 80)),
            ("socket.getaddrinfo", (host + ".attacker.invalid", 443)),
            ("socket.getaddrinfo", ("https://" + host, 443)),
            ("socket.gethostbyname", (host,)),
            ("socket.gethostbyaddr", (host,)),
        ):
            with pytest.raises(outbound.OutboundConnectionDisabled):
                outbound.native_network_audit(event, arguments)


def test_metadata_exception_preserves_canonical_portal_and_blocks_arbitrary_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outbound, "_metadata_allowed", True)
    monkeypatch.setattr(outbound, "_portal_allowed", True)
    monkeypatch.setattr(outbound, "_portal_ips", {"8.8.4.4"})
    monkeypatch.setattr(outbound, "_metadata_ips", {"api.themoviedb.org": {"8.8.8.8"}})
    outbound.native_network_audit("socket.getaddrinfo", ("blueashreel.com", 443))
    outbound.native_network_audit("socket.connect", (None, ("8.8.4.4", 443)))
    with socket.socket() as tcp, socket.socket(type=socket.SOCK_DGRAM) as udp:
        outbound.native_network_audit("socket.connect", (tcp, ("8.8.8.8", 443)))
        for event, arguments in (
            ("socket.connect", (tcp, ("1.1.1.1", 443))),
            ("socket.connect", (tcp, ("8.8.8.8", 80))),
            ("socket.connect", (udp, ("8.8.8.8", 443))),
            ("socket.bind", (tcp, ("8.8.8.8", 443))),
            ("socket.sendto", (udp, ("8.8.8.8", 443))),
            ("socket.getaddrinfo", ("unapproved.invalid", 443)),
            ("socket.getaddrinfo", ("blueashreel.com", 80)),
        ):
            with pytest.raises(outbound.OutboundConnectionDisabled):
                outbound.native_network_audit(event, arguments)


def test_native_metadata_wrappers_filter_dns_and_preserve_isolated_hosts() -> None:
    # Audit hooks cannot be removed; keep this process isolated and stub every
    # resolver/connect function before installation. No network call is made.
    program = r'''
import json, socket
from types import SimpleNamespace
from app.services import outbound
calls=[]
answers={
 'api.themoviedb.org': ['8.8.8.8', '127.0.0.1', '10.0.0.1'],
 'image.tmdb.org': ['1.1.1.1'],
 'blueashreel.com': ['8.8.4.4'],
}
def resolver(host,port,*args):
 calls.append(('dns',host,port))
 return [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',(ip,port)) for ip in answers[host]]
def connect(connection,address): calls.append(('connect',address)); return 0
socket.getaddrinfo=resolver
socket.socket.connect=connect
socket.socket.connect_ex=connect
outbound.install_native_network_guard(SimpleNamespace(deployment_mode='native_windows',outbound_integrations_enabled=False,tmdb_token='configured'),allow_portal=True)
assert [r[4][0] for r in socket.getaddrinfo('api.themoviedb.org',443,type=socket.SOCK_STREAM)] == ['8.8.8.8']
socket.getaddrinfo('image.tmdb.org',443,type=socket.SOCK_STREAM)
socket.getaddrinfo('blueashreel.com',443,type=socket.SOCK_STREAM)
with socket.socket() as connection:
 connection.connect(('8.8.8.8',443))
 connection.connect_ex(('1.1.1.1',443))
 connection.connect(('8.8.4.4',443))
 before=len(calls)
 for operation in (
  lambda: socket.getaddrinfo('attacker.invalid',443),
  lambda: socket.getaddrinfo('api.themoviedb.org',80),
  lambda: socket.getaddrinfo('image.tmdb.org',443,type=socket.SOCK_DGRAM),
  lambda: connection.connect(('api.themoviedb.org',443)),
  lambda: connection.connect(('8.8.8.8',80)),
  lambda: connection.connect(('9.9.9.9',443)),
 ):
  try: operation()
  except outbound.OutboundConnectionDisabled: pass
  else: raise AssertionError('Unexpected egress')
 assert len(calls)==before
 answers['api.themoviedb.org']=['192.168.1.1']
 try: socket.getaddrinfo('api.themoviedb.org',443)
 except outbound.OutboundConnectionDisabled: pass
 else: raise AssertionError('Private DNS answer accepted')
 assert 'api.themoviedb.org' not in outbound._metadata_ips
 try: connection.connect(('8.8.8.8',443))
 except outbound.OutboundConnectionDisabled: pass
 else: raise AssertionError('Stale rejected DNS destination accepted')
 connection.connect(('1.1.1.1',443))
 answers['api.themoviedb.org']=['8.8.8.8']*33
 try: socket.getaddrinfo('api.themoviedb.org',443)
 except outbound.OutboundConnectionDisabled: pass
 else: raise AssertionError('Unbounded DNS answers accepted')
print(json.dumps({'approved_connects':sum(c[0]=='connect' for c in calls),'no_network':True}))
'''
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"approved_connects": 4, "no_network": True}


@pytest.mark.parametrize("token", ["", "configured"])
def test_installing_native_guard_enables_metadata_only_with_valid_configured_token(
    token: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(outbound, "_native_guard_installed", False)
    monkeypatch.setattr(outbound, "_metadata_allowed", False)
    monkeypatch.setattr(outbound, "_metadata_ips", {"api.themoviedb.org": {"8.8.8.8"}})
    monkeypatch.setattr(outbound, "_portal_allowed", False)
    monkeypatch.setattr(outbound, "_install_native_socket_wrappers", lambda: None)
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: None)
    config = SimpleNamespace(deployment_mode="native_windows", outbound_integrations_enabled=False, tmdb_token=token)
    outbound.install_native_network_guard(config, allow_portal=True)  # type: ignore[arg-type]
    assert outbound._metadata_allowed is bool(token)
    assert outbound._metadata_ips == {}
    assert outbound._portal_allowed is True
