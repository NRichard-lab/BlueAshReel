#!/bin/sh
set -eu
set -o pipefail

# ONLY this port-owning container's network namespace. No host network, Docker
# socket, privileged mode, host mounts or host firewall access.
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
iptables -F OUTPUT
iptables -F FORWARD
ip6tables -F OUTPUT
ip6tables -F FORWARD
iptables -A OUTPUT -p tcp -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A OUTPUT -p tcp -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Resolve just our two internal upstreams before Caddy exists. Docker's external
# DNS fallback is disabled by dns:127.0.0.1; remove even local DNS next.
backend_ip=$(getent hosts backend | awk 'NR == 1 { print $1 }')
frontend_ip=$(getent hosts frontend | awk 'NR == 1 { print $1 }')
test -n "$backend_ip" && test -n "$frontend_ip"
iptables -D OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -p tcp -d 127.0.0.1 --dport 8080 -j ACCEPT
iptables -A OUTPUT -p tcp -d "$backend_ip" --dport 8000 -j ACCEPT
iptables -A OUTPUT -p tcp -d "$frontend_ip" --dport 3000 -j ACCEPT
BACKEND_UPSTREAM="$backend_ip:8000"
FRONTEND_UPSTREAM="$frontend_ip:3000"
export BACKEND_UPSTREAM FRONTEND_UPSTREAM

# Setup failure exits before listening. Caddy cannot recover a dropped identity
# or capability. Tini itself also starts unprivileged.
exec setpriv --reuid=1000 --regid=1000 --clear-groups --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all --no-new-privs /sbin/tini -- "$@"
