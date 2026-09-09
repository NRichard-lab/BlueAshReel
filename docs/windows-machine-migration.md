# Move a Windows Agent to another machine

This procedure preserves the old installation as the working fallback. It does
not perform cutover, revoke pairing, transfer decrypted keys, or modify the old
Agent. The new installer supplies Python, FFmpeg/FFprobe and runtime dependencies;
the media files and private migration snapshot travel separately.

## Identity finding: the supported path is B

| Outcome | Current implementation |
| --- | --- |
| A: securely export/import the same private identity | No supported export/import command or portable encrypted identity archive exists. |
| B: copy application data and pair a new Agent | Supported for the same Portal Owner. The new Agent gets a distinct identity; the old Agent remains paired. |
| C: transfer the existing Portal Agent identity through a migration workflow | A Portal key-rotation primitive exists, but an Agent migration/rollback workflow has not been implemented or accepted. |

`app.remote.storage.protect_secret` stores Windows Ed25519 private keys as
`dpapi-user:` ciphertext. It calls `CryptProtectData` with
`CRYPTPROTECT_UI_FORBIDDEN` and does not enable machine-wide protection. Decryption
requires the Windows account/profile's DPAPI key material, ordinarily on the
original computer. A matching username or copied identity file is insufficient.
Microsoft documents a roaming-profile exception; the Agent has no profile
migration tooling or accepted cross-machine recovery procedure relying on it.
See [Microsoft's DPAPI documentation](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

The connector reads and decrypts `remote-identity/identity.json` before connecting.
Copying that ciphertext to another account can fail startup; it does not establish
portability. Keep it in the private old-machine recovery snapshot. Do not convert
it to `protected-file:`, change DPAPI scope, copy Windows master keys, or put a
plaintext private key into a file, command line, report or installer.

The existing Portal `POST /api/agents/{id}/rotate` requires a fresh challenge and
signatures from both old and new keys. It preserves the Portal Agent ID while
replacing its public key, immediately invalidating the old key's access. It is
not a reversible staging mechanism. A future supported transfer would need
locally confirmed endpoints, protected key storage, an authenticated handoff,
replay protection, staged validation and an explicit recovery/cutover protocol.
That workflow is not part of this installer and must not be improvised in SQL.

Same-Owner re-pair has a specific local migration path in
`app.remote.media.RemoteMedia.principal`: the verified, enabled Owner grant
follows the new Agent ID while retaining its local user ID and watch history.
Other members do not follow automatically. Portal memberships are scoped to the
old Agent ID, and a restored local member grant for that ID is rejected by
`grants.set` on the new Agent. Re-invitation alone does not repair that local
mapping. A multi-member installation needs an explicit reviewed grant migration
that retains each local user ID, then fresh central membership and local library
consent. Do not delete users or their history to bypass the mismatch. The audited
migration snapshot has one local user and one Owner grant, so this limitation
does not block its Owner-only migration.

## Preserve and validate the old state

Keep the old Agent installed and paired. Finish playback normally before a final
snapshot and allow metadata/scan jobs to settle. Use SQLite's online backup API
for a running source; never copy a live `app.db` directly. An online snapshot must
verify that durable files and canonical database rows did not change across the
copy. A later cutover needs a fresh snapshot if viewing history or settings have
changed since the preserved baseline.

The private directory snapshot includes `database/app.db`, `configuration`,
`remote-identity`, `data`, `artwork`, the installer product configuration, source
provenance and a SHA-256 manifest. `database-summary.json` records schema, columns,
row counts and canonical row hashes. `reference-only` retains diagnosis/recovery
material such as old installation state and command queues. It is never replayed
on the new machine. Source media, temporary conversions and bundled dependency
binaries are excluded. This directory format supplements the standard ZIP
backup in [backup and restore](backup-and-restore.md); the standard archive does
not by itself constitute this complete identity/token migration snapshot.

The snapshot contains private configuration, token files, account data and DPAPI
ciphertext. Keep its owner/SYSTEM-only permissions, transfer it using private
encrypted storage, and keep the recorded manifest SHA-256 separately. A hash
copied from an untrusted modified manifest is not proof of authenticity.

The bundled validator uses only immutable, read-only SQLite access and never
decrypts identity material, reads media files, restores files, or contacts the
Portal. Run it with the installer's embedded runtime (replace the three values):

```powershell
$agentProgram = Join-Path $env:LOCALAPPDATA 'Programs\BlueAshReel-Development'
$snapshot = 'E:\PrivateMigration\migration-TIMESTAMP'
$manifestHash = 'RECORDED_64_CHARACTER_SHA256'
& "$agentProgram\runtime\python\python.exe" -I -B -m scripts.validate_migration $snapshot --manifest-sha256 $manifestHash
```

Use the actual program directory if the installer used a custom location. In a
source checkout the equivalent command is
`.venv\Scripts\python.exe -B scripts\validate_migration.py` with the same arguments.
Exit 0 verifies every manifested file, the exact database summary, SQLite
integrity/foreign keys and every cached artwork reference/content hash. The
validator rejects links/junctions, unsafe or duplicate paths, unmanifested files,
changed content and a nonempty SQLite WAL/journal. Empty WAL and shared-memory
files left by a read-only snapshot inspection contain no committed data; they
are ignored during immutable validation and must not be restored.

The audited baseline contains 2 libraries, 2 library paths, 12 media items,
11 media files, 13 metadata records, 110 artwork references to 71 unique cached
files, 2 progress rows, 1 user, 2 user/library assignments and 1 Owner grant at
schema `2d0100000001`. A fresh snapshot's own counts and hashes are authoritative.

## Stage the new machine

1. Verify the installer version, source revision and SHA-256 against its release
   evidence. Install under the intended Windows account using the normal wizard.
   Choose separate, ordinary local storage directories; do not target the old
   machine's live data or network-share its mutable database. The normal installer
   starts the tray and opens the pairing flow. This may generate a fresh local
   DPAPI identity as part of the deliberately selected B strategy. Do not approve
   pairing until the restored data is ready. Close that browser flow if staging
   offline; no old pairing is revoked by installing another Agent.
2. Exit the **new** tray cleanly and confirm its API, worker and owned FFmpeg
   processes have exited. Keep its freshly generated configuration, instance
   marker and any incomplete new identity as the new installation's own state.
   Do not use Unpair on the old machine. Never run both instances against one
   mutable database or artwork directory.
3. Validate the transferred snapshot. Keep the original snapshot unchanged.
   Privately retain the new installation's empty database/configuration as
   `pre-restore` copies outside its active storage. Copy only snapshot
   `database/app.db` into the new configured database directory. Copy artwork
   contents to the configured artwork directory. Do not copy `-wal`, `-shm`,
   temporary conversions, process locks or any `reference-only` material.
4. Keep the new `installation.json`, generated paths, instance marker,
   permissions, startup registration and runtime settings that name executables,
   ports or host directories. Merge reviewed application settings from the old
   `.env` privately; preserve its application secret when restoring its database,
   but replace old database/artwork/temp/log/backup/token filenames with the new
   generated paths. Do not replace the new `.env` wholesale. Copy durable `data`
   contents only after reviewing stored approved media roots for the new host.
   If `application_settings` contains `transcoding.policy`, its `temp_directory`
   also needs a reviewed new-machine value; retain its other settings. The
   audited snapshot has no persisted application-setting rows.
5. Leave the new machine's `remote-identity` intact. Do **not** restore the old
   `identity.json`, revocation tombstone, `revoked.json`, control spool, consent
   queues or status JSON. Those files are old-machine recovery evidence. Do not
   manually change Agent IDs in the database to simulate pairing.
6. Keep the new private `TMDB_TOKEN_FILE` location. Fresh installers configure
   `<AgentData>\configuration\tmdb-access-token.txt` and create an empty private
   file. With the new Agent stopped, privately copy the backed-up token file's
   contents into that file, or enter the token with a local editor. Preserve the
   directory ACL and never put the token in shell arguments. Review any old
   `TMDB_ACCESS_TOKEN` override because it takes precedence. Repair preserves
   existing values and custom token paths. See [metadata configuration](metadata.md).
7. Make source media available read-only to the new Windows account. To preserve
   every existing media/file ID without a path migration, use the **same absolute
   library paths** and relative media layout as the old installation (for example,
   the same drive letter and directory). `library_paths.canonical_path` holds the
   root and `media_files.relative_path` holds the path below it. Verify each root,
   expected file count, sizes and access before scanning. Do not recreate the
   libraries through Add Library, which allocates new IDs. If roots must change,
   stop here for a reviewed offline mapping on a disposable restored copy that
   retains library-path/media IDs and updates approved-root configuration; there
   is no general automated path-rebase command in this release. Junctions are not
   an accepted workaround. Do not scan missing or incorrectly mapped roots.

## Verify the restored data before starting

Use the new configured database/artwork locations, not guessed default paths:

```powershell
$restoredDb = 'C:\NewAgentData\database\app.db'
$restoredArtwork = 'C:\NewAgentData\artwork'
& "$agentProgram\runtime\python\python.exe" -I -B -m scripts.validate_migration $snapshot --manifest-sha256 $manifestHash --database $restoredDb --artwork-dir $restoredArtwork
```

This compares every table's columns/count/canonical rows and the SQLite schema
including indexes, constraints, views and triggers. It also verifies every
restored cached artwork reference and content SHA-256. Exact equality is expected
before first startup if no reviewed path/settings edits were required. The tool
prints counts/hashes and changed table names, never rows, private paths or keys.
Exit 2 means the restored database differs; exit 1 means validation failed.

Current provider artwork paths are relative to the configured artwork root and
therefore survive an absolute cache-location change without DB updates. For a
legacy absolute cached reference, `--original-artwork-root 'C:\OldData\artwork'`
maps it to the supplied staged artwork root **in memory only** for verification.
It does not make an old absolute reference usable at runtime. Actual rebasing
needs a separately reviewed offline change on the staged copy, followed by
content verification; never rewrite the only preserved snapshot.

After startup, login, re-pair and playback, session, grant, alias, audit and
progress rows can legitimately change. An exact comparison is then expected to
report those changes rather than silently exclude them. Keep the pre-start pass
and review each later difference against the intentional action. Recheck
catalog IDs/counts, metadata and artwork; confirm the Owner local user ID remains
the same and watch checkpoints reflect the actual test playback. MFA data stays
in the Portal and this procedure does not edit it.

## Pair, accept and retain fallback

1. Start the new tray. Confirm readiness and logs, then choose Pair Agent. Sign
   into the **same Portal Owner account**, complete its existing MFA, compare the
   new fingerprint locally and in the Portal, and explicitly confirm both.
   Give the new Agent a distinguishable name. Select that new Agent for testing.
   Its Agent ID and fingerprint will differ from the old entry; its Owner grant
   migrates locally on the first authenticated request. Old remote object aliases
   are not reusable under the new identity and new aliases are created as needed.
2. Confirm the new Portal entry stays connected; test Home, posters, Movies, TV,
   detail metadata, relay byte reads, playback, Resume and saved progress. Verify
   TMDB enrichment and another cached artwork read. Confirm provider credentials
   remain absent from the browser and the Portal's MFA behavior is unchanged.
   Test playback long enough to assess the retained conversion/storage policy;
   the migration preserves the old settings and does not claim to resolve any
   previously unconfirmed playback-limit diagnosis.
3. If testing fails, exit the **new** Agent and select the still-paired old Agent
   in the Portal. Resume the old tray if it was temporarily paused. No old DB,
   key or Portal grant needs restoration because none was replaced or revoked.
   New-machine test history is a separate branch of state; there is no automatic
   merge of progress written independently on both Agents.
4. Only after all new-machine acceptance checks pass should the user choose the
   cutover time and whether to retire the old Agent. Take a final backup first.
   Unpair/revoke the **old** Agent explicitly only when fallback access is no
   longer required. Revocation is intentionally not undone by copying a backup.
   Keep the old installation/data and private snapshot for the agreed retention
   period; uninstall or deletion is a separate deliberate action.

For same-machine recovery under the original Windows account, protected identity
can be restored only after confirming the Portal entry was not revoked, the old
processes are stopped and no other instance is using it. A backup cannot reverse
central revocation or replace lost DPAPI profile keys. Follow
[native recovery](native-windows.md#safe-service-migration-and-rollback) with the matching
release and reviewed original configuration; do not reinterpret this as a
cross-machine identity transfer.
