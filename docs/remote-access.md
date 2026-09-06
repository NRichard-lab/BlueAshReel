# Portal media access and legacy connector reference

The new Windows per-user Agent implements the encrypted Portal media workflow:
Portal sign-in/MFA, locally confirmed pairing, library administration, paginated
catalog/search, artwork, direct ranges/HLS, tracks and progress. Use
[the current protocol](encrypted-portal-agent.md) and
[acceptance status](portal-tray-acceptance.md). The Portal is required for ordinary
Windows Agent access; the loopback URL is protected status/callback only.

The reference below describes the preserved optional diagnostic-only Docker and
service-era connector. Its isolation, local account UI, 120/30-second diagnostic
limits and installation commands apply to that legacy path, not the new native
media Agent. Keep existing Docker workloads/data unchanged.

## Legacy connector reference

# Remote access foundation

Blue Ash Reel keeps the local media server and the public control plane separate.
Remote access is disabled by default. The local SQLite schema is unchanged. Local
playback, users, approved media roots, libraries and history work without Internet
access or the portal. This release supports **encrypted diagnostics only**: synthetic
echo and `{name, version, health: "ok", mode: "relay"}`. It cannot browse a library,
search, proxy an HTTP request, read a path, or play remote media.

## Enable and pair

1. Install the optional isolated connector using the deployment instructions below.
2. Sign in at `https://blueashreel.com` and select **Pair New Server**.
3. Sign in as a local Owner and open **Settings â†’ Remote Access** (also `/remote-access`).
4. Enter the five-minute, single-use pairing code and a friendly name. Avoid putting
   personal paths or media names in the friendly name.
5. Explicitly check the sharing explanation and select **Pair and enable remote access**.
6. Compare the SHA-256 Agent fingerprint in the local settings with the portal card.

Local mutations require an authenticated Owner and the existing session-bound CSRF
token. Administrator and Viewer roles cannot pair or manage remote credentials.
Pairing codes live only in a protected, bounded local command spool until processed;
they never enter SQLite, audit details, request-body logs or backups.

The Agent creates its own Ed25519 identity before pairing. It signs the code, public
key, Owner-chosen name, version and OS category. The portal atomically consumes the
code and returns a separate opaque Agent ID, account association, fingerprint and
the fixed canonical broker/relay endpoints. Ownership transfer is unavailable.
Neither the public portal nor the local media API receives the private key.

## Data boundaries

The central account/control database may store account and MFA state, sessions,
pairings, Agent public keys and IDs, the chosen friendly name, version, OS category,
coarse online/offline and heartbeat status, revocation and minimal security events.
Authorized session/ticket IDs bind each diagnostic to one account, login session
and Agent. Relay operators necessarily see these associations, timings and coarse
encrypted byte counts.

The connector sends only its opaque ID, public identity/proof, friendly name,
version, OS category, diagnostic capability/version information, heartbeat/coarse
health and opaque encrypted frames. Its health result describes the connector;
it does not inspect the media server or invent a database/FFmpeg health result.

Media files, titles, filenames, paths, media counts, library records, local usernames,
searches, history, progress, artwork, subtitles, stream selections, codecs and
transcodes stay local. The connector has no media/database route or import. It is
not an HTTP reverse proxy. Payloads and session keys remain in memory and are
discarded on close, timeout or process exit. No analytics or third-party telemetry
is added.

## Transport and encryption

The isolated process initiates two TLS connections on outbound port 443:
`wss://blueashreel.com/ws/agent` and
`wss://blueashreel.com/ws/relay/agent`. TLS uses certificate/hostname validation,
TLS 1.3 when negotiated, and TLS 1.2 as the minimum. Redirects and environment
proxies are rejected. No listening socket, inbound router rule or UPnP is created.

Each service sends a fresh one-use nonce. The Agent signs a domain-separated
transcript including its ID and the specific broker/relay role. Both application
sockets send heartbeats every 20 seconds; transport pings alone are insufficient.
Each retry uses jittered exponential backoff bounded at 60 seconds. Explicit
credential rejection closes the connection; infrastructure, idle and capacity
failures retry without deleting identity. The local page reports relay, offline,
reconnecting or failed state. It never labels a relay connection as direct.

The protocol contract is versioned separately in the portal repository at
`protocol/v1.md`. In v1, the browser and Agent use fresh X25519 keys per session.
The Agent signs a transcript binding ticket, account, browser session, Agent ID,
browser key and Agent ephemeral key. The browser verifies the selected Ed25519
identity before sending application data. HKDF-SHA256 derives separate directional
AES-256-GCM keys. SHA-256 of the signed transcript, direction and contiguous
sequence number are authenticated with each frame. Nonces cannot repeat within
a directional key. Gaps, replay, tampering, wrong identities, wrong Agents and
all-zero X25519 exchanges are rejected.

The relay sees only envelopes and authenticated ciphertext. Limits are 16 KiB per
WebSocket message, 4096 bytes of application plaintext, four sessions per Agent,
120 seconds absolute / 30 seconds idle per diagnostic, bounded eight-message
socket queues and five-second sends. Echo strings are at most 1024 UTF-8 bytes.
Full browser/user/session revocation and bandwidth quotas are also enforced by
the public relay. Revocation is checked against durable portal state at least
every second; distributed revocation includes this watchdog and scheduler delay.

The public site serves browser code. A compromised control plane could deliver
malicious JavaScript or misleading fingerprints. This architecture does **not**
provide an absolute cryptographic guarantee against that threat. Future native/TV
clients can independently pin identity keys. Future direct transport must preserve
the authenticated session binding and explicitly report its connection mode.

## Credentials, unpairing and rotation

The private identity is stored outside the shared control spool. POSIX directories
are mode `0700` and files `0600`, owned by the connector's service UID. Windows uses
an explicit protected ACL and user-scope DPAPI under the connector service identity
and AppContainer. It never uses machine-wide DPAPI. Durable replacements use
temporary protected files, fsync and atomic replacement.

**Unpair** and **Revoke local credentials** first stop both connections. The Agent
writes a signed, revoke-only tombstone, then removes the private key, account
association and enabled state. The tombstone cannot authenticate, rotate keys or
decrypt traffic. If the portal is offline it is retried with bounded backoff and
the UI says **Central revocation pending** until confirmed. The portal's Revoke
action can also finish revocation. Media and local account/history databases are
untouched. An offline connector process must start to consume a queued unpair;
the UI reports the queued action instead of claiming credentials already vanished.

Identity rotation in the local UI currently uses authenticated unpair/revoke,
followed by a fresh pairing code. This generates a new key, fingerprint and Agent
ID. In-place/automatic rotation is not exposed by this Agent release. The portal
also defines a dual-possession rotation endpoint for a later local client. Do not
copy a private identity to another Agent or run two connectors against one key.

Remote state is excluded from existing media backup volumes and no relay payload
is ever backed up. Native identities protected by DPAPI are bound to their service
profile; a copied file is not a portable recovery credential. After loss, revoke
the old portal registration and pair a new identity. If protecting connector state
separately, use service-private encrypted/offline storage, never the public relay
or repository, and restore only to the original trusted service profile.

## Docker deployment

The normal `compose.yml` stays private and unchanged. Start the optional override
alongside the existing instance configuration (retain an approved-root override if
your installation uses one):

```powershell
docker compose -f compose.yml -f compose.override.yml -f compose.remote.yml up -d --build
```

Omit `-f compose.override.yml` if none exists. Explicit `-f` arguments bypass
Compose's automatic override selection, so retain that file when configured.
Starting the connector does not enable pairing or open a tunnel. Its process
publishes a disabled status to the control volume for the Owner UI.

Only the media API mounts `remote_control`; its network stays private. The separate
connector image contains the remote package and crypto/WebSocket dependencies,
without the server, ORM, database drivers, FFmpeg, media mounts or private network.
Its only mounts are `remote_control` and `remote_identity`. No ports are published.

At container startup, a short privileged entrypoint sets only that container's
firewall to deny outbound traffic, resolves the canonical hostname, pins its IPv4
addresses and permits only TCP 443 to them. It removes DNS access and drops root,
all effective/bounding/ambient capabilities, supplementary groups and privilege
escalation before the connector runs. The parent media network/firewall is never
opened. No Docker socket, host network or host firewall access is used. Read-only
root filesystems, memory/CPU/PID limits, health checks and bounded logs apply.
DNS changes require restarting/recreating the connector to refresh pins. When
startup DNS is unavailable, the process still handles local disablement/unpairing,
with all network access denied; restart it after DNS recovers. The isolation setup
requires Docker IPv4 and `iptables` support and fails closed if setup fails.

## Native Windows installation

The unsigned development EXE provisions the isolated connector automatically.
It remains disabled and creates no identity until the local Owner pairs it in
**Settings → Remote Access**. No additional PowerShell setup is needed. The
packaged `support/install-remote.ps1` remains the administrator recovery helper.

The helper verifies the existing installation identity and paths, copies only the
pinned interpreter/remote modules/dependencies into `runtime\remote-python`, and
registers the stable optional `BlueReelRemote` or `BlueReelDevelopmentRemote`
service. It uses a distinct `NT SERVICE\...Remote` virtual account. The launcher
uses the original outbound-blocked interpreter, and starts the separate connector
inside Windows AppContainer with only `internetClient`, no local-network/server
or media capabilities. A job object limits memory/process count and terminates
the child when its launcher exits.

Python's Windows DLL dependencies require read access to the service's
noninteractive window station and desktop. The launcher verifies the exact Remote
virtual user, session 0, authentication-session station name, invisible station,
default desktop and ownership before adding read-only ACEs for its package SID.
It refuses interactive stations and grants no window creation, hooks, clipboard,
screen, write or ACL-control rights. This does not change media permissions or
the interactive user's desktop. See Microsoft's [window-station access rules](https://learn.microsoft.com/en-us/windows/win32/winstation/window-station-security-and-access-rights).

The sandbox package SID gets read-only code/config and its own low-integrity
protected state at the sibling `<DataDir>-Remote`. Only the local API service SID
also receives control-spool access; it has no identity-directory access. The
connector verifies its AppContainer token and denial of the local database, data,
artwork, configuration and approved roots before network use. If any path is
readable or unavailable, it refuses remote startup. No source-media ACL is changed.
The launcher cannot read the media database through other media service SIDs.

Windows firewall rules apply only to the separate connector executable. They block
the complement of pinned canonical IPv4 addresses, all IPv6, non-443 TCP, UDP,
ICMP and all inbound traffic, permitting only canonical TCP 443. AppContainer
adds its own network restrictions. An explicit `RefreshEndpoint` action updates
pins after DNS changes; normal media executable firewall blocks remain in place.
Setup verifies all effective firewall profiles, local-rule policy merging and
active enforcement of every connector block before starting it. Managed policy
that ignores local rules causes setup to fail without changing global policy.
Reused remote state is checked for reparse points and hardlinks before any ACL
or configuration update.
The helper saves an ACL-protected `.env` backup before adding `REMOTE_CONTROL_DIR`.

Repair and upgrade stop the verified connector, retain its virtual service
identity and DPAPI profile, retire only its separate runtime into protected
remote state, and install/revalidate the current sandbox code, policy, pins and
ACLs before restarting it. The key and account association remain unchanged.
The new EXE embeds matching pre-upgrade helpers so an older paired installation
can use the new preservation path before its program files are replaced.
Changing approved media roots rebuilds and revalidates the denial policy too.

Offline installation uses an empty destination list, blocks every destination
and leaves the TLS allow rule disabled. Re-run the EXE to repair after DNS
recovers. A plain service restart does not refresh static destination pins.
An explicit endpoint refresh with unavailable DNS preserves the existing rules.

Default uninstall removes the verified connector service and rules while
preserving protected remote state and local data. The explicit data-deletion
choice also purges the exact marked default `<DataDir>-Remote` sibling after
checking its owner, scope, reparse points and hardlinks. Unpair first to complete
central revocation; removing local state while offline cannot itself revoke the
portal's association.

Native acceptance includes a real AppContainer token, allowed spool write, denied
synthetic private-file read and DPAPI encryption/decryption across child-process
restart. The September 5 development build also passed elevated SCM provisioning,
effective firewall checks and DPAPI protection/decryption across actual virtual
service restarts. The local server stayed ready and remote access stayed disabled;
all synthetic fixture services, rules and directories were removed. Public WSS
pairing through trusted domain TLS must still pass before calling the native
remote installation production-ready. AppContainer/profile creation or DPAPI
failures are fail-closed; there is no fallback to a media-capable process.

## Incident recovery and next phase

Revoke a suspect Agent in the portal, unpair locally and pair a new identity after
repairing the installation. Revoke account sessions and change/reset credentials
if account compromise is suspected. Stop the optional connector service/container
to disable all remote sockets while local media services continue. Preserve only
minimal security evidence; do not collect encrypted relay payloads or private media
to diagnose a control-plane incident.

Remote browsing, playback, transcoding, direct transport, automatic key rotation,
ownership transfer, stronger native-client key pinning and public signed downloads
remain deferred. The protocol must undergo another privacy/security review before
adding any private media command.
