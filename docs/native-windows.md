# Native Windows Agent (development.6)

The Windows Agent runs as the signed-in Windows user in a native system tray. Fresh installations use no Windows service, scheduled task, administrator runtime, Docker, Node server, or local username/password UI. The Portal at https://blueashreel.com is the user interface. Existing service-only documentation in earlier Git revisions describes development.1 through development.4.

## Installation and storage

The unsigned development installer defaults program files to `%LOCALAPPDATA%\Programs\BlueAshReel-Development` and mutable data to `%LOCALAPPDATA%\BlueAshReel-Development`. The wizard configures the application-data directory, loopback port, transcode process/thread limits, and login startup. Advanced pages separately configure the database, artwork/cache, temporary transcodes, backups and logs. Locations must be dedicated absolute directories, must not overlap one another or program files/configuration, and must not contain junctions or reparse points. Existing settings and secrets survive repair.

Installation creates the empty database schema only. There is no Owner creation, application login, first-library selector, media-root selection, scan, permission setup, or pairing wizard. After runtime validation, the tray starts and the default browser opens the Portal. Sign in, complete MFA, compare the local Agent fingerprint, and confirm pairing. Library selection and administration require the authenticated encrypted Owner session. The folder picker and final consent run on the Agent workstation; source media remains read-only.

Local management listens on `127.0.0.1:<port>` (development default 18080). The Agent also binds a separate random port on exact `127.0.0.1` for the Portal callback; authorization binds that exact callback, and fragments carry sensitive callback material without placing it in query strings. Root/status access requires the Portal authorization flow. The tray distinguishes Not paired, Waiting for Portal approval, Waiting for local confirmation, Paired but Agent offline, Connecting, Connected, Revoked, Pairing expired and Error. Connected requires a fresh authenticated relay connection. Its menu provides Open Portal, Open local management, Pair Agent, Connection status, Reconnect, Unpair this Agent, pause/resume, restart, logs, settings, login startup and confirmed exit. The local status and pairing consent show the same short fingerprint as the Portal, and both surfaces require explicit approval. Closing a status/settings window leaves the tray running.

## Runtime ownership and privacy

Installer completion uses `--finish-install` after native health succeeds. The
tray defers this request until its current supervisor reports fresh health;
API readiness follows initialization of the random loopback callback listener.
An unpaired instance opens the Agent's local pairing start route, which generates
the protected production authorization handoff. A paired instance opens its
existing opaque Agent page. Repeated completion signals open at most one browser
flow per tray lifetime; normal Windows login startup does not create a request.
Cancelled or expired callbacks offer Retry Pairing. Retry replaces only the
request bound to that browser's HttpOnly loopback cookie and retains the key.
Changing this compiled behavior requires a new installer version and hash.

If the Portal saved a pairing but its response was interrupted, choose Reconnect
and Pair Agent to recover it under its original Owner. If that Portal entry was
revoked before recovery, use the tray's **Discard incomplete pairing** action.
It appears only for an unpaired local identity outside an active approval. The
confirmation names its fingerprint and explains that Portal entries, their
Owners, media and application data stay intact. The supervisor stops its owned
processes, verifies that the same key still has no completed account association
or pending revocation, deletes only that incomplete identity, and restarts.
Choose Pair Agent afterward and explicitly approve the new fingerprint on both
screens. Old callbacks are invalidated; a pairing completed during shutdown is
preserved instead of discarded.

`BlueAshReelAgent.exe` uses Windows inbox .NET Framework WinForms for its notification icon, menus and native dialogs. It starts embedded `pythonw.exe` as the current Windows account. The supervisor owns API and worker process handles and Windows Job Object cleanup. The Portal connector shares API lifespan and its playback manager. No service account accesses media; mapped drives and network shares use the signed-in user's access.

HKCU `Software\Microsoft\Windows\CurrentVersion\Run` provides opt-in login startup. Runtime state and consent queues have a user/SYSTEM-only DACL on a new installation. Child processes have no visible console; FFmpeg/FFprobe probes also use hidden process flags. The bundled FFmpeg is compiled with networking disabled. API egress is constrained to loopback and the canonical Portal TLS endpoints. Generic worker egress remains disabled; the metadata worker permits only canonical TMDB API/image hosts through the scoped provider policy described in [metadata](metadata.md).

Stop and pause close device/browser connections, stop the API and worker, finish bounded process teardown, and clear temporary consent requests. Restart waits for prior owned processes to exit before starting replacements and validating readiness. Logs rotate locally with redacted structured messages. Readiness allows bounded Windows runtime verification time rather than incorrectly failing healthy cold starts.

## Safe service migration and rollback

The installer reads and validates retained instance identity before replacing files, and refuses foreign channel, marker, program/data mismatch, invalid port, linked storage, or an incomplete retained-program uninstall. A legacy migration keeps existing mutable paths and the previous protected Program Files tree. Privileged work is limited to validating legacy backup/service identities, stopping those exact services, and granting the installing user access to retained data.

The helper creates a validated full legacy backup, snapshots configuration and ACLs, and captures the drained SQLite database. A matching recovery ledger is required before adoption. The new per-user program is installed separately. Old service-bound DPAPI identity material is retained at its original location; it cannot safely be silently reused by another Windows identity, so migration requests fresh local pairing. Agent catalog, watch progress, libraries and stored secrets remain intact.

Only after the new tray and API are healthy does the helper uninstall the exact obsolete service registrations. Their original executables/XML remain available for rollback. Failure before validation restores original configuration, database and permissions, recreates any removed registrations, restores start modes and restarts previously running services. Files from the failed user database are preserved in the recovery directory. The helper does not change unrelated services or firewall policy. Old Python firewall rules point to the old Program Files executable; the new user executable uses a separate path.

Per-user upgrades also snapshot the actual configured database (including an advanced location), private configuration/approved-root state and previous program files before replacement. Rollback preserves the failed database, restores the snapshot, and restores previous program files after the new user runtime stops. Backup and restore reject mismatched identity/storage and links. Interactive uninstall offers a clear choice: retain local identity and application data (the default), permanently delete them including configured advanced storage, or cancel uninstall. Unattended uninstall always retains data. Deletion requires explicit confirmation and a stopped Agent, validates every storage location, and refuses linked trees, shared system/user directories or overlap with approved media folders. Source media files are never deleted. Retained data keeps its HKCU location record for reinstall; a successful explicit deletion removes only the matching development or stable location values. Unpair in the tray before uninstalling to revoke Portal access immediately.

Privileged legacy migration must be validated in an elevated Windows acceptance environment before applying it to an existing workstation. A normal nonadmin test validates fresh user-mode installation/runtime; it cannot prove privileged service/ACL migration by itself.

## Build and validation

Run the native backend and packaging test suites and compile the C# tray and Inno wizard before release. The final builder requires a reviewed clean source commit. Build-only dependencies are pinned; all runtime dependencies, licenses and FFmpeg source are bundled. The packaged product version is set to the installer version, so device status and signed update metadata comparisons agree.

Example from a clean checkout with the existing Windows dependency caches:

```powershell
.venv\Scripts\python.exe packaging\windows\build_native.py --wheelhouse artifacts\native-dev\wheels-phase3 --stage-dir artifacts\native-dev\package-migration-development-6 --output-dir artifacts\native-dev\migration-development-6 --offline
```

The component lock and `config/product.json` both define `0.1.0-development.6` (`0.1.0.6` in Windows file versions). The frontend is freshly compiled with that product version. `--skip-frontend-build` requires a matching commit/version/output-hash provenance record from a previous native build. Source changes during staging or installer compilation stop publication of the artifact manifest. The tray executable records the version and full source SHA in its PE resources; About, Connection status, runtime status/logs, the API and Portal-reported Agent version use the packaged product identity.

`installer-artifact.json`, the component manifest and SHA256 sidecars identify the actual source revision, dirty state, bundled dependencies and unsigned installer. Both the established `BlueAshReel-Setup-Development-x64.exe` name and the byte-identical `BlueAshReel-Agent-0.1.0-development.6-x64.exe` are emitted. This repository has no established GitHub release/tag workflow; building does not publish through GitHub or the Portal.

The same installer supports explicit `/ISOLATEDTEST=<id>` acceptance, where `id` is 1–32 lowercase letters/digits. Its paths are fixed to `%LOCALAPPDATA%\BlueAshReel-Installer-Tests\<id>\program` and `data`; it requires a dedicated matching marker, uses a separate AppId and HKCU `Software\BlueReel\InstallerTests\<id>` registration, and rejects redirected or linked paths. The mode creates no startup entry or shortcuts, launches no browser or tray automatically, and disables the test tray's startup controls. Uninstall recovers the same scope from the verified installed path and marker. Normal installation retains the existing development AppId and startup convention. **Never test the normal installer with only `/DIR` or `/DATADIR` on a machine with an existing installation.**

After building, run `packaging/windows/test_isolated_installer.py` with the exact `--installer`, `--sha256`, `--source-revision`, `--inventory` (payload `.files.json`), a fresh `--test-id`, an unused `--port`, and a new `--report-dir`. The helper actually installs the binary, launches its tray, checks readiness/version/schema/bundled tools, seeds synthetic retention data, repairs, preserves data during uninstall, reinstalls, verifies identity/settings/artwork/data retention, then stops only its own runtime. Production registration/startup and shortcut hashes must remain unchanged. It does not click wizard controls or attempt Portal pairing; those are separate acceptance scopes.

New installs provision an empty private `configuration\tmdb-access-token.txt` and point `TMDB_TOKEN_FILE` at it. Empty credentials truthfully leave TMDB unavailable. Place the credential in that protected file privately on the new machine; the installer contains no real token, and repair preserves existing token-file choices and contents. See [Windows machine migration](windows-machine-migration.md) before transferring a database or handling the existing DPAPI-protected identity.
