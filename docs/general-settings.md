# General Agent settings

## Architecture review

General settings use the Agent's existing `application_settings` SQLite table,
the same durable local mechanism used by playback and transcoding policy. They do
not create a Portal settings table or browser-local copy. The native API owns the
database, the scan worker remains separate, and the Portal reaches settings through
the existing authenticated, end-to-end encrypted Agent RPC. `RemoteMedia` performs
the existing local Owner check before either settings operation. The Portal relay
continues to see only encrypted request and reply frames.

The native Agent already used the current-user
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry created by the
installer/tray for login startup. General settings read and write that exact
per-installation entry rather than mirroring it into SQLite. The remaining fields
are stored under the versioned `general.settings` key. SQLAlchemy transactions
provide the existing SQLite atomic-write behavior; all values are validated before
the row is committed. If the registry action succeeds but the database commit
fails, the registry value is restored to its previous state.

Pairing identity remains the Ed25519 key and permanent Agent UUID in the protected
identity directory. The friendly name is display metadata only. The connector
projects the current local name in an optional broker heartbeat field so the
Portal inventory can update without changing pairing, keys, membership, grants,
or the encrypted session transcript. Older Portals and Agents remain compatible
because the heartbeat field and both settings RPC operations are additive.

## Stored contract

`settings.general.get` takes an empty payload. `settings.general.update` accepts a
partial object containing any of `identity`, `language_region`, and
`startup_connection`; fields omitted from a supplied group retain their current
values. Both operations are Owner-only.

The version 1 model contains:

- `identity`: `agent_name` and optional `description`.
- `language_region`: `language`, ISO country `region`, IANA `timezone`, and stable
  `date_format` / `time_format` enum values.
- `startup_connection`: `start_with_windows`, `launch_portal_on_start`,
  `auto_connect`, and `auto_reconnect`.
- `capabilities.start_with_windows`: whether the current deployment can change
  the native Windows login registration.

English (`en-US`) is the only language currently accepted. Region defaults from
the operating-system locale, falling back to `US`. Time zone defaults to an IANA
identifier; native Windows IDs are converted at the Agent boundary. Date/time
preferences are stored for future centralized formatting and do not currently
rewrite unrelated displays or schedulers.

## Runtime behavior

None of these settings requires an Agent restart. A name save updates Portal state
immediately and is repeated on later authenticated heartbeats. Login startup is
applied during Save and verified against the actual registry value. Launch Portal
runs once during a real native Agent startup, never during reconnect loops.
`auto_connect` governs the next Agent start. `auto_reconnect` governs future
unexpected disconnects while preserving the existing bounded backoff. A manual
tray reconnect always remains available and neither setting changes pairing data.

Updates and every other Settings navigation section are outside this contract.
Global localization/date-time formatting and installer packaging are also deferred.
