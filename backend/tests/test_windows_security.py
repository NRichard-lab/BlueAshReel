from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import TestContext

from app.config import AppConfig
from app.services import outbound
from app.services.media_roots import (
    InvalidFolderSelection,
    list_folders,
    make_selection_id,
    read_only_enforced,
    resolve_selection_id,
    root_state,
)
from app.services.paths import (
    UnsafeMediaPath,
    native_directory_guard,
    safe_discovered_file,
    validate_media_directory,
    validate_windows_path_text,
)


def native_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        _env_file=None, deployment_mode="native_windows", windows_service_prefix="BlueReelDevelopment",
        native_program_dir=tmp_path / "program", native_data_dir=tmp_path / "state",
        app_data_dir=tmp_path / "state/data", temp_dir=tmp_path / "state/temp",
        artwork_dir=tmp_path / "state/artwork", media_roots=str(tmp_path / "media"), media_root_definitions="",
        app_secret_key="native-security-test-with-varied-long-secret-029",  # noqa: S106
    )


@pytest.mark.parametrize("value", [
    r"D:\Media\..\private", r"D:\Media\Movies.", "D:\\Media\\Movies ",
    r"D:\Media\file:stream", r"D:\Media\CON", r"D:\Media\lpt9.txt", r"D:\Media\nul.mp4",
    r"\\?\D:\Media", r"\\.\C:\Media", r"D:Media", r"\Media", "/media", "D:\\Media\\bad\x00name",
])
def test_windows_ambiguous_aliases_and_device_paths_are_rejected(value: str) -> None:
    with pytest.raises(UnsafeMediaPath):
        validate_windows_path_text(value)


@pytest.mark.parametrize("value", [r"D:\Media", r"D:\Media\TV Shows", r"\\server\share\Movies"])
def test_windows_real_drive_and_advanced_share_paths_are_accepted(value: str) -> None:
    validate_windows_path_text(value)


@pytest.mark.skipif(os.name != "nt", reason="Exercises Win32 directory handles")
def test_native_guard_prevents_directory_replacement_without_source_writes(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    fixture = media / "untouched.mp4"
    fixture.write_bytes(b"synthetic noncopyrighted fixture")
    before = (fixture.read_bytes(), fixture.stat().st_mtime_ns)
    with native_directory_guard(media), pytest.raises(PermissionError):
        media.rename(tmp_path / "moved")
    assert (fixture.read_bytes(), fixture.stat().st_mtime_ns) == before
    assert not (tmp_path / "moved").exists()
    assert read_only_enforced(media) is None


@pytest.mark.skipif(os.name != "nt", reason="Exercises real NTFS junctions")
def test_real_junction_child_and_ancestor_are_rejected(tmp_path: Path) -> None:
    config = native_config(tmp_path)
    media = tmp_path / "media"
    outside = tmp_path / "outside"
    media.mkdir()
    (outside / "nested").mkdir(parents=True)
    fixture = outside / "nested/secret.mp4"
    fixture.write_bytes(b"untouched outside fixture")
    junction = media / "junction"
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(executable), "-NoProfile", "-NonInteractive", "-Command",
         "New-Item -ItemType Junction -Path $env:TEST_JUNCTION -Target $env:TEST_TARGET | Out-Null"],
        env={**os.environ, "TEST_JUNCTION": str(junction), "TEST_TARGET": str(outside)},
        check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert junction.is_junction()
        root = config.approved_media_roots[0]
        assert list_folders(resolve_selection_id(make_selection_id(root, (), config), config)) == []
        with pytest.raises(InvalidFolderSelection, match="Linked"):
            resolve_selection_id(make_selection_id(root, ("junction",), config), config)
        with pytest.raises(UnsafeMediaPath, match="Linked"):
            validate_media_directory(str(junction / "nested"), config)
        assert safe_discovered_file(junction / "nested/secret.mp4", media) is None

        ancestor_config = config.model_copy(update={"media_roots": str(junction / "nested")})
        ancestor_root = ancestor_config.approved_media_roots[0]
        assert root_state(ancestor_root) == ("unavailable", None)
        with pytest.raises(InvalidFolderSelection, match="Linked"):
            resolve_selection_id(make_selection_id(ancestor_root, (), ancestor_config), ancestor_config)
        assert fixture.read_bytes() == b"untouched outside fixture"
    finally:
        # Remove this test's junction itself, never recursively traverse its target.
        assert junction.parent == media and junction.is_junction()
        junction.rmdir()
    assert outside.is_dir() and fixture.is_file()


def test_native_program_and_all_persistent_state_are_protected(tmp_path: Path) -> None:
    config = native_config(tmp_path)
    for target in (tmp_path / "program", tmp_path / "state/database", tmp_path / "state/backups"):
        target.mkdir(parents=True)
        configured = config.model_copy(update={"media_roots": str(target)})
        with pytest.raises(UnsafeMediaPath, match="Application data"):
            validate_media_directory(str(target), configured)


def test_native_folder_access_is_owner_only_and_uses_windows_platform(
    owner_context: tuple[TestContext, str],
) -> None:
    context, csrf = owner_context
    context.config.deployment_mode = "native_windows"
    root_response = context.client.get("/api/v1/media-roots")
    assert root_response.json()["platform"] == "windows"
    assert context.client.get("/api/v1/media-storage").json()["platform"] == "windows"
    root = root_response.json()["items"][0]
    assert root["internal_path"] == str(context.media_root)
    browse = context.client.post(
        "/api/v1/media-folders/browse", headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    )
    assert browse.status_code == 200
    assert browse.json()["platform"] == "windows"
    created = context.client.post(
        "/api/v1/users", headers={"X-CSRF-Token": csrf},
        json={"username": "native-admin", "password": "local native account password", "role": "Administrator"},
    )
    assert created.status_code == 201
    context.client.cookies.clear()
    login = context.client.post(
        "/api/v1/auth/login", json={"username": "native-admin", "password": "local native account password"},
    )
    admin_csrf = login.json()["csrf_token"]
    assert context.client.get("/api/v1/media-roots").status_code == 403
    assert context.client.get("/api/v1/media-storage").status_code == 403
    assert context.client.post(
        "/api/v1/media-folders/browse", headers={"X-CSRF-Token": admin_csrf},
        json={"selection_id": root["selection_id"]},
    ).status_code == 403
    assert context.client.post(
        "/api/v1/media-folders/validate", headers={"X-CSRF-Token": admin_csrf},
        json={"path": str(context.media_root)},
    ).status_code == 403


def firewall_payload(config: AppConfig) -> dict[str, Any]:
    assert config.native_program_dir is not None
    return {
        "profiles": ["True", "True", "True"],
        "rules": [
            {
                "name": f"{config.windows_service_prefix}-Outbound-{name}",
                "program": str(config.native_program_dir / path),
                "enabled": "True", "direction": "Outbound", "action": "Block", "profile": "Any", "status": "OK",
                "protocol": "Any", "local": ["Any"], "remote_port": ["Any"], "local_port": ["Any"],
                "remote": list(outbound._BLOCKED_REMOTE_RANGES), "service": "Any", "package": None,
                "interface": ["Any"], "interface_type": "Any",
            }
            for name, path in outbound._WINDOWS_PROGRAMS.items()
        ],
    }


def test_firewall_status_checks_effective_rules_not_just_configuration(tmp_path: Path) -> None:
    config = native_config(tmp_path)
    payload = firewall_payload(config)
    assert config.native_program_dir is not None
    assert outbound._verified_firewall(payload, config.windows_service_prefix, config.native_program_dir)
    for key, unsafe in (
        ("enabled", "False"), ("action", "Allow"), ("profile", "Private"), ("protocol", "TCP"),
        ("remote", ["Any"]), ("remote_port", ["443"]), ("program", str(tmp_path / "different.exe")),
        ("direction", "Inbound"), ("service", "OtherService"), ("interface", ["Ethernet"]),
        ("status", "Error"), ("package", "SomeOtherPackage"),
    ):
        modified = copy.deepcopy(payload)
        modified["rules"][0][key] = unsafe
        assert not outbound._verified_firewall(modified, config.windows_service_prefix, config.native_program_dir)
    payload["profiles"][0] = "False"
    assert not outbound._verified_firewall(payload, config.windows_service_prefix, config.native_program_dir)


def test_firewall_status_is_cached_and_errors_are_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = native_config(tmp_path)
    outbound._firewall_cache.clear()
    monkeypatch.setattr(outbound, "_native_guard_installed", True)
    calls: list[str] = []

    def query(group: str) -> dict[str, Any]:
        calls.append(group)
        return firewall_payload(config)

    monkeypatch.setattr(outbound, "_query_windows_firewall", query)
    if os.name != "nt":
        assert outbound.network_enforcement(config).status == "unknown"
        return
    assert outbound.network_enforcement(config).status == "enforced"
    assert outbound.network_enforcement(config).checked_at is not None
    assert len(calls) == 1
    outbound._firewall_cache.clear()

    def failed(_group: str) -> dict[str, Any]:
        raise PermissionError("C:\\PrivateFolder private token=password123")

    monkeypatch.setattr(outbound, "_query_windows_firewall", failed)
    status = outbound.network_enforcement(config)
    assert status.status == "unknown"
    assert "PrivateFolder" not in status.model_dump_json()
    assert "password123" not in status.model_dump_json()
    outbound._firewall_cache.clear()


@pytest.mark.parametrize("event,arguments", [
    ("socket.getaddrinfo", ("metadata.invalid", 443)), ("socket.gethostbyname", ("metadata.invalid",)),
    ("socket.gethostbyaddr", ("192.0.2.1",)), ("socket.connect", (None, ("192.0.2.1", 443))),
    ("socket.sendto", (None, ("192.0.2.1", 53))), ("socket.connect", (None, ("2001:db8::1", 443))),
    ("socket.getnameinfo", (("192.0.2.1", 443),)), ("socket.getnameinfo", (("127.0.0.2", 443),)),
    ("socket.getnameinfo", (("::1", 443, 0, 0),)), ("socket.gethostbyaddr", ("127.0.0.2",)),
    ("socket.gethostbyaddr", ("::1",)), ("socket.bind", (None, ("metadata.invalid", 0))),
    ("socket.getnameinfo", ()), ("socket.bind", (None, ("0.0.0.0", 0))),  # noqa: S104 - forbidden bind fixture
])
def test_native_guard_blocks_dns_and_direct_ip_without_network_activity(event: str, arguments: tuple) -> None:
    with pytest.raises(outbound.OutboundConnectionDisabled):
        outbound.native_network_audit(event, arguments)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost", "LOCALHOST."])
def test_native_guard_preserves_loopback(host: str) -> None:
    outbound.native_network_audit("socket.getaddrinfo", (host, 8080))
    outbound.native_network_audit("socket.connect", (None, (host, 8080)))
    outbound.native_network_audit("socket.bind", (None, (host, 8080)))


def test_real_python_dns_and_direct_ip_audits_fail_before_connection(tmp_path: Path) -> None:
    # The subprocess installs the irreversible audit hook, keeping pytest isolated.
    program = """
import json, socket, sys
from app.services.outbound import native_network_audit, OutboundConnectionDisabled
sys.addaudithook(native_network_audit)
blocked = []
for operation in (lambda: socket.getaddrinfo('nonexistent.invalid', 443),
                  lambda: socket.create_connection(('192.0.2.1', 443), timeout=0.1)):
    try:
        operation()
    except OutboundConnectionDisabled:
        blocked.append(True)
print(json.dumps(blocked))
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == [True, True]


def test_real_forward_and_reverse_dns_are_blocked_before_resolver_and_numeric_loopback_is_local() -> None:
    # A second audit trap prevents resolver activity even if the app regresses.
    # Capture raw functions to verify that the irreversible audit gate, not just
    # the friendly wrappers, catches getnameinfo/gethostbyaddr/gethostbyname_ex.
    program = r"""
import json, socket, sys
from app.config import get_config
from app.services.outbound import install_native_network_guard, OutboundConnectionDisabled
raw_nameinfo = socket.getnameinfo
raw_address = socket.gethostbyaddr
raw_host = socket.gethostbyname
raw_host_ex = socket.gethostbyname_ex
raw_addrinfo = socket.getaddrinfo
config = get_config().model_copy(update={'deployment_mode':'native_windows', 'outbound_integrations_enabled':False})
install_native_network_guard(config)
def no_resolver(event, arguments):
    if event in {'socket.getnameinfo', 'socket.gethostbyaddr', 'socket.gethostbyname', 'socket.getaddrinfo'}:
        raise AssertionError('A resolver call escaped the first audit guard')
sys.addaudithook(no_resolver)
operations = [
    lambda: raw_nameinfo(('192.0.2.1',443),0),
    lambda: raw_nameinfo(('127.0.0.2',443),0),
    lambda: raw_nameinfo(('::1',443,0,0),socket.NI_NUMERICHOST | socket.NI_NUMERICSERV),
    lambda: raw_address('192.0.2.1'), lambda: raw_address('127.0.0.2'), lambda: raw_address('::1'),
    lambda: raw_host('blocked.invalid'), lambda: raw_host_ex('blocked.invalid'),
    lambda: raw_addrinfo('blocked.invalid',443),
    lambda: socket.getnameinfo(('127.0.0.2',443),0),
    lambda: socket.getnameinfo(('192.0.2.1',443),socket.NI_NUMERICHOST | socket.NI_NUMERICSERV),
]
blocked = 0
for operation in operations:
    try: operation()
    except OutboundConnectionDisabled: blocked += 1
assert blocked == len(operations)
assert socket.getnameinfo(('127.0.0.2',443),socket.NI_NUMERICHOST | socket.NI_NUMERICSERV) == ('127.0.0.2','443')
assert socket.getnameinfo(('::1',8080,0,0),socket.NI_NUMERICHOST | socket.NI_NUMERICSERV) == ('::1','8080')
assert socket.gethostbyname('LOCALHOST.') == '127.0.0.1'
assert socket.gethostbyname_ex('localhost') == ('127.0.0.1',[],['127.0.0.1'])
# getfqdn deliberately catches socket errors, but its resolver was denied.
assert socket.getfqdn('blocked.invalid') == 'blocked.invalid'
print(json.dumps({'blocked':blocked,'numeric_loopback':True,'resolver_calls':0}))
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"blocked": 11, "numeric_loopback": True, "resolver_calls": 0}


def test_native_socket_entrypoints_reject_names_before_c_address_conversion() -> None:
    # Stub C entry points before installing wrappers: these checks cannot cause
    # even a loopback network operation, and catch the pre-audit DNS regression.
    program = r"""
import json, socket
from app.config import get_config
from app.services.outbound import install_native_network_guard, OutboundConnectionDisabled
calls = []
def operation(connection,*args):
    calls.append(args)
    return 0
for name in ('bind','connect','connect_ex','sendto'):
    setattr(socket.socket,name,operation)
socket.getaddrinfo = lambda *args: calls.append(args) or []
config = get_config().model_copy(update={'deployment_mode':'native_windows', 'outbound_integrations_enabled':False})
install_native_network_guard(config)
with socket.socket() as connection:
    for operation in (
        lambda: connection.bind(('blocked.invalid',0)),
        lambda: connection.connect(('blocked.invalid',443)),
        lambda: connection.connect_ex(('blocked.invalid',443)),
        lambda: connection.sendto(b'x',('blocked.invalid',53)),
        lambda: connection.sendto(b'x',0,('192.0.2.1',53)),
        lambda: socket.getaddrinfo('blocked.invalid',443),
    ):
        try: operation()
        except OutboundConnectionDisabled: pass
        else: raise AssertionError('An unsafe entrypoint was accepted')
    assert calls == []
    connection.bind(('localhost',0))
    connection.connect(('LOCALHOST.',8080))
    connection.connect_ex(('127.0.0.2',8080))
    connection.sendto(b'x',('localhost',8080))
    connection.sendto(b'x',0,('localhost',8080))
assert calls[0] == (('127.0.0.1',0),)
assert calls[1] == (('127.0.0.1',8080),)
assert calls[2] == (('127.0.0.2',8080),)
assert calls[3] == (b'x',('127.0.0.1',8080))
assert calls[4] == (b'x',0,('127.0.0.1',8080))
socket.getaddrinfo('localhost',8080)
assert calls[-1][0] == '127.0.0.1' and calls[-1][-1] & socket.AI_NUMERICHOST
socket.getaddrinfo('localhost',8080,family=socket.AF_INET6)
assert calls[-1][0] == '::1'
print(json.dumps({'unsafe_calls':0,'safe_numeric_calls':len(calls)}))
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, check=True, timeout=10)
    assert json.loads(result.stdout) == {"unsafe_calls": 0, "safe_numeric_calls": 7}


def test_socket_wrappers_are_not_installed_for_docker_or_enabled_outbound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(outbound, "_native_guard_installed", False)
    monkeypatch.setattr(outbound, "_install_native_socket_wrappers", lambda: called.append("wrappers"))
    monkeypatch.setattr(sys, "addaudithook", lambda _hook: called.append("audit"))
    config = native_config(tmp_path)
    outbound.install_native_network_guard(config.model_copy(update={"deployment_mode": "container"}))
    outbound.install_native_network_guard(config.model_copy(update={"outbound_integrations_enabled": True}))
    assert called == []
