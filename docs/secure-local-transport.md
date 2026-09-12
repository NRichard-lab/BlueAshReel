# Secure local transport

The optional LAN listener is disabled by default. It serves only WebSocket
`/ws/local` on one Owner-selected RFC1918 IPv4 address. Every connection needs a
fresh Portal-signed ticket and then uses the existing `blueashreel-e2e-v1`
handshake and encrypted frames. The relay remains enabled and unchanged.

To enable it on an installed Windows Agent, set these private configuration
values and run the established installer repair so its scoped firewall rule is
updated:

```text
LOCAL_TRANSPORT_ENABLED=true
LOCAL_TRANSPORT_ADDRESS=192.168.1.20
LOCAL_TRANSPORT_PORT=18443
```

The address must be statically assigned to this machine or reserved by DHCP.
Repair creates one inbound Private-profile TCP allow rule for that exact address
and port, the bundled Python executable, and `LocalSubnet`. Disabling the option
and repairing removes that rule. No wildcard bind, discovery multicast, port
forwarding, or unauthenticated HTTP endpoint is created.

The Portal deployment must provide `LOCAL_TICKET_SIGNING_KEY_FILE`, containing a
64-character hex encoding of a random 32-byte Ed25519 seed. The Portal sends the
derived public key to a paired Agent over its authenticated control channel; the
private seed never leaves the Portal.

Clients obtain a ticket with `POST /api/agents/{agent_id}/local-ticket` and body
`{"client_key":"<base64url X25519 public key>"}` using their signed-in,
MFA-verified session plus the normal Origin and CSRF protections. The response
is `{"ticket":"<payload>.<signature>","ticket_id":"<UUID>","expires_at":"<ISO-8601>",
"agent_id":"<UUID>"}`. The client opens `ws://<endpoint>/ws/local` and sends
`{"type":"local_offer","ticket":"...","offer":{...}}`; `offer` is the
unchanged protocol-v1 offer with `sid == ticket_id == ticket.jti` and the same
client key, user, session, Agent, role, and MFA fields.

Tickets can start a connection for only 60 seconds. The established encrypted
session is separately capped at 15 minutes. While local sessions are active,
the Agent checks their account-session and membership bindings through its
authenticated control heartbeat every five seconds and closes revoked sessions.
