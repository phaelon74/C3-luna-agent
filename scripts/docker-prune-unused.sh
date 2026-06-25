#!/usr/bin/env bash
# Remove Docker images not referenced by any container (running or stopped).
#
# Safe for the Mose stack: compose services keep the images they are using.
# Intermediate layers from `docker compose build` during the week are reclaimed.
#
# Manual run (as a user in the docker group):
#   ~/mose-agent/scripts/docker-prune-unused.sh
#
# Weekly schedule: see mose-docker-prune.service / mose-docker-prune.timer
set -euo pipefail

log() {
  echo "[$(date -Is)] $*"
}

if ! command -v docker >/dev/null 2>&1; then
  log "ERROR: docker not found in PATH"
  exit 1
fi

log "Docker disk usage before prune:"
docker system df || true

log "Pruning images not used by any container..."
docker image prune -a -f

log "Docker disk usage after prune:"
docker system df || true

log "Root filesystem:"
df -h /

log "Done."
