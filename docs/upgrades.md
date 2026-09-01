# Upgrade foundation

Upgrades are explicit and local. There is no unattended updater, remote control plane, or automatic rollback.

Phase 2 upgrades schema `773863f5a6aa` through `2a0100000001` (assignments,
preferences, watch state, FTS5) to `2b0100000001` (playback sessions). Existing
Owners receive access to existing libraries. New household accounts need explicit
library assignments. Populated Phase 1 records are preserved; downgrading discards
Phase 2 tables/history and must not substitute for a validated restore. Stop active
playback before upgrading. Conversion output is disposable, not backed up or migrated.

## Before changing versions

1. Read release notes and migration notes for the target revision.
2. Run the existing backend/frontend tests if developing locally.
3. Create a fresh online backup with `scripts/backup.ps1` or `scripts/backup.sh`.
4. Dry-validate that exact archive.
5. Record `git rev-parse HEAD`, `docker compose images`, configured paths, and the currently working health response without copying secrets into an issue.

## Apply an upgrade

The safe wrappers validate the existing deployment, create and validate an online backup first, build the checked-out source, stop only the worker/backend, run forward migrations through the backend image entrypoint, recreate the Compose services, and wait for readiness. The pre-upgrade tool discovers the exact Alembic head from the currently installed backend image, so newly checked-out scripts can still validate the legitimate pre-migration database; restore validation with a different release remains fail-closed. They never run `git pull`, guess a remote, remove volumes, or change firewall/router settings.

The rebuild also embeds the current `config/product.json` into the frontend image; this is required for branding changes because the frontend consumes product identity at build time. The backend consumes the same file from its read-only runtime mount.

```powershell
.\scripts\upgrade.ps1
```

```sh
sh scripts/upgrade.sh
```

Use `-NoPull` or `--no-pull` only when required images are already cached and the build host is intentionally offline. The wrappers upgrade exactly the source already present in the working tree; obtaining/reviewing that source is a separate operator action.

Equivalent manual commands, after a validated backup, are:

```sh
docker compose pull proxy
docker compose build --pull backend worker frontend
docker compose stop worker backend
docker compose run --rm --no-deps backend migrate
docker compose up --detach
docker compose ps
```

The backend runs `alembic upgrade head` before starting Uvicorn. The worker starts only after backend readiness. Watch local logs for migration/startup errors, then verify Owner login, libraries, recent jobs, and a small changed-files scan.

Builds may use Internet access to retrieve base images or dependencies. Runtime containers return to the internal, no-egress network.

## Rollback posture

Code rollback is not automatically the same as database rollback. Never point older code at a migrated database unless the release explicitly documents compatibility. Prefer restoring a validated pre-upgrade archive using the manual offline procedure, after preserving the failed upgraded state for diagnosis.

Do not run `alembic downgrade`, delete a database, prune Docker volumes, or overwrite configuration as an improvised rollback. This repository uses host-directory persistence, and broad Docker cleanup commands can affect unrelated workloads.
