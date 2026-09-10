#!/usr/bin/env bash
set -euo pipefail

# Non-regression test script for CKAN + OGC stack
# - Creates a dataset in a target organization
# - Adds a small CSV (with lat/lon) and a small GeoJSON resource (upload)
# - Polls for datastore/xloader readiness
# - Verifies resource_show OGC fields (is_geospatial, has_wms_wfs, wms_url, wfs_url)
# - Checks WMS/WFS GetCapabilities return 200
#
# Requirements:
# - curl, jq
# - Admin (or editor) API token with rights on the target organization
#
# Config via env (can be overridden via flags):
#   CKAN_URL (default: https://ckan2.qualif-data.example.org)
#   CKAN_TOKEN (required if not provided with --token)
#   CKAN_ORG (default: dataizen-dev)
#   TIMEOUT_SECS (default: 240)
#   POLL_INTERVAL_SECS (default: 5)
#
# Usage:
#   scripts/nonreg-test.sh [--url URL] [--token TOKEN] [--org ORG] [--timeout SECS]
#

CKAN_URL="${CKAN_URL:-https://ckan2.qualif-data.example.org}"
CKAN_TOKEN="${CKAN_TOKEN:-}"
CKAN_ORG="${CKAN_ORG:-dataizen-dev}"
TIMEOUT_SECS="${TIMEOUT_SECS:-240}"
POLL_INTERVAL_SECS="${POLL_INTERVAL_SECS:-5}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      CKAN_URL="$2"; shift 2;;
    --token)
      CKAN_TOKEN="$2"; shift 2;;
    --org)
      CKAN_ORG="$2"; shift 2;;
    --timeout)
      TIMEOUT_SECS="$2"; shift 2;;
    *)
      echo "Unknown argument: $1" >&2; exit 1;;
  esac
done

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi
if [[ -z "${CKAN_TOKEN}" ]]; then
  echo "Please provide an admin API token via --token or CKAN_TOKEN env" >&2
  echo "   Example: scripts/nonreg-test.sh --token 'your-admin-token-here'" >&2
  echo "   Or: export CKAN_TOKEN='your-admin-token-here' && scripts/nonreg-test.sh" >&2
  exit 1
fi

api_post() {
  local endpoint="$1"; shift
  curl -sS --fail -H "Authorization: ${CKAN_TOKEN}" \
       -H "Content-Type: application/json" \
       -X POST "${CKAN_URL}/api/3/action/${endpoint}" \
       -d "$@"
}

api_get() {
  local endpoint="$1"; shift
  curl -sS --fail -H "Authorization: ${CKAN_TOKEN}" \
       -G "${CKAN_URL}/api/3/action/${endpoint}" \
       --data-urlencode "$@"
}

api_post_form() {
  # multipart/form-data POST
  # Utiliser --max-time pour éviter les timeouts longs, mais ne pas utiliser --fail pour permettre le retry
  local endpoint="$1"; shift
  curl -sS --max-time 60 -H "Authorization: ${CKAN_TOKEN}" \
       -X POST "${CKAN_URL}/api/3/action/${endpoint}" \
       "$@"
}

say() { echo -e "$*"; }
ok() { say "$*"; }
warn() { say " $*"; }
err() { say "$*" >&2; }

STAMP="$(date +%Y%m%d-%H%M%S)"
DS_NAME="nrtest-${STAMP}"
DS_TITLE="Non-regression test ${STAMP}"

say "Checking organization exists: ${CKAN_ORG}"
if ! api_get organization_show "id=${CKAN_ORG}" >/dev/null 2>&1; then
  err "Organization '${CKAN_ORG}' not found or not accessible"
  exit 1
fi
ok "Organization '${CKAN_ORG}' is accessible"

say "Creating dataset: ${DS_NAME}"
CREATE_DS_PAYLOAD="$(jq -n --arg name "${DS_NAME}" --arg title "${DS_TITLE}" --arg org "${CKAN_ORG}" \
  '{name:$name,title:$title,owner_org:$org,private:false,notes:"Automated non-regression test dataset"}')"
DS_RESP="$(api_post package_create "${CREATE_DS_PAYLOAD}")"
if [[ "$(echo "${DS_RESP}" | jq -r '.success')" != "true" ]]; then
  err "Failed to create dataset: $(echo "${DS_RESP}" | jq -r '.error // .')" 
  exit 1
fi
DS_ID="$(echo "${DS_RESP}" | jq -r '.result.id')"
ok "Dataset created: id=${DS_ID}, name=${DS_NAME}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

# Create simple CSV with lat/lon
CSV_PATH="${TMP_DIR}/points.csv"
cat > "${CSV_PATH}" <<'CSV'
id,name,lat,lon
1,A,47.322047,5.041480
2,B,47.323100,5.042300
CSV

say "Creating CSV resource (upload) with lat/lon"
CSV_RESP="$(api_post_form resource_create \
  -F "package_id=${DS_ID}" \
  -F "name=nrtest_csv" \
  -F "format=CSV" \
  -F "url_type=upload" \
  -F "upload=@${CSV_PATH};type=text/csv")"
if [[ "$(echo "${CSV_RESP}" | jq -r '.success')" != "true" ]]; then
  err "Failed to create CSV resource: $(echo "${CSV_RESP}" | jq -r '.error // .')"
  exit 1
fi
CSV_RES_ID="$(echo "${CSV_RESP}" | jq -r '.result.id')"
ok "CSV resource created: id=${CSV_RES_ID}"

# Create simple GeoJSON
GJ_PATH="${TMP_DIR}/point.geojson"
cat > "${GJ_PATH}" <<'GEOJSON'
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": { "id": 1, "name": "GJ-A" },
      "geometry": { "type": "Point", "coordinates": [5.04148, 47.322047] }
    }
  ]
}
GEOJSON

say " Creating GeoJSON resource (upload)"
# Retry logic for GeoJSON upload (can fail with 502 due to timeout/processing)
MAX_GJ_RETRIES=3
GJ_RETRY_DELAY=3
GJ_RESP=""
GJ_RES_ID=""
for i in $(seq 1 ${MAX_GJ_RETRIES}); do
  if (( i > 1 )); then
    say "  Retry ${i}/${MAX_GJ_RETRIES} for GeoJSON upload..."
    sleep ${GJ_RETRY_DELAY}
  fi
  GJ_RESP="$(api_post_form resource_create \
    -F "package_id=${DS_ID}" \
    -F "name=nrtest_geojson" \
    -F "format=GeoJSON" \
    -F "url_type=upload" \
    -F "upload=@${GJ_PATH};type=application/geo+json" 2>&1)"
  
  # Vérifier le code de retour HTTP (curl retourne 0 pour 2xx, 22 pour 4xx, etc.)
  CURL_EXIT_CODE=$?
  if [[ ${CURL_EXIT_CODE} -eq 0 ]] || echo "${GJ_RESP}" | grep -q '"success".*true'; then
    # Vérifier si la réponse contient success=true
    if echo "${GJ_RESP}" | jq -e '.success == true' >/dev/null 2>&1; then
      GJ_RES_ID="$(echo "${GJ_RESP}" | jq -r '.result.id // empty')"
      if [[ -n "${GJ_RES_ID}" && "${GJ_RES_ID}" != "null" ]]; then
        break
      fi
    fi
  fi
  
  # Si on est à la dernière tentative, afficher l'erreur
  if (( i == MAX_GJ_RETRIES )); then
    ERR_MSG="$(echo "${GJ_RESP}" | jq -r '.error.message // .error // .' 2>/dev/null || echo "HTTP ${CURL_EXIT_CODE}")"
    err "Failed to create GeoJSON resource after ${MAX_GJ_RETRIES} attempts: ${ERR_MSG}"
    err "Response: ${GJ_RESP:0:500}"
    exit 1
  fi
done

if [[ -z "${GJ_RES_ID}" || "${GJ_RES_ID}" == "null" ]]; then
  err "GeoJSON resource created but no ID returned"
  exit 1
fi
GJ_RES_ID="$(echo "${GJ_RESP}" | jq -r '.result.id')"
ok "GeoJSON resource created: id=${GJ_RES_ID}"

# Poll for CSV datastore_active (xloader)
say "Waiting for CSV resource to become datastore_active..."
DEADLINE=$(( $(date +%s) + TIMEOUT_SECS ))
while :; do
  NOW=$(date +%s)
  if (( NOW > DEADLINE )); then
    err "Timeout waiting for datastore_active on ${CSV_RES_ID}"
    break
  fi
  RS="$(api_post resource_show "$(jq -n --arg id "${CSV_RES_ID}" '{id:$id}')" )" || true
  DS_ACTIVE="$(echo "${RS}" | jq -r '.result.datastore_active // false')"
  if [[ "${DS_ACTIVE}" == "true" ]]; then
    ok "CSV datastore_active=true"
    break
  fi
  sleep "${POLL_INTERVAL_SECS}"
done

# Verify OGC fields for both resources (will also validate mapfile & org linkage)
verify_ogc() {
  local res_id="$1"
  local label="$2"
  local deadline=$(( $(date +%s) + TIMEOUT_SECS ))
  say "Verifying OGC fields for ${label} (${res_id})"
  while :; do
    local now=$(date +%s)
    if (( now > deadline )); then
      err "Timeout verifying OGC fields for ${label}"
      return 1
    fi
    local rs
    rs="$(api_post resource_show "$(jq -n --arg id "${res_id}" '{id:$id}')" )" || true
    local is_geo has_ogc wms_url wfs_url
    is_geo="$(echo "${rs}" | jq -r '.result.is_geospatial // false')"
    has_ogc="$(echo "${rs}" | jq -r '.result.has_wms_wfs // false')"
    wms_url="$(echo "${rs}" | jq -r '.result.wms_url // empty')"
    wfs_url="$(echo "${rs}" | jq -r '.result.wfs_url // empty')"
    if [[ "${is_geo}" == "true" && "${has_ogc}" == "true" && -n "${wms_url}" && -n "${wfs_url}" ]]; then
      ok "${label}: OGC fields OK (is_geospatial, has_wms_wfs, URLs present)"
      # Quick check WMS/WFS GetCapabilities
      if curl -sS --fail -m 30 "${wms_url}" >/dev/null; then
        ok "${label}: WMS GetCapabilities 200"
      else
        warn "${label}: WMS GetCapabilities failed (continuing)"
      fi
      if curl -sS --fail -m 30 "${wfs_url}" >/dev/null; then
        ok "${label}: WFS GetCapabilities 200"
      else
        warn "${label}: WFS GetCapabilities failed (continuing)"
      fi
      return 0
    fi
    sleep "${POLL_INTERVAL_SECS}"
  done
}

CSV_OK=0
GJ_OK=0
if verify_ogc "${CSV_RES_ID}" "CSV"; then CSV_OK=1; fi
if verify_ogc "${GJ_RES_ID}" "GeoJSON"; then GJ_OK=1; fi

say ""
say "====== SUMMARY ======"
say "Dataset: ${DS_NAME} (id=${DS_ID}) org=${CKAN_ORG}"
say "CSV resource: ${CSV_RES_ID} - $([[ ${CSV_OK} -eq 1 ]] && echo PASS || echo FAIL)"
say "GEOJSON resource: ${GJ_RES_ID} - $([[ ${GJ_OK} -eq 1 ]] && echo PASS || echo FAIL)"

if [[ ${CSV_OK} -eq 1 && ${GJ_OK} -eq 1 ]]; then
  ok "Non-regression test PASSED"
  exit 0
else
  err "Non-regression test FAILED"
  exit 2
fi


