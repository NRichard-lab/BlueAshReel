FROM caddy:2-alpine
LABEL org.opencontainers.image.source="https://github.com/NRichard-lab/BlueAshReel"

# The upstream binary carries NET_BIND_SERVICE, which cannot execute with an
# empty capability bounding set. Port 8080 needs no capability: remove the file
# capability at build time rather than granting it to the runtime container.
RUN setcap -r /usr/bin/caddy && apk add --no-cache iptables util-linux tini
COPY docker/proxy-entrypoint.sh /usr/local/bin/proxy-entrypoint
COPY docker/proxy-health.sh /usr/local/bin/proxy-health
RUN chmod 0755 /usr/local/bin/proxy-entrypoint /usr/local/bin/proxy-health

# Only initialization is root; drop identity and the entire capability bounding
# set in the entrypoint before either tini or Caddy starts.
USER 0:0
ENTRYPOINT ["/usr/local/bin/proxy-entrypoint"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
