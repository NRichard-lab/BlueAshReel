#!/usr/bin/env sh
set -eu

umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENVIRONMENT_FILE="$REPOSITORY_ROOT/.env"
ENVIRONMENT_EXAMPLE="$REPOSITORY_ROOT/.env.example"
COMPOSE_FILE="$REPOSITORY_ROOT/compose.yml"
PRODUCT_CONFIG_FILE="$REPOSITORY_ROOT/config/product.json"
NO_BUILD=0
NO_START=0
MEDIA_OVERRIDE=""

usage() {
  printf '%s\n' "Usage: $0 [--media-path DIRECTORY] [--no-build] [--no-start]"
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --media-path)
      [ "$#" -ge 2 ] || fail "--media-path requires a directory."
      MEDIA_OVERRIDE=$2
      shift 2
      ;;
    --no-build)
      NO_BUILD=1
      shift
      ;;
    --no-start)
      NO_START=1
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

[ -f "$COMPOSE_FILE" ] || fail "compose.yml was not found at $COMPOSE_FILE. Run this script from a complete application checkout."
[ -f "$PRODUCT_CONFIG_FILE" ] || fail "Central product configuration is missing: $PRODUCT_CONFIG_FILE"
PRODUCT_NAME=$(awk -F'"' '$2 == "name" { print $4; exit }' "$PRODUCT_CONFIG_FILE")
[ -n "$PRODUCT_NAME" ] || fail "Central product configuration must define a non-empty JSON name."

if ! command -v docker >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Docker is not installed or is not on PATH. Install Docker Engine from Docker's
official repository plus the Docker Compose plugin, start the service, add your
user to the docker group if appropriate for your security policy, then rerun.
No packages, firewall rules, or services were changed by this script.
EOF
  exit 1
fi

docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable. Install the docker-compose-plugin package and rerun."
docker info >/dev/null 2>&1 || fail "The Docker engine is not reachable. Start it, or correct this user's Docker socket permissions, then rerun."
[ "$(id -u)" -ne 0 ] || fail "Run bootstrap as the intended unprivileged application owner, not root or through sudo."

generate_secret() {
  if [ -r /dev/urandom ] && command -v od >/dev/null 2>&1; then
    od -An -N48 -tx1 /dev/urandom | tr -d ' \n'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 48
  else
    fail "A secure random generator is unavailable (need /dev/urandom with od, or openssl)."
  fi
}

env_value() {
  awk -v wanted="$1" '
    /^[[:space:]]*#/ { next }
    {
      line=$0
      sub(/\r$/, "", line)
      pos=index(line, "=")
      if (pos == 0) next
      key=substr(line, 1, pos-1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == wanted) {
        value=substr(line, pos+1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        if ((substr(value,1,1) == "\"" && substr(value,length(value),1) == "\"") ||
            (substr(value,1,1) == "\047" && substr(value,length(value),1) == "\047")) {
          value=substr(value,2,length(value)-2)
        }
        print value
        exit
      }
    }
  ' "$ENVIRONMENT_FILE"
}

replace_env_value() {
  key=$1
  value=$2
  temp_file="$ENVIRONMENT_FILE.tmp.$$"
  awk -v wanted="$key" -v replacement="$value" '
    BEGIN { replaced=0 }
    {
      line=$0
      pos=index(line, "=")
      key=(pos > 0 ? substr(line,1,pos-1) : "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      if (key == wanted) {
        print wanted "=" replacement
        replaced=1
      } else {
        print $0
      }
    }
    END { if (!replaced) print wanted "=" replacement }
  ' "$ENVIRONMENT_FILE" > "$temp_file"
  mv -- "$temp_file" "$ENVIRONMENT_FILE"
}

CREATED_ENVIRONMENT=0
if [ -e "$ENVIRONMENT_FILE" ]; then
  [ -f "$ENVIRONMENT_FILE" ] || fail "$ENVIRONMENT_FILE exists but is not a regular file."
  printf '%s\n' "Keeping existing .env; it was not overwritten."
else
  [ -f "$ENVIRONMENT_EXAMPLE" ] || fail ".env.example is missing; refusing to invent an incomplete configuration."
  cp -- "$ENVIRONMENT_EXAMPLE" "$ENVIRONMENT_FILE"
  chmod 600 "$ENVIRONMENT_FILE"
  replace_env_value APP_SECRET_KEY "$(generate_secret)"
  replace_env_value PUID "$(id -u)"
  replace_env_value PGID "$(id -g)"
  if [ -n "$MEDIA_OVERRIDE" ]; then
    [ -d "$MEDIA_OVERRIDE" ] || fail "The selected media directory does not exist: $MEDIA_OVERRIDE"
    if printf '%s' "$MEDIA_OVERRIDE" | LC_ALL=C grep '[[:cntrl:]]' >/dev/null 2>&1; then
      fail "The selected media path contains an unsupported control character."
    fi
    MEDIA_OVERRIDE=$(CDPATH= cd -- "$MEDIA_OVERRIDE" && pwd)
    replace_env_value MEDIA_PATH "$MEDIA_OVERRIDE"
  fi
  CREATED_ENVIRONMENT=1
  printf '%s\n' "Created .env with a cryptographically secure application secret and this user's UID/GID."
fi

for key in APP_SECRET_KEY BIND_ADDRESS HTTP_PORT DATA_PATH DATABASE_PATH ARTWORK_PATH TEMP_PATH MEDIA_PATH BACKUP_PATH PUID PGID; do
  value=$(env_value "$key")
  [ -n "$value" ] || fail ".env is missing required setting $key. Compare it with .env.example; the script did not overwrite it."
done

APP_SECRET_KEY=$(env_value APP_SECRET_KEY)
[ "$APP_SECRET_KEY" != "GENERATE_WITH_BOOTSTRAP_DO_NOT_USE" ] || fail "APP_SECRET_KEY is still the example placeholder. Move .env aside and rerun, or replace it securely."
[ "${#APP_SECRET_KEY}" -ge 64 ] || fail "APP_SECRET_KEY must contain at least 64 characters of cryptographically random data."

BIND_ADDRESS=$(env_value BIND_ADDRESS)
case "$BIND_ADDRESS" in
  ::1) ;;
  *)
    printf '%s\n' "$BIND_ADDRESS" | awk -F. '
      NF != 4 { exit 1 }
      {
        for (i=1; i<=4; i++) {
          if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
        }
        if ($1 == 127 || $1 == 10 || ($1 == 172 && $2 >= 16 && $2 <= 31) ||
            ($1 == 192 && $2 == 168)) exit 0
        exit 1
      }
    ' || fail "BIND_ADDRESS must be loopback or an RFC1918 private address; wildcard and public binds are rejected."
    ;;
esac

HTTP_PORT=$(env_value HTTP_PORT)
case "$HTTP_PORT" in *[!0-9]*|'') fail "HTTP_PORT must be an integer from 1 through 65535.";; esac
[ "$HTTP_PORT" -ge 1 ] && [ "$HTTP_PORT" -le 65535 ] || fail "HTTP_PORT must be an integer from 1 through 65535."

PUID_VALUE=$(env_value PUID)
PGID_VALUE=$(env_value PGID)
case "$PUID_VALUE:$PGID_VALUE" in *[!0-9:]*|:*|*:) fail "PUID and PGID must be positive numeric container identities.";; esac
[ "$PUID_VALUE" -gt 0 ] && [ "$PGID_VALUE" -gt 0 ] || fail "PUID and PGID cannot be zero; running application containers as root is rejected."
[ "$PUID_VALUE" -eq "$(id -u)" ] || fail "PUID does not match the invoking user. Review the existing .env instead of changing directory ownership automatically."
[ "$PGID_VALUE" -eq "$(id -g)" ] || fail "PGID does not match the invoking user's primary group. Review the existing .env deliberately."

resolve_configured_path() {
  configured=$1
  case "$configured" in
    /*) absolute=$configured ;;
    *) absolute="$REPOSITORY_ROOT/$configured" ;;
  esac
  parent=$(dirname -- "$absolute")
  leaf=$(basename -- "$absolute")
  mkdir -p -- "$parent"
  parent=$(CDPATH= cd -- "$parent" && pwd -P)
  candidate="$parent/$leaf"
  if [ -d "$candidate" ]; then
    (CDPATH= cd -- "$candidate" && pwd -P)
  else
    printf '%s\n' "$candidate"
  fi
}

STATE_PATHS=""
for key in DATA_PATH DATABASE_PATH ARTWORK_PATH TEMP_PATH BACKUP_PATH; do
  configured=$(env_value "$key")
  resolved=$(resolve_configured_path "$configured")
  case "$resolved" in /|"$REPOSITORY_ROOT") fail "$key resolves to an unsafe broad directory: $resolved";; esac
  mkdir -p -- "$resolved"
  probe="$resolved/.application-write-test-$$"
  (umask 077 && printf test > "$probe") || fail "$key is not writable: $resolved"
  rm -f -- "$probe"
  if [ -n "$STATE_PATHS" ]; then
    while IFS= read -r existing_path; do
      [ -n "$existing_path" ] || continue
      case "$resolved/" in "$existing_path/"*) fail "Application state paths overlap; they must be isolated, not equal or nested.";; esac
      case "$existing_path/" in "$resolved/"*) fail "Application state paths overlap; they must be isolated, not equal or nested.";; esac
    done <<EOF
$STATE_PATHS
EOF
  fi
  STATE_PATHS="${STATE_PATHS}${STATE_PATHS:+
}$resolved"
done

MEDIA_CONFIGURED=$(env_value MEDIA_PATH)
MEDIA_RESOLVED=$(resolve_configured_path "$MEDIA_CONFIGURED")
if [ ! -d "$MEDIA_RESOLVED" ]; then
  if [ "$CREATED_ENVIRONMENT" -eq 1 ] && [ "$MEDIA_CONFIGURED" = "./media" ]; then
    mkdir -p -- "$MEDIA_RESOLVED"
    printf 'Created the default empty media directory at %s.\n' "$MEDIA_RESOLVED"
  else
    fail "MEDIA_PATH does not exist or is not a directory: $MEDIA_RESOLVED"
  fi
fi
[ -r "$MEDIA_RESOLVED" ] && [ -x "$MEDIA_RESOLVED" ] || fail "MEDIA_PATH is not readable/traversable by the current user: $MEDIA_RESOLVED"

printf '%s\n' "$STATE_PATHS" | while IFS= read -r state_path; do
  [ -n "$state_path" ] || continue
  case "$state_path/" in "$MEDIA_RESOLVED/"*) fail "A runtime state path is inside MEDIA_PATH; source media must remain isolated and read-only.";; esac
  case "$MEDIA_RESOLVED/" in "$state_path/"*) fail "MEDIA_PATH is inside a runtime state path; choose isolated directories.";; esac
done

cd -- "$REPOSITORY_ROOT"
docker compose --env-file "$ENVIRONMENT_FILE" config --quiet

if [ "$NO_START" -eq 1 ]; then
  printf '%s\n' "Configuration and paths are valid. Services were not started because --no-start was supplied."
  exit 0
fi

if [ "$NO_BUILD" -eq 1 ]; then
  docker compose --env-file "$ENVIRONMENT_FILE" up --detach
else
  docker compose --env-file "$ENVIRONMENT_FILE" up --detach --build
fi

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
    fail "Services started, but curl or wget is required to run the host health check. Visit $HEALTH_URL manually."
  fi
  sleep 2
  attempt=$((attempt + 1))
done

[ "$healthy" -eq 1 ] || fail "Services started but readiness did not succeed within 120 seconds. Inspect with: docker compose logs"

printf '%s is ready at %s\n' "$PRODUCT_NAME" "$LOCAL_URL"
printf '%s\n' "Complete Owner setup in the browser. No public firewall or router settings were changed."
