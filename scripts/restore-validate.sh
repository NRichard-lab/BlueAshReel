#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENVIRONMENT_FILE="$REPOSITORY_ROOT/.env"

[ "$#" -ge 1 ] || { printf 'Usage: %s BACKUP_ARCHIVE [--json]\n' "$0" >&2; exit 2; }
[ -f "$ENVIRONMENT_FILE" ] || { printf '%s\n' "Run scripts/bootstrap.sh first; .env does not exist." >&2; exit 1; }

BACKUP_SETTING=$(awk -F= '/^[[:space:]]*BACKUP_PATH[[:space:]]*=/{sub(/^[^=]*=/, ""); gsub(/^[[:space:]\"\047]+|[[:space:]\"\047]+$/, ""); print; exit}' "$ENVIRONMENT_FILE")
[ -n "$BACKUP_SETTING" ] || BACKUP_SETTING=./backups
case "$BACKUP_SETTING" in /*) BACKUP_DIRECTORY=$BACKUP_SETTING;; *) BACKUP_DIRECTORY="$REPOSITORY_ROOT/$BACKUP_SETTING";; esac
BACKUP_DIRECTORY=$(CDPATH= cd -- "$BACKUP_DIRECTORY" && pwd)
ARCHIVE_DIRECTORY=$(CDPATH= cd -- "$(dirname -- "$1")" && pwd)
ARCHIVE_PATH="$ARCHIVE_DIRECTORY/$(basename -- "$1")"
[ -f "$ARCHIVE_PATH" ] || { printf 'Backup archive does not exist: %s\n' "$ARCHIVE_PATH" >&2; exit 1; }
case "$ARCHIVE_PATH" in "$BACKUP_DIRECTORY"/*) ;; *) printf 'Archive must be inside BACKUP_PATH: %s\n' "$BACKUP_DIRECTORY" >&2; exit 1;; esac

RELATIVE=${ARCHIVE_PATH#"$BACKUP_DIRECTORY"/}
shift
cd -- "$REPOSITORY_ROOT"
docker compose --env-file .env --profile tools run --rm --no-deps \
  --entrypoint python backup /tools/restore_validate.py "/backups/$RELATIVE" "$@"
