# Troubleshooting

## Phase 2 playback

- **No media / 404:** check the user's library assignments, library/path enabled state,
  and source availability. Administration privileges do not imply viewing access.
- **Source changed:** rescan locally. A changed size/mtime invalidates old playback URLs.
- **Stream expired:** reconnect/resume. Checkpoint inactivity, logout, revoked access,
  source loss or server restart ends ephemeral sessions, not saved watch progress.
- **429 / capacity reached:** stop an unused stream. Completed converted output reserves
  storage until its last stream stops. Do not raise limits without CPU/disk headroom.
- **Conversion failure / 507:** inspect System Health, FFmpeg support and free temp
  space; choose a lower quality. Routine errors intentionally omit filenames/arguments.
- **Image subtitles:** unsupported in this build. Select text subtitles or Off.
- **Stop not confirmed:** the row stays visible and playback is denied. Retry Stop and
  inspect storage/process health. Do not delete a directory whose supervisor holds its lock.
- **Autoplay blocked:** press Play. Browser policy can require a gesture after navigation.
- **No GPU shown active:** select an optional configured encoder and run its real local
  test. Stream copy is not hardware encoding; software fallback is expected.
- **Development hot reload interruption:** reconnect after source/dependency refresh.
  Validate uninterrupted behavior again with the final production build.

Never publish media directories, disable authorization, enable egress, or remove
read-only mounts as a playback workaround.

Start with narrow, read-only checks:

```sh
docker compose ps
docker compose logs --tail 200 backend worker frontend proxy
docker compose config --quiet
```

Do not post `.env`, backup archives, raw database files, full FFprobe commands, media paths/titles, cookies, or unreviewed debug logs.

## Bootstrap cannot find Docker

On Windows, start Docker Desktop and wait for the engine. Confirm `docker info` in the same PowerShell session. If `docker` is missing, install/update Docker Desktop; if WSL is reported, follow Docker Desktop's WSL 2 guidance.

On Linux, verify Docker Engine, the Compose v2 plugin, daemon status, and current-user socket permission. The script does not elevate, start services, or add a user to the privileged `docker` group.

## Existing `.env` is rejected

Bootstrap never overwrites it. Compare keys with `.env.example`. Generate a new random secret only when intentionally creating a fresh deployment; changing the secret can invalidate sessions. Validate `BIND_ADDRESS`, port range, directory uniqueness, and media/state separation.

If `.env` came from a different host, update host paths, `PUID`/`PGID`, bind address, and cookie settings deliberately. Keep a reviewed backup before changes.

## Port already in use

Choose an unused high port in `HTTP_PORT`, then rerun `docker compose up --detach`. Do not stop an unrelated process or Docker project without identifying it. Backend, worker, and frontend ports are intentionally not published and should not be added as a workaround.

## Backend is not ready

```sh
docker compose logs --tail 200 backend
docker compose run --rm --no-deps backend migrate
```

The second command explicitly retries only forward migrations; run it after reviewing logs and backing up an existing database. Common causes are an unwritable `DATABASE_PATH`, an invalid/missing secret, malformed product configuration, or a migration error. Health output intentionally omits full paths.

## FFprobe is unavailable

The backend image installs FFmpeg/FFprobe. Rebuild it and inspect the image locally:

```sh
docker compose build --no-cache backend worker
docker compose run --rm --no-deps --entrypoint ffprobe backend -version
```

Readiness can report FFprobe unavailable without disclosing its host path. Scanning/analysis will fail until the tool works, even if basic administration remains available.

## Media path is missing or denied

Confirm the host `MEDIA_PATH` exists and is readable. On Docker Desktop, confirm the drive/directory is shared. In the application, use the container path (`/media/Movies`), not `D:\Media\Movies` or `/srv/media/Movies`.

Inspect the read-only mount without modifying it:

```sh
docker compose run --rm --no-deps --entrypoint sh backend -c 'test -r /media && find /media -maxdepth 1 -mindepth 1 -print | head'
```

Do not change the Compose mount to read/write. Fix host sharing/permissions or select a different root.

## Worker or scan appears stuck

Check `docker compose ps worker` and privacy-reviewed worker logs, then inspect the Background Jobs page. The durable queue recovers stale running work after the configured threshold. Do not edit job rows directly or start multiple workers as a quick fix; library overlap and SQLite write pressure need diagnosis first.

A changed-files scan uses size/mtime fingerprints. A full scan is appropriate after parser/config changes, not for every routine update.

## Frontend or proxy is unhealthy

Check both services and the backend independently:

```sh
docker compose logs --tail 200 frontend proxy
docker compose exec proxy wget -qO- http://backend:8000/api/v1/health/live
```

Rebuild the frontend if its compiled assets are stale. Do not expose its internal port or replace relative API URLs with a public endpoint.

## Database is locked or damaged

Stop creating new jobs, capture logs, and make an online backup if the API still operates. SQLite WAL files beside `app.db` are part of the live database state; never copy just the active main file. Use the backup validator. If integrity fails, preserve all files and restore a previously validated archive using the documented offline process.

Do not delete `app.db-wal`, `app.db-shm`, the database directory, or migration rows while services run.

## Backup fails

Ensure `BACKUP_PATH` exists, is writable by the configured PUID/GID, has free space, and does not equal, contain, or sit inside application data, artwork, database, or temporary paths. All writable state paths must be mutually isolated. The backup tool has no network and reads state/config mounts read-only. An archive-member, schema-head, integrity, foreign-key, size, or checksum failure leaves no promoted archive and does not apply retention.

## Internet-disconnected operation

Already-built services should operate offline. A rebuild may fail because base images/dependencies are not cached; this does not justify granting runtime egress. Build while connected, then verify the normal internal network remains configured.

## Safe diagnostic bundle

When requesting help, provide the application version, operating system, Docker/Compose versions, service states, endpoint status codes, and a short manually reviewed/redacted log excerpt. Replace usernames, host paths, media names, addresses, IDs, and tokens. Never attach `.env`, the database, or a backup.
