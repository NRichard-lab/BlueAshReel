# Encrypted Portal media and local authorization

The native per-user API process owns the playback manager and outbound connector.
It binds only `127.0.0.1`; ordinary native HTTP routes redirect to Portal
authentication. Container installations retain their existing local interface.
The native scan worker and FFmpeg stay local. The API socket guard permits only
loopback and certificate-validated `blueashreel.com:443`; canonical DNS results
must be global addresses. No arbitrary HTTP proxy or filesystem API is exposed.

## Authorization and pairing

The tray opens `/portal/start?purpose=pair` or `purpose=status`. Each native runtime
also exclusively binds an OS-selected random port on `127.0.0.1` for the Portal
callback and authenticated local status routes; this listener exposes no media
or ordinary API routes and closes with the runtime. The Agent creates
a random state, nonce, PKCE verifier, and HttpOnly browser-binding cookie. Only
the S256 challenge goes to the Portal. Authorization request fields travel in
a URL fragment that the Portal immediately removes while retaining the request
in browser memory. A pairing request includes the Agent's
locally generated public key and fingerprint. The Portal authenticates the user,
requires completed MFA (email in the new flow, existing TOTP retained during migration), and issues a single-use code bound to that key.
The local callback accepts the code in a URL fragment, immediately removes that
fragment, and POSTs to the loopback callback with exact Origin validation.
It consumes local state before network activity and requires the native tray's
fingerprint confirmation before redeeming a pairing code. Both surfaces display
the first 16 SHA-256 hex digits in four uppercase groups, while the complete
fingerprint remains the cryptographic identity binding. The v2 pairing proof
signs the code, public key, friendly name, OS/version, state, nonce, S256 challenge
and exact callback; the Agent checks returned state/nonce/challenge/callback
before persisting the account association. Local status exchange signs its exact
callback with its other one-use bindings as well. Codes, passwords,
private keys, and PKCE verifiers never enter HTTP access logs.

The private key uses current-user DPAPI on Windows. Pairing preserves an existing
valid identity. Ordinary Portal browser sessions and the durable device identity
are separate. The local status page requires a one-use Portal authorization and
fresh signed validation on each view; logout or membership revocation therefore
rejects the next view. Temporary callback state is memory-only and cleared at
shutdown. Pairing commands use the private per-user spool and are removed after
consumption or shutdown.

Refreshing or reopening the local start route reuses a still-pending request,
and local confirmation is consumed once. Portal Cancel invalidates the bound
authorization and returns a state/nonce-bound cancellation fragment. Declined
local confirmation never queues an exchange. Pending requests expire after five
minutes, including while native confirmation is open. The tray distinguishes
Portal approval, local confirmation, connection, expiry, cancellation and
revocation. If an already-approved browser callback was interrupted, choose
Reconnect in the tray, then Pair Agent to create a fresh authorization. A durable
nonsecret revocation marker preserves the Revoked label across restart and
completes private-key destruction if the process stops midway through revocation.

The connector authenticates both the broker and relay with device signatures.
It reports connected only after both authenticated sockets are active. Scoped
routes use the opaque Agent UUID so the Portal can consistently route an Agent
and its browsers to the same relay worker. Before accepting browser E2E keys,
the Agent obtains a one-use challenge and signs live session validation. The
Portal binds this proof to the redeemed ticket, user, Agent, browser key, active
MFA session, membership role, and access version. A Portal membership alone does
not grant library access: `portal_grants` and `user_libraries` in local SQLite
must agree. A sole existing local Owner is mapped to the paired Portal Owner to
preserve accessible progress; ambiguous multiple Owners fail closed.

While connected, a separate signed device request checks the currently approved
published installer version at most once per hour. Semantic version comparison
handles development version numbers correctly. Only `update_available` and the
new version enter tray status; no package is downloaded automatically. Metadata
failure never changes the connection state. Atomic status publication retries
temporary Windows file-sharing denial for at most 500 milliseconds.

## Encrypted RPC

Protocol 1 retains the Ed25519-signed X25519 transcript, HKDF-SHA256 directional
keys, AES-256-GCM, direction-separated AAD, and strictly increasing sequences.
The envelope contains only type, session UUID, sequence, and ciphertext. Session
lifetimes are at most 900 seconds with a 180-second idle timeout. The larger
idle bound accommodates a 120-second native folder confirmation. Browser
reauthorization under the same Portal authentication session can continue an
existing local playback session. Explicit revoked close frames stop its local
FFmpeg jobs; normal transport renewal preserves them until local inactivity
expiry. Library and local-grant authorization is checked on every operation and
every requested media range. The relay rechecks central revocation separately.

Requests are `{id: UUIDv4, op: string, payload: object}`. Replies are
`{id, ok: true, result}` or `{id, ok: false, error: stable_code}`. No arbitrary
method names, source paths, internal HTTP URLs, or exception strings are
dispatched. Relevant operations:

| Operation | Payload |
| --- | --- |
| `catalog.home` | empty |
| `catalog.list` | `kind`, `q`, `history`, `page`, `page_size` (maximum 24), optional `library_id` |
| `catalog.detail`, `catalog.next`, `catalog.seasons` | opaque `media_id`; optional page |
| `catalog.episodes` | opaque `season_id`; optional page |
| `artwork.bytes` | opaque `artwork_id`, byte offset and length |
| `folders.select` | optional manual path, always requiring native confirmation |
| `libraries.create` | name, library_type movies/tv/other, approved selection_ids |
| `libraries.update` | library_id, optional name/enabled/add_selection_ids/remove_path_ids |
| `libraries.list`, `.delete`, `.scan`, `.stop`, `.status`, `.errors` | optional page or required library_id; scan mode changed/full |
| `libraries.errors` | library_id, optional page/page_size; bounded errors with opaque file/media IDs and sanitized messages |
| `grants.set` | user_id, viewer/manager role, enabled, access_version, local library_ids |
| `grants.list` | optional page |
| `grants.get` | user_id; exact member grant, independent of list pagination; maximum 100 library assignments |
| `playback.decision`, `playback.start` | file_id, browser capabilities, audio_index, subtitle_index, quality, position_seconds |
| `playback.bytes` | session_id, resource file/manifest/segment/subtitles, optional segment UUID, offset, length; optional snapshot_id UUID for manifests |
| `playback.manifest.release` | session_id, snapshot_id; idempotent release of an authorized manifest snapshot |
| `playback.progress` | session_id, position_seconds, playing, reason, sequence |
| `playback.stop`, `playback.state` | session_id |

Catalog response IDs are persistent random aliases in `remote_objects`, scoped
to the Agent. They reveal neither internal database IDs nor filenames. Every
alias lookup still authorizes the resulting object. Poster/background references
are `artwork_id`/`background_id`. Playback start returns `id`, decision, duration,
position, video_offset, selected tracks and quality, plus `resource` file or
manifest; internal HTTP URLs are removed. HLS manifest segment names are mapped
to random UUIDs and are usable only in their authorized playback session.

For each browser HLS manifest response, generate one random `snapshot_id` and
read `playback.bytes` with `resource: "manifest"`, starting at offset zero. Every
following chunk with that ID uses the exact same rendered bytes and `total`,
even while FFmpeg grows the local playlist. A missing or expired snapshot cannot
be continued at a nonzero offset; restart that HTTP response with a new ID.
Snapshots bind to the Agent, local user, stable Portal authentication session,
and playback session. Renewal of only the encrypted relay session preserves this
binding. Every cached chunk still checks current local and playback access.

Snapshots expire 45 seconds after creation; reads do not extend that lifetime.
The Agent holds at most eight snapshots and 8 MiB of total snapshot content,
with a 2 MiB limit per rendered manifest. Limits reject additional allocation
instead of evicting an in-progress response. After assembling the complete raw
manifest, the browser rewrites it once, serves a bounded immutable response,
and sends `playback.manifest.release` on completion/cancellation (or immediately
after assembling the raw snapshot). Its reply is `{released: true}`, including
when the authorized snapshot is already absent. Explicit playback stop, local
grant changes, and Portal authorization revocation also discard bound snapshots
and segment aliases. Existing clients may omit `snapshot_id`, retaining the
original independently rendered byte-read behavior.
Before admitting a manifest, the Agent also reclaims aliases and snapshots for
playbacks that the local watchdog expired or history retention removed. This
prevents abandoned browser sessions from exhausting the shared segment limit;
active playback resources remain available across encrypted tunnel renewal.

Binary replies are `{data: base64url, total, offset, mime, eof}`. A single byte
request is at most 131072 bytes, AEAD plaintext at most 262144 bytes, and the
encrypted relay frame at most 524288 bytes. Per-session queues hold at most four
pending requests; the connector admits four sessions and uses bounded websocket
queues. The browser fetches only needed cards, details, artwork, or media ranges.
Media remains on the Agent, with only encrypted frames crossing the relay and
decrypted display state in the authorized browser. Source files are read-only;
only locally owned transcode/cache areas receive writes.

Progress sequence numbers start at one. Playing, periodic, pause, seek, exit,
ended, and restart events update local watch progress. Fetching/prefetching media
bytes does not advance or reset watch-credit timing. `catalog.next` returns
`{item: card_or_null}`; `catalog.home` returns bounded `continue`, `recent_movies`,
`recent_episodes`, `movies`, `shows`, and `recent_watched` arrays. All scan job
references, including `active_job_id`, use the same opaque alias, and failures
expose stable summaries rather than raw local exception strings.

## Validation and limits

`backend/tests/test_remote_media.py` covers random alias isolation, local grant
denial/revocation/version changes, bounded ranges, source changes, encrypted
payload privacy, Portal callback state/cookie/Origin/replay checks, fresh local
status revalidation, Owner progress migration, native-consented library lifecycle,
and source preservation. Set `TEST_FFMPEG_PATH` to run generated six-second media
through actual remux, software video/audio conversion, encrypted HLS segment
reads, authenticated renewal, revocation/process cleanup, audio selection, and
subtitle extraction. Fixtures use synthetic media only. Hardware acceleration
requires the separate host-specific hardware acceptance tests; software tests
do not establish hardware availability or performance.

Manifest tests use a synthetic 4,000-segment growing playlist to verify stable
reads above 128 KiB, renewed transport access, cross-user/session/playback/Agent
isolation, fixed expiry, allocation limits, and release/stop/revocation cleanup.
