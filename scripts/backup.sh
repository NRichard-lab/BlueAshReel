#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

[ -f "$REPOSITORY_ROOT/.env" ] || { printf '%s\n' "Run scripts/bootstrap.sh first; .env does not exist." >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf '%s\n' "Docker is required." >&2; exit 1; }

cd -- "$REPOSITORY_ROOT"
docker compose --env-file .env --profile tools run --rm --no-deps backup "$@"
