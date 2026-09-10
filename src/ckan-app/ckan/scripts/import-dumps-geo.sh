#!/bin/bash

# Script pour importer les dumps SQL géospatiaux dans CKAN/datastore
# et créer automatiquement les mapfiles correspondants

DUMP_DIR="${1:-.}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_NAME="${DB_NAME:-datastore}"
CKAN_URL="${CKAN_URL:-http://localhost:5000}"
CKAN_API_KEY="${CKAN_API_KEY:-}"

echo " Import des dumps géospatiaux dans CKAN/datastore"
echo "====================================================="
echo ""

# 1. datagis_20250124.sql - Base de données géospatiale majeure (42G)
if [ -f "${DUMP_DIR}/datagis_20250124.sql" ]; then
    echo "datagis_20250124.sql (42G) - Base de données géospatiale majeure"
    echo "   Contient: communes, adresses, assemblages régionaux, etc."
    echo "   Colonnes: the_geom (Polygon/MultiPolygon, SRID 4171)"
    echo ""
    echo " Ce dump est très volumineux (42G). Options:"
    echo "   1. Importer dans une base séparée 'datagis'"
    echo "   2. Importer des tables spécifiques dans datastore"
    echo "   3. Créer des vues depuis datagis vers datastore"
    echo ""
    read -p "Voulez-vous importer datagis ? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Création de la base 'datagis'..."
        docker exec $DB_HOST psql -U $DB_USER -d postgres -c "CREATE DATABASE datagis;" 2>&1 || echo "Base existe déjà ou erreur"
        
        echo "Import du dump (cela peut prendre du temps)..."
        docker exec -i $DB_HOST psql -U $DB_USER -d datagis < "${DUMP_DIR}/datagis_20250124.sql" 2>&1 | tail -20
        
        echo "Import terminé. Les tables sont dans la base 'datagis'"
        echo "   Pour utiliser ces données avec MapServer, créer des vues ou importer dans datastore"
    fi
    echo ""
fi

# 2. idgo_admin_20250123.sql - Données administratives géoréférencées (662M)
if [ -f "${DUMP_DIR}/idgo_admin_20250123.sql" ]; then
    echo "idgo_admin_20250123.sql (662M) - Données administratives"
    echo "   Contient: communes, jurisdictions avec geom/bbox"
    echo "   Colonnes: latitude, longitude, geom (MultiPolygon, SRID 4171)"
    echo ""
    read -p "Voulez-vous importer idgo_admin ? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Import dans datastore..."
        docker exec -i $DB_HOST psql -U $DB_USER -d $DB_NAME < "${DUMP_DIR}/idgo_admin_20250123.sql" 2>&1 | tail -20
        
        echo "Import terminé. Tables disponibles dans datastore:"
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "\dt idgo_admin_*" 2>&1
        
        echo "Conversion SRID 4171 -> 4326 si nécessaire..."
        # Les tables avec SRID 4171 doivent être converties en 4326 pour MapServer
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
            SELECT table_name, column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name LIKE 'idgo_admin_%' 
            AND (data_type LIKE '%geometry%' OR column_name IN ('geom', 'bbox', 'geometry'))
            LIMIT 10;
        " 2>&1
    fi
    echo ""
fi

# 3. catalogue_20250123.sql - Métadonnées géospatiales (26M)
if [ -f "${DUMP_DIR}/catalogue_20250123.sql" ]; then
    echo "catalogue_20250123.sql (26M) - Métadonnées géospatiales"
    echo "   Contient: métadonnées ISO 19139, références EPSG/SRID"
    echo "   Utile pour: enrichir les métadonnées CKAN"
    echo ""
    read -p "Voulez-vous importer catalogue ? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Import dans base 'catalogue'..."
        docker exec $DB_HOST psql -U $DB_USER -d postgres -c "CREATE DATABASE catalogue;" 2>&1 || echo "Base existe déjà"
        docker exec -i $DB_HOST psql -U $DB_USER -d catalogue < "${DUMP_DIR}/catalogue_20250123.sql" 2>&1 | tail -20
        echo "Import terminé. Métadonnées disponibles dans base 'catalogue'"
    fi
    echo ""
fi

# 4. onegeo_admin_20250123.sql - Système de recherche géographique (214M)
if [ -f "${DUMP_DIR}/onegeo_admin_20250123.sql" ]; then
    echo "onegeo_admin_20250123.sql (214M) - Système de recherche géographique"
    echo "   Contient: API de recherche, index, sources"
    echo "   Colonnes: latitude, longitude"
    echo ""
    echo "Ce dump semble être pour un système de recherche (OneGeo)"
    echo "   Probablement pas directement utilisable avec MapServer"
    echo ""
fi

# 5. collab_20250123.sql - Système de collaboration géographique (25M)
if [ -f "${DUMP_DIR}/collab_20250123.sql" ]; then
    echo "collab_20250123.sql (25M) - Collaboration géographique"
    echo "   Contient: geocontrib_* (features, layers, comments)"
    echo "   Colonnes: geom (Geometry, SRID 4326)"
    echo ""
    read -p "Voulez-vous importer collab ? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Import dans base 'collab'..."
        docker exec $DB_HOST psql -U $DB_USER -d postgres -c "CREATE DATABASE collab;" 2>&1 || echo "Base existe déjà"
        docker exec -i $DB_HOST psql -U $DB_USER -d collab < "${DUMP_DIR}/collab_20250123.sql" 2>&1 | tail -20
        echo "Import terminé. Données collaboratives dans base 'collab'"
    fi
    echo ""
fi

echo "Import terminé"
echo ""
echo "Prochaines étapes:"
echo "   1. Vérifier les tables importées: docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c '\dt'"
echo "   2. Convertir SRID 4171 -> 4326 si nécessaire (script à créer)"
echo "   3. Créer des datasets CKAN pour les tables géospatiales"
echo "   4. Générer les mapfiles: docker exec mapserver python3 /usr/local/bin/generate-mapfile.py --all"



