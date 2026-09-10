#!/bin/bash

# Script pour créer une colonne géométrie PostGIS à partir de latitude/longitude

RESOURCE_ID="${1:-}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_NAME="${DB_NAME:-datastore}"

if [ -z "$RESOURCE_ID" ]; then
    echo "Usage: $0 RESOURCE_ID"
    echo "Exemple: $0 8e9f2622-dc9c-455a-a2ea-9a361700b96e"
    exit 1
fi

echo "Création de la colonne géométrie pour la ressource: $RESOURCE_ID"
echo "================================================================"
echo ""

# Vérifier que la table existe
TABLE_EXISTS=$(docker exec db psql -U $DB_USER -d $DB_NAME -t -c "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '$RESOURCE_ID');" 2>&1 | tr -d ' \n')

if [ "$TABLE_EXISTS" != "t" ]; then
    echo "Table '$RESOURCE_ID' n'existe pas dans PostGIS"
    exit 1
fi

echo "Table trouvée: $RESOURCE_ID"

# Vérifier que les colonnes latitude/longitude existent
HAS_LAT=$(docker exec db psql -U $DB_USER -d $DB_NAME -t -c "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = '$RESOURCE_ID' AND column_name = 'latitude');" 2>&1 | tr -d ' \n')
HAS_LON=$(docker exec db psql -U $DB_USER -d $DB_NAME -t -c "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = '$RESOURCE_ID' AND column_name = 'longitude');" 2>&1 | tr -d ' \n')

if [ "$HAS_LAT" != "t" ] || [ "$HAS_LON" != "t" ]; then
    echo "Colonnes latitude/longitude non trouvées"
    exit 1
fi

echo "Colonnes latitude/longitude trouvées"

# Vérifier si la colonne géométrie existe déjà
HAS_GEOM=$(docker exec db psql -U $DB_USER -d $DB_NAME -t -c "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = '$RESOURCE_ID' AND column_name = 'geometry');" 2>&1 | tr -d ' \n')

if [ "$HAS_GEOM" = "t" ]; then
    echo " Colonne 'geometry' existe déjà"
    read -p "Voulez-vous la recréer ? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Annulé"
        exit 0
    fi
    echo "Suppression de l'ancienne colonne..."
    docker exec db psql -U $DB_USER -d $DB_NAME -c "ALTER TABLE \"$RESOURCE_ID\" DROP COLUMN IF EXISTS geometry;" 2>&1
fi

# Créer la colonne géométrie
echo "Création de la colonne géométrie..."
docker exec db psql -U $DB_USER -d $DB_NAME -c "ALTER TABLE \"$RESOURCE_ID\" ADD COLUMN geometry geometry(Point, 4326);" 2>&1

if [ $? -ne 0 ]; then
    echo "Erreur lors de la création de la colonne"
    exit 1
fi

echo "Colonne géométrie créée"

# Remplir la colonne géométrie à partir de latitude/longitude
echo "Remplissage de la colonne géométrie..."
docker exec db psql -U $DB_USER -d $DB_NAME -c "
UPDATE \"$RESOURCE_ID\" 
SET geometry = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
WHERE latitude IS NOT NULL 
  AND longitude IS NOT NULL 
  AND latitude BETWEEN -90 AND 90 
  AND longitude BETWEEN -180 AND 180;
" 2>&1

if [ $? -ne 0 ]; then
    echo "Erreur lors du remplissage de la colonne"
    exit 1
fi

# Compter les géométries créées
COUNT=$(docker exec db psql -U $DB_USER -d $DB_NAME -t -c "SELECT COUNT(*) FROM \"$RESOURCE_ID\" WHERE geometry IS NOT NULL;" 2>&1 | tr -d ' \n')
echo "$COUNT géométries créées"

# Créer un index spatial pour améliorer les performances
echo "Création de l'index spatial..."
docker exec db psql -U $DB_USER -d $DB_NAME -c "CREATE INDEX IF NOT EXISTS idx_${RESOURCE_ID//-/_}_geometry ON \"$RESOURCE_ID\" USING GIST (geometry);" 2>&1

echo "Index spatial créé"
echo ""
echo "Colonne géométrie créée avec succès !"
echo ""
echo "Prochaines étapes:"
echo "   1. Régénérer le mapfile: docker exec ckan python3 /usr/local/bin/generate-mapfile.py --dataset-id {dataset_name}"
echo "   2. Tester WMS: curl \"http://localhost:8081/wms?map=/mapserver/mapfiles/{dataset_name}.map&SERVICE=WMS&REQUEST=GetCapabilities\""



