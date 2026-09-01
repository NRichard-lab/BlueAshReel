# Phase 2 validation

This is the original **native Windows** validation record. The subsequent
[Docker validation](container-validation.md) closes its container-validation gap
and records container-specific fixes, current test counts and the local handoff.

Validated on Windows with Python 3.12.13, local FFmpeg 9.0.1, SQLite WAL, and the
desktop app's Chromium browser. The frontend and API were tested on the same
`localhost:3000` origin. Only synthetic test-pattern/sine-wave media and disposable
local accounts/database were used. All generated media, downloaded binaries,
databases, caches and conversion output remain ignored under `runtime/`.

## Automated checks

- Backend: **99 passed**, no skipped FFmpeg tests; **86% statement coverage**.
- Bootstrap/backup/restore scripts: **25 passed**.
- Frontend: **9 passed**; accessibility-aware lint and TypeScript checks passed.
- Python Ruff and strict mypy passed (33 application modules).
- Production frontend build passed. Its bundled HLS player produces a >500 kB
  chunk warning; this is a size warning, not a failed build or runtime CDN.
- Clean Alembic upgrade/downgrade/re-upgrade and populated Phase 1 preservation
  passed. Final schema head is `2b0100000001`.
- Compose YAML structure checks passed: internal network, read-only media, loopback
  default, service hardening, health checks and bounded log retention preserved.

Coverage is in-process Python statement coverage, not branch coverage or complete
subprocess coverage. The worker entry loop is not measured; scanner/job behavior is
tested directly. A Starlette/httpx TestClient deprecation warning remains.

Tests include permissions, revoked/disabled sessions, last-Owner protection,
independent progress/history deletion, FTS maintenance, 20,000 records, variant
pagination, byte ranges/HEAD/cancellation, unsafe IDs/paths and simulated symlink
rejection, subtitle authorization/conversion, HLS cross-user denial, natural full
output completion, software video/audio conversion, 720p-to-480p reduction, quota
reservation, running-job cancellation, killed-parent reaping, ownership-safe cleanup,
failed-stop visibility, pending-shutdown admission and failed-stop sweep continuation.
Runtime network calls are blocked in direct/catalog/subtitle/HLS integration tests;
FFmpeg receives only local file/pipe protocols and explicit argument arrays.

## Real-browser checks

| Area | Observed result |
| --- | --- |
| Owner login and household creation | Owner created a Viewer and assigned Movies/TV, excluding Private |
| Home/Movies/Search | Six rails, grid/list controls, title/year/episode search, available/watch filters |
| TV hierarchy/details | Show, season selector, numbered episodes, local track/codec details and next-unwatched action |
| Permissions | Viewer had no administration navigation; direct `/admin` showed access denial; Private was absent; Owner progress was separate |
| Direct Play | H.264/AAC MP4 decoded at 640×360 with advancing media time |
| Seek/resume | Ten-second seek and pause saved position; resume at 42 seconds survived logout/login and native backend restart |
| Remux | H.264/AAC MKV played through local progressive HLS and completed the full 90-second fixture |
| Software conversion | MPEG-4 Part 2 video converted to H.264 and decoded through HLS; AC3 audio converted to AAC and played |
| Audio selection | Explicit second 880 Hz track played; changing to the first 440 Hz track created a new representation at the current position |
| Text subtitles | Embedded SRT converted to WebVTT; captions were visibly rendered, aligned after HLS offset seeking, and removed when set Off |
| Next episode | Episode 1 finished, displayed countdown, navigated automatically to Episode 2 and decoded it |
| Watch/history | Manual Watched/Unwatched, Continue Watching, separate Owner/Viewer activity and persistent saved progress |
| Source loss | Temporarily renamed only the synthetic fixture; playback stopped with an error; restored it and recovered decoded playback |
| Active Streams | Owner saw method, resolutions, configured/observed bitrate, libx264 speed, startup and temporary bytes |
| Stop Stream | Confirmation followed termination/cleanup; player detached within its polling interval; zero FFmpeg processes and no representation directories remained |
| Privacy/Health | Outbound application gate blocked, integrations locked, telemetry off; local dependencies ready, no paths in health |
| Responsive/keyboard | 390×844 mobile and 768×1024 tablet checked visually; no page-wide horizontal overflow; navigation scrolls on small screens; keyboard K/M and focused controls worked |

The player verifies decoded frames, not merely HTTP success. Its HLS library and
all UI assets are served locally. Image-subtitle rejection is tested from synthetic
codec metadata; no image-subtitle burn-in or binary image-subtitle playback is claimed.

## Measurements and hardware

These are small local-fixture observations, not production throughput guarantees:

- Browser-session startup records: Direct Play **63–89 ms**, remux **166–183 ms**,
  software video conversion **288–318 ms**. Caption conversion/offset restarts took
  approximately **462–617 ms**. This measures session creation to first reported
  playback, not a laboratory end-to-end latency distribution.
- Indexed 20,000-item search, second 24-item page: **158 ms**.
- Home plus administration requests while a deliberately rate-paced FFmpeg job was
  running: **48 ms combined**.
- Batch card diagnostics used six queries for both one and 100 cards. Viewer detail
  variants are capped at ten; manager lists return one preferred file per item.
- A monitored 90-second software conversion reported approximately **36.6×** encoding
  speed and **51.5 MiB** completed output. Source complexity and hardware vary.
- Real tiny hardware self-tests: **AMD AMF passed**; QSV and NVENC were unavailable.
  Configuration remained software. A successful probe is not full hardware-playback,
  driver stability, or production-workstation certification.

## Privacy and deployment boundary

Application/source audits found no external media, artwork, subtitle, analytics or
authentication calls. Routine request logs use route templates, status and timing,
not media titles, paths, usernames, request bodies or tokens. Tests fail closed for
outbound calls and redact sensitive fields. These checks are not a packet capture of
the entire host. Native execution does not provide Docker's network-level isolation.

Docker was unavailable during this native run. Container builds, runtime no-egress,
restart recovery and browser playback were subsequently tested on 2026-09-01; see
[the separate container record](container-validation.md). Native AMF success above
does **not** imply Docker GPU support. No public deployment, router change,
remote-access feature or Blue Ash portal integration was performed.

## Dedicated-workstation checklist (for future installations)

1. Back up and validate the Phase 1 installation; record its revision and directories.
2. Run bootstrap/upgrade with Docker installed; validate resolved Compose, build all
   images, and verify migrations, health checks, non-root identity and read-only media.
3. Confirm only the intended loopback/private-LAN listener is exposed and test denied
   runtime egress from every container with the deployment's network controls.
4. Repeat the browser matrix with real household codecs, long files, multiple users,
   concurrent scans, reduced quality, disk-pressure failures and restart recovery.
5. Test cancellation and cleanup across backend/container interruption; never remove
   unknown temporary directories or user media to force a passing result.
6. Test Safari/Firefox separately. Validate GPU device mappings/drivers and full
   playback only after a real encoder probe; retain software fallback.
7. Verify backup/restore includes history and preferences and excludes conversions;
   expire old backups separately after a user deletes viewing history.

Known product limits: embedded text subtitles only, no image-subtitle burn-in,
single selected quality rather than automatic adaptive bitrate, precise HLS seeking
starts a new local transcode, one API manager/scan worker, and up to the last
15 seconds of progress can be lost on forced browser termination. See
[limitations](not-implemented.md) and [conversion sizing](transcoding.md).
