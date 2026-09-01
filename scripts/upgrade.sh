#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENVIRONMENT_FILE="$REPOSITORY_ROOT/.env"
PRODUCT_CONFIG_FILE="$REPOSITORY_ROOT/config/product.json"
NO_PULL=0
RETENTION_DAYS=""

usage() {
  printf 'Usage: %s [--retention-days DAYS] [--no-pull]\n' "$0"
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --retention-days)
      [ "$#" -ge 2 ] || fail "--retention-days requires a positive integer."
      RETENTION_DAYS=$2
      case "$RETENTION_DAYS" in *[!0-9]*|'') fail "Retention days must be a positive integer.";; esac
      [ "$RETENTION_DAYS" -ge 1 ] || fail "Retention days must be at least 1."
      shift 2
      ;;
    --no-pull)
      NO_PULL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "Unknown option: $1"
      ;;
  esac
done

[ -f "$ENVIRONMENT_FILE" ] || fail "Upgrade requires an existing .env. Run bootstrap for a new installation instead."
[ -f "$PRODUCT_CONFIG_FILE" ] || fail "Central product configuration is missing: $PRODUCT_CONFIG_FILE"
PRODUCT_NAME=$(awk -F'"' '$2 == "name" { print $4; exit }' "$PRODUCT_CONFIG_FILE")
[ -n "$PRODUCT_NAME" ] || fail "Central product configuration must define a non-empty name."

# This preserves .env and only validates prerequisites, paths, and Compose.
sh "$SCRIPT_DIR/bootstrap.sh" --no-start

if [ -n "$RETENTION_DAYS" ]; then
  sh "$SCRIPT_DIR/backup.sh" --retention-days "$RETENTION_DAYS"
else
  sh "$SCRIPT_DIR/backup.sh"
fi

cd -- "$REPOSITORY_ROOT"
if [ "$NO_PULL" -eq 0 ]; then
  docker compose --env-file .env pull proxy || fail "The proxy image could not be retrieved; running services were left unchanged."
  docker compose --env-file .env build --pull backend worker frontend || fail "Image build failed; running services were left unchanged."
else
  docker compose --env-file .env build backend worker frontend || fail "Image build failed; running services were left unchanged."
fi

docker compose --env-file .env stop worker backend || fail "Could not stop the worker/backend cleanly. Inspect service state before continuing."
if ! docker compose --env-file .env run --rm --no-deps backend migrate; then
  fail "Database migration failed. Backend/worker remain stopped; preserve state and follow docs/backup-and-restore.md."
fi
docker compose --env-file .env up --detach || fail "Migration succeeded, but upgraded services did not start. Inspect docker compose logs."

env_value() {
  awk -v wanted="$1" '
    /^[[:space:]]*#/ { next }
    {
      line=$0; sub(/\r$/, "", line); pos=index(line, "="); if (!pos) next
      key=substr(line,1,pos-1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == wanted) {
        value=substr(line,pos+1); gsub(/^[[:space:]\"\047]+|[[:space:]\"\047]+$/, "", value)
        print value; exit
      }
    }
  ' "$ENVIRONMENT_FILE"
}

BIND_ADDRESS=$(env_value BIND_ADDRESS)
HTTP_PORT=$(env_value HTTP_PORT)
case "$BIND_ADDRESS" in 127.*|::1) DISPLAY_HOST=localhost;; *) DISPLAY_HOST=$BIND_ADDRESS;; esac
LOCAL_URL="http://$DISPLAY_HOST:$HTTP_PORT"
HEALTH_URL="$LOCAL_URL/api/v1/health/ready"

attempt=1
healthy=0
while [ "$attempt" -le 60 ]; do
  if command -v curl >/dev/null 2>&1; then
    if curl --fail --silent --show-error --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then healthy=1; break; fi
  elif command -v wget >/dev/null 2>&1; then
    if wget --quiet --timeout=3 --output-document=/dev/null "$HEALTH_URL"; then healthy=1; break; fi
  else
    fail "curl or wget is required for the post-upgrade host health check."
  fi
  sleep 2
  attempt=$((attempt + 1))
done

[ "$healthy" -eq 1 ] || fail "Upgrade commands completed, but readiness failed within 120 seconds. Inspect service state and logs; do not delete data."
printf '%s upgrade completed and is ready at %s\n' "$PRODUCT_NAME" "$LOCAL_URL"
printf '%s\n' "A validated backup was created before migration. No Git remote, firewall, router, volume, or unrelated workload was changed."
