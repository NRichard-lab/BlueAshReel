"""Exercise the exact installer in its explicitly isolated, disposable namespace.

Does not interact with the desktop, pair an Agent, or read production secrets.
Leaves the test data and logs available for inspection; stops only its own runtime.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise RuntimeError('Linked acceptance paths are not supported')


def registry_snapshot() -> str:
    import winreg
    records = {}
    for name in (
        r'Software\BlueReel\development',
        r'Software\Microsoft\Windows\CurrentVersion\Run',
        r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5E41781A-6BE6-4505-B5D9-177F592ED611}_is1',
    ):
        values = []
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, name) as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    values.append(winreg.EnumValue(key, index))
        except FileNotFoundError:
            pass
        records[name] = sorted(values)
    # Values are only hashed in memory; never write paths or startup commands to a report.
    return hashlib.sha256(json.dumps(records, sort_keys=True, default=str).encode()).hexdigest()


def shortcut_snapshot() -> str:
    root = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Blue Ash Reel Development'
    no_links(root)
    rows = []
    if root.exists():
        for path in sorted(root.rglob('*')):
            no_links(path)
            if path.is_file():
                rows.append((str(path.relative_to(root)), digest(path)))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--test-id', required=True)
    parser.add_argument('--port', default=29280, type=int)
    parser.add_argument('--report-dir', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt' or not re.fullmatch(r'[a-z0-9]{1,32}', args.test_id):
        raise RuntimeError('Windows and an explicit safe disposable ID are required')
    if not re.fullmatch(r'[a-f0-9]{40}', args.source_revision) or not re.fullmatch(r'[a-f0-9]{64}', args.sha256):
        raise RuntimeError('Exact source and installer checksums are required')
    if not 1024 <= args.port <= 65533:
        raise RuntimeError('Invalid test port')
    args.installer = args.installer.resolve(strict=True)
    no_links(args.installer)
    if digest(args.installer) != args.sha256:
        raise RuntimeError('Installer checksum mismatch')
    root = Path(os.environ['USERPROFILE']) / 'BlueAshReel-Installer-Tests' / args.test_id
    program, data = root / 'program', root / 'data'
    no_links(root)
    if root.exists():
        raise RuntimeError('Choose a fresh test ID; retained tests are never overwritten')
    for port in range(args.port, args.port + 3):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    no_links(args.report_dir)
    args.report_dir.mkdir(parents=True, exist_ok=False)
    baseline = (registry_snapshot(), shortcut_snapshot())
    inventory = json.loads(args.inventory.read_text())['files']
    report = {'source_revision': args.source_revision, 'installer_sha256': args.sha256,
              'test_id': args.test_id, 'program': str(program), 'data': str(data), 'port': args.port,
              'started_at': dt.datetime.now(dt.UTC).isoformat(), 'phases': {},
              'scope': 'Exact installer: unattended isolated install, manual tray launch, repair, preserve-uninstall, reinstall',
              'gui_click_coverage': False, 'portal_pairing_attempted': False}
    report_path = args.report_dir / 'acceptance.json'
    runtime_started = False

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def run(command: list[str | Path], name: str, timeout: int = 360) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        (args.report_dir / (name + '.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect the disposable test log')
        return result

    def maintenance(action: str, name: str) -> None:
        run([Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe',
             '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', program / 'support/user-install.ps1', '-Action', action,
             '-ProgramDir', program, '-DataDir', data, '-Instance', 'development'], name)

    def install(phase: str) -> None:
        run([args.installer, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
             '/ISOLATEDTEST=' + args.test_id, '/PORT=' + str(args.port),
             '/LOG=' + str(args.report_dir / (phase + '-installer.log'))], phase)
        if baseline != (registry_snapshot(), shortcut_snapshot()):
            raise RuntimeError('Production registration/startup/shortcuts changed')

    def launch(phase: str) -> dict:
        nonlocal runtime_started
        subprocess.Popen([str(program / 'BlueAshReelAgent.exe'), '--data-dir', str(data)],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        runtime_started = True
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                status = json.loads((data / 'state/tray-status.json').read_text())
                if (status['healthy'] and not status['paired'] and
                        time.time() - status['updated_at'] < 5):
                    assert status['version'] == '0.1.0-development.6'
                    assert status['source_revision'] == args.source_revision
                    with opener.open(f'http://127.0.0.1:{args.port}/api/v1/health/ready', timeout=5) as response:
                        assert response.status == 200
                    with opener.open(f'http://127.0.0.1:{args.port}/api/v1/version', timeout=5) as response:
                        assert json.load(response)['version'] == status['version']
                    report['phases'][phase] = {'healthy': True, 'tray_started': True, 'paired': False,
                        'version': status['version'], 'source_revision': status['source_revision']}
                    save()
                    return status
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.5)
        raise RuntimeError('The disposable tray/runtime did not become healthy')

    def validate_payload() -> None:
        manifest = json.loads((program / 'included-components.json').read_text())
        assert manifest['source_revision'] == args.source_revision and manifest['source_revision_dirty'] is False
        assert manifest['version'] == '0.1.0-development.6'
        for row in inventory:
            path = program / row['path']
            assert path.resolve().is_relative_to(program.resolve())
            no_links(path)
            assert digest(path) == row['sha256'], 'Installed payload file mismatch: ' + row['path']
        report['payload_files_verified'] = len(inventory)

    def retained() -> dict[str, str]:
        paths = ['configuration/.env', 'configuration/tmdb-access-token.txt',
                 'remote-identity/acceptance-sentinel.txt', 'artwork/acceptance-sentinel.txt']
        assert not (data / 'remote-identity/identity.json').exists()
        return {path: digest(data / path) for path in paths}

    try:
        install('clean_install')
        validate_payload()
        launch('clean_install')
        record = json.loads((data / 'configuration/installation.json').read_text())
        assert record['program_dir'] == str(program) and record['data_dir'] == str(data)
        assert record['runtime_mode'] == 'per_user'
        assert all(Path(path).is_relative_to(data) for path in record['storage'].values())
        database = Path(record['storage']['database']) / 'app.db'
        with sqlite3.connect(database) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not connection.execute('PRAGMA foreign_key_check').fetchall()
            report['schema_revision'] = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
            assert report['schema_revision'] == '2d0100000001'
            report['clean_counts'] = {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                                     for table in ('users', 'libraries', 'media_items', 'portal_grants')}
            assert not any(report['clean_counts'].values())
            connection.execute('INSERT INTO application_settings(key,value,updated_at) VALUES(?,?,?)',
                               ('isolated_installer_sentinel', json.dumps('retain me'), dt.datetime.now(dt.UTC).isoformat()))
        (data / 'artwork/acceptance-sentinel.txt').write_text('synthetic artwork preservation fixture')
        (data / 'remote-identity/acceptance-sentinel.txt').write_text('noncredential identity-directory preservation fixture')
        token = data / 'configuration/tmdb-access-token.txt'
        assert token.is_file() and token.stat().st_size == 0
        for binary in ('ffmpeg', 'ffprobe'):
            result = run([program / f'runtime/ffmpeg/{binary}.exe', '-version'], binary, timeout=20)
            report[binary] = result.stdout.splitlines()[0]
        probe = run([program / 'runtime/python/python.exe', '-I', '-B', '-c',
            ("import json,secrets,sys;from pathlib import Path;from app.native_runtime import load_installation,load_configuration;"
            "from app.metadata.tmdb import TMDBMetadataProvider;from app.metadata.service import configured_provider;"
            "i=load_installation(Path(sys.argv[1]));c=load_configuration(i);"
            "assert c.tmdb_token_file==Path(sys.argv[1])/'configuration/tmdb-access-token.txt';"
            "assert configured_provider(c) is None;"
            "c.tmdb_token_file.write_text(secrets.token_urlsafe(48),encoding='ascii');"
            "provider=configured_provider(c);assert isinstance(provider,TMDBMetadataProvider);provider.close();"
            "print(json.dumps({'python':sys.version.split()[0],'metadata_provider_imported':True,"
            "'tmdb_token_file_configured':True,'empty_token_truthfully_unconfigured':True,"
            "'synthetic_token_provider_configured_without_network':True}))"),
            data], 'runtime-imports', timeout=30)
        report.update(json.loads(probe.stdout))
        original = retained()
        assert (Path(record['storage']['logs']) / 'agent.log').is_file()
        report['version_in_runtime_log'] = '0.1.0-development.6' in (Path(record['storage']['logs']) / 'agent.log').read_text()
        assert report['version_in_runtime_log'] is True
        for phase in ('repair', 'retained_reinstall'):
            if phase == 'retained_reinstall':
                run([program / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                     '/LOG=' + str(args.report_dir / 'preserve-uninstall.log')], 'preserve_uninstall')
                runtime_started = False
                assert retained() == original
                assert not (program / 'runtime/python/python.exe').exists()
                report['preserve_uninstall'] = True
            install(phase)
            validate_payload()
            launch(phase)
            assert retained() == original
            with sqlite3.connect(database) as connection:
                assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                assert json.loads(connection.execute('SELECT value FROM application_settings WHERE key=?',
                    ('isolated_installer_sentinel',)).fetchone()[0]) == 'retain me'
            report['phases'][phase]['data_configuration_synthetic_sentinels_preserved'] = True
            report['phases'][phase]['unpaired_identity_absence_preserved'] = True
        report['passed'] = True
    except (RuntimeError, OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError, sqlite3.Error) as error:
        report['passed'] = False
        report['failure_type'] = type(error).__name__
        report['failure'] = str(error)  # Assertions contain only test-scoped labels.
    finally:
        if runtime_started:
            try:
                maintenance('Stop', 'final-owned-stop')
                report['disposable_runtime_stopped'] = True
            except (RuntimeError, OSError, subprocess.SubprocessError):
                report['disposable_runtime_stopped'] = False
                report['passed'] = False
        report['production_registration_startup_shortcuts_unchanged'] = baseline == (registry_snapshot(), shortcut_snapshot())
        if not report['production_registration_startup_shortcuts_unchanged']:
            report['passed'] = False
        report['finished_at'] = dt.datetime.now(dt.UTC).isoformat()
        save()
    print(json.dumps({'passed': report['passed'], 'report': str(report_path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
