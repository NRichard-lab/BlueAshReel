# BlueReel

**A Blue Ash Application.**

BlueReel is a private, local-first home-media catalog and administration service. This first development phase combines a FastAPI API, a React/TypeScript interface, SQLite in WAL mode, FFmpeg/FFprobe analysis, a durable database-backed worker, and a localhost-only Docker Compose deployment.

The product name, subtitle, version, and API prefix live in one place: [`config/product.json`](config/product.json). Do not duplicate branding in source or deployment secrets.

## Quick start

Prerequisites are Docker Desktop on Windows or Docker Engine with Compose v2 on Linux. The bootstrap scripts detect missing prerequisites, preserve existing configuration, generate a secure application secret, validate all bind-mounted paths, start the stack, and wait for readiness.

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1 -MediaPath "D:\Media"
```

Ubuntu/Linux:

```sh
sh scripts/bootstrap.sh --media-path /srv/media
```

Without `-MediaPath` or `--media-path`, the first run creates an empty local `media/` directory. Source media is mounted read-only. The application is available at [http://localhost:8080](http://localhost:8080), where the first browser visit begins Owner setup.

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
- [Privacy and outbound connections](docs/privacy-and-outbound.md)
- [Backup and restore](docs/backup-and-restore.md)
- [Upgrade procedure](docs/upgrades.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Phase-one limitations](docs/not-implemented.md)
- [Architecture decision record](docs/adr/0001-local-first-foundation.md)

## Repository map

```text
backend/     FastAPI application, durable worker, migrations, and tests
config/      Central product identity
docker/      Production-style container images and Caddy configuration
docs/        Architecture, operations, privacy, and recovery guidance
frontend/    React and TypeScript administration interface
scripts/     Bootstrap, online backup, and restore-validation tools
compose.yml  Local runtime topology and hardening
```

## Privacy posture

Runtime services share an internal Docker network with no outbound route. Only Caddy publishes a host port, bound to loopback by default. Media is read-only; inspection and artwork handling occur locally. The project includes no analytics, advertising, remote fonts, tracking pixels, external crash reporting, or metadata-provider calls. Future outbound integrations require both an operator opt-in and an Owner-controlled setting, and are disabled by default.

Backups contain the database, local configuration (including the application secret), application data, and optionally cached artwork. Treat them as sensitive household data.

## Development status

This phase establishes administration, scanning, local metadata/artwork, durable jobs, health, privacy controls, and operational foundations. It does not include media playback/transcoding delivery, metadata-provider integrations, remote access, recommendations, live TV/DVR, a Google TV client, or Blue Ash portal integration. See [the complete limitations list](docs/not-implemented.md).

BlueReel is not deployed publicly by this repository.
