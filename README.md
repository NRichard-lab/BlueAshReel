# BlueReel

**A Blue Ash Application.**

BlueReel is a private, local-first home-media streaming website. Phase 2 adds household library permissions, movie/TV browsing, a first-party browser player, durable per-user progress, and local FFmpeg remuxing/transcoding to the Phase 1 FastAPI, React/TypeScript, SQLite WAL, scanner, and localhost-only Docker foundations.

The product name, subtitle, version, and API prefix live in one place: [`config/product.json`](config/product.json). Do not duplicate branding in source or deployment secrets.

## Quick start

Prerequisites are Docker Desktop on Windows or Docker Engine with Compose v2 on Linux. The bootstrap scripts detect missing prerequisites, preserve existing configuration, generate a secure application secret, validate all bind-mounted paths, start the stack, and wait for readiness.

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
```

Ubuntu/Linux:

```sh
sh scripts/bootstrap.sh --media-path /srv/media
```

An interactive first run offers media-root selection (a native folder dialog on Windows, with typed fallback). You can approve multiple folders or explicitly skip and configure them later; skipped/non-interactive installations retain an empty local `media/` directory. Source media is mounted read-only. The application is available at [http://localhost:8080](http://localhost:8080), where Step 3 of protected first-run setup provides a folder browser. See [approved media storage](docs/media-storage.md) for explicit path arguments and reconfiguration.

The default bind is `127.0.0.1`; no firewall, router, or public-access setting is changed. For household LAN access, deliberately set `BIND_ADDRESS` in `.env` to this computer's RFC1918 address and restart. TLS and remote access are not part of this phase.

## Common operations

```sh
# Status and privacy-safe local logs
docker compose ps
docker compose logs --tail 200

# Stop without deleting persistent data
docker compose down

# Start again
docker compose up --detach

# Create and validate an online backup
sh scripts/backup.sh

# Upgrade the already-checked-out source with backup + migration safeguards
sh scripts/upgrade.sh
```

PowerShell users can run `scripts\backup.ps1`. Never use `docker compose down --volumes` as a troubleshooting shortcut; persistent application state is intentionally kept in configured host directories.

Health and API discovery:

- Liveness: `GET /api/v1/health/live`
- Readiness: `GET /api/v1/health/ready`
- Version: `GET /api/v1/version`
- OpenAPI schema: `/api/v1/openapi.json` (the interactive CDN-backed UI is disabled)

Health responses are intentionally narrow and do not include users, media titles, full filesystem paths, tokens, or secrets.

## Documentation

- [Architecture overview](docs/architecture.md)
- [Local development](docs/local-development.md)
- [Windows installation](docs/install-windows.md)
- [Ubuntu/Linux installation](docs/install-linux.md)
- [Configuration reference](docs/configuration.md)
- [Approved media storage and folder browser](docs/media-storage.md)
- [Privacy and outbound connections](docs/privacy-and-outbound.md)
- [Backup and restore](docs/backup-and-restore.md)
- [Upgrade procedure](docs/upgrades.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Household users and permissions](docs/household-users.md)
- [Playback and history](docs/playback.md)
- [Local conversion and hardware](docs/transcoding.md)
- [API reference](docs/api.md)
- [Phase 2 validation and workstation checklist](docs/phase2-validation.md)
- [Docker validation and local handoff](docs/container-validation.md)
- [Current limitations](docs/not-implemented.md)
- [Architecture decision record](docs/adr/0001-local-first-foundation.md)

## Repository map

```text
backend/     FastAPI application, durable worker, migrations, and tests
config/      Central product identity
docker/      Production-style container images and Caddy configuration
docs/        Architecture, operations, privacy, and recovery guidance
frontend/    React and TypeScript streaming website and administration
scripts/     Bootstrap, online backup, and restore-validation tools
compose.yml  Local runtime topology and hardening
```

## Privacy posture

Backend, worker and frontend use an internal Docker network with no outbound route.
Caddy's startup guard applies deny-by-default rules in its own network namespace,
permitting only replies, local health checks and the two internal upstreams.
Only Caddy publishes a host port, bound to loopback by default. No host
firewall rules are changed. Media is read-only; inspection and artwork handling
occur locally. There are no analytics, advertising, remote fonts, tracking pixels,
external crash reporting or metadata-provider calls. Outbound integrations remain
disabled and unavailable. See the [network boundary](docs/privacy-and-outbound.md).

Backups contain the database, local configuration (including the application secret), application data, and optionally cached artwork. Treat them as sensitive household data.

## Development status

Phase 2 includes assigned-library Home rails, paginated movie/search/TV views, local artwork, Owner/Administrator/Viewer management, authenticated range playback, text subtitles, explicit audio/quality changes, resume/watch history, progressive HLS remuxing, software H.264/AAC conversion, optional tested hardware encoders, and Owner stream monitoring. Image-subtitle burn-in is honestly unsupported in this build. Docker/workstation and cross-browser validation status is recorded separately from native tests; see [validation](docs/phase2-validation.md) and [limitations](docs/not-implemented.md).

BlueReel is not deployed publicly by this repository.
