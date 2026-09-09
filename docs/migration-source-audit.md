# Migration source audit — 2026-09-09

The working Windows Agent contains no unpublished first-party source hotfix. Every installed source change since its original installer is represented in GitHub main at `ab49dc6728614ac04f8dfb143c3c69d85031288c`. The complete installation is a mixed source baseline: two executable modules still use their original installer versions and do not yet contain an already committed pairing-completion improvement. The new installer should retain that reviewed improvement; reverting it would discard working fixes merely to make an equality statement true.

This audit made no production changes, restarted no process, generated no identity, and did not read the TMDB token file contents. The existing machine remains the fallback. The runtime and data checks below describe the source audit snapshot, not acceptance of the future installer or a new machine.

## Source and package provenance

| Record | Value |
|---|---|
| Installed product version | `0.1.0-development.5` |
| Original package source | `8be913327166ffc90f005ba987c7a5bcedc3739a` |
| Original package build time | `2026-09-06T18:14:08.406412Z` |
| Original package Windows file version | `0.1.0.5` |
| Original package source status | Clean; unsigned development package |
| Latest installed metadata/source record | `ab49dc6728614ac04f8dfb143c3c69d85031288c` |
| Local HEAD, cached `origin/main`, live GitHub main at audit | `ab49dc6728614ac04f8dfb143c3c69d85031288c` |
| Branch and worktree at both ends of source snapshot | `main`; clean |
| Database/Alembic revision | `2d0100000001` |

The embedded `included-components.json` records the original package, not all subsequent source overlays. `state/stabilization-hotfix.json` records `f00f5c3e9d4462a8ea9122ac64073cc7ea2b7227`; `state/home-hotfix.json` records `330ae3ba2227c72d93831ea772369ff930055973`; `state/metadata-deployment.json` records the latest 23-file overlay at `ab49dc6728614ac04f8dfb143c3c69d85031288c`. All 23 current hashes match that latest record. Older hashes for files subsequently changed are historical evidence, not current mismatch defects. The Home `agent-installed.json` is byte-identical to `state/home-hotfix.json`; the Home `final-audit.json` records that earlier four-file deployment and its then-successful source check.

## Complete source comparison

The audit mapped **110 installed first-party source/configuration copies** to their repository locations, including all installed backend modules, Alembic sources, maintenance scripts, direct source copies, and the remapped `support`, `source/packaging`, and `source/ffmpeg` build controls. No required backend/migration source was missing, and no first-party source in those installed directories lacked a current Git mapping.

- 101 of 110 installed copies matched HEAD and `origin/main` byte-for-byte.
- 104 of 110 matched after **only CRLF-to-LF normalization**. Normalized agreement is reported separately from byte agreement.
- The local working checkout matched 103 byte-for-byte and 104 after newline normalization; Git checkout newline conversion accounts for the different raw count.
- Three copies differed from Git solely in newlines: `source/packaging/components.lock.json`, `source/packaging/development-notice.txt`, and `support/development-notice.txt`.
- Of 79 executable backend/migration/maintenance source files, 77 matched current main; the remaining two are documented below.

The six substantive comparisons consist of four stale source copies and two intentional build transformations:

| Installed file | Finding |
|---|---|
| `backend/app/native_tray.py` | Exact original `8be9133` source. Current main additionally publishes the existing `agent_id` in local tray status. |
| `backend/app/remote/local_auth.py` | Exact original `8be9133` source. Current main additionally supports an explicit browser-bound pairing retry and its callback-page link. |
| `source/packaging/BlueAshReelAgent.cs` | Archived original tray source. Current main adds the healthy `--finish-install` handoff. |
| `source/packaging/installer.iss` | Archived original installer source, differing from the original Git blob only in newlines. Current main invokes the native completion handoff after health succeeds. |
| `config/product.json` | The builder replaces repository version `0.1.0` with package version `0.1.0-development.5`; all other JSON fields match. |
| `frontend/package.json` | Generated standalone runtime package metadata; it matches the shipped base payload. It is not the frontend development package manifest. |

The source audit therefore does **not** claim that all old installed bytes equal one Git revision. It establishes that there is no missing installed hotfix to copy back to GitHub, precisely identifies the old copies, and preserves the existing installation while building the next version from main.

### Retain the committed pairing-completion fixes

All four stale source copies are explained by commit `42151356fdc1ea7eda17177c824c65ea38406de7`, “Launch healthy installed Agent into pairing and provide safe retry.” It is already an ancestor of `ab49dc6`.

`native_tray.py` adds only `agent_id` to the local status projection. The tray uses that value to open the exact already-paired Agent page after a successful installer run. It does not change key material, pairing, relay behavior, media paths, or playback.

`local_auth.py` keeps ordinary requests unchanged unless `retry=true` is explicitly requested. A retry removes only the pending request identified by the current browser cookie when its purpose and callback match. The previous callback becomes unusable; another browser's pending request remains intact. Existing loopback, cookie, nonce, PKCE, expiration, one-use callback, already-paired, revocation, and local fingerprint approval checks remain in force. The callback page exposes a same-origin retry link without placing credentials in it.

The companion tray change waits for the owned supervisor's fresh healthy status before opening either the unpaired loopback pairing route or a validated paired Agent UUID on the canonical Portal. The installer dispatches this as the original user after its health check. Retaining these changes is safer than reverting them to match the old installation.

Focused validation during this audit passed:

```powershell
cd backend
& '..\.venv\Scripts\python.exe' -m pytest tests/test_pairing_workflow.py tests/test_native_user.py -q
# 83 passed
cd ..
& '.\.venv\Scripts\python.exe' -m pytest packaging/windows/tests/test_installer_pairing_launch.py -q
# 2 passed, including compilation and execution of the actual C# launch selector
```

These are local tests with fixtures, not a re-pair of production. The final version/build commit remains a later release artifact; `ab49dc6` is the audited pre-versioning source baseline.

## Exact in-place changes since the original installer

The original 3,729-file payload inventory was checked in full: **3,713 original files remain byte-identical**, and **16 original executable source files changed**. All 16 current versions match GitHub main:

```text
backend/app/api/catalog.py
backend/app/api/playback.py
backend/app/config.py
backend/app/models.py
backend/app/native_install.py
backend/app/native_runtime.py
backend/app/remote/media.py
backend/app/remote/storage.py
backend/app/services/catalog.py
backend/app/services/compatibility.py
backend/app/services/hardware.py
backend/app/services/outbound.py
backend/app/services/scanner.py
backend/app/services/transcoding.py
backend/app/worker.py
scripts/backup_format.py
```

Thirteen first-party files were added after the original package: `backend/alembic/versions/2d01_metadata.py`, `backend/app/home_schemas.py`, `backend/app/services/home.py`, `docs/metadata.md`, and the nine files in `backend/app/metadata/` (`__init__.py`, `artwork.py`, `cli.py`, `matching.py`, `provider.py`, `service.py`, `tmdb.py`, `types.py`, `view.py`). Every added file matches main. No generated frontend or third-party payload file from the original manifest changed. The old generated frontend backup directory remains historical local material, not a source input for reconciliation.

## Bundled runtime identity

Every executable below is byte-identical to the original payload inventory. Python and Node have valid publisher Authenticode signatures. The other listed development binaries are unsigned; their provenance is the pinned component/build records and verified payload hashes.

| Component | Executed version / file version | SHA-256 |
|---|---|---|
| Python | `3.13.15` | `85b71d8c6ec1905935f74be0c9869aae198d00e98f39df699ec66f9c5a84cecd` |
| FFmpeg | `8.1.2-BlueReel-Development` | `a9129a52015e1b31a3aac5887d41eade082af3e0124683e135ac87e57d40259d` |
| FFprobe | `8.1.2-BlueReel-Development` | `f6c2a2aa19864c973880353d98e4366da58b754bf1a03715dfe42ca323035b0f` |
| Caddy | `2.11.4` | `5cb9ab71e5756ce72840b8234177a2f40c8b4ab47a806b8e841e2b784e9df62b` |
| Node.js | `24.19.0` | `3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237` |
| Native tray | Original development.5 binary; PE version `0.0.0.0` | `90b0422b020cbd1bc77443cdb7fd8203944dd711210c44cc0046bb153fece92d` |
| Legacy WinSW | `2.12.0.0` | `b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f` |

The old tray's zero PE version is a version-traceability gap for the new installer to address; it is not evidence of an unknown tray binary. Caddy, Node, and WinSW remain bundled, while the observed per-user process tree consists of the tray, Python supervisor, API, and worker. No BlueReel/BlueAshReel Windows service or scheduled task was registered.

All **29 installed Python distributions** match every name/version in `packaging/windows/requirements.lock`, with none missing or additional: alembic 1.19.1; annotated-doc 0.0.5; annotated-types 0.8.0; anyio 4.14.2; argon2-cffi 25.1.0; argon2-cffi-bindings 26.1.0; cffi 2.1.1; click 8.5.0; cryptography 46.0.7; fastapi 0.141.1; greenlet 3.5.5; h11 0.16.0; httptools 0.8.0; idna 3.19; Mako 1.4.1; MarkupSafe 3.0.3; pycparser 3.0; pydantic 2.13.5; pydantic-settings 2.15.0; pydantic_core 2.46.5; python-dotenv 1.2.3; PyYAML 6.0.3; SQLAlchemy 2.0.52; starlette 1.6.0; typing-inspection 0.4.4; typing_extensions 4.16.0; uvicorn 0.52.4; watchfiles 1.2.0; websockets 15.0.1.

## Working instance locations and state

The observed instance is `development`, runtime mode `per_user`, prefix `BlueReelDevelopment`, loopback port `18080`. Sign-in startup is enabled through `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `BlueReelDevelopmentTray`, which launches the installed tray with the explicit data directory. No service migration or startup setting change was made.

| Purpose | Existing location convention |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\BlueAshReel-Development` |
| Persistent instance root | `%LOCALAPPDATA%\BlueAshReel-Development` |
| SQLite | `<instance>\database\app.db` |
| Artwork | `<instance>\artwork` |
| App data, logs, temp, backups | `<instance>\data`, `logs`, `temp`, `backups` |
| Explicit native configuration | `<instance>\configuration\.env` |
| Installed layout | `<instance>\configuration\installation.json` |
| Private pairing identity | `<instance>\remote-identity\identity.json` |
| Remote control and supervisor state | `<instance>\remote-control`, `<instance>\state` |
| TMDB private token file | `<instance>\configuration\tmdb-access-token.txt` |

`TMDB_TOKEN_FILE` points to that private token file, which exists and is nonempty. The token contents were not read by this audit or included in artifacts. Paths above describe this audited development instance, not hardcoded target-machine installation paths. Native layout/configuration is installation-specific and must not be copied blindly to another machine.

SQLite was opened read-only. Integrity and foreign-key checks passed. Counts were 2 libraries, 2 library paths, 12 media items, 11 media files, 2 watch-progress records, 13 metadata records, 110 artwork records, and 1 Portal grant. These are migration comparison evidence; preserving counts alone is insufficient to prove identical rows or a successful identity transfer.

## Evidence and handoff

The ignored local evidence under `artifacts/migration/` contains `installed-source-audit.json` (every source hash/comparison and historical manifest check), `installed-payload-hashes.json` (all 3,729 original payload entries), `startup-dependency-audit.json`, `installed-python-distributions.json`, and `python-lock-verification.json`. They contain selected non-secret provenance and aggregate state, not media paths or credential values.

The separately verified migration snapshot is named `migration-20260909T213644168588Z`; its recorded manifest SHA-256 is `9aaaf898630398abc723e26c39b483e7c23153885a33ebf03785f2d407a8c794`. Snapshot creation/restore validation and DPAPI migration constraints belong to the migration procedure. Nothing in this source audit authorizes copying protected identity blobs across machines or running two machines with one active identity.

Build the next development installer from the final committed main revision with the legitimate pairing-completion fixes retained, and record its new version and SHA independently. Preserve the original source/payload manifests and snapshot as the old-machine fallback. Do not claim literal old/new byte equality, successful new-machine pairing, or cutover acceptance from this audit alone.
