> Historical service/Docker-era record. It is preserved as evidence for earlier builds, not acceptance for the per-user development.5 Agent. See [current installation](native-windows.md) and [current acceptance](portal-tray-acceptance.md).

# Native Windows runtime security

The native installer uses the same application, schema, scanner, job queue, and
playback services as Docker. `app.native_runtime` supplies only Windows process,
configuration, and service lifecycle integration. Docker never imports or needs
Win32 service components.

## Instance boundary

Every service receives an absolute instance data directory and a role. It reads
only `configuration/installation.json` and `configuration/.env` beneath that
directory. Inherited application settings, a repository `.env`, and the current
working directory cannot select a different database, secret, or media roots.
Runtime storage must remain below the instance data directory; bundled media
tools must match the recorded program directory. Program files and persistent
data cannot contain one another. Directory junctions are rejected.

The installer must protect the instance directory using Windows ACLs. Unix
`chmod(0600)` is not a Windows confidentiality boundary. Runtime markers and
heartbeat files inherit that protected directory; their contents contain only
role, state, and time. Node and Caddy never receive the application secret.

## Approved media

The Owner may browse only installer-approved roots. The limited first-run setup
capability can browse those same roots before the Owner exists. On native Windows
an Administrator or Viewer cannot browse or manually validate host folders.
Docker's existing Administrator workflow is unchanged.

The path adapter rejects traversal, device paths, alternate data streams, reserved
Win32 filenames, trailing-dot/space aliases, and every reparse-point component,
including a junction above an approved root. Native browsing holds read/list
handles on the entire directory chain while resolving and enumerating it. The
handles deny write and delete sharing, preventing replacement during a browse.
Only directories are listed; no source-media write operations are performed.

`read_only: true` describes application behavior. Native
`read_only_enforced: null` deliberately makes **no NTFS ACL enforcement claim**.
Missing or disconnected roots and service-account read denial are reported using
safe messages without paths. Network shares remain an advanced deployment case:
the service identity must have share and filesystem access, and strict-local
network policy may prevent share access. They are never mounted automatically.

## Outbound status

The installer creates application-specific rules, not a global firewall policy.
The backend queries the merged Windows Firewall `ActiveStore` and checks the
expected Python, Node, FFmpeg, FFprobe, and Caddy program rules, all firewall
profiles, outbound/block direction, full protocol/port scope, and the exact
non-loopback address ranges. A settings flag or installer state file is not
accepted as evidence of enforcement. Results are cached for at most 15 seconds;
query failure reports `unknown`, and absent/disabled/mismatched rules report
`not_enforced`. The Privacy response includes the last check time.

The Python runtime also installs an audit hook before the API or worker starts.
It rejects non-loopback DNS lookup, connect, and datagram-send events before
Windows can delegate a DNS lookup to its shared DNS Client service. This is a
defense-in-depth application restriction, not a sandbox for untrusted Python
code. The packaged Node preload provides the corresponding application guard.
Neither defense replaces effective OS firewall rules.

## Service lifecycle

The four roles are API, worker, web, and proxy. Each owns a lock and stop marker.
Startup health has a 60-second bound; three subsequent failed checks end the role
instead of creating an internal crash loop. WinSW separately owns the installer's
bounded restart policy. API and worker stop cooperatively, with a 90-second final
backstop for a stuck native operation. Worker scans retain their last committed
batch, requeue on a cooperative stop, and do not consume a failure retry. A hard
termination still uses the existing stale-job lease recovery.

Each native runner owns a Windows Job Object with kill-on-close protection for
all descendants. Thus an abrupt runner exit also ends its FFmpeg children. The
shared playback manager still performs ordinary graceful FFmpeg cleanup.

Node closes its servers when its stop marker appears. Caddy's unauthenticated
administration endpoint remains disabled: a hidden Windows service has no console
for a reliable Ctrl-Break shutdown, so the stateless proxy is terminated during
stop. This is not a graceful proxy-drain or rollback claim. API and worker state
are drained separately, and maintenance rejects new playback during upgrades.

## Regression evidence

`backend/tests/test_windows_security.py` includes real NTFS junction tests, an
actual directory-replacement denial test, Owner authorization, protected-state
selection rejection, effective-firewall verification and redaction, and Python
DNS/direct-IP audit rejection before network activity. The test suite never
modifies global or application firewall rules.

`backend/tests/test_native_runtime.py` verifies configuration isolation, local
markers, bounded health failures, secret-free child arguments, cooperative scan
restart, and a real Windows Job Object child-reaping test. These automated tests
supplement, but do not replace, installation and service-account testing.

The handle and audit implementations follow the primary references:
[Win32 CreateFile sharing and reparse semantics](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [CPython socket auditing events](https://docs.python.org/3/library/socket.html).
