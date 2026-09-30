# Variant of Plow's maintained OpenClaw runtime. Pin the upstream commit
# encoded in the immutable tag and the registry manifest digest.
ARG PLOW_BASE=public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-771198a9609dcef54d44843e7da5329c17fa51b4@sha256:f1e7c421b97a80f1bd17015f96daceb965f350a241f7edc7e4d856a0e3a6f8f5

FROM ${PLOW_BASE} AS stl-plugin-build
USER root
WORKDIR /tmp/print-farm-stl-plugin
COPY openclaw/plugins/print-farm-stl/package.json openclaw/plugins/print-farm-stl/package-lock.json ./
RUN npm ci --include=dev
COPY openclaw/plugins/print-farm-stl/tsconfig.json ./tsconfig.json
COPY openclaw/plugins/print-farm-stl/openclaw.plugin.json ./openclaw.plugin.json
COPY openclaw/plugins/print-farm-stl/src/ ./src/
RUN npm run build

FROM ${PLOW_BASE} AS runtime

LABEL org.opencontainers.image.title="Print Farm Operator" \
      org.opencontainers.image.description="OpenClaw on Plow for small 3D printing farm operations"

ENV AGENT_ID=print-farm-operator \
    AGENT_NAME="Print Farm Operator" \
    AGENT_BLURB="A 3D printing farm operations agent for STL analysis, quoting review, and manual production workflows." \
    AGENT_RUNTIME="OpenClaw on Plow" \
    PLOW_THREAD_TRUST=ask \
    PRINT_FARM_DATABASE_PATH=/var/lib/plow/print-farm/farm.sqlite \
    PRINT_FARM_JOBS_PATH=/var/lib/plow/print-farm/jobs

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/plow/print-farm /var/lib/plow/print-farm/jobs \
    && chown -R node:node /var/lib/plow/print-farm

COPY experiments/ /opt/plow/print-farm/experiments/
COPY docker/plow_domain_tools.py /opt/plow/print-farm/plow_domain_tools.py
COPY docker/enable_print_farm_plugin.py /opt/plow/print-farm/enable_plugin.py
COPY --from=stl-plugin-build /tmp/print-farm-stl-plugin/package.json /opt/plow/print-farm-stl-plugin/package.json
COPY --from=stl-plugin-build /tmp/print-farm-stl-plugin/openclaw.plugin.json /opt/plow/print-farm-stl-plugin/openclaw.plugin.json
COPY --from=stl-plugin-build /tmp/print-farm-stl-plugin/dist/ /opt/plow/print-farm-stl-plugin/dist/
COPY --from=stl-plugin-build /tmp/print-farm-stl-plugin/node_modules/typebox/ /opt/plow/print-farm-stl-plugin/node_modules/typebox/
COPY openclaw/workspace/AGENTS.md /opt/plow/prompt/AGENTS.md
COPY openclaw/workspace/skills/analyze-stl/ /opt/plow/skills/analyze-stl/
COPY openclaw/workspace/skills/print-farm-operations/ /opt/plow/skills/print-farm-operations/

RUN python3 /opt/plow/print-farm/enable_plugin.py \
    && node /opt/plow/build.ts

USER node
