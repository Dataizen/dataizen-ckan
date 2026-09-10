#!/bin/bash
set -e

# Timeout global des jobs RQ : 2 h (7200 s). Nécessaire pour l'import Datagis
# (gros fichiers, 600+ ressources). Sinon RQ tue le job à 180 s par défaut.
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"
echo "Configuration ckan.jobs.timeout = 7200 (2 h) pour import Datagis / jobs longs..."
ckan config-tool "$CKAN_INI" "ckan.jobs.timeout=7200" || echo "Impossible de configurer ckan.jobs.timeout"
echo "ckan.jobs.timeout configuré"
