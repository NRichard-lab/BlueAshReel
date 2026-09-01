# Configuration reference

Deployment settings live in the uncommitted `.env` file. Product identity lives only in `config/product.json`. Application settings that need an audit trail are stored in SQLite and changed through Owner-authorized APIs/UI.

Run a bootstrap script to generate `.env`; do not copy the placeholder secret into a live installation.

## Product identity

| Field | Purpose |
| --- | --- |
| `name` | Displayed product name |
| `subtitle` | Displayed product subtitle |
| `version` | Application version returned by the version API |
| `api_prefix` | Versioned REST base path; fixed at `/api/v1` for phase-one compatibility |

The backend reads this file at runtime from the read-only mount, while the frontend imports it at image build time. After changing product identity, run `docker compose up --detach --build backend worker frontend` so the browser bundle and backend agree; a container restart alone does not rebuild frontend branding. Renaming should be done here and through components that consume this configuration, never by a repository-wide hard-coded string replacement. Keep `api_prefix` at `/api/v1` throughout phase one because proxy health and compatibility contracts use the required versioned path.

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
| `BACKUP_PATH` | `./backups` | Validated backup archive destination |
| `PUID` / `PGID` | `1000` | Numeric identity used by app containers for bind-mount ownership |

Relative host paths resolve from the repository directory. State paths must be writable, distinct, narrower than a filesystem/repository root, and isolated from `MEDIA_PATH`. On Windows, use forward slashes or a quoted path when editing manually; the PowerShell bootstrap normalizes an explicit media path.

Inside containers, storage paths are fixed (`/data`, `/database`, `/artwork`, `/tmp/app`, `/media`). Library paths are container paths below `/media`, not host paths.

## Security and runtime settings

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

The Compose file sets `DATABASE_URL=sqlite:////database/app.db`, `APP_DATA_DIR=/data`, `ARTWORK_DIR=/artwork`, `TEMP_DIR=/tmp/app`, `MEDIA_ROOTS=/media`, `FFMPEG_PATH=ffmpeg`, `FFPROBE_PATH=ffprobe`, and `PRODUCT_CONFIG_FILE=/app/config/product.json`. Changing these container-internal values is an advanced topology change and normally unnecessary.

The backend also supports comma-separated `SCAN_EXTENSIONS` and `IGNORED_DIRECTORIES` values. Defaults cover common video suffixes and operating-system/sample directories. Extensions must include or normalize to a leading dot. Changes affect later scans; they do not mutate source media.

## Multiple media roots

The reference deployment mounts one common read-only root. Register any number of library subdirectories below `/media`. Hosts with media on unrelated filesystems can create a reviewed Compose override that adds read-only mounts such as `/media/disk2` and sets Linux-container `MEDIA_ROOTS` to an `os.pathsep` (`:`)-separated list. Never add a writable source-media mount.

## Binding and cookies

Plain HTTP on loopback/private LAN is the phase-one deployment. A future TLS reverse proxy must be explicitly designed; only then set `SESSION_COOKIE_SECURE=true`. Setting it on current plain HTTP prevents the browser from returning the authentication cookie.

Do not set `BIND_ADDRESS=0.0.0.0`, publish backend/frontend ports, or attach runtime services to an egress-capable network as a convenience workaround.
