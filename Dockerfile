# Image épinglée : rebuilds reproductibles (bump volontaire uniquement)
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# CA d'inspection TLS de la machine hôte (WatchGuard / Cloudflare Gateway)
COPY certs/*.crt /usr/local/share/ca-certificates/

# Node.js + npm (requis par Claude CLI) + gosu (drop de privilèges) + docker CLI statique
# claude-code épinglé : ses releases hebdomadaires changent le comportement des agents
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gosu ca-certificates nodejs npm \
    && update-ca-certificates \
    && npm install -g @anthropic-ai/claude-code@2.1.198 \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dépendances des templates préinstallées : l'agent dev n'a plus de pip install à faire (~60 s/run)
COPY templates/webapp/requirements.txt /tmp/req-webapp.txt
COPY templates/api/requirements.txt /tmp/req-api.txt
RUN pip install --no-cache-dir -r /tmp/req-webapp.txt -r /tmp/req-api.txt

# Chromium + dépendances système pour Playwright (partagé via PLAYWRIGHT_BROWSERS_PATH)
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Outillage qualité (layer léger, placé après les layers lourds pour préserver leur cache)
RUN pip install --no-cache-dir ruff==0.15.21

COPY . .

# Utilisateur non-root (requis pour le mode bypassPermissions du SDK)
RUN useradd -m -s /bin/bash usine \
    && sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /app/output /app/data /home/usine/.claude \
    && chown -R usine:usine /app /home/usine/.claude

# Toute la config du CLI Claude (y compris .claude.json, sinon dans ~) vit dans le volume
# persistant : elle survit d'une invocation à l'autre au lieu d'être perdue à chaque conteneur
ENV CLAUDE_CONFIG_DIR=/home/usine/.claude

ENTRYPOINT ["/app/entrypoint.sh"]
