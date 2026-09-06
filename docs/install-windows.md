# Windows installation

There are two deployment options sharing the same application:

- [Native Windows development installer](native-windows.md): self-contained,
  per-user tray runtime and Portal authentication, with no Docker/WSL requirement.
  The development identity defaults to port 18080 and preserves separate storage.
  Development.5 acceptance is pending; the published installer is still development.4.
- Docker Desktop: the advanced/development path documented below, using port
  8080 by default. Its prerequisites apply only to this path.

See [native validation](native-windows-validation.md) for actual installer tests
and remaining development limitations. The unsigned installer is not a public
release or the end of application development.

## Docker Desktop deployment

Phase 2 adds local streaming. Allocate transcode space deliberately and confirm
FFmpeg supports libx264/AAC. The application processes remain non-root, media remains
read-only, and only loopback Caddy is published. Native development uses Windows
paths in MEDIA_ROOTS (semicolon-separated), not Linux container paths.

After upgrade, verify assigned libraries, a direct MP4, MKV remux, incompatible-video
conversion, pause/resume across restart, and Stop Stream cleanup. Optional QSV/NVENC/AMF
requires real System Health test encodes; do not infer support from a GPU name.
Docker Desktop/WSL2 validation is recorded in [container validation](container-validation.md).
Caddy's initializer configures only its container firewall, then drops to UID 1000
and zero capabilities before serving requests. GPU availability must be checked
inside Docker independently of native Windows support.

## Requirements

- A supported 64-bit Windows workstation
- Docker Desktop with Docker Compose v2
- WSL 2 enabled when Docker Desktop selects that backend
- Enough local storage for the SQLite database, artwork, temporary work, and backups
- A media directory readable by Docker Desktop file sharing

The bootstrap script does not install packages, request elevation, change firewall rules, expose a router port, or alter unrelated Docker workloads. When a prerequisite is missing, it reports the blocker and stops. Docker Desktop can be installed separately with its official installer or, where `winget` is available:

```powershell
winget install --exact --id Docker.DockerDesktop
```

Restart/sign out only if the Docker Desktop installer requests it. Start Docker Desktop and wait for its engine to be ready.

## Install Blue Ash Reel

Open PowerShell in the repository directory. A process-scoped execution-policy change does not alter the machine policy permanently:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
```

On a new interactive desktop installation, bootstrap offers a native folder
dialog and a typed-path fallback. You may choose more than one folder or skip
the choice and keep the empty repository-local `media` directory. A fully
scripted installation can supply one or more paths explicitly:

```powershell
.\scripts\bootstrap.ps1 -MediaPath @("E:\Videos", "F:\Family recordings")
```

The selected directories must already exist. Paths containing spaces are
supported. Bootstrap rejects whole-drive roots, links/reparse points, duplicate
or nested roots, and overlap with writable application state.

On first run the script:

1. checks Docker, Compose, engine availability, and WSL status;
2. creates `.env` only if it does not exist and generates a cryptographic secret;
3. validates a loopback/private bind, port, read access, write access, distinct state paths, and media/state separation;
4. records approved roots locally and renders identical read-only mounts for backend and worker;
5. builds and starts only this Compose project; and
6. waits for readiness and prints the local URL.

Existing `.env` values and secrets are preserved. Root changes require the
explicit `-ConfigureMediaRoots` switch; bootstrap updates only the three managed
media-root values and its ignored generated registry/override after Compose
validation succeeds:

```powershell
.\scripts\bootstrap.ps1 -ConfigureMediaRoots -MediaPath @("E:\Videos", "F:\Family recordings")
```

An unrelated `compose.override.yml` is never overwritten. Move or merge such an
override deliberately before using managed multi-root configuration.

## Owner setup

Open [http://localhost:8080](http://localhost:8080). Enter the initial Owner details,
then use **Browse folders** in the signed 30-minute setup session to choose a directory below an approved root.
**Finish secure setup** saves the Owner and optional first library together. The normal
interface does not require Windows or container paths. The first configured root
retains internal path `/media`; additional roots receive stable internal IDs.

After the Owner exists, the first-run endpoint no longer permits another account. Store the Owner password in a password manager; it is hashed and cannot be recovered from the database.

## Windows path guidance

Docker Desktop handles Windows bind mounts, but files on a native Linux WSL filesystem and files on an NTFS drive have different performance characteristics. Large Windows media collections can remain on NTFS. Keep the database and artwork on a fast local disk, not a network-synchronized folder. Do not place runtime state beneath the read-only media directory.

For additional household devices, set `BIND_ADDRESS` in `.env` to this workstation's fixed private address, then run:

```powershell
docker compose up -d
```

The bootstrap deliberately rejects `0.0.0.0` and public addresses. A private bind does not configure Windows Firewall; if access is blocked, create only the narrow private-profile rule your household needs after reviewing local policy. Public exposure and router port forwarding are not supported.

## After a Windows reboot

If Docker Desktop is not configured to start at sign-in, start it and wait for the
Linux engine. Then, from the repository directory, run:

```powershell
.\scripts\bootstrap.ps1 -NoBuild
```

This preserves `.env`, accounts, libraries and saved progress. Compose uses project
name `bluereel`; services have `unless-stopped` restart policies. After changing or
rebuilding backend/frontend containers, use `docker compose up -d --build` for the
whole project so Caddy refreshes its narrowly permitted upstream addresses.

## Stop or remove containers

```powershell
docker compose down
```

This leaves configured host data intact. Do not add `--volumes` as a cleanup shortcut. See [backup and restore](backup-and-restore.md) before upgrades or storage changes.
