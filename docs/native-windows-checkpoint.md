# Native Windows installer — resume checkpoint

Paused at the user's explicit request on September 1, 2026 (America/Denver).
Do not resume automatically. No background agents, installer, monitor or scheduled
task is intended to keep working. Read this file and the validation report before
continuing. This is a local savepoint, not the completed public release.

## Current machine state

- Original Docker Desktop installation is **restored and healthy** on port 8080.
  All four original `bluereel-*` containers retain their original IDs and image
  IDs. Their start times changed only because Desktop was deliberately stopped
  for native independence tests, then restarted during pause cleanup.
- Original repository `.env` is untouched. Its SHA-256 remains
  `0C593B26E4BFEB5A2AB07A4B08243BC9F0BACC51843B07E0336C0A41605EB6F7`.
- The disposable **BlueReel Development native instance is uninstalled/purged**.
  Zero `BlueReelDevelopment*` services, instance firewall rules or processes
  remain. Both `C:\Program Files\BlueReel Development` and
  `C:\ProgramData\BlueReel-Development` were removed by the real uninstaller.
  This purge was already running when the user asked to stop and was allowed to
  finish safely. The disposable accounts/database/backups in that data directory
  were permanently removed; this was not household data.
- All 43 generated source fixture files remain under
  `C:\ProgramData\BlueReel-Development-TestMedia`, unchanged. Their aggregate
  path/size/content fingerprint is
  `9ea5c66f597c3a735f7d868e090a197b1d59f1bb675a8f43a6a3e56c8b5c8668`.
  The original 13 fixtures also matched their initial individual hashes.
- Download caches, compiled payloads, installer and test evidence remain under
  ignored `artifacts/native-dev`. Do not commit them. Some evidence/credential
  files contain disposable secrets and protected raw logs; never publish them.

## Source and artifact savepoint

Repository: `C:\Users\dog10\Documents\ChatGPT\BlueReel`, branch `main`.

The initially clean local main, fetched origin/main and GitHub default branch
matched `395c1dcf145abf78c94a90599fbaa7a603b595f4` before work.

Completed source commits before this checkpoint:

- `a7e218404025f3b7634e0b9d2b510f19340c399a` — shared playback policies/native adapters.
- `7e0d77fdd42c756f723aa3aa9acb8c43228d5762` — Windows packaging, installer, tests/docs.
- The commit containing this checkpoint saves the later helper-only log-race fix,
  its tests and validation documentation. Find its ID with `git log -1`.

**Nothing has been pushed during this task.** Remote verification/push remains
pending. Preserve existing history and inspect/fetch again before pushing.

Ready unsigned installer:

`C:\Users\dog10\Documents\ChatGPT\BlueReel\artifacts\native-dev\BlueReel-Setup-Development-x64.exe`

| Field | Value |
| --- | --- |
| Product / numeric version | `0.1.0-dev.2` / `0.1.0.2` |
| SHA-256 | `eb8941f924508ed892ca6eed9031affe61b405f6c6fe01a97b57fec0d9c0ed24` |
| Size | 575,332,951 bytes |
| Signature | NotSigned |
| Build source | clean `7e0d77fdd42c756f723aa3aa9acb8c43228d5762` |
| Payload | `artifacts/native-dev/package-dev2` |
| Inventory | 4,998 files; 660 component records; all hashes verified |

Later checkpoint changes are acceptance-helper/docs only, not installed payload
changes. EXE/manifest/file inventories and notices are adjacent to the payload.
Prototype dev.1 EXE and recovered bytecode are retained under
`artifacts/native-dev/prototype-dev1` for evidence, not further installation.

## What is already verified

See [native-windows-validation.md](native-windows-validation.md) for details and
[native-windows.md](native-windows.md) for architecture/build/recovery guidance.

- One shared backend/frontend/schema/scanner/auth/playback/backup implementation;
  embedded Python, Node, FFmpeg/FFprobe, Caddy and WinSW NET461; four delayed-auto
  LocalService services with service SIDs and bounded recovery.
- Actual dev.1 clean install and browser Owner setup/approved-root scan.
- Actual final dev.2 EXE upgrade, same-version repair, ordinary restart,
  backup/dry validation, private configuration read-only ACLs and firewall checks.
- Actual private-LAN enable/disable on the assigned Private-profile interface:
  narrow Caddy rule, loopback/internal listeners, then exact configuration and
  unrelated-rule/profile hashes restored. No second LAN-device test.
- Final installed dev.2 nine-group API acceptance: all five policies, real AMF,
  CPU fallback, Required failure, direct/ranges/remux/video/audio conversions,
  tracks/WebVTT/seek, logout/login resume, Viewer isolation, next episode,
  source loss/restoration, zero leftover streams/conversions/temp bytes.
- Real browser-decoded direct/remux/software/hardware output, correct Active
  Streams, selected audio/text subtitles and next episode; final dev.2 AMF
  browser smoke also passed.
- Exact bundled FFmpeg AMF tests pass on RX 7900 XTX/AMD graphics under the actual
  service identity. QSV/NVENC are unavailable and reported honestly. Encoding
  only; no hardware-decode or cross-vendor certification claim.
- Real unguarded installed-Python outbound connection failed with Windows 10013;
  Python/Node pre-DNS guards and localhost succeeded. Native log audit: zero
  secret/password/generated-media marker matches in 12 logs. Wrapper startup
  logs contain expected installation paths and are not claimed path-free.
- Installed payload hashes and PE closure passed. Both VC runtime DLLs load from
  the bundle. No additional VC++ install was needed. Longest installed file path
  is 184 characters. No clean-VM/Windows 10 execution claim.
- Actual default uninstall retained 10,087 persistent files byte-for-byte and
  removed all services/rules/processes. Actual preserved-data reinstall passed,
  retaining Owner/session/catalog/configuration/secret/counts. Explicit purge
  then passed, removing both native program/data directories.
- Backend/shared operations: **327 passed, four platform skips**, branch-enabled
  aggregate coverage **80.60%** (statements 83.79%, branches 68.67%); Ruff and
  strict mypy (43 files) passed.
- Frontend: **58 tests**, TypeScript, lint and native production build passed.
- Latest packaging: **197 passed, zero skips**, Ruff and strict mypy passed.
- Earlier isolated Docker regression: production builds/health, 28 API checks,
  readonly media/egress/backup/modes, **261 Linux tests passed, seven skips**.
  The project was removed; original deployment was not recreated/reconfigured.

## Actual issues and recovery — do not hide these

1. Dev.1 embedded Python `_pth` resolved terminal `../..` incorrectly. Dev.2
   uses `../../.` and tests isolated backup/restore imports.
2. Dev.1 WinSW concatenated duplicated common/start/stop module arguments, causing
   a stuck stop. Fixed XML splits arguments correctly. Prototype-specific
   checksum-gated repairs and two verified orphan-wrapper terminations were
   explicit recovery; ordinary dev.2 restart passed without them.
3. Upgrade retained 48 old fingerprinted web files and three old backup-module
   `.pyc` files. Real uninstall removed all web files; preserved-reinstall
   preflight correctly refused the remaining three caches (EXE exit 7, no
   replacement). Their content/hashes matched prototype source compilation.
   Root moved only those three files to recoverable ignored
   `prototype-dev1/retained-bytecode`, then removed only empty directories without
   recursion. Reinstall then passed. Dev.2 maintenance/service Python uses `-B`.
4. The first uninstall evidence helper raced Inno's late log close by ~0.5s.
   Actual uninstaller exited 0; a separate read-only follow-up proved preservation.
   Helper now retries only bounded sharing/lock/not-empty errors on its exact
   private log. The final purge exercised this successfully (four attempts).
   The first protected raw log is retained as evidence, not publicly exposed.

No automatic rollback is implemented or claimed. Four upstream MIT-declared npm
packages lack a full standalone upstream notice; authentic metadata/source are
included and the manifest flags the gap. WinSW's old log4net dependency risk is
documented. Do not present this unsigned development artifact as production ready.

## Next session — do not redo completed work

1. Reconfirm this machine/Git/artifact state. Read the full original task at
   `C:\Users\dog10\.codex\attachments\9fc0cd81-5cf2-42b4-9955-78f385a2e82f\pasted-text.txt`
   if needed. Do not touch the existing Docker deployment's data/configuration.
2. **Final dev.2 clean GUI install is still pending.** For that independence test,
   stop Docker Desktop deliberately again, saving its current baseline. Use the
   existing guarded `packaging/windows/test_installer_ui.ps1`, via hidden RunAs,
   with the exact EXE/hash, `-AllowDisposableInstallerUI -ConfirmOwnedTestMedia`,
   media root above, and an existing ignored acceptance report directory. The
   helper has not yet driven any installer UI. Use a reasonable installation
   deadline (e.g. 600 seconds), then verify the Finish/browser action and fresh
   Owner setup using the Browser skill. Native UI helper never controls browsers.
   Any helper UI-pattern mismatch must be diagnosed from scoped redacted controls,
   not guessed or silently marked passed.
3. Verify the clean final install, protected ACLs, ordinary restart/backup and
   absence of prototype extras; finish clean-instance lifecycle cleanup as needed.
   Fresh Owner credentials may be created for this disposable instance; do not
   reuse any Docker secret/database. The old acceptance credential file is ignored
   and refers to the now-purged disposable Owner.
4. Restore Docker Desktop/original containers after native-only tests. Rerun the
   final isolated Docker regression against latest shared sources; the earlier
   pass predates the last native/DNS/backup hardening. Existing ignored harnesses
   are under `artifacts/native-dev/docker-regression`. Use fresh state/secrets,
   a unique image tag and port 28080, not original production data. Capture a new
   original-container baseline after Desktop restart rather than comparing old
   pre-stop start times. Backend agent prepared this, but did not run it.
5. Finish remaining actual-executable/browser evidence and report. Rebuild/version
   bump only if payload/installer source changes; helper/docs changes alone do not
   alter this dev.2 EXE. Re-run affected tests if anything changes.
6. Commit completed work, push `main` as originally requested, fetch again and
   verify local/origin/default-branch equality, clean tree, published-tree audit
   and Actions/commit checks. **No public GitHub Release.** Deliver the concise
   final artifact/test report, separating unsupported/unrun cases honestly.

Do not enable Sandbox/major Windows features or reboot just for testing. Neither
was available/practical during this session. No scheduled follow-up was created.

## Useful evidence / commands

Evidence is ignored under `artifacts/native-dev`:

- `final-tests/{backend-operations-summary,packaging-summary}.md`
- `final-tests/dev2-{payload,installer}-verification.json`
- `acceptance-api-174819af136e/evidence.json` (final dev.2 API matrix)
- `acceptance/private-lan-44096a4b04a94a2cabeb1ebb2d9fa6ca.json`
- `acceptance/VerifyUninstallPreserve-20260902T030607-6ad7c7d21e434b7293c5afe035999864.json`
- `acceptance/UninstallPurge-20260902T031226-1b039b37b3a4412981210d0430373df7.json`
- `acceptance/preserved-reinstall-dev2-inno.log` (private operational log)
- `pe-closure-audit-dev2.json`, `dev2-installed-payload.json`
- `final-tests/native-log-privacy-audit.json`

Repository Python: `.venv\Scripts\python.exe`. Bundled build Python:
`C:\Users\dog10\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
Bundled pnpm: same runtime root, `dependencies\bin\fallback\pnpm.cmd`; Node:
`dependencies\node\bin\node.exe`. Inno compiler:
`artifacts/native-dev/inno/ISCC.exe`. FFmpeg build:
`artifacts/native-dev/ffmpeg`. Use fresh payload staging directories; retain dev.2.
