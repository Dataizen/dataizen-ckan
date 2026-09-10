#!/bin/bash
# Script wrapper pour fix-failed-transactions.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/fix-failed-transactions.py"

# Vérifier si on est dans un conteneur Docker ou localement
if [ -f /.dockerenv ] || [ -n "${DOCKER_CONTAINER:-}" ]; then
    # Dans un conteneur Docker
    python3 "$SCRIPT_PATH" "$@"
else
    # Localement, utiliser docker compose exec
    docker compose exec -T ckan python3 /srv/app/scripts/fix-failed-transactions.py "$@"
fi











