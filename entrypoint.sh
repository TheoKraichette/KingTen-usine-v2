#!/bin/sh
# Démarre en root : prépare credentials, accès au socket Docker et volumes, puis bascule sur usine.
set -e

# Credentials Claude : prendre les PLUS RÉCENTS entre le montage hôte (read-only) et le
# volume persistant, par comparaison des expiresAt. L'hôte rafraîchit ses tokens en continu :
# un vieux refresh token côté conteneur finit invalidé par la rotation (vu le 2026-07-16),
# et inversement un run long peut rafraîchir côté conteneur — on garde alors le volume.
mkdir -p /home/usine/.claude
if [ -f /creds/.credentials.json ]; then
    host_exp=$(sed -n 's/.*"expiresAt": *\([0-9]*\).*/\1/p' /creds/.credentials.json | head -n 1)
    vol_exp=$(sed -n 's/.*"expiresAt": *\([0-9]*\).*/\1/p' /home/usine/.claude/.credentials.json 2>/dev/null | head -n 1)
    if [ ! -f /home/usine/.claude/.credentials.json ] || [ "${host_exp:-0}" -gt "${vol_exp:-0}" ]; then
        cp /creds/.credentials.json /home/usine/.claude/.credentials.json
    fi
fi

# Config CLI (.claude.json, dans le volume via CLAUDE_CONFIG_DIR) : si elle manque mais
# qu'un backup du CLI existe, restaurer le plus récent — sans elle le CLI refuse de démarrer
if [ ! -f /home/usine/.claude/.claude.json ]; then
    latest_backup=$(ls -t /home/usine/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n 1)
    if [ -n "$latest_backup" ]; then
        cp "$latest_backup" /home/usine/.claude/.claude.json
    fi
fi

chown -R usine:usine /home/usine/.claude
chmod 600 /home/usine/.claude/.credentials.json 2>/dev/null || true

# Socket Docker : accès par groupe + g+rw scopé (pas de chmod 666 — le socket est
# partagé avec tout l'hôte, l'élargissement au monde persisterait après la sortie)
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c %g /var/run/docker.sock)
    if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
        groupadd -g "$SOCK_GID" dockersock
    fi
    usermod -aG "$SOCK_GID" usine 2>/dev/null || true
    chmod g+rw /var/run/docker.sock 2>/dev/null || true
fi

# Volumes montés : appartiennent à usine
mkdir -p /app/output /app/data
chown usine:usine /app/output /app/data

exec gosu usine python -m usine "$@"
