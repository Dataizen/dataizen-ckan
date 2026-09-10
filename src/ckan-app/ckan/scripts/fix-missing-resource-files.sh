#!/bin/bash
#
# Script wrapper pour fix-missing-resource-files.py
#
# Usage:
#   ./fix-missing-resource-files.sh --check                    # Vérifier toutes les ressources
#   ./fix-missing-resource-files.sh --check --resource-id <id>  # Vérifier une ressource spécifique
#   ./fix-missing-resource-files.sh --fix                      # Corriger automatiquement
#   ./fix-missing-resource-files.sh --fix --resource-id <id>   # Corriger une ressource spécifique
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/fix-missing-resource-files.py"

# Vérifier si on est dans un conteneur Docker ou localement
if [ -f /.dockerenv ] || [ -n "${DOCKER_CONTAINER:-}" ]; then
    # Dans un conteneur Docker
    python3 "$SCRIPT_PATH" "$@"
else
    # Localement, utiliser docker compose exec
    docker compose exec -T ckan python3 /srv/app/scripts/fix-missing-resource-files.py "$@"
fi











