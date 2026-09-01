# Backup and restore

BlueReel backs up a running SQLite database through SQLite's online backup API. It never copies an active `app.db` file directly. The resulting timestamped ZIP includes a standalone validated database, application data, product/deployment configuration, and cached artwork unless excluded.

## Create a backup

Windows:

```powershell
.\scripts\backup.ps1
.\scripts\backup.ps1 -RetentionDays 14 -SkipArtwork
```

Linux:

```sh
sh scripts/backup.sh
sh scripts/backup.sh --retention-days 14 --skip-artwork
```

The wrapper runs the already-built local backend image as an isolated, one-shot tool with no network and never pulls a backup tool from a registry. Run bootstrap successfully before the first backup. The tool opens the source database read-only, uses `sqlite3.Connection.backup`, runs SQLite integrity and foreign-key checks on the copy, requires all phase-one application tables at the exact Alembic head installed in that backend image, records that revision, computes SHA-256 and size for every archived file, validates the ZIP and manifest against the same exact revision, atomically renames the completed archive, and only then applies retention. Restore dry-run validation remains pinned to the exact head of the release performing the restore.

Archive filenames use a safe lowercase slug derived from `config/product.json`, in the form `PRODUCT-SLUG-backup-YYYYMMDDTHHMMSSZ.zip`; changing the configured product name changes the prefix without requiring a script edit. Retention removes only valid archives with the current product-derived prefix inside the resolved `BACKUP_PATH` that are older than the configured number of days. It never deletes the backup just created or arbitrary files. Archives created under an earlier product slug are retained until the operator reviews them. A partial or invalid archive is not promoted.

Backups include `.env`, which contains the application secret, and database records can reveal household media/account information. Restrict and encrypt backup storage. Artwork can be omitted to reduce archive size because it can be regenerated from local sources where available.

## Validate a backup (restore dry run)

The archive must be inside configured `BACKUP_PATH` so the tool receives read-only, narrowly scoped access.

```powershell
.\scripts\restore-validate.ps1 .\backups\PRODUCT-SLUG-backup-YYYYMMDDTHHMMSSZ.zip
```

```sh
sh scripts/restore-validate.sh ./backups/PRODUCT-SLUG-backup-YYYYMMDDTHHMMSSZ.zip
```

Validation rejects duplicate, encrypted, non-regular, unsafe, extra, and unmanifested ZIP members; duplicate manifest records and JSON keys; invalid sizes and SHA-256 values; corrupt ZIP data; an incomplete application schema; and any database or manifest revision other than this release's exact Alembic head. It extracts only the database into a neutral temporary directory, prints a plan summary, and never writes to application targets. This is intentionally the only automated restore command in phase one.

## Explicit offline restore

Restoring replaces household state and is therefore manual. Read all steps first.

1. Validate the selected archive with the dry-run command above.
2. While the current installation still runs, create one fresh backup and record its exact filename.
3. Stop BlueReel cleanly with `docker compose down`. Do not use `--volumes`.
4. Resolve `DATABASE_PATH`, `DATA_PATH`, `ARTWORK_PATH`, and `BACKUP_PATH` from `.env`. Confirm each absolute path before moving anything.
5. Extract the selected ZIP into a new, empty staging directory outside all configured runtime and media paths. Use `Expand-Archive` on Windows or `unzip` on Linux. Do not extract directly over live directories.
6. Inspect `manifest.json` and compare the archived `configuration/.env` with the current `.env`. Host paths, bind address, UID/GID, port, and secure-cookie settings may need to remain host-specific. Do not blindly replace the current `.env`.
7. Move the current database directory, application-data directory, and artwork directory to uniquely timestamped `pre-restore` sibling names. This is the recovery point; do not delete it yet.
8. Create fresh configured target directories. Copy staged `database/app.db` to `DATABASE_PATH/app.db`. Do not copy old `app.db-wal` or `app.db-shm` files. Copy staged `application-data` and `artwork` contents to their respective targets. Preserve the invoking user's ownership/permissions.
9. If deliberately restoring product identity, copy staged `configuration/product/product.json` to `config/product.json` after reviewing the diff.
10. Start with `docker compose up --detach`. The backend applies forward migrations before readiness.
11. Check `/api/v1/health/ready`, log in, inspect libraries/jobs, and run another dry backup validation.
12. Keep the `pre-restore` directories and fresh pre-restore archive until the restored installation has been verified. Delete them later only as a separate, deliberate operation.

A backup from a different application version is rejected when its Alembic revision differs. Validate and restore it with the matching application release first, then follow the documented forward upgrade procedure. Never edit a manifest or version row to bypass this compatibility check.

If startup fails, stop the stack, move the failed restored targets aside, put the timestamped pre-restore directories back at their exact configured paths, and restart. Do not attempt an Alembic downgrade on the only copy of household data.

## What is not backed up

Source media is never copied. The disposable `TEMP_PATH` is excluded. Container images and logs are excluded. Recreate images from source, and preserve logs separately only when needed for a privacy-reviewed diagnosis.

The backup is application-consistent, not a substitute for independent encrypted/off-site resilience chosen by the operator.
