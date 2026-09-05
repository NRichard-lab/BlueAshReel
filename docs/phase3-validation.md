# Blue Ash Reel foundation validation — September 5, 2026

The Agent and public portal are separate repositories. This record describes
implementation tests, not an independent security audit or a public-release approval.

## Source and compatibility

The initial clean local `main` was `37918643beefcfd8a9a49d2c5023ac072ac0fb8d`.
Fetched `origin/main` was `395c1dcf145abf78c94a90599fbaa7a603b595f4`;
three existing local commits were reviewed and fast-forwarded without rewriting
history. Local, remote and GitHub default branch were then verified at `3791864`.
GitHub renamed `NRichard-lab/BlueReel` to `NRichard-lab/BlueAshReel`; the old URL
redirects to the new repository. The checkout keeps its original on-disk name.

Central branding now drives Blue Ash Reel display names, server identity, browser
metadata and native installer labels. Stable service names, AppIds, registry keys,
data directories, API prefix and cookie identifiers are preserved. No local SQLite
migration was added for the rename or remote connector. Existing media, users,
history, backups and local Docker services were preserved.

## Tests and builds

- Complete Python regression run: 568 passed, four platform-specific skips. This
  includes real bundled FFmpeg/FFprobe and Caddy execution for playback, streaming,
  local permissions, backup, native packaging and reinstall regression.
- Local frontend: 58 tests passed; TypeScript and lint passed.
- Backend strict mypy: 49 modules passed with `backend/pyproject.toml`; Ruff passed.
- Agent application coverage: 82.07% statements (4999/6091), 64.93% branches
  (1048/1614), 78.48% combined. Scope is `backend/app`, not the entire repository.
- Separate renamed backend/frontend Docker images built successfully. The optional
  connector image ran with zero capabilities, UID 1000 and no-new-privileges; only
  its two dedicated state/control volumes were mounted. Arbitrary HTTPS and
  external DNS probes were denied. Temporary connector containers were removed.
- The existing local Docker server was upgraded through `scripts/upgrade.ps1`
  after a validated backup (`blue-ash-reel-backup-20260905T190137Z.zip`). All four
  media containers are healthy, with the same mounts and network memberships.
  The version endpoint reports Blue Ash Reel Server. The private network remains
  internal, and direct-IP HTTPS and external DNS probes from the updated backend
  are blocked. Remote access remains disabled; no optional override was activated.
- Native development installer built from pinned runtimes and hashed dependency
  locks. It remains unsigned and is not published for download.
- Native connector tests exercise a real AppContainer token, permitted control
  spool access, denied synthetic private-file access, and DPAPI protection across
  two child processes. A later PowerShell 5 argument-handling regression was
  reproduced and corrected by passing a protected pins-file path instead of JSON.

## Real browser and relay acceptance

The separate portal passed 64 PostgreSQL security tests and 12 frontend tests,
including Python-to-browser cryptographic vectors, concurrency and receive limits.
Portal coverage is 83.72% statements, 63.87% branches and 80.00% combined;
separate real TCP subprocess tests are additionally checked functionally.

A disposable account completed password login and TOTP, pairing and online status
in a real browser. The actual Agent connector exchanged an authenticated encrypted
status result through the isolated loopback gateway. A fresh pairing generated a
new identity after revocation. Restarting both the connector and all five portal
containers retained the new identity and restored the encrypted diagnostic.
Final browser revocation was observed locally within 3.02 seconds, with the Agent
unpaired and credentials removed. Reusing the consumed code failed. The test
account, registrations, sessions, credentials and temporary test database were removed.

The browser validates Ed25519 identity signatures; ephemeral X25519/HKDF keys
protect directional AES-GCM messages with sequence/replay checks. The relay sees
only bounded ciphertext frames and authorization metadata; synthetic opacity,
non-persistence, oversize, queue/backpressure, idle and cross-user/Agent/session
tests pass. No media command is implemented in the remote protocol.

## Production boundary and remaining acceptance

The private portal repository is `NRichard-lab/BlueAshReelPortal`. Its Ubuntu stack
uses five isolated containers behind the existing Caddy edge. PostgreSQL migration
head is `914fedcc620a`. The protected initial Owner bootstrap, post-Owner backup,
integrity check and full dry restore passed. Existing unrelated services retained
their container identities and configuration hashes.

Workstation public IPv4 was verified as `174.29.198.176`. The domain still points
to Hostinger parking at `2.57.91.91`; no DNS records were changed. Authenticated
zone export and update require the Hostinger connector to load after an app
restart. Trusted TLS, public login, www redirect and actual Internet WSS/443
acceptance remain pending. Loopback/hairpin tests are not claimed as those checks.

Public registration and email delivery remain disabled. The real Owner must
complete MFA enrollment and change the generated bootstrap password privately.
SMTP credentials and sender DNS verification remain required for delivery.

Remote media browsing, playback, direct transport, automatic router changes,
ownership transfer, automatic key rotation and signed installer publication are
deliberately deferred. Identity rotation currently uses unpair and fresh pairing.
The public website can serve browser JavaScript; that is an explicit trust boundary.
