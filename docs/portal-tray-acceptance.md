# Portal and Windows tray acceptance checkpoint

This records the current change, not a completed release. The published
Owner-only unsigned installer remains **0.1.0-development.4**. Development.5
is the current source/candidate and must not replace it until acceptance passes.
No new Portal deployment or development.5 publication is recorded at this
checkpoint. Existing production data, Agent data and unrelated services must
remain preserved throughout the remaining work.

## Evidence recorded

- September 6 resumed regression: 676 Agent/backend/packaging/script tests passed
  with the bundled FFmpeg and Caddy; four platform/privilege skips. Ruff passed.
  A subsequent 38-test remote-media run passed after adding abandoned-session
  cleanup, including 16 manifest snapshot and resource cleanup cases.
- Actual 32-bit PowerShell now validates the 64-bit supervisor through its exact
  CIM executable path. Disposable preserve-data uninstall and retained reinstall
  passed; settings, advanced SQLite data, artwork and port29190 were retained.
  The fixture is stopped and its login startup is disabled. Actual legacy service
  migration remains unverified: the former development service registrations
  were absent at the resumed read-only inventory, and were not recreated or removed.
- Browser regressions cover stable rewritten 1,500/3,000-segment HLS responses,
  growing source playlists, bounded snapshots and cancellation, and acknowledged
  final playback progress before stop/restart. Synthetic Chromium detail-view
  acceptance covers seasons, episodes and file versions beyond their first page.
- Hostinger Mail exposes the parent mailbox nicholas.richard@blueashdigital.tech.
  The user confirmed donotreply@blueashdigital.tech is its configured alias.
  The connector exposes neither an SMTP credential nor a documented alias sender
  parameter; production SMTP authentication and real delivery still require a
  protected mailbox/app-password reference. No Owner MFA setting was changed.
- Portal backend: **152 tests passed on Linux in 56.18 seconds**, against an
  explicitly isolated PostgreSQL test database. This covers accounts/TOTP/email
  MFA, synthetic SMTP, invitations, memberships, PKCE/signed authorization,
  encrypted relay privacy/revocation and protected installer downloads.
- Synthetic relay smoke: four Agents/four sessions, 32 encrypted roundtrips,
  14,922,432 encrypted wire bytes in 0.479 seconds (29.70 MiB/s aggregate).
  This is one-host synthetic coverage, not real-media or multi-host capacity.
- Browser production-build acceptance used only generated six-second fixtures and
  an isolated loopback control plane. Direct MP4 (6.016s), HLS remux (6.099s),
  and hardware conversion (6.037s) loaded at video readyState4 and played to end
  without browser alerts. The local decision confirmed `h264_amf`, no fallback;
  CPU AAC conversion accompanied the hardware video encode. Selected subtitles
  reached the browser track loaded state. Agent SQLite recorded watched progress.
- Opaque media-route reload restored details after authorization; a fresh tab
  required Agent selection. Signing out in a second tab removed the first tab's
  player/catalog; Agent SQLite reported zero active playback sessions and there
  were no remaining FFmpeg processes from the fixture. This used a synthetic
  MFA-completed session, not the production Owner account.
- Frontend: production TypeScript/Vite build and 21 protocol/range/encryption tests
  passed. Full-size encrypted chunks and browser-to-Agent identity binding use
  an independently generated cryptographic test peer.
- New native/Portal media functionality exists in source: local-confirmed library
  operations, paginated movies/TV/search, artwork, tracks, progress, direct ranges
  and HLS remux/transcode transport. Its live browser/Windows acceptance must be
  reported independently of the Portal backend tests.
- Real SMTP credentials/delivery and current production Owner authentication
  remain unresolved. Synthetic email tests do not verify receipt by the Owner.
  Do not enable Owner email MFA until actual delivery and code entry succeed.

## Required acceptance still to record

| Area | Required evidence before claiming completion |
| --- | --- |
| Installer | Clean install, development.4 upgrade, runtime/advanced paths and resource settings, no user/library/pairing wizard, repair, preserve-uninstall, explicit disposable removal and failed-upgrade rollback |
| Windows runtime | Actual signed-in user/token, no console/service dependency, real tray/menu, start at login and stop at logoff, Docker/WSL stopped, pause/restart/exit, no orphan FFmpeg/runtime processes |
| Migration | Database/media records/artwork/progress/roots preserved; identity retained safely or explicitly re-paired; new runtime healthy before old services retire; rollback restores prior working state |
| Portal auth | Real Owner login, delivered email MFA, expiry/replay/logout/revocation, Agent-bound ticket, protected loopback callback and no password sent to Agent |
| Invitations | Real new/existing recipient delivery and same-email verification, wrong/unverified/expired/used/revoked rejection, resend bounds, role/remove/disable, local grant propagation and live playback revocation |
| Libraries/media | Native folder confirmation (including remote Owner/manual path), multiple folders and traversal/reparse/readability checks, create/edit/remove/enable/disable, scans/stop/errors/changed/full rescans, movies/TV/search/Continue Watching/details/artwork |
| Playback | Direct Play and byte-range seek, remux, software transcode, actual supported hardware encode, audio/text subtitle selection, progress/next episode, Agent outage and revoked-user behavior |
| Privacy/scaling | Opaque authorized routes and cross-Agent/user isolation; no media/catalog/search/progress in central DB/queue/logs/cache/backup; real relay non-persistence, bounded saturation/drain and multi-host routing/failover |
| Production | Exact running commit/host/container baseline, validated DB/config backup, safe migrations, targeted service deployment/drain, trusted TLS, Owner/Agent/email/invitation/media checks, unrelated service comparison |
| Publication/Git | Accepted development.5 version/path/size/hash/source commit, archive development.4, Owner-only publication, canonical downloaded-byte comparison, both repositories pushed and local/origin/default heads matched |

## Product/deployment boundaries

The Windows tray Agent requires the public Portal for ordinary access. It owns
local media/catalog/artwork/progress/FFmpeg and executes as the logged-in Windows
user. Detailed library grants stay in local SQLite; central membership alone
does not grant media access. The Portal stores account/control data only and
relays opaque encrypted frames.

The existing Docker local website and household-account setup are a preserved
legacy/development path. Its historical acceptance and optional diagnostic
connector do not demonstrate the new Windows workflow. Older service-based
installer records remain useful recovery evidence, not instructions to create
new Windows service installations.

Unsupported/deferred features include signed/public installer release, automatic
router configuration, direct peer transport, image-subtitle burn-in, sidecar
subtitle discovery and an adaptive bitrate ladder. Hardware/browser/platform
support is limited to combinations actually validated; no GPU result is inferred
from a product name. Update this checkpoint with concrete results as remaining
acceptance and deployment finish.
