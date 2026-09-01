# Phase 2 Docker validation — 2026-09-01

Starting revision: `a0d6084defd6cf6cbb67cf7baade8bb439df3e9e`.
This closes the Docker gap in the [native Phase 2 record](phase2-validation.md).
Tests used generated FFmpeg test patterns/sine waves, disposable accounts, and a
separate Compose project `bluereel-validation-20260901-77b3` on loopback port 18080.
No real media, existing database, Blue Ash service, public listener, host firewall,
unrelated Docker setting or resource was changed.

## Environment and build

- Windows, Docker Desktop 4.89.0, Linux Engine 29.7.2, Compose 5.5.0, WSL2
  `docker-desktop`, kernel 6.18.33.2. Minimal restricted container execution passed.
- Host: 16 logical CPUs, about 62.9 GiB RAM and 1.46 TiB free on C: at preflight.
  Docker exposed 16 CPUs and approximately 30.8 GiB RAM.
- Built backend/worker (`bluereel-backend:local`), production frontend, and custom
  Caddy (`bluereel-proxy:local`). Worker and backup intentionally reuse backend.
  Python 3.12.14 and FFmpeg/FFprobe 5.1.9 were available inside Linux containers.
- Four services healthy; backend applies migrations before worker/proxy startup.
  SQLite integrity check passed and schema revision was `2b0100000001`.
- Actual runtime identities, capabilities, no-new-privileges, read-only roots,
  mounts, cgroup CPU/memory/PID ceilings, bounded logs and restart policies checked.
  Source-media write attempts by both backend and worker failed with EROFS.

## Defects found and corrected

1. Native `backend/data` and environment/runtime variants could enter the Docker
   build context. Expanded `.dockerignore`; required tests, fixtures, migrations,
   examples, configuration, lockfiles and scripts remain source-controlled.
2. Alpine/musl could not execute the frontend's workerd binary. Both frontend
   stages now use Debian/glibc. Bounded scratch tmpfs mounts preserve the read-only
   root; telemetry and Miniflare's remote request-metadata lookup are disabled.
3. Caddy's packaged file capability conflicted with the empty capability bounding
   set. Removed that unnecessary capability at build time; port 8080 is unprivileged.
4. Docker's internal-only network did not provide the published loopback listener.
   A dedicated ingress bridge now belongs only to the proxy. Its fail-closed
   initializer installs namespace-local deny-by-default IPv4/IPv6 rules, resolves
   two internal upstreams, blocks runtime DNS, then drops root and every capability
   before starting Tini/Caddy. No host firewall changes or privileged container.
   A trial separate guard had a stale-namespace restart failure and was replaced:
   listener, firewall and Caddy now have one container lifecycle.
5. Added enforced CPU, memory and PID ceilings, generous bounded shutdown grace,
   and privacy filters for Caddy error/startup fields that could contain requests
   or paths. Access logs remain disabled.
6. Production client navigation failed because Rolldown's redundant-chunk-load
   optimization emitted an incorrect cross-chunk namespace. Disabled only that
   client optimization; an executable minified/unminified bundler regression tests
   the actual build options. No framework or dependency migration.
7. Chromium retained a stale text cue after replacing an HLS representation.
   Explicitly disable old text tracks before detachment, conditionally render the
   selected track, and ignore stale load/error callbacks. Unit and visual retests passed.
8. Backup partial ZIPs now use exclusive creation and mode 0600 from the first
   byte, not only after completion. POSIX permissions and no-overwrite regressions
   pass. Windows bind-mount confidentiality still depends on host NTFS ACLs.

## Same-origin functional matrix

All browser traffic used Caddy, not direct backend/frontend ports. Real Chromium
rendered decoded 640×360 frames with advancing time; HTTP success alone was not
treated as playback proof.

| Area | Observed result |
| --- | --- |
| Setup and sessions | First Owner setup, repeat rejection (409), login/logout and persistent sessions passed |
| Administration | Dashboard, Owner-created Viewer with Movies/TV assignments, privacy and health screens passed |
| Libraries and scan | Invalid `/etc` path rejected; read-only Movies/TV/Private roots; 12 files analyzed by FFprobe, no scan errors |
| Browse and navigation | Movies, TV show/season/three episodes, search and production client links passed |
| Direct Play and ranges | H.264/AAC MP4 decoded; HEAD 200, valid/suffix ranges 206, invalid ranges 416; seeking passed |
| Remux | H.264/AAC MKV decoded through authenticated local HLS |
| Software conversion | MPEG-4 video → H.264 and AC3 → AAC decoded; selected output codecs independently probed |
| Audio | Second 880 Hz track selected; switching to first 440 Hz track preserved the source position |
| Subtitles | Embedded SRT and mov_text converted locally to authorized WebVTT; visible first cue, correctly retimed seek cue without overlap, and Off cleared all tracks |
| Progress | Pause/seek checkpoint, logout/login resume, independent Viewer progress; Owner retained 1:06 while Viewer had about 16 seconds |
| Next episode | Episode 1 finished and automatically navigated to/decoded Episode 2 |
| Active Streams | Method, encoder, bitrate, speed and storage visible; Stop Stream confirmed termination and cleanup |
| Disconnect/cleanup | Idle session expired; ended endpoints returned 410; no FFmpeg/supervisor or representation directories remained (only manager lock) |
| Source loss | Renamed only the generated source; player stopped with an error; restoring it recovered decoded direct playback |
| Authorization | Anonymous requests 401, missing CSRF 403, Viewer administration 403; unassigned details/playback and other user's stream 404 |
| Browser permissions | Viewer lacked admin navigation/Private media; direct `/admin` displayed access denial |
| Media/HLS/subtitles | Authorized requests succeeded; anonymous/cross-user access denied; stopped streams invalidated |
| Restart persistence | Full restart, full container recreation, and proxy process termination/restart-policy recovery all returned healthy same-origin HTTP 200 |

After recreation, database integrity remained `ok`: two disposable users, three
libraries, 12 media files and 11 progress rows. The Owner's saved 1:06 resume point
was visibly retained. Proxy restart count increased after internal PID 1 termination;
actual Tini/Caddy returned as UID 1000 with CapPrm/Eff/Bnd/Amb all zero and NNP=1.
Starting the proxy without required firewall authority or without upstream
resolution failed before serving HTTP.

## Runtime network and privacy evidence

Each application container was tested individually after the final proxy fix.
These were controlled network probes, not a host-wide packet capture.

| Container | Required local communication | Unapproved outbound attempts |
| --- | --- | --- |
| Backend | API health 200 | External names failed resolution; UDP/TCP DNS, direct-IP HTTP/HTTPS and IPv6 unreachable |
| Worker | Backend health 200 | Same DNS, direct-IP HTTP/HTTPS and IPv6 denials |
| Frontend | Local UI and backend health 200 | Same denials; UDP DNS refused/unreachable |
| Caddy | Same-origin `/login` and API health 200 | UID-1000/capability-free DNS to Docker resolver and 8.8.8.8 denied; named/direct-IP HTTP/HTTPS blocked; IPv6 unreachable |

Probes included `example.com`, `api.cloudflare.com`, external DNS at 8.8.8.8 and
direct TCP HTTP/HTTPS to 1.1.1.1. Caddy OUTPUT/FORWARD defaults were DROP for both
address families. New outbound TCP was limited to localhost:8080, backend:8000
and frontend:3000; established TCP replies were allowed. Runtime DNS was blocked.
Docker-admin-controlled privileged exec is outside the application threat model.

Metadata/artwork/subtitle providers, analytics, CDNs and cloud services are not
used by the runtime. Bundled player/assets and media processing stay local.
Integration enable attempts returned 409/422 and left persisted gates disabled.
Health responses and routine/startup/failure logs were checked against disposable
usernames, media names/paths, credentials and the generated secret: no disclosure.
Image configuration/history and exported application trees were checked for
embedded secrets, databases, media, native runtime data and credential directories.
No prohibited content was found. Build-time dependency downloads are separate from
the runtime no-egress guarantee.

## Performance, limits and recovery

These small-fixture observations are not household/4K throughput guarantees:

- Browser-reported session-to-playback startup: Direct Play 117–137 ms, remux
  approximately 329 ms, software video approximately 440 ms.
- During an actual running FFmpeg conversion, Home took 40.8 ms and dashboard
  121 ms (161.8 ms combined); FFmpeg CPU time advanced during the requests.
- One conversion reported libx264 about 28.5×, 4,839 kbps and 51.9 MiB output.
  FFmpeg RSS observed near 78 MiB. CPU/memory usage was visible via Docker stats.
- Two conversion reservations admitted; third returned 429. Per-user stream
  ceiling likewise rejected the third session. Temp quota/reservation, cancellation,
  killed-parent cleanup, expiry and shutdown behavior passed the Linux tests.
- Enforced ceilings: backend 4 CPUs/2 GiB, worker 1/1 GiB, frontend 2/1 GiB,
  proxy 1/256 MiB. Default conversion storage budget remains 4 GiB and two processes
  with two threads each. No OOM event observed.
- Representative idle ranges: backend 69–128 MiB, worker 43–46 MiB, frontend
  247–285 MiB, proxy 14–24 MiB. Values vary with cache warm-up and workload.
- Actual container test encodes found **QSV, NVENC and AMF unavailable**.
  `/dev/dri`, `/dev/nvidia0` and `/dev/dxg` were absent. Software remains selected;
  the previous native Windows AMF probe is not Docker acceleration evidence.

The Compose backup tool created an online archive, and the network-disabled dry
restore validator checked its manifest, safe paths, integrity and schema. Its
database contained users, library assignments, media catalog and progress; the
configuration secret was included without printing it. Media and conversions were
excluded. No restore was performed over a live database.

## Automated checks

- Linux backend: **104 passed**, no skips, **86% statement coverage**.
- Linux operations/scripts: **26 passed**; 63% coverage over backup, backup-format
  and restore-validation modules (not shell/bootstrap subprocess coverage).
- Frontend: **11 passed**, including client-bundler and text-track regressions.
- Ruff, strict mypy (33 application modules), TypeScript and frontend lint passed.
- Production Docker frontend build, all service builds, Compose resolution,
  migration upgrade/downgrade/re-upgrade and populated-schema preservation passed.
- Runtime health, authorization, no-egress and Chromium playback checks above are
  additional manual integration evidence, not counted as unit-test passes.

Non-blocking warnings: existing Starlette/httpx TestClient deprecation and the
bundled HLS chunk exceeding 500 kB. No skipped codec tests or hidden build failures.

## Cleanup and local handoff

Validation resources were scoped by exact project labels and a manifest under
`runtime/docker-validation-20260901-77b3`. The disposable databases, credentials,
12 synthetic media files, subtitles, backup and scratch output are not deliverables.
Reusable image/build caches are retained; earlier native runtime data is preserved.

The normal project is `bluereel`, bound only to `127.0.0.1:8080`. Bootstrap generates
a fresh ignored `.env`; permanent Owner credentials must be created by the user
at [localhost:8080](http://localhost:8080). Default persistent locations beneath the
repository are `runtime/database`, `runtime/data`, `runtime/artwork`, `runtime/temp`
and `backups`; `media` starts empty and is mounted read-only. Do not delete these
directories when stopping containers.

Docker Desktop auto-start was disabled on this workstation. After a Windows reboot,
start Docker Desktop, wait for its Linux engine, then run from the repository:

```powershell
.\scripts\bootstrap.ps1 -NoBuild
```

For an upgrade/rebuild use the whole project (`docker compose up -d --build`) so
the proxy refreshes upstream addresses. No Windows reboot, real-media library,
Safari/Firefox, long-duration load, GPU passthrough or public deployment is claimed
by this validation. Those remain separate operator/product scope, not blockers to
local first-run testing.
