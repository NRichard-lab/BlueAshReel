FROM python:3.12-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/app \
    TMPDIR=/tmp/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/ /app/
COPY config/ /app/config/
COPY docker/backend-entrypoint.sh /usr/local/bin/bluereel-entrypoint

RUN pip install . \
    && chmod 0755 /usr/local/bin/bluereel-entrypoint \
    && mkdir -p /data /database /artwork /tmp/app \
    && chown -R "${APP_UID}:${APP_GID}" /data /database /artwork /tmp/app /app

USER ${APP_UID}:${APP_GID}

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/bluereel-entrypoint"]
CMD ["api"]
