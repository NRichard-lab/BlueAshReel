# Windows installation

Phase 2 adds local streaming. Allocate transcode space deliberately and confirm
FFmpeg supports libx264/AAC. The reference containers remain non-root, media remains
read-only, and only loopback Caddy is published. Native development uses Windows
paths in MEDIA_ROOTS (semicolon-separated), not Linux container paths.

After upgrade, verify assigned libraries, a direct MP4, MKV remux, incompatible-video
conversion, pause/resume across restart, and Stop Stream cleanup. Optional QSV/NVENC/AMF
requires real System Health test encodes; do not infer support from a GPU name.
Follow the [dedicated-workstation checklist](phase2-validation.md). Docker was not
available on the development host, so its runtime validation remains outstanding.

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

## Install BlueReel

Open PowerShell in the repository directory. A process-scoped execution-policy change does not alter the machine policy permanently:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1 -MediaPath "D:\Media"
```

The selected media directory must already exist. Omit `-MediaPath` to create an empty repository-local `media` directory. Paths containing spaces are supported when quoted.

On first run the script:

1. checks Docker, Compose, engine availability, and WSL status;
2. creates `.env` only if it does not exist and generates a cryptographic secret;
3. validates a loopback/private bind, port, read access, write access, distinct state paths, and media/state separation;
4. renders the Compose configuration;
5. builds and starts only this Compose project; and
6. waits for readiness and prints the local URL.

Existing `.env` files are never overwritten. If a partial first run created one that you do not want, rename it for review rather than deleting it blindly, then rerun.

## Owner setup

Open [http://localhost:8080](http://localhost:8080). Create the initial Owner and register a library below `/media` (the container view of the selected `MEDIA_PATH`). A host path such as `D:\Media\Movies` appears to the application as `/media/Movies`.

After the Owner exists, the first-run endpoint no longer permits another account. Store the Owner password in a password manager; it is hashed and cannot be recovered from the database.

## Windows path guidance

Docker Desktop handles Windows bind mounts, but files on a native Linux WSL filesystem and files on an NTFS drive have different performance characteristics. Large Windows media collections can remain on NTFS. Keep the database and artwork on a fast local disk, not a network-synchronized folder. Do not place runtime state beneath the read-only media directory.

For additional household devices, set `BIND_ADDRESS` in `.env` to this workstation's fixed private address, then run:

```powershell
docker compose up -d
```

The bootstrap deliberately rejects `0.0.0.0` and public addresses. A private bind does not configure Windows Firewall; if access is blocked, create only the narrow private-profile rule your household needs after reviewing local policy. Public exposure and router port forwarding are not supported.

## Stop or remove containers

```powershell
docker compose down
```

This leaves configured host data intact. Do not add `--volumes` as a cleanup shortcut. See [backup and restore](backup-and-restore.md) before upgrades or storage changes.
