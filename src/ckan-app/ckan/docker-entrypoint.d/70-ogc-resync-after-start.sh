#!/bin/bash
# Resync OGC/pygeoapi apres demarrage CKAN.
#
# Au redemarrage du conteneur, la config pygeoapi (local.config.yml, volume partage)
# peut retomber a ~1 collection tant que l'auto-sync du plugin ogc n'a pas repasse
# tous les datasets (l'indexation Solr n'est pas terminee au boot). On relance donc,
# en arriere-plan et sans bloquer le demarrage, un full sync qui repeuple pygeoapi.
#
# Idempotent : ne fait rien si pygeoapi expose deja plusieurs collections (garde
# anti-double, l'image sert aussi le conteneur pygeoapi). Non bloquant.

set -e

CONF="/srv/app/pygeoapi-conf/local.config.yml"
SYNC="/srv/app/ckanext-ogc/pygeoapi-providers/ckan_provider/ckan_sync.py"
[ -f "$SYNC" ] || SYNC="/srv/app/pygeoapi-providers/ckan_provider/ckan_sync.py"
CKAN_LOCAL="http://localhost:5000"
PYGEOAPI="${PYGEOAPI_URL:-http://pygeoapi:5001}"

if [ ! -f "$SYNC" ]; then
  echo "[ogc-resync] ckan_sync.py introuvable, skip"
  exit 0
fi

(
  # attendre que CKAN reponde (Solr + indexation en cours de demarrage)
  for i in $(seq 1 60); do
    curl -sf "$CKAN_LOCAL/api/3/action/status_show" >/dev/null 2>&1 && break
    sleep 5
  done
  # laisser l'indexation avancer
  sleep 45
  # garde : si pygeoapi expose deja plusieurs collections, ne rien faire
  n=$(curl -sf "$PYGEOAPI/collections?f=json" 2>/dev/null \
        | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('collections',[])))" 2>/dev/null || echo 0)
  if [ "${n:-0}" -gt 5 ]; then
    echo "[ogc-resync] $n collections deja exposees, skip" >> /tmp/ogc-resync.log 2>&1
    exit 0
  fi
  echo "[ogc-resync] $(date) full sync (collections=$n)" >> /tmp/ogc-resync.log 2>&1
  python3 "$SYNC" \
    --ckan-url "${CKAN_SITE_URL:-http://ckan:5000}" \
    --ckan-api-key "${CKAN_API_KEY}" \
    --pygeoapi-config "$CONF" \
    --pygeoapi-url "$PYGEOAPI" >> /tmp/ogc-resync.log 2>&1 || true
  echo "[ogc-resync] $(date) termine" >> /tmp/ogc-resync.log 2>&1
) &

echo "[ogc-resync] planifie en arriere-plan"
