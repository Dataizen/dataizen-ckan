#!/bin/bash

# Script pour analyser les dumps SQL et identifier les données géospatiales

DUMP_DIR="${1:-dumps/geo}"

echo "Analyse des dumps SQL pour données géospatiales"
echo "=================================================="
echo ""

# Liste des dumps à analyser
DUMPS=(
    "catalogue_20250123.sql"
    "datagis_20250124.sql"
    "idgo_admin_20250123.sql"
    "onegeo_admin_20250123.sql"
    "collab_20250123.sql"
    "extractor_20250123.sql"
)

for dump in "${DUMPS[@]}"; do
    dump_path="${DUMP_DIR}/${dump}"
    
    if [ ! -f "$dump_path" ]; then
        echo " Fichier non trouvé: $dump_path"
        continue
    fi
    
    echo ""
    echo "Analyse de: $dump"
    echo "----------------------------------------"
    
    # Vérifier la taille
    size=$(du -h "$dump_path" | cut -f1)
    echo "Taille: $size"
    
    # Chercher des indices de données géospatiales
    echo ""
    echo "Recherche d'indices géospatiaux:"
    
    # Chercher PostGIS/geometry
    geom_count=$(grep -i "geometry\|postgis\|st_geomfromtext\|st_makepoint" "$dump_path" | wc -l)
    if [ "$geom_count" -gt 0 ]; then
        echo "  Colonnes géométrie trouvées: $geom_count occurrences"
        echo "     Exemples:"
        grep -i "geometry\|postgis\|st_geomfromtext\|st_makepoint" "$dump_path" | head -3 | sed 's/^/       - /'
    else
        echo "  Aucune colonne géométrie trouvée"
    fi
    
    # Chercher des tables avec des noms géospatiaux
    table_names=$(grep -i "CREATE TABLE\|CREATE TABLE IF NOT EXISTS" "$dump_path" | grep -iE "geo|spatial|commune|departement|region|cadastre|parcelle|adresse|route|voie" | head -10)
    if [ -n "$table_names" ]; then
        echo ""
        echo "  Tables potentiellement géospatiales:"
        echo "$table_names" | sed 's/^/       - /'
    fi
    
    # Chercher des extensions PostGIS
    if grep -qi "CREATE EXTENSION.*postgis\|CREATE EXTENSION IF NOT EXISTS.*postgis" "$dump_path"; then
        echo ""
        echo "  Extension PostGIS activée"
    fi
    
    # Chercher des références à EPSG/SRID
    srid_count=$(grep -iE "epsg|srid|4326|3857|2154" "$dump_path" | wc -l)
    if [ "$srid_count" -gt 0 ]; then
        echo ""
        echo "  Références CRS/SRID: $srid_count occurrences"
        echo "     Exemples:"
        grep -iE "epsg|srid|4326|3857|2154" "$dump_path" | head -3 | sed 's/^/       - /'
    fi
    
    # Compter les INSERT (données)
    insert_count=$(grep -i "^INSERT INTO" "$dump_path" | wc -l)
    if [ "$insert_count" -gt 0 ]; then
        echo ""
        echo "  Nombre d'INSERT (données): $insert_count"
    fi
    
    # Chercher des noms de colonnes géographiques
    geo_cols=$(grep -iE "latitude|longitude|lat|lon|coord|x_coord|y_coord|geom|the_geom" "$dump_path" | head -5)
    if [ -n "$geo_cols" ]; then
        echo ""
        echo "  Colonnes géographiques trouvées:"
        echo "$geo_cols" | sed 's/^/       - /'
    fi
done

echo ""
echo "Analyse terminée"
echo ""
echo "Recommandations:"
echo "   - datagis_* et onegeo_* semblent être les plus prometteurs pour des données géospatiales"
echo "   - idgo_admin_* pourrait contenir des données administratives géoréférencées"
echo "   - Pour importer: utiliser pg_restore ou psql selon le format du dump"

