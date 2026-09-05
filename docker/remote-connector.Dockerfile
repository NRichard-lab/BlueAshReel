FROM python:3.12-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000
ENV APP_UID=${APP_UID} APP_GID=${APP_GID} PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates iptables util-linux tini \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'cryptography>=44,<47' 'websockets>=14,<16'
WORKDIR /app
# Deliberately exclude media server, ORM, database drivers, FFmpeg and all user data.
COPY backend/app/remote/ /app/app/remote/
COPY config/product.json /app/config/product.json
COPY docker/remote-connector-entrypoint.sh /usr/local/bin/remote-connector-entrypoint
COPY docker/remote-connector-health.sh /usr/local/bin/remote-connector-health
RUN chmod 0755 /usr/local/bin/remote-connector-* \
    && mkdir -p /remote/control /remote/identity \
    && chmod 0700 /remote/control /remote/identity \
    && chown -R "${APP_UID}:${APP_GID}" /remote
ENTRYPOINT ["/usr/local/bin/remote-connector-entrypoint"]
CMD ["python", "-m", "app.remote.connector", "--control-dir", "/remote/control", "--identity-dir", "/remote/identity", "--product-config", "/app/config/product.json", "--endpoint-pins", "/tmp/portal-ips.json"]
