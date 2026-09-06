# Blue Ash Reel

**A Blue Ash Application.**

Blue Ash Reel uses [blueashreel.com](https://blueashreel.com) for sign-in, MFA,
Agent selection, invitations, library management, browsing and playback. Its
Windows Agent runs in the signed-in user's system tray and keeps the media
catalog, source files, artwork, watch progress and FFmpeg processing on that
workstation. Browser-to-Agent requests and media travel through the Portal as
application-layer encrypted frames.

This repository contains the Agent, shared local media engine, Windows installer
and preserved Docker deployment. The [Portal repository](https://github.com/NRichard-lab/BlueAshReelPortal)
contains the public interface and account/control services. Docker's existing
local website and household-account workflow remain available as a separate
legacy/development deployment; they are not the Windows Agent's primary interface.

## Windows Agent

The development.5 source replaces the service runtime with a native tray host,
per-user startup and Portal authentication. Setup configures program/data paths,
separate database/artwork/temp/backup/log locations, a loopback port and resource
limits. It does not create application users, select media libraries, scan media
or pair an Agent in the wizard. Runtime uses the installing Windows user's normal
permissions, including authorized drives/shares. No Docker, WSL or separately
installed Python, Node or FFmpeg is required on the target workstation.

After installation, the tray opens the Portal. Sign in, complete MFA, compare
fingerprints, approve pairing locally, then add libraries in the Portal. Folder
selection requires a native confirmation on the Agent workstation. Local status
also requires a short-lived Portal authorization. Device identity remains separate
from browser login sessions.

See [native installation and migration](docs/native-windows.md),
[encrypted media and local authorization](docs/encrypted-portal-agent.md), and
[acceptance status](docs/portal-tray-acceptance.md).

The **currently published** Owner-only unsigned development installer remains
`0.1.0-development.4`. The development.5 source/candidate must pass acceptance
before replacing it. No public GitHub Release is created. At this documentation
checkpoint the new Portal changes have not been deployed and a new installer has
not been published; real SMTP delivery and current Owner authentication remain
blocked. Historical validation records do not establish acceptance for this change.

## Preserved Docker deployment

Docker Desktop or Docker Engine with Compose v2 is required only for this path.
Bootstrap preserves existing configuration, validates bind-mounted paths and starts
the local stack. Source media is mounted read-only.

```powershell
.\scripts\bootstrap.ps1
```

```sh
sh scripts/bootstrap.sh --media-path /srv/media
```

The legacy local interface defaults to [http://localhost:8080](http://localhost:8080).
Its local household setup/media-root options belong to Docker, not the new Windows
installer. Its optional isolated connector supports the earlier diagnostic protocol;
the new native Agent owns the Portal media workflow. Existing Docker configuration,
volumes, users, media and services are preserved.

```sh
docker compose ps
docker compose down
docker compose up --detach
sh scripts/backup.sh
sh scripts/upgrade.sh
```

Stopping Compose without `--volumes` preserves application state. Backups include
sensitive local database/configuration and optional artwork; protect them and
validate a restore before relying on them. See [Docker installation](docs/install-linux.md)
and [backup and restore](docs/backup-and-restore.md).

## Media and privacy

The implemented Portal interface includes paginated movies/TV/search, Continue
Watching, details/artwork, audio/text-subtitle selection, progress and next episode.
The Agent serves bounded direct-play ranges or locally produced HLS remux/transcode
output. Opaque random IDs are Agent-scoped and still require authorization. Library
and media permissions are checked locally; accepting an invitation alone cannot
reveal a library. Real GPU support must be established by an actual test encode.

PostgreSQL holds account/control records, not a synchronized media catalog. Media
names, paths, searches, artwork and payloads are encrypted before the relay. They
may be displayed temporarily in the authorized browser. No central media cache,
metadata-provider calls, analytics, remote fonts or cloud transcoding are added.
Source media is application-read-only on Windows and mounted read-only in Docker.

## Development and documentation

Branding/base version lives in `config/product.json`; the Windows component lock
also records the development package number. Do not commit runtime secrets,
databases, media, artwork, logs, backups or installer binaries.

- [Architecture and deployment boundaries](docs/architecture.md)
- [Current acceptance checklist](docs/portal-tray-acceptance.md)
- [Windows configuration, migration and recovery](docs/native-windows.md)
- [Encrypted Portal media protocol](docs/encrypted-portal-agent.md)
- [Privacy and outbound policy](docs/privacy-and-outbound.md)
- [Local development](docs/local-development.md)
- [Playback and transcoding](docs/transcoding-settings.md)
- [Current limitations](docs/not-implemented.md)
- [Historical Windows service validation](docs/native-windows-validation.md)

The Portal's latest isolated Linux backend run passed 152 tests. Native tray,
installer, browser/media and production acceptance are tracked separately; passing
API tests does not certify a Windows installer or real email delivery.
