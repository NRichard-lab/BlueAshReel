#!/bin/sh
set -eu

role="${1:-api}"

case "$role" in
  api)
    alembic upgrade head
    # Uvicorn's default access log includes raw query strings. Application-level
    # structured logging is redacted, so keep the raw access logger disabled.
    # Do not trust client-supplied forwarding headers. The backend is private to
    # the Compose network, and treating the proxy as the client is the safe
    # fail-closed identity for host-scoped throttling.
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers --no-access-log
    ;;
  worker)
    exec python -m app.worker
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown container role: $role" >&2
    exit 64
    ;;
esac
