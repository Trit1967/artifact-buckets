# Artifact Buckets app: FastAPI API + MCP endpoint + the built web UI.
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# Optional build secret "ca": an extra CA for networks with a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ubuntu:24.04
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PATH=/opt/venv/bin:$PATH
# acl: setfacl/getfacl to write SMB permissions. attr: mergerfs control xattrs.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 python3-venv acl attr ca-certificates \
    && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/venv
WORKDIR /app
COPY server/requirements.txt ./
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; pip install -r requirements.txt
COPY server/app ./app
COPY server/scripts ./scripts
COPY plugin-marketplace/artifact-buckets ./plugin
COPY --from=web /web/dist ./web
ENV AB_STATIC_DIR=/app/web AB_PLUGIN_DIR=/app/plugin AB_TREE_ROOT=/data/tree AB_VOLUMES_ROOT=/data/volumes
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
# Runs as root inside the container, with every capability dropped except the three it needs to
# set file owners and ACLs for AD users (see docker-compose.yml). The root filesystem is read-only.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
