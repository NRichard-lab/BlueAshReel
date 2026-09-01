# Privacy and outbound connections

BlueReel's phase-one runtime is designed to function with no Internet route. Media inspection, filename parsing, artwork discovery, account data, audit data, and job processing remain on the host.

## Data that remains local

- source media bytes, filenames, and full paths;
- libraries, media records, searches, and availability history;
- accounts, password hashes, browser sessions, and audit events;
- watch-related schema reserved for later phases;
- FFprobe output and derived stream information;
- local artwork and application-controlled cache; and
- configuration, logs, and backups.

Source media is mounted read-only and is never renamed, moved, deleted, or uploaded. Temporary analysis files use the configured local temp directory. The application includes no analytics, advertisements, tracking pixels, external crash service, remote font, JavaScript CDN, cloud account, or phase-one metadata-provider client.

## Defense in depth

Outbound permission has three separate layers:

1. The Compose `private` network is `internal`, so runtime containers have no ordinary external route.
2. `OUTBOUND_INTEGRATIONS_ENABLED` defaults to `false` and gates every known integration category.
3. Each known category (`metadata`, `artwork`, `portal`, and `telemetry`) also requires an explicit Owner-controlled database setting.

An unknown integration name is denied. Passing only one layer never grants access. In this phase no integration implementation should make an external call even if the flags are changed. Build-time image/dependency downloads are distinct from runtime and require Internet only while installing or building.

Future work that needs outbound access must add a reviewed network override, declare destinations and data fields, implement timeouts/auditing/redaction, expose an Owner control, and update this document. Do not weaken the internal network globally for an unrelated troubleshooting issue.

## Logs and health endpoints

Application logs are JSON and redact keys associated with passwords, secrets, tokens, cookies, sessions, authorization, paths, filenames, titles, and database URLs. Caddy access logging is not enabled, reducing accidental query/cookie capture. Docker retains at most three 10 MiB JSON log files for each service.

Avoid adding raw exception payloads, FFprobe commands, request bodies, media names, or filesystem paths to routine logs. Debug logging can still reveal implementation detail and should be enabled only briefly on a trusted host.

Liveness, readiness, and version endpoints reveal only service/check status and configured product version. They must not grow into diagnostic dumps containing host paths, users, libraries, media, tokens, or environment values.

## Browser privacy

Authentication uses an HTTP-only cookie rather than browser local storage. UI assets are packaged locally. The proxy content-security policy limits scripts, styles, connections, fonts, images, and media to the same origin or narrowly required local data/blob forms; it also sets a no-referrer policy, denies framing, prevents MIME sniffing, isolates the top-level browsing context, and disables browser camera, location, and microphone features.

The backend does not trust `Forwarded` or `X-Forwarded-For` values. It treats the internal reverse proxy as the host identity for host-scoped setup/login throttles, so a caller cannot select a new rate-limit bucket by forging request headers. This deliberately shares that defensive bucket across the local household deployment.

The default endpoint is HTTP on loopback. Private-LAN HTTP can be observed by other parties with network-level access and is intended only for a trusted home network. Public/remote access and managed TLS are not implemented.

## Backups

Backups intentionally contain sensitive local configuration and the application secret so sessions and protected state can be recovered consistently. They may also contain media names and account/audit data through SQLite. Store archives on trusted encrypted storage, restrict file permissions, do not sync them to a cloud service by default, and delete them according to household policy.
