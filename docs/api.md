# Phase 2 API

Paths are relative to /api/v1. The local OpenAPI schema is /api/v1/openapi.json.
Mutations require the same-origin session cookie and X-CSRF-Token. Login and
initial setup-session issuance are the bootstrap exceptions. Errors use the Phase 1 envelope: unavailable resources 404,
expired streams 410, incompatible selections 422, admission/rate limits 429.

| Endpoint | Purpose / access |
| --- | --- |
| GET /browse/home | Six bounded assigned-library rails |
| GET /browse/libraries | Library choices, page/page_size up to 100 |
| GET /browse/media | Search/kind/library/watch/availability/resolution/sort, pages up to 100 |
| GET /browse/media/{id} | Safe details; file_page selects ten variants |
| GET /browse/shows/{id}/seasons | Paginated seasons |
| GET /browse/seasons/{id}/episodes | Paginated episodes |
| GET /browse/media/{id}/next | Next available / next unwatched episode |
| PUT /browse/media/{id}/watched | Own watched status |
| GET /browse/artwork/{id} | Protected local artwork |
| GET /media; GET /media/{id}?file_page=1 | Manager diagnostics; one preferred file per list item, ten variants per detail page; file_total exposed |
| GET/POST /users; PATCH/DELETE /users/{id} | Manager users; Owner-only Owner changes; explicit delete_history query |
| POST /users/{id}/revoke | Revoke login and playback sessions |
| GET/PATCH /profile; DELETE /profile/history | Own preferences/history |
| POST /playback/decision | Versioned capability/track/quality decision |
| POST /playback/sessions | Admit file_id, capabilities, tracks, quality, restart/position_seconds |
| GET/HEAD /playback/{id}/file | Protected direct stream and single byte range |
| GET /playback/{id}/hls/{name} | Existing manifest/segment; never starts FFmpeg |
| GET /playback/{id}/subtitles/{index}.vtt | Bounded local conversion, retimed for offset HLS |
| GET /playback/{id} | Session validity/state |
| POST /playback/{id}/progress | Ordered/throttled checkpoint |
| POST /playback/{id}/end; POST /playback/{id}/stop | Own final checkpoint/confirmed stop |
| GET /streams; POST /streams/{id}/stop | Owner monitoring/confirmed stop |
| GET/PATCH /playback-policy | Owner watched threshold/retention |
| GET /playback-health; POST /playback-health/detect | Owner capacity and real hardware tests |
| POST /setup/session | Issue a limited 30-minute setup cookie and CSRF token only while no Owner exists |
| POST /setup/owner | Setup session + CSRF; atomically create Owner and optional browsed first library |
| GET /media-roots | Setup session or Owner/Administrator; approved-root status and signed root selections |
| POST /media-folders/browse | Setup session or Owner/Administrator + CSRF; bounded directory-only navigation |
| POST /media-folders/validate | Setup session or Owner/Administrator + CSRF; advanced internal-path validation |
| GET /media-storage | Owner-only root enforcement state and library associations |

Folder browsing never accepts a host path or returns media files. Browse requests
carry a signed selection identifier and optional cursor; responses contain a safe
root-relative display path, breadcrumbs, directories, and the next cursor. Library
creation consumes `folder_ids`, while adding a path consumes `folder_id`. The
backend canonicalizes and revalidates the folder immediately before storing it.
During first run, `/setup/session` issues an HttpOnly, SameSite-strict signed
capability and a separate CSRF token. It authorizes only approved-folder browsing
and setup completion, not libraries, playback, or administration. The public
setup-status check cannot enumerate directory names. `/setup/owner` accepts
`initial_library.folder_ids`, creates the account and library together, and
replaces the setup capability with the normal Owner session. All setup browsing
capabilities become unusable as soon as an Owner exists; no scan is started.

Progress includes position_seconds, playing, reason and increasing sequence. Server
derives user, duration, method and fingerprint. Clients cannot nominate paths. Track/
quality replacement creates new sessions; old URLs stop working. Admission allows
12 creates/minute/user, decisions 60, progress 40, subtitles 20, plus concurrency limits.
No streaming endpoint calls external providers or returns server path details.
