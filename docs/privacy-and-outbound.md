# Privacy and outbound connections

## Optional control-plane connection

The separate [remote connector](remote-access.md) is disabled by default and needs
explicit local Owner pairing. It shares only its opaque identity, public key,
Owner-selected friendly name, account association, version, OS category, coarse
status and encrypted diagnostics with the separate Blue Ash Reel portal. Public
account records are separate from the household accounts described below.

The connector cannot read the media database or media roots. Docker uses a separate
image, volumes and network; Windows uses a distinct service identity and AppContainer.
Its permitted outbound destination is `blueashreel.com` on TCP 443. The media
services retain their existing outbound restrictions and work when the connector
or Internet is unavailable. No media browsing, playback or automatic router changes
are enabled. The public site's ability to serve browser JavaScript remains an
explicit trust boundary; encrypted relay transport does not remove that risk.

## Phase 2 streaming boundary

Media, subtitles, artwork, searches, codec details and viewing records stay local.
HLS and subtitle conversion use argument arrays with only file/pipe input protocols
and approved local demuxers. No external authentication/player/CDN is added. hls.js
is a pinned bundled dependency, not a runtime download. Capability reports contain
only minimal codec/height hints, not user-agent strings or device identifiers.

Every segment/file/subtitle request rechecks the originating login and assigned
enabled library. Routine request logs use route templates, never titles, IDs from
URLs, search text, usernames or paths. FFmpeg stderr is discarded. Active Streams
shows household identities only to an authenticated Owner. Health exposes counters,
encoder names and generic failure states, not media or server paths.

History is stored in SQLite. Users may delete their own; deleting it invalidates
active streams so old checkpoints cannot recreate it. Owner retention (365 days
default, zero indefinite) runs locally at startup and once per minute. Existing
backups must be expired separately. Live privacy badges refresh enforcement state.
Native tests verify application-level no-outbound behavior. Actual per-container
DNS, direct-IP HTTP/HTTPS and internal-connectivity probes are recorded separately
in [container validation](container-validation.md).

Blue Ash Reel's phase-one runtime is designed to function with no Internet route. Media inspection, filename parsing, artwork discovery, account data, audit data, and job processing remain on the host.

## Data that remains local

- source media bytes, filenames, and full paths;
- libraries, media records, searches, and availability history;
- household accounts, password hashes, browser sessions, and audit events;
- watch history and playback progress;
- FFprobe output and derived stream information;
- local artwork and application-controlled cache; and
- configuration, logs, and backups.

Source media is mounted read-only and is never renamed, moved, deleted, or uploaded. Temporary analysis files use the configured local temp directory. The application includes no analytics, advertisements, tracking pixels, external crash service, remote font, JavaScript CDN, or metadata-provider client. The optional portal uses its own public account system.

## Defense in depth

Outbound permission has three separate layers:

1. Backend, worker and frontend have only an internal network. Caddy's dedicated
   ingress namespace has default-deny outbound rules with only two internal
   destination/port exceptions, established replies and a loopback health check.
   External DNS, including Docker resolver forwarding, is blocked after startup.
2. `OUTBOUND_INTEGRATIONS_ENABLED` defaults to `false` and gates every known integration category.
3. Each known category (`metadata`, `artwork`, `portal`, and `telemetry`) also requires an explicit Owner-controlled database setting.

An unknown integration name is denied. Passing only one layer never grants access. These flags do not enable outbound access in the media processes; the optional connector has a separate deployment and Owner consent described above. Build-time image/dependency downloads are distinct from runtime and require Internet only while installing or building.

Future work that needs outbound access must add a reviewed network override, declare destinations and data fields, implement timeouts/auditing/redaction, expose an Owner control, and update this document. Do not weaken the internal network globally for an unrelated troubleshooting issue.

## Logs and health endpoints

Application logs are JSON and redact keys associated with passwords, secrets,
tokens, cookies, sessions, authorization, paths, filenames, titles and database
URLs. Caddy access logging is not enabled; its runtime formatter removes request
objects, file/storage fields and error traces. Docker retains at most three 10 MiB
JSON log files for each service. Startup and controlled-failure logs are included
in the container privacy audit, not just successful access requests.

Avoid adding raw exception payloads, FFprobe commands, request bodies, media names, or filesystem paths to routine logs. Debug logging can still reveal implementation detail and should be enabled only briefly on a trusted host.

Liveness, readiness, and version endpoints reveal only service/check status and configured product version. They must not grow into diagnostic dumps containing host paths, users, libraries, media, tokens, or environment values.

## Browser privacy

Authentication uses an HTTP-only cookie rather than browser local storage. UI assets are packaged locally. The proxy content-security policy limits scripts, styles, connections, fonts, images, and media to the same origin or narrowly required local data/blob forms; it also sets a no-referrer policy, denies framing, prevents MIME sniffing, isolates the top-level browsing context, and disables browser camera, location, and microphone features.

The backend does not trust `Forwarded` or `X-Forwarded-For` values. It treats the internal reverse proxy as the host identity for host-scoped setup/login throttles, so a caller cannot select a new rate-limit bucket by forging request headers. This deliberately shares that defensive bucket across the local household deployment.

The default local endpoint is HTTP on loopback. Private-LAN HTTP can be observed by other parties with network-level access and is intended only for a trusted home network. The separate public portal requires HTTPS; the connector uses outbound TLS and application-layer encrypted diagnostics. It does not expose this local endpoint.

## Backups

Backups intentionally contain sensitive local configuration and the application secret so sessions and protected state can be recovered consistently. They may also contain media names and account/audit data through SQLite. Store archives on trusted encrypted storage, restrict file permissions, do not sync them to a cloud service by default, and delete them according to household policy.
