#!/bin/bash
# Script pour tester directement GetMap avec MapServer

MAPFILE="/mapserver/mapfiles/perimetres-des-papi-programmes-dactions-de-preventiondes-inondations-en-bourgogne-franche-comte.map"
OUTPUT="/tmp/test-getmap.png"

echo "TEST GETMAP DIRECT AVEC MAPSERVER"
echo "=" | head -c 80 && echo ""

# Test GetMap avec les mêmes paramètres que QGIS
curl -s -o "$OUTPUT" \
  "https://mapserver.qualif-data.example.org/wms?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&BBOX=45.70750000000000313,2.240909999999999958,48.66409999999999769,5.715690000000000381&CRS=EPSG:4326&WIDTH=286&HEIGHT=243&LAYERS=perimetres-des-papi-programmes-dactions-de-preventiondes-inondations-en-bourgogne-franche-comte&STYLES=&FORMAT=image/png&TRANSPARENT=TRUE&map=$MAPFILE"

echo "Taille du fichier: $(wc -c < "$OUTPUT") bytes"
echo ""

# Vérifier si c'est une image PNG valide
if file "$OUTPUT" | grep -q "PNG"; then
    echo "Fichier PNG valide"
    # Afficher les dimensions de l'image
    identify "$OUTPUT" 2>/dev/null || echo " ImageMagick non disponible pour vérifier les dimensions"
else
    echo "Fichier n'est pas un PNG valide"
    echo "Contenu (premiers 500 caractères):"
    head -c 500 "$OUTPUT" | cat -A
    echo ""
fi

echo ""
echo "Vérification de la table dans datagis..."
PGPASSWORD=ckan psql -h db -U ckan -d datagis -c "
    SELECT 
        COUNT(*) as nb_records,
        COUNT(CASE WHEN the_geom IS NOT NULL THEN 1 END) as nb_with_geom,
        (SELECT srid FROM geometry_columns WHERE f_table_name = 'res_bfe3499b_91e6_4e1e_a872_4276115429d5') as srid_table,
        (SELECT DISTINCT ST_SRID(the_geom) FROM res_bfe3499b_91e6_4e1e_a872_4276115429d5 WHERE the_geom IS NOT NULL LIMIT 1) as srid_geom
    FROM res_bfe3499b_91e6_4e1e_a872_4276115429d5;
"

echo ""
echo "Vérification de l'extent réel des données..."
PGPASSWORD=ckan psql -h db -U ckan -d datagis -c "
    SELECT 
        ST_Extent(ST_Transform(the_geom, 4326)) as extent_4326,
        ST_Extent(the_geom) as extent_original
    FROM res_bfe3499b_91e6_4e1e_a872_4276115429d5
    WHERE the_geom IS NOT NULL;
"

echo ""
echo "Vérification de la validité des géométries..."
PGPASSWORD=ckan psql -h db -U ckan -d datagis -c "
    SELECT 
        COUNT(*) as total,
        COUNT(CASE WHEN ST_IsValid(the_geom) THEN 1 END) as valid,
        COUNT(CASE WHEN NOT ST_IsValid(the_geom) THEN 1 END) as invalid
    FROM res_bfe3499b_91e6_4e1e_a872_4276115429d5
    WHERE the_geom IS NOT NULL;
"

echo ""
echo "Fichier de sortie: $OUTPUT"
echo "Pour visualiser: xdg-open $OUTPUT (ou ouvrir manuellement)"
