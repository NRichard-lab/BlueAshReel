# Current limitations and acceptance boundaries

The source now implements the Windows per-user tray Agent and encrypted Portal
media interface. Remote browsing, library administration and playback are no
longer deferred architectural placeholders. See [encrypted Portal media](encrypted-portal-agent.md)
for the supported operations and [current acceptance](portal-tray-acceptance.md)
for what has actually been verified.

The currently published Owner-only unsigned installer remains development.4.
Development.5 packaging/migration/tray acceptance and real Portal/email deployment
are separate gates. The latest Portal Linux run passed 132 tests with synthetic
accounts/mail; real SMTP delivery and current Owner authentication are unresolved.
No new installer publication or production deployment is recorded here.

Current limitations:

- Windows service-to-user migration has backup/rollback machinery, but successful
  source/unit tests do not prove the real development.4 upgrade, logoff lifecycle
  or rollback on every workstation. Preserve existing ProgramData and identity.
- Runtime uses the signed-in Windows user's drive/share permissions. Readability,
  network-share behavior, endpoint policy and logoff must be checked on the actual
  workstation; source media is application-read-only, not rewritten NTFS ACLs.
- Existing Docker keeps its local UI/household accounts and optional isolated
  diagnostic connector. It is distinct from the Portal-required Windows workflow.
- Hardware acceleration requires an actual successful encode on the installed
  GPU/driver. H.264 hardware encoding does not imply hardware decode or AAC/text
  subtitle acceleration. Other GPU/browser/Windows combinations remain unverified.
- Image-based subtitle burn-in, external sidecar subtitle discovery and an
  adaptive bitrate ladder are unsupported. Text styling is stripped for WebVTT.
- Forced browser termination may lose the final progress checkpoint. Reconnection,
  seeking and expiry behavior require browser acceptance with the final build.
- The relay has scoped routing, bounded buffers/rates/TTL and draining; the
  four-Agent smoke test is not multi-host failover or a capacity guarantee.
- SMTP delivery is at least once with bounded retries; a crash after SMTP
  acceptance can duplicate an email, while codes/invitation tokens remain one use.

The following remain outside this change: signed/public installer release,
automatic router configuration, direct peer transport, ownership transfer,
unattended destructive upgrades/restores, metadata-provider calls, remote poster/
subtitle/trailer downloads, recommendations/social/analytics, live TV/DVR,
native TV/mobile apps, cloud media storage or backup, and unauthenticated
filesystem browsing. Local Docker household-account email recovery is not replaced
by Portal account email. No media database moves into PostgreSQL or Redis.
