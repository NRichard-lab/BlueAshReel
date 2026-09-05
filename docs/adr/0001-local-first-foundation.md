# ADR 0001: Local-first application foundation

- Status: accepted
- Date: 2026-08-31

## Context

Blue Ash Reel needs a secure first phase that a household can operate on one workstation, continues to work without Internet access, scans tens of thousands of files without blocking page requests, and leaves room for later streaming clients and optional integrations. Operational complexity and accidental data disclosure are higher risks than early distributed scale.

## Decision

Use FastAPI and SQLAlchemy 2.x for a typed, versioned REST API and generated OpenAPI documentation. Use React with strict TypeScript for an accessible administration interface. Keep HTTP work and durable background jobs in separate processes.

Use SQLite in WAL mode with Alembic migrations. SQLite is easy to install, back up online, and keep entirely local. Application UUIDs, normalized provider-ID tables, repository boundaries, and migrations avoid coupling domain identity to SQLite and preserve a path to PostgreSQL if later concurrency requires it.

Use FFmpeg/FFprobe locally for media inspection. They support common containers and streams without uploading source files or depending on a metadata service.

Use Docker Compose for repeatable workstation installation and Caddy as a small local reverse proxy. Caddy publishes one localhost-bound port; the remaining services stay on an internal network. Persistent state uses explicit host directories so backup ownership and recovery remain visible to the operator. Media mounts are read-only.

Make local processing and outbound denial the default. A future integration must pass an environment-level gate and an Owner-controlled per-integration setting, and deployment networking must explicitly permit egress.

## Consequences

The initial deployment is simple, private, and offline-capable. SQLite write concurrency and a single-host Compose topology limit horizontal scaling, which is acceptable for this phase. Local FFprobe work consumes household CPU and disk I/O, so it runs in a bounded worker outside requests. Caddy does not solve public TLS or remote access; those remain intentionally out of scope.

Moving to PostgreSQL, a distributed queue, hardware-aware transcoding, or remote clients requires a later ADR and migration plan rather than an implicit dependency added to this foundation.
