# Native Windows development installer validation

Installation-testing phase, September 1–2, 2026. This is an unsigned development
build, not a public GitHub Release or a production-readiness certification.

**Paused at the user's request on September 1, 2026.** This report is a work-in-progress
checkpoint, not a claim that all requested acceptance work is complete. See the
[resume checkpoint](native-windows-checkpoint.md) for current machine state and
the remaining clean GUI installation / final Docker and GitHub checks.

## Baseline and isolation

The initial clean `main`, fetched `origin/main`, and GitHub default branch all
matched `395c1dcf145abf78c94a90599fbaa7a603b595f4` before changes. Git history was
preserved. Only the development product identity was installed: separate Program
Files, ProgramData, database, secrets, services, temporary data, backups, rules
and port 18080. The original Docker deployment retained port 8080 and its own
configuration/media mappings. Native tests used generated noncopyrighted fixtures
under a newly created disposable test-media directory, never household media.

Host: x64 Windows 11 Pro, build 26200. GPU inventory included an AMD Radeon RX
7900 XTX and AMD integrated graphics. Windows Sandbox was not already installed;
no major Windows feature was enabled. No reboot was performed. The development
workstation contains build tools, so independence is demonstrated by the isolated
installed executables, process/configuration checks, and running with Docker
Desktop and WSL processes stopped—not by claiming a second clean-machine pass.

## Test methods

Evidence is separated deliberately:

- Unit/integration tests exercise shared backend, paths, privacy, migrations,
  transcoding policy, backup format, installer definitions and service adapters.
- Native API acceptance uses the actual installed services and exact bundled
  FFmpeg, verifies real HLS segments with FFprobe, and records redacted JSON.
- In-app browser checks use the actual website, player and Owner administration.
  A playing label alone is not accepted as decoding proof: the player reports
  its decoded-frame callback and the video element exposes valid dimensions and
  advancing time.
- Elevated acceptance reads actual SCM identities/restart actions, Windows ACLs,
  effective firewall rules, installed backup tools and actual Inno executables.
  Test helpers are fixed to the disposable development identity and opt-in only.
- Docker regression uses an independently named project, unique image tags,
  loopback port 28080, fresh secrets/state and copied synthetic media.

Ignored local evidence lives under `artifacts/native-dev`; raw logs, private
configuration, test passwords, databases and backups are not committed. The
tracked report contains outcomes and reproducible source/test locations only.

## Initial prototype failures and corrections

The first `0.1.0-dev.1` EXE clean-installed, created four LocalService services,
opened a healthy loopback site and passed the native API playback matrix. Its
actual lifecycle tests exposed two defects not found by the initial smoke test:

1. The pinned embedded CPython normalized terminal `../..` in its `_pth` file to
   the wrong directory. Backend imports worked, but shared backup modules did
   not. The build now uses explicit `../../.` and smoke-imports both backup and
   restore validation from an isolated, relocated embedded runtime.
2. WinSW 2.12 concatenates common arguments to start/stop arguments. Repeating the
   full Python module command broke its stop CLI and left the wrapper waiting.
   XML now splits common role/data arguments from `-I -B -m` start/stop commands,
   with an exact-concatenation regression test. PowerShell requests stop without
   an unbounded implicit wait, then applies an explicit deadline.

The dev.1 path file received an explicit checksum-gated prototype repair, with
its original and ACL retained privately. Backup then passed. Cooperative stop
signals ended the prototype's application processes; the original failed restart
evidence is retained and is not counted as a successful unassisted restart.
Two wrapper processes remained orphaned in SCM after their application children
had exited. An explicit prototype-only recovery verified their exact identities,
corrected XML, stop markers and absence of children before terminating only those
two wrappers. No database or media process was forcibly terminated. The next
ordinary four-service restart completed in nine seconds, healthy, with configuration,
secret and persistent database counts unchanged.
Prototype-specific repair tools are not automatic rollback support. Final-package
clean-install/lifecycle results are recorded separately below.

Additional review hardened elevated maintenance against service-writable metadata
redirecting program/database paths; native configuration is service-readable, not
service-writable. Private ACL repair rejects hardlinks/reparse points before
changes and uses verified file handles. It also closed reverse-DNS and CPython
socket hostname-resolution gaps before resolver entry. Backup now includes the
native installation/network configuration in the existing v1 archive format.

One initial acceptance failure was a **test assumption**, not an application
failure: resume progress requires two seconds of actual watch credit before a
seek. The corrected harness verifies that behavior and exact eight-second resume.

## Playback and Owner workflow evidence

The initial browser Owner wizard completed all four steps, selected an approved
Windows subfolder, created a library and scanned eight initial files with zero
errors. The native browser showed Windows approved roots, not `/media` paths.
Movies and television catalogs were also populated by the API acceptance scan.

Native API acceptance completed all nine groups:

| Group | Verified outcome |
| --- | --- |
| Native identity | Separate Windows deployment and exact installed FFmpeg hash |
| Scan/catalog | Generated movies/TV/private libraries, successful worker/FFprobe analysis |
| Direct playback | HEAD, ordinary/suffix ranges, 416 for invalid multi-range, 410 after stop |
| Local conversions | MKV stream-copy remux; CPU MPEG-4→H.264 video; CPU AC3→AAC audio; real probed segments |
| Five modes | Direct-first behavior; verified AMF; explicit CPU fallback; Required fails without CPU; Direct/Remux Only rejects conversion |
| Track/seek | Second audio track, embedded text→WebVTT, precise HLS seek |
| Progress/permissions | Exact resume after logout/login, separate Viewer progress, private/cross-user denial, next episode |
| Source loss | Fails closed while an owned test file is unavailable; restores identical source bytes |
| Cleanup | Zero active streams/conversions/temporary bytes, original settings restored, test Viewer disabled |

The complete nine-group API matrix was rerun against the installed final dev.2
payload after its upgrade/restart and passed again (CLI exit 0). The final run
independently checked the installed version and exact FFmpeg hash, produced real
AMF output in Automatic/Required modes, and ended with zero active streams,
conversions and temporary bytes. The original Automatic policy was restored and
the disposable test Viewer disabled. Evidence: `acceptance-api-174819af136e/evidence.json`.

The browser separately confirmed Direct Play with valid video dimensions and
advancing playback; MKV remux decoded locally at 640×360. Seeking the MKV created
a precise hardware representation in Automatic mode and again decoded locally.
Active Streams showed `Hardware Transcode`, `h264_amf`, Automatic, and no fallback.
After selecting Software Only, MPEG-4 video decoded locally at 320×240 and Active
Streams showed `Software Transcode`, `libx264`, Software Only, and no fallback.
Owner Stop Stream reported process/temp cleanup and the player displayed the
expected ended-session message.

The browser also decoded the AC3 audio-conversion fixture at 640×360 under
Software Only. This is stream/decoder evidence, not a claim of hearing physical
audio output. Selecting the second Spanish AAC track and embedded SubRip subtitle
produced a decoded 320×240 remux and a loaded local subtitle track (ready state 2).
The episode-one details link opened episode two, which Direct Played with decoded
frames and advancing time. Playback settings were restored to Automatic and Active
Streams returned to zero before installer lifecycle testing.

AMF short test encodes passed with the exact installed FFmpeg under the actual
LocalService identity. QSV and NVENC failed honestly on this hardware; neither was
presented as available. Automatic and Hardware Required produced real AMF HLS.
Preferred with unavailable QSV used a reported CPU fallback; Required with QSV
failed without CPU fallback. An already-active stream retained its policy when
the Owner changed settings. Interactive numeric AMD device 0/1 test encodes also
passed; that is distinct from the service-account automatic-device test.

Hardware acceleration is H.264 **encoding** only. Decode/audio/subtitles remain
CPU work; there is no claim of hardware decoding, 4K load qualification or
cross-vendor driver certification.

## OS security and lifecycle evidence

Actual inventory verified four delayed-automatic LocalService services with
service SIDs, two restart attempts (10/30 seconds), then no action, with one-day
reset and no reboot/command recovery action. Protected private ACLs had no
unexpected principals. Loopback remained available.

The effective-firewall test found five exact per-binary outbound rules and three
enabled profiles. An **unguarded installed Python** direct-IP socket received
Windows error **10013 (access denied)**, not a timeout presented as proof.
Python/Node guarded name lookup failed before DNS, and guarded Node numeric and
named localhost HTTP succeeded. No global policy change or unrelated rule was
required. The Privacy page displayed actual enforcement and a check timestamp.

A separate elevated read-only review of 12 native operational logs found zero
matches for the actual application secret, disposable Owner password or generated
media paths/filenames/title markers derived from all 43 synthetic files. Wrapper
startup logs do contain installation Program/Data paths; this is not a claim that
every operational log is path-free. Raw log/credential values were not exported.

The first successfully repaired installed backup passed checksum/schema/integrity
validation and a non-writing restore dry run.

The actual `0.1.0-dev.2` EXE upgraded the repaired dev.1 installation successfully
in 78.5 seconds (exit 0), while Docker Desktop and WSL processes remained absent.
The installed component manifest matched the rebuilt version/source. All four
services returned Running. Before/after snapshots preserved the configuration,
secret, metadata, approved root and all selected persistent table counts: seven
libraries, 26 media files, 28 media items, three users and six progress records.

The upgraded Inventory inspected 6,517 private data entries with no unexpected
principals/owners. All four configuration entries had the protected administrator
ownership and exact service-read-only ACLs. Five effective outbound rules passed
again, including real OS 10013 direct-IP denial and pre-DNS name blocking, with
both numeric and named localhost available. The installed dev.2 backup produced a
51,650-byte archive and passed the shared validator without applying a restore.
An ordinary dev.2 restart passed without prototype repair helpers, with no orphaned
children, all services healthy and persistent counts/configuration preserved.

The opt-in private-LAN lifecycle passed using the host's assigned Private-profile
IPv4 interface: one exact Caddy rule, TCP 18080, selected local address, Private
profile and LocalSubnet only. Both loopback and that LAN address served health;
API/frontend internal ports remained loopback. Disable removed the LAN rule and
restored the original configuration bytes and localhost-only listener. Global
profiles and all unrelated effective firewall rules/filters had unchanged hashes.
This is a same-host LAN-address test, not a claim of a second LAN-device test.

The browser then played the final installed dev.2 MPEG-4 fixture via real AMF HLS:
decoded 320×240 frames, ready state 4 and advancing time. Active Streams agreed on
Hardware Transcode, `h264_amf`, Automatic and no fallback.

All 4,998 installed payload files matched the final inventory exactly. The
upgrade also retained 48 content-hashed frontend files matching the old prototype
and three administrator-owned Python cache files containing exactly its old
backup-module code. Cache creation times coincide with the prototype pre-upgrade
backup step; the exact creating process was not captured. Dev.2 uses `-B` for its
Python maintenance and service commands. These are reported as
known extras, not silently counted as an exact no-extra installation. Their
uninstall behavior and clean-final-install audit are checked separately.

Running the same dev.2 EXE again performed the documented repair workflow:
validated backup, clean stop, program replacement, migration check and healthy
restart. It exited 0 in 82.5 seconds; configuration, secret, metadata and selected
database counts were unchanged.

The real default uninstaller exited 0 and removed all services, rules, running
processes, final payload files and old frontend assets. Its evidence helper first
hit a sharing race: the temporary Inno process continued writing its protected
log about half a second after the launcher returned. The helper now retries only
bounded sharing/lock/not-empty conditions on its exact private log and checks
application results independently. The original failed helper report is retained.
A separate read-only follow-up proved that all **10,087 persistent files**,
including configuration, database, history, artwork, backups and recovery copies,
had identical before/after hashes. Volatile logs/state/temp are excluded from
that data-preservation digest.

The EXE correctly rejected a preserved-data reinstall while the three old cache
files remained (exit 7, preflight failure, no payload/service replacement). After
exact path/size/SHA and absent-service/registration checks, only those three
prototype bytecode files were moved to recoverable ignored artifacts under
`prototype-dev1/retained-bytecode`; their now-empty program directories were
removed without recursion. No ProgramData or source media was modified. This is
explicit prototype cleanup, not an automatic rollback feature.

After prototype-only cleanup, the actual dev.2 EXE preserved-data reinstall
passed (exit 0, 68.1 seconds). Its log confirmed backup before Install/migration;
all four services became healthy. Configuration, secret, metadata and selected
database counts remained identical (10 libraries, 35 media files, 38 media items,
four users, seven progress records). The browser retained the Owner session and
movie/TV catalog without a new setup prompt.

Explicit disposable-data purge was already running when the user requested a
pause. It was allowed to finish safely: real uninstaller exit 0, zero services,
rules or processes, and both native Program Files and ProgramData directories
removed. The corrected private-log cleanup passed after four bounded attempts.
Only disposable native test data was permanently removed; all 43 generated source
files remained byte-identical. Installer, build inputs and ignored evidence were
retained. The final clean dev.2 GUI install has **not yet run**.

## Independent Docker regression

Passed production builds, all four healthy services, 28 same-origin API checks,
scanning, Direct Play/ranges, MKV remux, CPU video/audio conversion, all five
settings modes and truthful unavailable hardware. Backend/worker source writes
failed EROFS and all fixture hashes were unchanged. Each service was tested for
name/direct-IP egress denial; the proxy had UID 1000, no capabilities and
NoNewPrivs. Shared backup and dry validation passed at Alembic
`2b0100000001` (33 tables). Privacy-reviewed logs contained no fixture credential,
secret, source-name/path or host fixture-path matches.

The isolated Linux test run passed **261 tests, seven platform-specific skips**.
Its four containers and two networks were removed. Before the deliberate
Docker-Desktop-stop test, original Docker container/image IDs and start times
were unchanged. Original deployment restoration is checked at the end of native
testing; it is not rebuilt or reconfigured using this task's changes.

On the user's pause request, Docker Desktop was restored as cleanup. All four
original containers were healthy, retained their exact container/image IDs, and
port 8080 returned ready. The original `.env` hash was unchanged. Start times
naturally changed due to the deliberate Desktop stop/start. No new isolated
Docker regression was started during pause cleanup; the latest shared-source
rerun remains a next-session item. Desktop restoration overlapped the final
seconds of the already-running native purge, not native playback/upgrade tests.

## Automated checks and artifact record

The latest frontend checks passed **58 tests**, TypeScript, lint and the native
production build. The large HLS client chunk warning is a build-size warning,
not a build failure. Frozen shared backend/operations checks passed **327 tests,
four platform-specific skips**, including real bundled FFmpeg/FFprobe and Caddy.
Branch-enabled aggregate coverage is **80.60%** (statement coverage **83.79%**,
branch coverage **68.67%**) over backend application and shared scripts, excluding
test files and packaging. Ruff and strict mypy passed (43 shared source files).
Packaging count, installer identity and source-control verification are recorded
below; executable lifecycle results follow completion of their acceptance runs.

Packaging passed **197 tests, zero skips**, including the pinned embedded Python,
real Node preload checks, actual temporary NTFS ACL tests and Inno compilation.
Packaging Ruff and strict mypy passed (three build/harness source files).

The `0.1.0-dev.2` installer was built offline from clean source commit
`7e0d77fdd42c756f723aa3aa9acb8c43228d5762`; its manifest records `source_revision_dirty=false`.

| Artifact fact | Value |
| --- | --- |
| Filename | `BlueReel-Setup-Development-x64.exe` |
| Product / Windows file version | `0.1.0-dev.2` / `0.1.0.2` |
| Size | 575,332,951 bytes |
| SHA-256 | `eb8941f924508ed892ca6eed9031affe61b405f6c6fe01a97b57fec0d9c0ed24` |
| Authenticode | NotSigned |
| Payload | 4,998 files; all hashes independently matched |
| Component manifest | 660 records; includes runtime, upstream and conservative build-input attribution |
| Downloads / Python runtime wheels | 29 upstream pins and 28 wheel pins independently verified |
| External runtime prerequisites | No separately installed runtime; supported Windows supplies UCRT and .NET Framework |

Local deliverables are under ignored `artifacts/native-dev`: EXE, adjacent
`.sha256`, `installer-artifact.json`, `package-dev2/included-components.json`,
`package-dev2.files.json`, `package-dev2/OPEN-SOURCE-NOTICES.txt`, and the packaged
`licenses` / `source` trees. No generated payload or binary is committed.

The PE audit checked all 52 executable/native-library files, including static and
delay imports and forwarded exports. No unresolved non-Windows DLL or private
search-path gap was found. Both required Visual C++ runtime DLLs are bundled
beside Python; isolated native-module imports confirmed the actual loaded paths.
Node/Caddy/FFmpeg/FFprobe import Windows inbox DLLs; managed WinSW uses inbox .NET.
This does not certify all optional dynamic hardware loads or replace a clean-VM pass.

## Limits of this evidence

Unsigned development artifact; no public release. No reboot, second Windows
machine/Sandbox, Windows 10 execution, long household-media load, other browser
families or other GPU vendors were exercised. Automatic rollback/destructive
restore is not implemented. Configuration/backup recovery is explicit and
offline. The stateless proxy is terminated at shutdown, not gracefully drained.
Network shares, image-subtitle burn-in, hardware decoding and external metadata
remain deferred. See [component provenance and development dependency risks](native-windows.md#components-and-licenses).
