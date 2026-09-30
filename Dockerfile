FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    APP_ROOT=/opt/print-farm-operator \
    OPENCLAW_STATE_DIR=/data/openclaw \
    OPENCLAW_CONFIG_PATH=/data/openclaw/openclaw.json \
    HOME=/data/home \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global openclaw@2026.8.1 \
    && mkdir -p /opt/print-farm-operator /data \
    && chown -R node:node /data

WORKDIR /opt/print-farm-operator

COPY experiments/ ./experiments/
COPY openclaw/workspace/AGENTS.md ./openclaw/workspace/AGENTS.md
COPY openclaw/workspace/skills/ ./openclaw/workspace/skills/
COPY openclaw/plugins/print-farm-stl/ ./openclaw/plugins/print-farm-stl/
COPY standalone/agent_index_client.py ./standalone/agent_index_client.py
COPY docker/ ./docker/

RUN cd openclaw/plugins/print-farm-stl \
    && npm ci \
    && npm run build \
    && npm prune --omit=dev \
    && chmod 0555 /opt/print-farm-operator/docker/entrypoint.sh \
    && chmod 0555 /opt/print-farm-operator/docker/agent-index-loop.sh \
    && chmod 0555 /opt/print-farm-operator/standalone/agent_index_client.py \
    && chown -R node:node /opt/print-farm-operator

USER node
EXPOSE 18789
VOLUME ["/data"]
ENTRYPOINT ["/opt/print-farm-operator/docker/entrypoint.sh"]
