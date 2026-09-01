FROM node:22-alpine AS build

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    NEXT_TELEMETRY_DISABLED=1 \
    WRANGLER_SEND_METRICS=false

WORKDIR /workspace/frontend

RUN corepack enable

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
COPY config/product.json /workspace/config/product.json
RUN pnpm run build
RUN node -e "const fs=require('node:fs');const p='dist/server/wrangler.json';const c=JSON.parse(fs.readFileSync(p,'utf8'));c.observability={enabled:false};if(c.dev)c.dev.enable_containers=false;fs.writeFileSync(p,JSON.stringify(c));"

FROM node:22-alpine AS runtime

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    HOST=0.0.0.0 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    WRANGLER_SEND_METRICS=false \
    WRANGLER_WRITE_LOGS=false

WORKDIR /app

COPY --from=build --chown=node:node /workspace/frontend /app

USER node

EXPOSE 3000

CMD ["node", "node_modules/wrangler/bin/wrangler.js", "dev", "--config", "dist/server/wrangler.json", "--local", "--ip", "0.0.0.0", "--port", "3000", "--show-interactive-dev-session=false", "--log-level=warn"]
