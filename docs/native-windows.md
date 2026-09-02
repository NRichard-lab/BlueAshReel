# Native Windows development installer

This is the first **unsigned, x64 development installer**, not a public release.
Use small disposable libraries first. Installation requires a Windows
administrator, but there are no separately installed runtime prerequisites on
Windows 10 22H2 or Windows 11. Supported Windows includes the .NET Framework 4.8
used by the service wrapper. Hardware encoding additionally depends on a working
vendor display driver; CPU playback/conversion does not require a GPU encoder.

## One application, two deployments

The installer packages the existing FastAPI application and durable worker with
embedded CPython, the same compiled React frontend with Node, FFmpeg/FFprobe,
SQLite, Alembic migrations, backup tools, and Caddy. There is no native fork of
the API, database, authentication, scanner, playback or backup format. Windows
adapters own only paths, isolated configuration, service lifecycle and firewall
inspection. Docker keeps its Linux images, read-only media mounts, internal
network and existing operating procedures.

Inno Setup was selected for its maintained Windows installer/compiler, elevation,
version/upgrade detection, native wizard, shortcuts, logging and uninstaller. The
payload is an ordinary directory tree, not a single-file Python executable that
extracts at each service startup. Installation does not download dependencies.

| Item | Development package | Stable packaging definition |
| --- | --- | --- |
| Product | BlueReel Development | BlueReel |
| Program files | `C:\Program Files\BlueReel Development` | `C:\Program Files\BlueReel` |
| Persistent data | `C:\ProgramData\BlueReel-Development` | `C:\ProgramData\BlueReel` |
| Browser port | 18080 | 8080 |
| Internal API / web | 18081 / 18082 | 8081 / 8082 |
| Service prefix | `BlueReelDevelopment` | `BlueReel` |

Only the development identity was installed/tested in this phase. Stable defaults
are not a claim of a separately validated stable/public release.

## Installation and media selection

1. Verify the installer SHA-256 against its generated sidecar before running it.
2. Run `BlueReel-Setup-Development-x64.exe` and accept Windows elevation. Because
   it is unsigned, SmartScreen may warn; do not disable machine-wide protection.
3. Keep localhost access unless deliberately enabling a fixed private LAN IPv4
   address. The public port and two adjacent internal ports must be available.
4. Use **Browse** for the native folder-selection dialog, or enter one absolute
   Windows directory per line. Multiple approved roots are supported. Choose
   existing readable folders, not whole drives or application-state directories.
5. Finish installation, then open [BlueReel Development](http://127.0.0.1:18080).
   Create the initial Owner, choose an approved subfolder, and scan the library.

The Start Menu includes Open BlueReel, media-root and network configuration,
online backup, restore dry-run validation, notices and uninstall. Maintenance
actions request elevation separately; the web API does not run as administrator.
Repair preserves the previous roots, ports, application secret and database.

Services run as LocalService, not the interactive user. Give the chosen media
folders read/list access usable by that identity where needed; the installer
does not silently rewrite source-media ACLs. Missing drives and access denial are
reported in the root browser/health checks. Native source access never writes,
moves or deletes media, but is not advertised as NTFS-enforced read-only access.
Directory junctions/reparse points, path traversal, device/alternate-stream paths
and ambiguous Win32 aliases are rejected. Normal folder browsing is Owner-only;
the limited first-run capability can browse the same approved roots during setup.

Network shares are an advanced case: LocalService is not the signed-in user's
network identity, mapped drive letters are session-specific, and strict-local
egress restrictions deliberately prevent normal remote-share access. Do not
weaken strict-local policy or run as LocalSystem as a troubleshooting shortcut.

For unattended disposable tests, `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` and
`/LOG="absolute-log-path"` are supported. `/MEDIAROOTSLIST="absolute-UTF8-file"`
supplies one approved path per line. These are installation inputs, not passwords.

## Services and persistent state

Four delayed-automatic services form one product:

- `BlueReelDevelopmentAPI`: API, authentication, SQLite, playback management.
- `BlueReelDevelopmentWorker`: shared durable scan/job worker.
- `BlueReelDevelopmentWeb`: compiled production frontend on internal loopback.
- `BlueReelDevelopmentProxy`: Caddy same-origin routing on the browser port.

All run as **NT AUTHORITY\LocalService** with individual service SIDs. Program
Files is immutable runtime content. The data root has a protected ACL for SYSTEM,
Administrators and these four service SIDs, not a blanket LocalService grant.
Configuration/secret, database, artwork, local logs, temporary transcodes, backup
archives and runtime markers live beneath that data root. Repository `.env`,
working directory and inherited application settings cannot redirect services.
Node and Caddy do not receive the application secret.

WinSW restarts failed services after 10 seconds, then 30 seconds, then takes no
action; the failure counter resets after one day. Runtime health checks are
bounded. API, worker and Node use cooperative shutdown; Windows Job Objects reap
FFmpeg descendants even after an abrupt runner exit. Scans checkpoint/requeue and
recover stale leases. Caddy's admin API is disabled and the stateless proxy is
terminated at stop, not gracefully drained. No logged-in desktop is needed by
the service design; actual reboot/sign-out tests are separately reported.

## Privacy and LAN access

Default routing binds only `127.0.0.1`. There are no router, UPnP, global firewall,
CDN, remote-font, metadata-provider, analytics or cloud transcoding changes.
The five installed Python, Node, FFmpeg, FFprobe and Caddy binaries receive exact
application-specific outbound-block rules for non-loopback destinations on all
profiles. Python and Node additionally reject non-local DNS before Windows can
delegate it to its shared DNS Client. FFmpeg is built with networking disabled.

The Privacy page checks the merged effective Windows Firewall rules, enabled
profiles and Python guard. Missing/mismatched rules are not called enforced;
query failure is unknown. See [native security](native-runtime-security.md).

Optional LAN access is an explicit elevated Owner maintenance action. Caddy also
binds the chosen RFC1918 address, with a single inbound rule limited to its binary,
that address/port, Private profile and LocalSubnet. It does not change the Windows
network's profile. Choosing localhost removes that rule. HTTP LAN access is not
internet access or managed TLS; use only a trusted private household network.

## Playback controls

[Playback & Transcoding](transcoding-settings.md) shares settings with Docker:
Automatic (default), Hardware Preferred, Software Only, Direct Play/Remux Only,
and advanced Hardware Required. Compatible media still direct-plays first.
Settings apply to new streams; existing streams retain their policy snapshot.
Only a successful short encode with the exact installed FFmpeg marks QSV, NVENC
or AMF available. Hardware Required never falls back. Active Streams and the
player report actual method/encoder/fallback, not just the selected preference.

Conservative defaults are two conversions, 1080p, 8 Mbps, `veryfast` CPU encoding,
4 GiB temporary output and 90-second inactive cleanup; 4K conversion is off.
Hardware acceleration is currently H.264 video **encoding** only. Video decoding,
AAC audio and text-to-WebVTT subtitle work remain on the CPU. Image-subtitle
burn-in, external sidecar discovery and an adaptive bitrate ladder are deferred.

## Backup, repair and upgrade

The Start Menu backup action uses the shared SQLite online-backup implementation,
checks integrity/foreign keys/schema and all archive checksums, then performs a
restore dry run. Archives include household state, configuration and optional
artwork, never source media or disposable transcodes. They contain secrets and
must remain private. Dry validation does not overwrite live data.

Running the installer again repairs the same identity. An in-place upgrade first
rejects new playback, creates and validates an online backup, saves the prior
program/configuration, stops the four services, replaces binaries, migrates the
shared schema, restarts services and checks readiness. Prior program/configuration
copies and backup metadata are retained under the private data root's `upgrade`
and `state` directories. They are not automatically expired; review disk usage.

**Automatic rollback is not implemented.** A failed maintenance/upgrade reports
failure rather than claiming recovery. Do not delete the saved recovery point.
Use a matching trusted prior installer and the verified pre-upgrade archive
for an explicit offline recovery, preserving failed/new state separately.
Treat retained program copies as recovery material, not trusted executable code:
verify them against an independently retained installer/component manifest before
running anything from a recovery directory. Never execute code from ProgramData
with elevation solely because it was found beside a backup.

For manual restore, validate with the matching release, take a fresh backup,
stop all four instance services and confirm no instance FFmpeg processes remain.
Extract into a new private staging directory, not over live state. Review the
manifest, secret, approved-root IDs, program/data paths and LAN settings. Restore
`database/app.db` without old WAL/SHM files, `application-data` and artwork only
after moving current targets to an explicitly named recovery location. Review
native configuration before applying it; do not import another machine's paths,
service identity, secret or LAN exposure blindly. Repair with the matching
installer to reapply private ACLs/service/firewall definitions, then check health,
login, libraries, playback and another backup. Never edit the Alembic version to
bypass compatibility checks. See [shared backup format](backup-and-restore.md).

## Uninstall

Default uninstall stops/removes only this instance's services, binaries,
shortcuts and installer-created firewall rules. **ProgramData is preserved**,
including database, users, settings, history, artwork and backups. Source media
is always left alone. Reinstalling the same identity can reuse preserved data.

Deletion is a separate explicit uninstall choice. For scripted disposable testing
only, `/PURGEDATA=BlueReel-Development` opts into deleting that exact default data
directory after identity/marker/path checks. It is not a wildcard or general
cleanup command. Take a backup before any deliberate removal of real data.

## Build from source

Build tools are developer prerequisites only. Use x64 Windows with Python 3.12+
and Node/pnpm, then install the backend development dependencies into a virtual
environment. `components.lock.json` pins every downloaded tool/archive by SHA-256;
`requirements.lock` pins CPython 3.13 Windows runtime wheels, and the existing
frontend lock pins npm input. No unpinned latest downloads occur at installation.

Build the exact network-disabled FFmpeg from `ffmpeg.Dockerfile` when refreshing
the pinned media binaries; this cross-build may use Docker on the **build host**,
never on the installed target. The build records source archives, compiler package
versions, configure flags and build controls beside the result. Alternatively use
the already verified output directory from that build, not arbitrary FFmpeg.

```powershell
.\packaging\windows\build.ps1 `
  -Python .\.venv\Scripts\python.exe `
  -Pnpm pnpm `
  -FfmpegDir .\artifacts\native-dev\ffmpeg `
  -StageDir .\artifacts\native-dev\package-new `
  -Iscc 'C:\Path\To\Inno Setup\ISCC.exe'
```

The stage directory must be new. The builder verifies pins, builds the production
frontend, stages the embedded runtime/application, smoke-tests the isolated
payload, copies notices/corresponding source, inventories all packaged files and
compiles Inno Setup. `-Offline` requires the complete verified download cache.
`-SkipFrontendBuild` is for an already completed **native** production build,
not a development server. `-SkipInstaller` stages only, not a deliverable EXE.

Output under ignored `artifacts/native-dev` includes the installer, SHA-256,
artifact metadata, `included-components.json`, per-file inventory and notices.
Never commit binaries, dependencies, configuration, media, databases or logs.
Bump `version` and `windows_file_version` together in the component lock for the
next development build. Compiler macros carry those versions into the EXE.

Automated packaging tests live in `packaging/windows/tests`. The opt-in
`acceptance_api.py` and elevated `test_instance.ps1` exercise only the fixed
disposable development identity; they require explicit test-instance flags and
produce redacted evidence under ignored artifacts. Do not aim them at household
data. The [validation report](native-windows-validation.md) distinguishes unit,
API, browser and actual installer/SCM/firewall evidence.

## Components and licenses

Core pins for this build are CPython 3.13.15 (SQLite 3.50.4; OpenSSL 3.0.21),
Node 24.19.0, FastAPI 0.141.1, SQLAlchemy 2.0.52, Alembic 1.19.1, React 19.2.6,
Vinext 1.0.0-beta.5, FFmpeg/FFprobe 8.1.2, Caddy 2.11.4 and WinSW 2.12.0
NET461. Inno Setup 7.1.0 is the build-time compiler. The generated component
manifest, runtime wheel/lock records, upstream CPython SPDX SBOM, Go modules and
npm notices provide the complete transitive inventory and file hashes.

FFmpeg is GPL-3.0-or-later with x264; it is **not** an LGPL-only build. The package
contains the exact corresponding FFmpeg/x264 source archives, build controls and
license notices, plus AMF/NVENC/VPL header/runtime-source notices and MinGW/GCC
runtime material. Caddy's vendored source and module notices and MPL-covered
frontend source are included. See [FFmpeg legal guidance](https://ffmpeg.org/legal.html)
and [Inno Setup licensing](https://jrsoftware.org/isinfo.php).

Four MIT-declared npm releases omit a standalone full upstream license text in
their published packages: `css-box-shadow@1.0.0-3`, `unpic@4.2.2`,
`@unpic/core@1.0.3`, and `react-remove-scroll-bar@2.3.8`. Their authentic declarations
and complete hash-pinned published source are included; no copyright notice is
invented. The manifest flags this provenance gap. Review it before public release.

WinSW 2.12.0 embeds log4net 2.0.12 with advisory GHSA-4f7c-pmjv-c25w. This service
configuration uses PatternLayout, not the affected XML layout; that observation
is not a claim that an old dependency is risk-free. Modernizing the wrapper and
resolving upstream notice gaps remain release-hardening tasks. This development
inventory is not a legal certification or permission to omit redistribution
obligations. There is no code-signing certificate or public GitHub Release.
