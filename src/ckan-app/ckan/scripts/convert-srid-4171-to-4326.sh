#!/bin/bash

# Script pour convertir les géométries de SRID 4171 (RGF93) vers SRID 4326 (WGS84)
# pour les tables importées depuis datagis ou idgo_admin

DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_NAME="${DB_NAME:-datastore}"
TABLE_NAME="${1:-}"

if [ -z "$TABLE_NAME" ]; then
    echo "Usage: $0 TABLE_NAME"
    echo "Exemple: $0 idgo_admin_commune"
    echo ""
    echo "Tables avec SRID 4171 détectées:"
    docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
        SELECT DISTINCT table_name, column_name
        FROM information_schema.columns c
        JOIN pg_class cl ON cl.relname = c.table_name
        JOIN pg_attribute a ON a.attrelid = cl.oid AND a.attname = c.column_name
        JOIN pg_type t ON t.oid = a.atttypid
        WHERE c.table_schema = 'public'
        AND t.typname = 'geometry'
        AND c.table_name LIKE '%admin%' OR c.table_name LIKE '%commune%'
        LIMIT 20;
    " 2>&1
    exit 1
fi

echo "Conversion SRID 4171 -> 4326 pour table: $TABLE_NAME"
echo "======================================================"
echo ""

# Détecter les colonnes géométrie avec SRID 4171
GEOM_COLS=$(docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -t -c "
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name = '$TABLE_NAME'
    AND (data_type LIKE '%geometry%' OR column_name IN ('geom', 'bbox', 'geometry', 'the_geom'))
    LIMIT 5;
" 2>&1 | tr -d ' \n')

if [ -z "$GEOM_COLS" ]; then
    echo "Aucune colonne géométrie trouvée dans $TABLE_NAME"
    exit 1
fi

echo "Colonnes géométrie trouvées: $GEOM_COLS"
echo ""

for COL in $GEOM_COLS; do
    echo "Conversion de la colonne: $COL"
    
    # Vérifier le SRID actuel
    CURRENT_SRID=$(docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -t -c "
        SELECT DISTINCT ST_SRID($COL) 
        FROM \"$TABLE_NAME\" 
        WHERE $COL IS NOT NULL 
        LIMIT 1;
    " 2>&1 | tr -d ' \n')
    
    if [ "$CURRENT_SRID" = "4171" ] || [ "$CURRENT_SRID" = "2154" ]; then
        echo "   SRID actuel: $CURRENT_SRID (RGF93/Lambert-93)"
        echo "   Conversion vers SRID 4326 (WGS84)..."
        
        # Créer une nouvelle colonne temporaire
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
            ALTER TABLE \"$TABLE_NAME\" 
            ADD COLUMN ${COL}_4326 geometry(Geometry, 4326);
        " 2>&1
        
        # Convertir les géométries
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
            UPDATE \"$TABLE_NAME\"
            SET ${COL}_4326 = ST_Transform($COL, 4326)
            WHERE $COL IS NOT NULL;
        " 2>&1
        
        # Remplacer l'ancienne colonne
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
            ALTER TABLE \"$TABLE_NAME\" DROP COLUMN $COL;
            ALTER TABLE \"$TABLE_NAME\" RENAME COLUMN ${COL}_4326 TO $COL;
        " 2>&1
        
        # Créer l'index spatial
        docker exec $DB_HOST psql -U $DB_USER -d $DB_NAME -c "
            CREATE INDEX IF NOT EXISTS idx_${TABLE_NAME}_${COL}_gist 
            ON \"$TABLE_NAME\" USING GIST ($COL);
        " 2>&1
        
        echo "   Conversion terminée"
    elif [ "$CURRENT_SRID" = "4326" ]; then
        echo "   Colonne déjà en SRID 4326, pas de conversion nécessaire"
    else
        echo "    SRID inconnu: $CURRENT_SRID"
    fi
    echo ""
done

echo "Conversion terminée pour $TABLE_NAME"



