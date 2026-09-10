#!/bin/bash

# Script pour explorer les relations entre datagis et CKAN

DB_NAME_DATAGIS="${DB_NAME_DATAGIS:-datagis}"
DB_NAME_DATASTORE="${DB_NAME_DATASTORE:-datastore}"
DB_NAME_CKAN="${DB_NAME_CKAN:-ckan}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"

echo "Exploration des relations entre datagis et CKAN"
echo "=================================================="
echo ""

# Vérifier que les bases existent
for db in "$DB_NAME_DATAGIS" "$DB_NAME_DATASTORE" "$DB_NAME_CKAN"; do
    if ! docker exec db psql -U "$DB_USER" -d postgres -t -c "SELECT 1 FROM pg_database WHERE datname = '$db';" | grep -q 1; then
        echo "Base de données '$db' non trouvée"
        exit 1
    fi
done

echo "Toutes les bases de données sont accessibles"
echo ""

# 1. Statistiques générales
echo "Statistiques générales:"
echo "------------------------"
echo ""

echo "Base datagis:"
DATAGIS_TABLES=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT COUNT(*) 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE';
" | tr -d ' ')
echo "  - Tables: $DATAGIS_TABLES"

DATAGIS_GEOM_TABLES=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT COUNT(DISTINCT table_name)
    FROM information_schema.columns
    WHERE table_schema = 'public'
    AND data_type = 'USER-DEFINED'
    AND udt_name = 'geometry';
" | tr -d ' ')
echo "  - Tables avec géométrie: $DATAGIS_GEOM_TABLES"
echo ""

echo "Base datastore (CKAN):"
DATASTORE_TABLES=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATASTORE" -t -c "
    SELECT COUNT(*) 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE'
    AND table_name NOT LIKE 'pg_%'
    AND table_name NOT LIKE 'spatial_%';
" | tr -d ' ')
echo "  - Tables: $DATASTORE_TABLES"
echo ""

# 2. Chercher des correspondances de noms de tables
echo "Recherche de correspondances de noms:"
echo "--------------------------------------"
echo ""

echo "Tables datagis qui pourraient correspondre à des datasets CKAN:"
echo ""

# Récupérer les noms de datasets CKAN depuis l'API ou la base
CKAN_DATASETS=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_CKAN" -t -c "
    SELECT name 
    FROM package 
    WHERE state = 'active'
    ORDER BY name
    LIMIT 50;
" | tr -d ' ')

if [ -n "$CKAN_DATASETS" ]; then
    echo "  Datasets CKAN trouvés: $(echo "$CKAN_DATASETS" | wc -l)"
    echo ""
    echo "  Correspondances potentielles dans datagis:"
    
    for dataset in $CKAN_DATASETS; do
        # Nettoyer le nom pour chercher dans datagis
        clean_name=$(echo "$dataset" | tr '-' '_' | tr '[:upper:]' '[:lower:]')
        
        # Chercher des tables qui contiennent ce nom
        matches=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            AND table_name LIKE '%$clean_name%'
            LIMIT 5;
        " | tr -d ' ')
        
        if [ -n "$matches" ]; then
            echo "    $dataset ->"
            echo "$matches" | sed 's/^/        - /'
        fi
    done
else
    echo "   Aucun dataset CKAN trouvé"
fi

echo ""

# 3. Analyser les noms de tables datagis pour trouver des patterns
echo "Patterns dans les noms de tables datagis:"
echo "-------------------------------------------"
echo ""

echo "  Tables contenant 'adresse':"
docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public'
    AND table_name LIKE '%adresse%'
    ORDER BY table_name
    LIMIT 10;
" | sed 's/^/    - /'

echo ""
echo "  Tables contenant 'commune':"
docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public'
    AND table_name LIKE '%commune%'
    ORDER BY table_name
    LIMIT 10;
" | sed 's/^/    - /'

echo ""

# 4. Analyser les colonnes géométriques et leurs SRID
echo " Analyse des colonnes géométriques dans datagis:"
echo "-------------------------------------------------"
echo ""

echo "  Distribution des SRID:"
docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT 
        COUNT(*) as count,
        (SELECT Find_SRID('public', table_name, column_name) 
         FROM information_schema.columns c2 
         WHERE c2.table_name = c.table_name 
         AND c2.column_name = c.column_name 
         LIMIT 1) as srid
    FROM information_schema.columns c
    WHERE table_schema = 'public'
    AND data_type = 'USER-DEFINED'
    AND udt_name = 'geometry'
    GROUP BY srid
    ORDER BY count DESC
    LIMIT 5;
" | sed 's/^/    - /'

echo ""

# 5. Chercher des ressources CKAN qui pourraient pointer vers datagis
echo "Ressources CKAN potentiellement liées à datagis:"
echo "-------------------------------------------------"
echo ""

echo "  Ressources avec format géospatial dans CKAN:"
docker exec db psql -U "$DB_USER" -d "$DB_NAME_CKAN" -t -c "
    SELECT DISTINCT r.format, COUNT(*) as count
    FROM resource r
    JOIN package p ON r.package_id = p.id
    WHERE p.state = 'active'
    AND r.state = 'active'
    AND r.format IN ('SHP', 'ZIP', 'GeoJSON', 'GPKG', 'KML')
    GROUP BY r.format
    ORDER BY count DESC;
" | sed 's/^/    - /'

echo ""

# 6. Suggestions de mapping
echo "Suggestions de mapping:"
echo "-------------------------"
echo ""
echo "  1. Tables datagis avec SRID 4171 doivent être converties en 4326 pour MapServer"
echo "  2. Créer des vues dans datastore pointant vers datagis si nécessaire"
echo "  3. Utiliser generate-mapfile.py avec option --datagis-db pour MapServer"
echo "  4. Analyser les noms de tables pour créer des correspondances automatiques"
echo ""

# 7. Générer un rapport de correspondances potentielles
echo "Génération d'un rapport de correspondances..."
echo ""

REPORT_FILE="datagis-ckan-relations-$(date +%Y%m%d_%H%M%S).txt"
{
    echo "Rapport de correspondances datagis <-> CKAN"
    echo "Généré le: $(date)"
    echo "=========================================="
    echo ""
    
    echo "Tables datagis avec géométrie (premières 20):"
    docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
        SELECT DISTINCT table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        AND data_type = 'USER-DEFINED'
        AND udt_name = 'geometry'
        ORDER BY table_name
        LIMIT 20;
    "
    
    echo ""
    echo "Datasets CKAN actifs (premiers 30):"
    docker exec db psql -U "$DB_USER" -d "$DB_NAME_CKAN" -t -c "
        SELECT name, title
        FROM package
        WHERE state = 'active'
        ORDER BY name
        LIMIT 30;
    "
} > "$REPORT_FILE"

echo "  Rapport sauvegardé dans: $REPORT_FILE"
echo ""

echo "Exploration terminée"
echo ""



