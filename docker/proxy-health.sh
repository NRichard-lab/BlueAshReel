#!/bin/sh
set -eu
awk '
  /^Uid:/ { user_ok = ($2 == 1000 && $3 == 1000) }
  /^CapEff:/ { effective_ok = ($2 == "0000000000000000") }
  /^CapBnd:/ { bounding_ok = ($2 == "0000000000000000") }
  /^CapPrm:/ { permitted_ok = ($2 == "0000000000000000") }
  /^CapAmb:/ { ambient_ok = ($2 == "0000000000000000") }
  /^NoNewPrivs:/ { privileges_ok = ($2 == 1) }
  END { exit !(user_ok && effective_ok && bounding_ok && permitted_ok && ambient_ok && privileges_ok) }
' /proc/1/status
exec setpriv --reuid=1000 --regid=1000 --clear-groups --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all --no-new-privs \
    wget --quiet --tries=1 --spider http://127.0.0.1:8080/api/v1/health/live
