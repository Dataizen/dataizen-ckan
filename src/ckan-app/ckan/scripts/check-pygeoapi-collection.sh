#!/bin/bash
# Script pour vérifier si une collection pygeoapi existe et la synchroniser si nécessaire

set -e

COLLECTION_NAME="${1:-test-guillaume-sld}"
PYGEOAPI_URL="${PYGEOAPI_URL:-http://localhost:5001}"
PYGEOAPI_CONFIG="${PYGEOAPI_CONFIG:-/srv/app/pygeoapi/local.config.yml}"

echo "Vérification de la collection pygeoapi: $COLLECTION_NAME"
echo "pygeoapi URL: $PYGEOAPI_URL"
echo "Config: $PYGEOAPI_CONFIG"

# Vérifier si la collection existe dans pygeoapi
echo ""
echo "1⃣ Vérification via API pygeoapi..."
COLLECTIONS_RESPONSE=$(curl -s "$PYGEOAPI_URL/collections" || echo "ERROR")
if [ "$COLLECTIONS_RESPONSE" = "ERROR" ]; then
  echo "Impossible de contacter pygeoapi à $PYGEOAPI_URL"
  exit 1
fi

COLLECTION_EXISTS=$(echo "$COLLECTIONS_RESPONSE" | grep -o "\"id\":\"$COLLECTION_NAME\"" || echo "")
if [ -n "$COLLECTION_EXISTS" ]; then
  echo "Collection '$COLLECTION_NAME' trouvée dans pygeoapi"
else
  echo "Collection '$COLLECTION_NAME' NON trouvée dans pygeoapi"
fi

# Vérifier si la collection existe dans la config
echo ""
echo "2⃣ Vérification dans la configuration..."
if [ ! -f "$PYGEOAPI_CONFIG" ]; then
  echo "Fichier de configuration non trouvé: $PYGEOAPI_CONFIG"
  exit 1
fi

CONFIG_EXISTS=$(grep -A 5 "resources:" "$PYGEOAPI_CONFIG" | grep -A 5 "$COLLECTION_NAME:" || echo "")
if [ -n "$CONFIG_EXISTS" ]; then
  echo "Collection '$COLLECTION_NAME' trouvée dans la configuration"
  echo "Extrait de la config:"
  echo "$CONFIG_EXISTS" | head -10
else
  echo "Collection '$COLLECTION_NAME' NON trouvée dans la configuration"
fi

# Vérifier si le dataset existe dans CKAN
echo ""
echo "3⃣ Vérification dans CKAN..."
CKAN_URL="${CKAN_URL:-http://localhost:5000}"
CKAN_API_KEY="${CKAN_API_KEY:-}"

if [ -z "$CKAN_API_KEY" ]; then
  echo " CKAN_API_KEY non défini, utilisation sans authentification"
  HEADERS=""
else
  HEADERS="-H \"Authorization: $CKAN_API_KEY\""
fi

DATASET_RESPONSE=$(eval curl -s $HEADERS "$CKAN_URL/api/action/package_show?id=$COLLECTION_NAME" || echo "ERROR")
if [ "$DATASET_RESPONSE" = "ERROR" ]; then
  echo "Impossible de contacter CKAN à $CKAN_URL"
  exit 1
fi

DATASET_EXISTS=$(echo "$DATASET_RESPONSE" | grep -o "\"success\":true" || echo "")
if [ -n "$DATASET_EXISTS" ]; then
  echo "Dataset '$COLLECTION_NAME' trouvé dans CKAN"
  
  # Vérifier les ressources
  RESOURCES=$(echo "$DATASET_RESPONSE" | grep -o "\"resources\":\[.*\]" || echo "")
  if [ -n "$RESOURCES" ]; then
    echo "Ressources trouvées dans le dataset"
    # Extraire les formats
    FORMATS=$(echo "$DATASET_RESPONSE" | grep -o "\"format\":\"[^\"]*\"" | head -5)
    echo "Formats: $FORMATS"
  fi
else
  echo "Dataset '$COLLECTION_NAME' NON trouvé dans CKAN"
  exit 1
fi

# Suggestions
echo ""
echo "Suggestions:"
if [ -z "$COLLECTION_EXISTS" ] || [ -z "$CONFIG_EXISTS" ]; then
  echo "   Pour synchroniser la collection, exécutez:"
  echo "   ckan -c /srv/app/ckan.ini pygeoapi-sync --dataset-id=$COLLECTION_NAME"
  echo ""
  echo "   Ou pour synchroniser tous les datasets:"
  echo "   ckan -c /srv/app/ckan.ini pygeoapi-sync"
else
  echo "   La collection semble être synchronisée"
  echo "   Si elle n'apparaît toujours pas, redémarrez pygeoapi:"
  echo "   kubectl delete pod <pod-pygeoapi> -n <namespace>"
fi








