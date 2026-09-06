> Historical service/Docker-era record. It is preserved as evidence for earlier builds, not acceptance for the per-user development.5 Agent. See [current installation](native-windows.md) and [current acceptance](portal-tray-acceptance.md).

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

- Complete Python regression run: 569 passed, four platform-specific skips. This
  includes real bundled FFmpeg/FFprobe and Caddy execution for playback, streaming,
  local permissions, backup, native packaging and reinstall regression.
- Local frontend: 58 tests passed; TypeScript and lint passed.
- Backend strict mypy: 50 modules passed with `backend/pyproject.toml`; Ruff passed.
- Agent application coverage: 81.15% statements (5025/6192), 63.79% branches
  (1050/1646), 77.51% combined. Scope is `backend/app`, not the entire repository.
  Elevated Windows service acceptance runs separately and is not included in
  these coverage numbers.
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
  locks at clean source `df26a7b6ab5b2f024cb572198b116a85d785ea14`. It remains
  unsigned and is not published for download.
- Native connector tests exercise a real AppContainer token, permitted control
  spool access, denied synthetic private-file access, and DPAPI protection across
  two child processes. A later PowerShell 5 argument-handling regression was
  reproduced and corrected by passing a protected pins-file path instead of JSON.
- Elevated native installation passed with the actual dedicated virtual service
  account and AppContainer. The unmodified production helper verified running
  state and fresh status before reporting success. A synthetic diagnostic then
  verified DPAPI protection/decryption across service restart, denied media/data
  access, disabled remote access with no generated Agent identity, six effective
  scoped firewall rules, and local server readiness before and after installation.
  All synthetic services, firewall rules and directories were removed successfully.
  The Session 0 desktop read-access fix grants only the verified private service
  station/desktop permissions needed to initialize Python DLLs; interactive
  desktops and media ACLs are unchanged. Public native WSS pairing remains pending.

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

The same acceptance subsequently ran against the real public domain with trusted
TLS, an isolated Docker Agent and a disposable MFA account. Password/TOTP login,
fingerprint matching and the encrypted status diagnostic passed. Revocation
removed the identity file within 1.61 seconds and published revoked/unpaired state
within 4.00 seconds. Fresh pairing created a different Agent ID and fingerprint;
reusing that consumed code with a valid fresh-key signature returned HTTP 400
without changing the active identity. Restarting the Agent and all five production
portal containers preserved its key and restored the relay automatically. The
portal showed offline state during the outage; the encrypted diagnostic passed
again after recovery. The connector had only its two dedicated volumes, UID 1000,
zero capabilities and no-new-privileges; canonical HTTPS succeeded while an
arbitrary external HTTPS probe was blocked. No URL or TLS bypass was used.

Final local unpair confirmed central revocation and removed the Agent identity.
The browser then showed no Agents and was signed out. The disposable production
account, both Agent registrations, pairing/session/ticket records, fixture audit
and rate-limit rows, local credentials, container, network and state volumes were
removed and their absence verified. The sole real Owner remained unchanged.

The browser validates Ed25519 identity signatures; ephemeral X25519/HKDF keys
protect directional AES-GCM messages with sequence/replay checks. The relay sees
only bounded ciphertext frames and authorization metadata; synthetic opacity,
non-persistence, oversize, queue/backpressure, idle and cross-user/Agent/session
tests pass. No media command is implemented in the remote protocol.

## Production boundary and public acceptance

The private portal repository is `NRichard-lab/BlueAshReelPortal`. Its Ubuntu stack
uses five isolated containers behind the existing Caddy edge. PostgreSQL migration
head is `914fedcc620a`. The protected initial Owner bootstrap, post-Owner backup,
integrity check and full dry restore passed. Existing unrelated services retained
their container identities and configuration hashes.

The pre-DNS backup is `blueashreel-20260905T185006Z`, with source `b09798f`
and schema `914fedcc620a` captured. The actual systemd backup/prune job succeeded;
its daily timer is enabled, with protected storage and 14-day retention.

Workstation public IPv4 was verified as `174.29.198.176`. The full authenticated
DNS zone was exported before replacing only the apex A record, previously
`2.57.91.91`. Hostinger required increasing its TTL from 50 to 60 seconds. The
existing `www` CNAME and its TTL were unchanged, and the complete after-zone diff
was verified. Both authoritative nameservers and Cloudflare/Google resolvers
returned the new IP.

Trusted Let's Encrypt certificates were issued for apex and www on September 5,
expiring December 4, 2026. HTTP redirects to HTTPS; HTTPS www redirects to the
canonical apex while preserving path/query. TLS 1.3, security headers and public
login/MFA/diagnostics passed. Successful external TLS-ALPN-01 validations prove
public port 443 reachability. Browser and Agent acceptance used real public DNS
from this LAN; those requests are not described as independent outside-LAN
authenticated sessions. An external web-fetch service refused the new URLs before
fetching. Public Windows-service pairing remains separate future native release
acceptance; its elevated AppContainer/DPAPI/firewall tests passed as recorded above.

Public registration and email delivery remain disabled. The real Owner must
complete MFA enrollment and change the generated bootstrap password privately.
SMTP credentials and sender DNS verification remain required for delivery.

Remote media browsing, playback, direct transport, automatic router changes,
ownership transfer, automatic key rotation and signed installer publication are
deliberately deferred. Identity rotation currently uses unpair and fresh pairing.
The public website can serve browser JavaScript; that is an explicit trust boundary.
