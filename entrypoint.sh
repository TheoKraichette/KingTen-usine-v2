#!/bin/sh
# Démarre en root : prépare credentials, accès au socket Docker et volumes, puis bascule sur usine.
set -e

# Credentials Claude : seed depuis le montage read-only UNIQUEMENT s'ils n'existent pas encore.
# Le home /home/usine/.claude est un volume persistant : les refresh de token OAuth faits par
# le CLI dans le conteneur survivent entre invocations au lieu de diverger de l'hôte.
mkdir -p /home/usine/.claude
if [ -f /creds/.credentials.json ] && [ ! -f /home/usine/.claude/.credentials.json ]; then
    cp /creds/.credentials.json /home/usine/.claude/.credentials.json
fi
chown -R usine:usine /home/usine/.claude
chmod 600 /home/usine/.claude/.credentials.json 2>/dev/null || true

# Socket Docker : accès par groupe (pas de chmod 666 — le socket est partagé avec tout l'hôte)
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c %g /var/run/docker.sock)
    if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
        groupadd -g "$SOCK_GID" dockersock
    fi
    usermod -aG "$SOCK_GID" usine 2>/dev/null || true
fi

# Volumes montés : appartiennent à usine
mkdir -p /app/output /app/data
chown usine:usine /app/output /app/data

exec gosu usine python -m usine "$@"
