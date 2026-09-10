#!/bin/bash

# Script pour analyser les relations entre les dumps SQL et les données CKAN

DUMP_DIR="${1:-dumps/geo}"
CKAN_URL="${CKAN_URL:-http://localhost:5000}"
CKAN_API_KEY="${CKAN_API_KEY:-ckan-local-dev-apikey}"

echo "Analyse des relations entre dumps SQL et données CKAN"
echo "========================================================"
echo ""

# Liste des dumps
echo "Dumps disponibles:"
echo "-------------------"
DUMPS=(
    "catalogue_20250123.sql"
    "datagis_20250124.sql"
    "idgo_admin_20250123.sql"
    "onegeo_admin_20250123.sql"
    "collab_20250123.sql"
    "extractor_20250123.sql"
    "ckan_20250124.sql"
    "datastore_20250124.sql"
)

for dump in "${DUMPS[@]}"; do
    dump_path="${DUMP_DIR}/${dump}"
    if [ -f "$dump_path" ]; then
        size=$(du -h "$dump_path" | cut -f1)
        echo "  $dump ($size)"
    else
        echo "  $dump (non trouvé)"
    fi
done

echo ""
echo "Analyse des relations avec CKAN:"
echo "-----------------------------------"

# 1. Analyser les noms de tables dans les dumps
echo ""
echo "Tables dans les dumps:"

# datagis
if [ -f "${DUMP_DIR}/datagis_20250124.sql" ]; then
    echo ""
    echo "  datagis_20250124.sql:"
    grep -i "CREATE TABLE" "${DUMP_DIR}/datagis_20250124.sql" | head -20 | sed 's/^/    - /'
    echo "    ... (peut-être plus de tables)"
fi

# idgo_admin
if [ -f "${DUMP_DIR}/idgo_admin_20250123.sql" ]; then
    echo ""
    echo "  idgo_admin_20250123.sql:"
    grep -i "CREATE TABLE" "${DUMP_DIR}/idgo_admin_20250123.sql" | sed 's/^/    - /'
fi

# catalogue
if [ -f "${DUMP_DIR}/catalogue_20250123.sql" ]; then
    echo ""
    echo "  catalogue_20250123.sql:"
    grep -i "CREATE TABLE" "${DUMP_DIR}/catalogue_20250123.sql" | head -10 | sed 's/^/    - /'
fi

# collab
if [ -f "${DUMP_DIR}/collab_20250123.sql" ]; then
    echo ""
    echo "  collab_20250123.sql:"
    grep -i "CREATE TABLE.*geocontrib" "${DUMP_DIR}/collab_20250123.sql" | sed 's/^/    - /'
fi

# 2. Chercher des références à CKAN dans les dumps
echo ""
echo "Références à CKAN dans les dumps:"
echo "------------------------------------"

for dump in "${DUMPS[@]}"; do
    dump_path="${DUMP_DIR}/${dump}"
    if [ -f "$dump_path" ]; then
        ckan_refs=$(grep -i "ckan\|package\|dataset\|resource" "$dump_path" | wc -l)
        if [ "$ckan_refs" -gt 0 ]; then
            echo "  $dump: $ckan_refs références à CKAN"
            echo "    Exemples:"
            grep -i "ckan\|package\|dataset\|resource" "$dump_path" | head -3 | sed 's/^/      - /'
        fi
    fi
done

# 3. Analyser les noms de datasets dans CKAN et chercher des correspondances
echo ""
echo "Datasets CKAN et correspondances potentielles:"
echo "-----------------------------------------------"

# Récupérer les datasets CKAN
if command -v curl &> /dev/null && [ -n "$CKAN_API_KEY" ]; then
    echo "  Récupération des datasets depuis CKAN..."
    datasets=$(curl -s -H "Authorization: $CKAN_API_KEY" \
        "${CKAN_URL}/api/action/package_search?rows=1000" | \
        python3 -c "import sys, json; data=json.load(sys.stdin); \
        print('\n'.join([d['name'] for d in data.get('result', {}).get('results', [])]))" 2>/dev/null)
    
    if [ -n "$datasets" ]; then
        echo "  $(echo "$datasets" | wc -l) datasets trouvés dans CKAN"
        echo ""
        echo "  Recherche de correspondances avec les tables des dumps..."
        
        # Chercher des correspondances dans datagis
        if [ -f "${DUMP_DIR}/datagis_20250124.sql" ]; then
            echo ""
            echo "  Correspondances potentielles avec datagis:"
            for dataset in $(echo "$datasets" | head -20); do
                # Nettoyer le nom du dataset pour chercher dans les tables
                clean_name=$(echo "$dataset" | tr '-' '_' | tr '[:upper:]' '[:lower:]')
                if grep -qi "$clean_name\|$(echo "$dataset" | tr '-' '_')" "${DUMP_DIR}/datagis_20250124.sql" 2>/dev/null; then
                    echo "    $dataset -> possible correspondance dans datagis"
                fi
            done
        fi
        
        # Chercher des correspondances dans idgo_admin
        if [ -f "${DUMP_DIR}/idgo_admin_20250123.sql" ]; then
            echo ""
            echo "  Correspondances potentielles avec idgo_admin:"
            for dataset in $(echo "$datasets" | grep -iE "commune|admin|jurisdiction" | head -10); do
                echo "    $dataset -> possible correspondance dans idgo_admin"
            done
        fi
    else
        echo "   Impossible de récupérer les datasets CKAN"
    fi
else
    echo "   curl ou CKAN_API_KEY non disponible"
fi

# 4. Analyser les noms de ressources dans datastore
echo ""
echo " Tables dans datastore CKAN:"
echo "-------------------------------"

if command -v docker &> /dev/null; then
    tables=$(docker exec db psql -U ckan -d datastore -t -c "
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_type = 'BASE TABLE'
        AND table_name NOT LIKE 'pg_%'
        AND table_name NOT LIKE 'spatial_%'
        ORDER BY table_name
        LIMIT 50;
    " 2>/dev/null | tr -d ' ')
    
    if [ -n "$tables" ]; then
        echo "  Tables trouvées dans datastore:"
        echo "$tables" | head -20 | sed 's/^/    - /'
        echo ""
        echo "  Recherche de correspondances avec les dumps..."
        
        # Chercher des correspondances
        for table in $(echo "$tables" | head -20); do
            # Chercher dans datagis
            if [ -f "${DUMP_DIR}/datagis_20250124.sql" ]; then
                if grep -qi "$table" "${DUMP_DIR}/datagis_20250124.sql" 2>/dev/null; then
                    echo "    Table datastore '$table' -> possible correspondance dans datagis"
                fi
            fi
        done
    else
        echo "   Impossible de récupérer les tables du datastore"
    fi
else
    echo "   docker non disponible"
fi

# 5. Résumé des relations
echo ""
echo "Résumé des relations:"
echo "-----------------------"
echo ""
echo "  Dumps directement liés à CKAN:"
echo "    ckan_20250124.sql - Base CKAN principale"
echo "    datastore_20250124.sql - Datastore CKAN (données géospatiales CKAN)"
echo ""
echo "  Dumps complémentaires (données externes):"
echo "    datagis_20250124.sql - Base géospatiale externe (42G)"
echo "    idgo_admin_20250123.sql - Données administratives (662M)"
echo "    catalogue_20250123.sql - Métadonnées ISO 19139 (26M)"
echo "    collab_20250123.sql - Collaboration géographique (25M)"
echo "    onegeo_admin_20250123.sql - Système de recherche (214M)"
echo ""
echo "  Relations possibles:"
echo "    - datagis peut contenir les données sources des datasets CKAN"
echo "    - idgo_admin peut enrichir les datasets administratifs CKAN"
echo "    - catalogue peut enrichir les métadonnées CKAN"
echo "    - collab peut être lié aux contributions géographiques CKAN"

