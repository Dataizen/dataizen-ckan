#!/bin/bash
# Script pour corriger le SRID 900914 vers 4326 pour la table PAPI

TABLE_NAME="res_bfe3499b_91e6_4e1e_a872_4276115429d5"

echo "CORRECTION DU SRID 900914 → 4326"
echo "=" | head -c 80 && echo ""
echo ""

# Vérifier l'état actuel
echo "État actuel:"
PGPASSWORD=ckan psql -h db -U ckan -d datagis -c "
SELECT 
    (SELECT srid FROM geometry_columns WHERE f_table_name = '$TABLE_NAME') as srid_geometry_columns,
    (SELECT DISTINCT ST_SRID(the_geom) FROM \"$TABLE_NAME\" WHERE the_geom IS NOT NULL LIMIT 1) as srid_geometries;
"

echo ""
echo " ATTENTION: Ce script suppose que les coordonnées sont déjà en WGS84 (EPSG:4326)"
echo "   Si les coordonnées sont dans un autre système, il faut réimporter avec le bon SRID"
echo ""
read -p "Continuer ? (oui/non): " response

if [ "$response" != "oui" ] && [ "$response" != "o" ] && [ "$response" != "yes" ] && [ "$response" != "y" ]; then
    echo "Correction annulée"
    exit 1
fi

echo ""
echo "Correction en cours..."

# Exécuter la correction
PGPASSWORD=ckan psql -h db -U ckan -d datagis -f /srv/app/scripts/fix-srid-900914-to-4326.sql

if [ $? -eq 0 ]; then
    echo ""
    echo "Correction terminée"
    echo ""
    echo "État après correction:"
    PGPASSWORD=ckan psql -h db -U ckan -d datagis -c "
    SELECT 
        (SELECT srid FROM geometry_columns WHERE f_table_name = '$TABLE_NAME') as srid_geometry_columns,
        (SELECT DISTINCT ST_SRID(the_geom) FROM \"$TABLE_NAME\" WHERE the_geom IS NOT NULL LIMIT 1) as srid_geometries;
    "
    echo ""
    echo "Régénérer le mapfile maintenant:"
    echo "   python3 /srv/app/ckanext-ogc/scripts/generate-mapfile.py --dataset perimetres-des-papi-programmes-dactions-de-preventiondes-inondations-en-bourgogne-franche-comte"
else
    echo ""
    echo "Erreur lors de la correction"
    exit 1
fi
