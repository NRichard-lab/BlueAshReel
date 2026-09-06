# Native Windows Agent (development.5)

The Windows Agent runs as the signed-in Windows user in a native system tray. Fresh installations use no Windows service, scheduled task, administrator runtime, Docker, Node server, or local username/password UI. The Portal at https://blueashreel.com is the user interface. Existing service-only documentation in earlier Git revisions describes development.1 through development.4.

## Installation and storage

The unsigned development installer defaults program files to `%LOCALAPPDATA%\Programs\BlueAshReel-Development` and mutable data to `%LOCALAPPDATA%\BlueAshReel-Development`. The wizard configures the application-data directory, loopback port, transcode process/thread limits, and login startup. Advanced pages separately configure the database, artwork/cache, temporary transcodes, backups and logs. Locations must be dedicated absolute directories, must not overlap one another or program files/configuration, and must not contain junctions or reparse points. Existing settings and secrets survive repair.

Installation creates the empty database schema only. There is no Owner creation, application login, first-library selector, media-root selection, scan, permission setup, or pairing wizard. After runtime validation, the tray starts and the default browser opens the Portal. Sign in, complete MFA, compare the local Agent fingerprint, and confirm pairing. Library selection and administration require the authenticated encrypted Owner session. The folder picker and final consent run on the Agent workstation; source media remains read-only.

Local management listens on `127.0.0.1:<port>` (development default 18080). The Agent also binds a separate random port on exact `127.0.0.1` for the Portal callback; authorization binds that exact callback, and fragments carry sensitive callback material without placing it in query strings. Root/status access requires the Portal authorization flow. The tray distinguishes Not paired, Waiting for Portal approval, Waiting for local confirmation, Paired but Agent offline, Connecting, Connected, Revoked, Pairing expired and Error. Connected requires a fresh authenticated relay connection. Its menu provides Open Portal, Open local management, Pair Agent, Connection status, Reconnect, Unpair this Agent, pause/resume, restart, logs, settings, login startup and confirmed exit. The local status and pairing consent show the same short fingerprint as the Portal, and both surfaces require explicit approval. Closing a status/settings window leaves the tray running.

## Runtime ownership and privacy

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

HKCU `Software\Microsoft\Windows\CurrentVersion\Run` provides opt-in login startup. Runtime state and consent queues have a user/SYSTEM-only DACL on a new installation. Child processes have no visible console; FFmpeg/FFprobe probes also use hidden process flags. The bundled FFmpeg is compiled with networking disabled. API egress is constrained to loopback and the canonical Portal TLS endpoints; worker network integrations remain disabled.

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
.venv\Scripts\python.exe packaging\windows\build_native.py --version 0.1.0-development.5 --wheelhouse artifacts\native-dev\wheels-phase3 --stage-dir artifacts\native-dev\package-development-5 --output-dir artifacts\native-dev\development-5 --offline
```

`installer-artifact.json`, the component manifest and SHA256 sidecar identify the actual source revision, dirty state, bundled dependencies and unsigned installer. A candidate built for isolated acceptance is not a published download. Publication metadata must name only an approved artifact.

Disposable acceptance uses separate program/data paths and port29180. The current acceptance evidence includes nonadmin user ownership, loopback-only binding, Portal redirect for unauthenticated root, startup, pause, restart and reconnect, native status-window inspection, and closing the window without exiting. Synthetic tests cover foreign paths/channels, storage isolation, consent expiration and response binding, backup schema validation and advanced-database rollback. Production pairing/MFA and actual legacy service removal require their own verified acceptance evidence.
