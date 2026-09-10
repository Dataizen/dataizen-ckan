#!/bin/bash
# Test des 3 API CKAN avec champs OGC : resource_show, package_show, organization_show
# Usage: ./scripts/test-ogc-api-show.sh [ORGANIZATION_ID]
# Exemple: ./scripts/test-ogc-api-show.sh dreal-bourgogne-franche-comte

CKAN_URL="${CKAN_URL:-https://ckan2.qualif-data.example.org}"
ORG_ID="${1:-dreal-bourgogne-franche-comte}"

echo "=========================================="
echo "Test API OGC - $CKAN_URL"
echo "Organisation: $ORG_ID"
echo "Note: package_show et organization_show requièrent include_ogc=true pour les champs OGC"
echo "=========================================="

# 1. organization_show (CKAN API 3.x requiert POST)
echo ""
echo "--- 1. organization_show ---"
ORG_RESP=$(curl -sS -X POST "${CKAN_URL}/api/3/action/organization_show" -H "Content-Type: application/json" -d "{\"id\": \"${ORG_ID}\"}")
if echo "$ORG_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
  echo "organization_show OK"
  echo "$ORG_RESP" | jq '.result | {
    name,
    title,
    package_count,
    wms_url,
    wfs_url,
    wms_getcapabilities_url,
    wfs_getcapabilities_url,
    ogc_datasets_count,
    ogc_datasets: (.ogc_datasets | length)
  }'
  echo ""
  echo "Premiers datasets OGC (wms_url, wfs_url):"
  echo "$ORG_RESP" | jq -r '.result.ogc_datasets[:3][]? | "  - \(.name): WMS=\(.wms_url // "n/a")"'
else
  echo "organization_show FAILED"
  echo "$ORG_RESP" | jq .
  exit 1
fi

# Récupérer un dataset pour package_show
PACKAGE_ID=$(echo "$ORG_RESP" | jq -r '.result.ogc_datasets[0].name // .result.packages[0].name // empty')
if [ -z "$PACKAGE_ID" ]; then
  # Fallback: package_search
  SEARCH=$(curl -sS -X POST "${CKAN_URL}/api/3/action/package_search" -H "Content-Type: application/json" -d "{\"fq\": \"organization:${ORG_ID}\", \"rows\": 1}")
  PACKAGE_ID=$(echo "$SEARCH" | jq -r '.result.results[0].name // empty')
fi

if [ -z "$PACKAGE_ID" ]; then
  echo ""
  echo "Aucun dataset trouvé dans l'organisation, test package_show/resource_show ignorés"
  exit 0
fi

# 2. package_show
echo ""
echo "--- 2. package_show (dataset: $PACKAGE_ID) ---"
PKG_RESP=$(curl -sS -X POST "${CKAN_URL}/api/3/action/package_show" -H "Content-Type: application/json" -d "{\"id\": \"${PACKAGE_ID}\", \"include_ogc\": true}")
if echo "$PKG_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
  echo "package_show OK"
  echo "$PKG_RESP" | jq '.result | {
    name,
    title,
    is_geospatial,
    has_wms_wfs,
    wms_url,
    wfs_url,
    ogc_resources_count: (.ogc_resources | length)
  }'
else
  echo "package_show FAILED"
  echo "$PKG_RESP" | jq .
fi

# Récupérer une ressource pour resource_show
RESOURCE_ID=$(echo "$PKG_RESP" | jq -r '.result.resources[0].id // empty')
if [ -z "$RESOURCE_ID" ]; then
  echo ""
  echo "Aucune ressource dans le dataset, test resource_show ignoré"
  exit 0
fi

# 3. resource_show
echo ""
echo "--- 3. resource_show (resource: $RESOURCE_ID) ---"
RES_RESP=$(curl -sS -X POST "${CKAN_URL}/api/3/action/resource_show" -H "Content-Type: application/json" -d "{\"id\": \"${RESOURCE_ID}\"}")
if echo "$RES_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
  echo "resource_show OK"
  echo "$RES_RESP" | jq '.result | {
    id,
    name,
    format,
    is_geospatial,
    has_wms_wfs,
    wms_url,
    wfs_url
  }'
else
  echo "resource_show FAILED"
  echo "$RES_RESP" | jq .
fi

echo ""
echo "=========================================="
echo "Résumé - URLs pour QGIS (organisation)"
echo "=========================================="
echo "$ORG_RESP" | jq -r '
  "WMS (toute l'\''org):  " + (.result.wms_url // "n/a") + "\n" +
  "WFS (toute l'\''org):  " + (.result.wfs_url // "n/a")
'
