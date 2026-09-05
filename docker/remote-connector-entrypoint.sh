#!/bin/sh
set -eu

# These rules affect only this isolated container's namespace. The media services
# retain their private network and existing outbound-deny policies.
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
iptables -F OUTPUT
iptables -F FORWARD
ip6tables -F OUTPUT
ip6tables -F FORWARD
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Permit DNS during privileged startup only, then pin canonical IPv4 destinations
# in a root-owned runtime policy file. No DNS or arbitrary HTTPS egress
# remains available to the unprivileged connector process.
portal_ips=$(getent -s dns ahostsv4 blueashreel.com | awk '{ print $1 }' | sort -u)
# Empty DNS results retain deny-all egress but still let the local Owner unpair,
# destroy credentials and see truthful offline status during an Internet outage.
iptables -D OUTPUT -o lo -j ACCEPT
for portal_ip in $portal_ips; do
    case "$portal_ip" in *[!0-9.]*|'') exit 1 ;; esac
    iptables -A OUTPUT -p tcp -d "$portal_ip" --dport 443 -j ACCEPT
done
umask 077
python -c 'import json,sys; json.dump({"allowed_ips":sys.argv[1:]},open("/tmp/portal-ips.json","w"))' $portal_ips
chmod 0444 /tmp/portal-ips.json
exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups --bounding-set=-all \
    --inh-caps=-all --ambient-caps=-all --no-new-privs /usr/bin/tini -- "$@"
