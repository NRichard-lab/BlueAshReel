# Configuration reference

## Phase 2 playback settings

Compose forwards the following host .env values explicitly. Native execution uses
the same names through AppConfig. Owner playback-policy settings override the
history defaults in SQLite. Owner Playback & Transcoding settings override the
conversion defaults for new streams; existing streams retain their snapshots.

| Variable | Default | Bounds / meaning |
| --- | --- | --- |
| PLAYBACK_SESSION_TIMEOUT_SECONDS | 90 | 30–600 seconds without a checkpoint |
| PLAYBACK_MAX_STREAMS | 8 | 1–32 household streams |
| PLAYBACK_STREAMS_PER_USER | 2 | 1–8 per user |
| PLAYBACK_WATCHED_THRESHOLD | 90 | 50–100 percent |
| PLAYBACK_MINIMUM_WATCH_SECONDS | 30 | 5–300; capped at half the clip duration |
| PLAYBACK_HISTORY_DAYS | 365 | 0–3650; zero keeps indefinitely |
| TRANSCODE_MAX_PROCESSES | 2 | 1–8 conversion/storage reservations |
| TRANSCODE_THREADS | 2 | 1–16 decoder/encoder threads per job |
| TRANSCODE_MAX_HEIGHT | 2160 | 240–4320 pixels |
| TRANSCODE_MAX_BITRATE_KBPS | 12000 | 500–50000 kbps |
| TRANSCODE_MAX_STORAGE_MB | 4096 | 64–1048576 MiB, split among slots |
| TRANSCODE_STARTUP_TIMEOUT_SECONDS | 45 | 5–180 seconds |
| TRANSCODE_MODE | automatic | automatic/hardware_preferred/software_only/direct_only/hardware_required |
| TRANSCODE_CPU_PRESET | veryfast | ultrafast/superfast/veryfast/faster/fast/medium/slow |
| TRANSCODE_ALLOW_4K | false | Explicit permission to transcode 4K sources; direct/remux remain available |
| TRANSCODE_DEVICE | auto | auto or adapter index 0–99; actual test required for this device |
| TRANSCODE_HARDWARE | software | software/qsv/nvenc/amf; successful test required |

See [Playback & Transcoding policy](transcoding-settings.md) and
[temporary sizing and hardware](transcoding.md). Keep one API process per
database/temp root. A second manager fails its ownership lock instead of racing
cleanup. Base Compose exposes no GPU devices, so its default Automatic policy
uses software when conversion is necessary. Native Windows can use a verified
GPU under the same Automatic policy. Fresh native installations set more
conservative conversion ceilings of 1080p and 8 Mbps; the table above records
the shared configuration/Compose defaults.

Deployment settings live in the uncommitted `.env` file. Product identity lives only in `config/product.json`. Application settings that need an audit trail are stored in SQLite and changed through Owner-authorized APIs/UI.

Run a bootstrap script to generate `.env`; do not copy the placeholder secret into a live installation.

## Product identity

| Field | Purpose |
| --- | --- |
| `name` | Displayed product name |
| `subtitle` | Displayed product subtitle |
| `version` | Application version returned by the version API |
| `api_prefix` | Versioned REST base path; fixed at `/api/v1` for phase-one compatibility |

The backend reads this file at runtime from the read-only mount, while the frontend imports it at image build time. After changing product identity, run `docker compose up --detach --build --force-recreate backend worker frontend proxy` so the browser bundle and backend agree and Caddy refreshes its private address allowlist; a container restart alone does not rebuild frontend branding. Renaming should be done here and through components that consume this configuration, never by a repository-wide hard-coded string replacement. Keep `api_prefix` at `/api/v1` throughout phase one because proxy health and compatibility contracts use the required versioned path.

Backup archive prefixes also derive from `name` after conversion to a safe lowercase filesystem slug. The backup manifest format and temporary-directory labels are deliberately product-neutral so identity changes do not alter the restore contract.

The Compose project name (`bluereel`), local backend image tag (`bluereel-backend:local`), and container entrypoint filename are intentionally stable deployment identifiers, not user-facing product identity. They remain fixed when `config/product.json` changes so Compose continues to address the same local stack and reuse the expected local image. Changing those identifiers is an operator migration, not a branding edit.

## Host and storage settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface for Caddy; bootstrap accepts loopback or RFC1918 only |
| `HTTP_PORT` | `8080` | Local published HTTP port |
| `DATA_PATH` | `./runtime/data` | Persistent application-controlled files |
| `DATABASE_PATH` | `./runtime/database` | Directory containing `app.db`, WAL, and shared-memory files |
| `ARTWORK_PATH` | `./runtime/artwork` | Persistent local artwork/cache |
| `TEMP_PATH` | `./runtime/temp` | Disposable local analysis/transcode workspace |
| `MEDIA_PATH` | `./media` | Source media root, mounted read-only at `/media` |
| `MEDIA_ROOTS` | `/media` | Bootstrap-managed `:`-separated container paths for all approved roots |
| `MEDIA_ROOT_DEFINITIONS` | primary `Media` root | Bootstrap-managed JSON containing stable IDs, display names, and container paths only |
| `BACKUP_PATH` | `./backups` | Validated backup archive destination |
| `PUID` / `PGID` | `1000` | Numeric identity used by app containers for bind-mount ownership |

Relative host paths resolve from the repository directory. State paths must be writable, distinct, narrower than a filesystem/repository root, and isolated from `MEDIA_PATH`. On Windows, use forward slashes or a quoted path when editing manually; the PowerShell bootstrap normalizes an explicit media path.

Inside containers, writable storage paths are fixed (`/data`, `/database`,
`/artwork`, `/tmp/app`). The primary source root remains `/media`; additional
roots use stable `/media-roots/<id>` targets. The normal interface uses opaque
folder selections and friendly names rather than host or container paths.

## Security and runtime settings

### Container resource ceilings

Compose enforces these CPU, memory and PID limits (not just deployment hints):

| Service | CPU variable / default | Memory variable / default | PIDs |
| --- | --- | --- | --- |
| Backend | `BACKEND_CPUS=4.0` | `BACKEND_MEMORY=2g` | 256 |
| Worker | `WORKER_CPUS=1.0` | `WORKER_MEMORY=1g` | 128 |
| Frontend | `FRONTEND_CPUS=2.0` | `FRONTEND_MEMORY=1g` | 256 |
| Caddy | `PROXY_CPUS=1.0` | `PROXY_MEMORY=256m` | 64 |
| Backup tool | 2.0 | 1g | 128 |

Playback's process, thread and temporary-storage limits remain separate. Inspect
actual use with `docker compose stats --no-stream`. Backend/worker shutdown grace
periods are 60/90 seconds so supervised media processes can terminate cleanly.

The frontend uses Debian/glibc because workerd cannot run on Alpine/musl. Its
small Wrangler scratch directories are size-limited tmpfs mounts; the root
filesystem remains read-only. `CLOUDFLARE_CF_FETCH_ENABLED=false` prevents
Miniflare's default remote request-metadata lookup, and Wrangler telemetry is off.

The proxy image starts a short root-only firewall initializer with NET_ADMIN,
SETUID, SETGID and SETPCAP in its own namespace. It then drops all capabilities,
groups and root identity before executing Tini/Caddy. Docker administrators retain
host-level authority; the application process cannot regain these privileges.

| Variable | Default | Constraint / effect |
| --- | --- | --- |
| `APP_SECRET_KEY` | generated | At least 64 random characters in bootstrap output; never commit or log |
| `OUTBOUND_INTEGRATIONS_ENABLED` | `false` | Deployment-wide gate; per-integration Owner approval is also required |
| `SESSION_TTL_HOURS` | `24` | Server session lifetime, 1–720 hours |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` only behind a deliberately configured HTTPS endpoint |
| `LOG_LEVEL` | `INFO` | Structured application log threshold |
| `WORKER_POLL_INTERVAL` | `1.0` | Durable queue polling delay, 0.1–60 seconds |
| `SCAN_BATCH_SIZE` | `50` | Database write batch, 1–1000 media records |
| `FFPROBE_TIMEOUT_SECONDS` | `45` | Per-file FFprobe limit, 1–600 seconds |
| `JOB_STALE_MINUTES` | `15` | Age used to recover interrupted running jobs, 1–1440 minutes |
| `BACKUP_RETENTION_DAYS` | `30` | Valid backup files older than this are removed after a new validated backup |

Bootstrap rejects any equal or ancestor/descendant overlap among data, database, artwork, temporary, and backup directories. It also rejects overlap between those writable paths and source media. This prevents a live database, WAL file, temporary work, or backup archive from being copied recursively through another configured directory.

The Compose file sets `DATABASE_URL=sqlite:////database/app.db`, `APP_DATA_DIR=/data`, `ARTWORK_DIR=/artwork`, `TEMP_DIR=/tmp/app`, `MEDIA_ROOTS=/media` by default, `FFMPEG_PATH=ffmpeg`, `FFPROBE_PATH=ffprobe`, and `PRODUCT_CONFIG_FILE=/app/config/product.json`. Bootstrap manages approved-root overrides; changing these container-internal values by hand is an advanced topology change and normally unnecessary.

The backend also supports comma-separated `SCAN_EXTENSIONS` and `IGNORED_DIRECTORIES` values. Defaults cover common video suffixes and operating-system/sample directories. Extensions must include or normalize to a leading dot. Changes affect later scans; they do not mutate source media.

## Multiple media roots

Bootstrap supports multiple unrelated host directories without granting access to
their parent filesystems. It keeps the first root at `/media`, assigns stable IDs
and `/media-roots/<id>` targets to later roots, writes host-specific details only
to ignored `.bluereel/media-roots.tsv` and `compose.override.yml`, and mounts the
same roots read-only into backend and worker. Frontend and Caddy receive no media
mounts. `MEDIA_ROOT_DEFINITIONS` contains only IDs, friendly names, and internal
paths; it never contains host paths.

Use `-ConfigureMediaRoots` on Windows or `--configure-media-roots` on Linux.
Never hand a web process Docker socket access or add writable source-media
mounts. Root changes become effective only after controlled container recreation.
Spaces, `#`, and apostrophes are safely quoted in generated local configuration;
bootstrap rejects `$` in a root path or display name because Compose treats it as
interpolation syntax.

## Binding and cookies

Plain HTTP on loopback/private LAN is the phase-one deployment. A future TLS reverse proxy must be explicitly designed; only then set `SESSION_COOKIE_SECURE=true`. Setting it on current plain HTTP prevents the browser from returning the authentication cookie.

Do not set `BIND_ADDRESS=0.0.0.0`, publish backend/frontend ports, or attach runtime services to an egress-capable network as a convenience workaround.
