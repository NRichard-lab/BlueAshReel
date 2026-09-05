#!/bin/sh
set -eu
awk -v uid="$APP_UID" '
  /^Uid:/ { user_ok = ($2 == uid && $3 == uid) }
  /^CapEff:/ { effective_ok = ($2 == "0000000000000000") }
  /^CapBnd:/ { bounding_ok = ($2 == "0000000000000000") }
  /^NoNewPrivs:/ { privileges_ok = ($2 == 1) }
  END { exit !(user_ok && effective_ok && bounding_ok && privileges_ok) }
' /proc/1/status
exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all --no-new-privs \
    python -c 'import json,time; p=json.load(open("/remote/control/status.json")); raise SystemExit(0 if time.time()-p["updated_at"]<60 else 1)'
