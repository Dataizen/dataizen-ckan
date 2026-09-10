#!/bin/bash

# Script pour ajouter un dataset de test avec données géospatiales
# Utilisé pour tester les services OGC (WMS/WFS/GeoJSON)

echo "Ajout d'un dataset de test géospatial..."

# Configuration (jeton CKAN via l'environnement, ne pas coder en dur)
API_KEY="${CKAN_API_KEY:?Définir CKAN_API_KEY (jeton CKAN) dans l'environnement}"
CKAN_URL="${CKAN_URL:-http://localhost:5000}"
DATASET_NAME="test-ogc-dataset"
DATASET_TITLE="Dataset de test OGC - Villes BFC"

# Vérifier que CKAN est accessible
if ! curl -s -f "$CKAN_URL/api/action/status_show" > /dev/null; then
    echo "CKAN n'est pas accessible"
    exit 1
fi

# Supprimer le dataset s'il existe déjà
echo "Nettoyage du dataset existant..."
curl -s -X POST "$CKAN_URL/api/action/package_delete" \
    -H "Authorization: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"id\": \"$DATASET_NAME\"}" > /dev/null 2>&1 || true

curl -s -X POST "$CKAN_URL/api/action/dataset_purge" \
    -H "Authorization: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"id\": \"$DATASET_NAME\"}" > /dev/null 2>&1 || true

# Créer le dataset
echo "Création du dataset..."
DATASET_RESPONSE=$(curl -s -X POST "$CKAN_URL/api/action/package_create" \
    -H "Authorization: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"$DATASET_NAME\",
        \"title\": \"$DATASET_TITLE\",
        \"notes\": \"Dataset de test contenant 3 villes de Bourgogne-Franche-Comté avec coordonnées géographiques. Utilisé pour tester les services OGC (WMS, WFS, GeoJSON).\",
        \"owner_org\": \"dataizen-dev\",
        \"license_id\": \"cc-by\",
        \"tags\": [
            {\"name\": \"test\"},
            {\"name\": \"geospatial\"},
            {\"name\": \"ogc\"},
            {\"name\": \"bourgogne-franche-comte\"}
        ],
        \"extras\": [
            {\"key\": \"spatial\", \"value\": \"{\\\"type\\\": \\\"Polygon\\\", \\\"coordinates\\\": [[[4.8, 46.7], [6.1, 46.7], [6.1, 47.4], [4.8, 47.4], [4.8, 46.7]]]}\"}
        ]
    }")

# Vérifier la création du dataset
if echo "$DATASET_RESPONSE" | grep -q '"success": true'; then
    DATASET_ID=$(echo "$DATASET_RESPONSE" | grep -o '"id": "[^"]*"' | head -1 | cut -d'"' -f4)
    echo "Dataset créé avec l'ID: $DATASET_ID"
else
    echo "Erreur lors de la création du dataset"
    echo "$DATASET_RESPONSE"
    exit 1
fi

# Créer le fichier GeoJSON temporaire
echo "Création du fichier GeoJSON..."
cat > /tmp/test-geospatial.geojson << 'EOF'
{
  "type": "FeatureCollection",
  "name": "test-geospatial-dataset",
  "crs": {
    "type": "name",
    "properties": {
      "name": "urn:ogc:def:crs:EPSG::4326"
    }
  },
  "features": [
    {
      "type": "Feature",
      "properties": {
        "id": 1,
        "name": "Dijon",
        "type": "Ville",
        "population": 155090,
        "region": "Bourgogne-Franche-Comté"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [5.0415, 47.3220]
      }
    },
    {
      "type": "Feature", 
      "properties": {
        "id": 2,
        "name": "Besançon",
        "type": "Ville",
        "population": 116466,
        "region": "Bourgogne-Franche-Comté"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [6.0244, 47.2380]
      }
    },
    {
      "type": "Feature",
      "properties": {
        "id": 3,
        "name": "Chalon-sur-Saône", 
        "type": "Ville",
        "population": 44985,
        "region": "Bourgogne-Franche-Comté"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [4.8565, 46.7833]
      }
    }
  ]
}
EOF

# Ajouter la ressource GeoJSON
echo "Ajout de la ressource GeoJSON..."
RESOURCE_RESPONSE=$(curl -s -X POST "$CKAN_URL/api/action/resource_create" \
    -H "Authorization: $API_KEY" \
    -F "package_id=$DATASET_ID" \
    -F "name=Villes BFC (GeoJSON)" \
    -F "description=Fichier GeoJSON contenant les coordonnées de 3 villes principales de Bourgogne-Franche-Comté" \
    -F "format=GeoJSON" \
    -F "upload=@/tmp/test-geospatial.geojson")

# Vérifier l'ajout de la ressource
if echo "$RESOURCE_RESPONSE" | grep -q '"success": true'; then
    RESOURCE_ID=$(echo "$RESOURCE_RESPONSE" | grep -o '"id": "[^"]*"' | head -1 | cut -d'"' -f4)
    echo "Ressource GeoJSON ajoutée avec l'ID: $RESOURCE_ID"
else
    echo "Erreur lors de l'ajout de la ressource"
    echo "$RESOURCE_RESPONSE"
    exit 1
fi

# Nettoyer le fichier temporaire
rm -f /tmp/test-geospatial.geojson

# Attendre que XLoader traite le fichier
echo "Attente du traitement par XLoader..."
sleep 10

# Vérifier que les données sont dans le datastore
echo "Vérification du datastore..."
DATASTORE_CHECK=$(curl -s -X POST "$CKAN_URL/api/action/datastore_search" \
    -H "Authorization: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"resource_id\": \"$RESOURCE_ID\", \"limit\": 1}")

if echo "$DATASTORE_CHECK" | grep -q '"success": true'; then
    RECORD_COUNT=$(echo "$DATASTORE_CHECK" | grep -o '"total": [0-9]*' | cut -d' ' -f2)
    echo "Données disponibles dans le datastore ($RECORD_COUNT enregistrements)"
else
    echo " Données pas encore disponibles dans le datastore (traitement en cours)"
fi

echo ""
echo "Dataset de test créé avec succès !"
echo ""
echo "Informations du dataset :"
echo "   - Nom: $DATASET_NAME"
echo "   - ID: $DATASET_ID"
echo "   - Ressource ID: $RESOURCE_ID"
echo "   - URL CKAN: $CKAN_URL/dataset/$DATASET_NAME"
echo ""
echo "Tests OGC disponibles :"
echo "   - GeoJSON: http://localhost:5001/collections/$DATASET_NAME/items"
echo "   - WFS: http://localhost:5001/collections/$DATASET_NAME/wfs"
echo "   - WMS: http://localhost:5001/collections/$DATASET_NAME/wms"
echo ""
echo "Commandes de test :"
echo "   curl 'http://localhost:5001/collections/$DATASET_NAME/items?limit=3'"
echo "   curl 'http://localhost:5001/collections/$DATASET_NAME/wfs?service=WFS&request=GetCapabilities'"
echo "   curl 'http://localhost:5001/collections/$DATASET_NAME/wms?service=WMS&request=GetCapabilities'"
