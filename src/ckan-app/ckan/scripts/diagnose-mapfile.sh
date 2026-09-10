#!/bin/bash
# Script de diagnostic pour un mapfile qui ne s'affiche pas dans QGIS

MAPFILE_PATH="${1:-/mapserver/mapfiles/perimetres-des-papi-programmes-dactions-de-preventiondes-inondations-en-bourgogne-franche-comte.map}"

if [ ! -f "$MAPFILE_PATH" ]; then
    echo "Mapfile non trouvé: $MAPFILE_PATH"
    exit 1
fi

echo "DIAGNOSTIC DU MAPFILE: $MAPFILE_PATH"
echo "=" | head -c 80 && echo ""

# Extraire les informations du mapfile
TABLE_NAME=$(grep -A 1 'DATA' "$MAPFILE_PATH" | grep -oP 'FROM "\\K[^"]+' | head -1)
GEOM_COLUMN=$(grep -A 1 'DATA' "$MAPFILE_PATH" | grep -oP '^\s+[A-Z_]+ FROM' | awk '{print $1}' | head -1)
SRID_MAPFILE=$(grep -A 1 'DATA' "$MAPFILE_PATH" | grep -oP 'SRID=\K[0-9]+' | head -1)
UNIQUE_COL=$(grep -A 1 'DATA' "$MAPFILE_PATH" | grep -oP 'UNIQUE \K[A-Z_]+' | head -1)
CONNECTION=$(grep -A 1 'CONNECTION' "$MAPFILE_PATH" | grep -v 'CONNECTIONTYPE' | grep -oP 'host=\K[^ ]+' | head -1)
DB_NAME=$(grep -A 1 'CONNECTION' "$MAPFILE_PATH" | grep -v 'CONNECTIONTYPE' | grep -oP 'dbname=\K[^ ]+' | head -1)

echo "INFORMATIONS EXTRAITES DU MAPFILE:"
echo "   Table: $TABLE_NAME"
echo "   Colonne géométrie: $GEOM_COLUMN"
echo "   SRID (mapfile): $SRID_MAPFILE"
echo "   Colonne unique: $UNIQUE_COL"
echo "   Base de données: $DB_NAME"
echo "   Host: $CONNECTION"
echo ""

# Vérifier la connexion à la base de données
echo "VÉRIFICATION DE LA CONNEXION À LA BASE DE DONNÉES..."
if PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -c "SELECT 1;" > /dev/null 2>&1; then
    echo "   Connexion réussie"
else
    echo "   Échec de la connexion"
    exit 1
fi
echo ""

# Vérifier si la table existe
echo "VÉRIFICATION DE LA TABLE..."
TABLE_EXISTS=$(PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -tAc "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$TABLE_NAME');")
if [ "$TABLE_EXISTS" = "t" ]; then
    echo "   Table existe: $TABLE_NAME"
else
    echo "   Table n'existe pas: $TABLE_NAME"
    exit 1
fi
echo ""

# Vérifier la colonne géométrique
echo " VÉRIFICATION DE LA COLONNE GÉOMÉTRIQUE..."
GEOM_INFO=$(PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -tAc "
    SELECT f_geometry_column, srid, type
    FROM geometry_columns
    WHERE f_table_schema = 'public'
    AND f_table_name = '$TABLE_NAME';
")
if [ -n "$GEOM_INFO" ]; then
    GEOM_COL_REAL=$(echo "$GEOM_INFO" | cut -d'|' -f1)
    SRID_REAL=$(echo "$GEOM_INFO" | cut -d'|' -f2)
    GEOM_TYPE=$(echo "$GEOM_INFO" | cut -d'|' -f3)
    echo "   Colonne géométrie trouvée: $GEOM_COL_REAL"
    echo "   SRID réel (table): $SRID_REAL"
    echo "   Type géométrie: $GEOM_TYPE"
    
    if [ "$GEOM_COL_REAL" != "$GEOM_COLUMN" ]; then
        echo "    INCOHÉRENCE: Colonne géométrie dans mapfile ($GEOM_COLUMN) ≠ colonne réelle ($GEOM_COL_REAL)"
    fi
    
    if [ "$SRID_REAL" != "$SRID_MAPFILE" ]; then
        echo "    INCOHÉRENCE: SRID dans mapfile ($SRID_MAPFILE) ≠ SRID réel ($SRID_REAL)"
    fi
else
    echo "   Aucune colonne géométrie trouvée dans geometry_columns"
    exit 1
fi
echo ""

# Compter les enregistrements
echo "NOMBRE D'ENREGISTREMENTS..."
RECORD_COUNT=$(PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM \"$TABLE_NAME\";")
echo "   Nombre d'enregistrements: $RECORD_COUNT"
if [ "$RECORD_COUNT" -eq 0 ]; then
    echo "    TABLE VIDE - C'est probablement la cause du problème !"
fi
echo ""

# Vérifier la colonne unique
echo "VÉRIFICATION DE LA COLONNE UNIQUE..."
UNIQUE_COL_EXISTS=$(PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -tAc "
    SELECT EXISTS(
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = '$TABLE_NAME'
        AND column_name = '$UNIQUE_COL'
    );
")
if [ "$UNIQUE_COL_EXISTS" = "t" ]; then
    echo "   Colonne unique existe: $UNIQUE_COL"
else
    echo "    Colonne unique n'existe pas: $UNIQUE_COL"
    echo "   Colonnes disponibles:"
    PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='$TABLE_NAME' ORDER BY ordinal_position;" | head -20
fi
echo ""

# Calculer l'extent réel
echo "CALCUL DE L'EXTENT RÉEL..."
EXTENT_REAL=$(PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -tAc "
    SELECT ST_Extent(ST_Transform(\"$GEOM_COL_REAL\", 4326))
    FROM \"$TABLE_NAME\"
    WHERE \"$GEOM_COL_REAL\" IS NOT NULL;
")
if [ -n "$EXTENT_REAL" ] && [ "$EXTENT_REAL" != "(,)" ]; then
    echo "   Extent réel: $EXTENT_REAL"
    EXTENT_MAPFILE=$(grep '^    EXTENT' "$MAPFILE_PATH" | awk '{print $2, $3, $4, $5}')
    echo "   Extent mapfile: $EXTENT_MAPFILE"
else
    echo "    Impossible de calculer l'extent (table vide ou géométries NULL)"
fi
echo ""

# Vérifier quelques enregistrements
echo "ÉCHANTILLON DES DONNÉES (5 premiers enregistrements)..."
PGPASSWORD=ckan psql -h db -p 5432 -U ckan -d "$DB_NAME" -c "
    SELECT 
        \"$UNIQUE_COL\" as id,
        ST_GeometryType(\"$GEOM_COL_REAL\") as geom_type,
        ST_SRID(\"$GEOM_COL_REAL\") as srid,
        ST_IsValid(\"$GEOM_COL_REAL\") as is_valid
    FROM \"$TABLE_NAME\"
    LIMIT 5;
" 2>&1 | head -10
echo ""

# Résumé
echo "=" | head -c 80 && echo ""
echo "RÉSUMÉ DU DIAGNOSTIC:"
echo ""

if [ "$RECORD_COUNT" -eq 0 ]; then
    echo "PROBLÈME PRINCIPAL: Table vide"
    echo "   → La table existe mais ne contient aucun enregistrement"
    echo "   → Vérifier l'import OGR dans les logs"
elif [ "$GEOM_COL_REAL" != "$GEOM_COLUMN" ]; then
    echo "PROBLÈME PRINCIPAL: Colonne géométrie incorrecte"
    echo "   → Mapfile utilise: $GEOM_COLUMN"
    echo "   → Table utilise: $GEOM_COL_REAL"
elif [ "$SRID_REAL" != "$SRID_MAPFILE" ]; then
    echo " PROBLÈME: SRID incohérent"
    echo "   → Mapfile utilise: $SRID_MAPFILE"
    echo "   → Table utilise: $SRID_REAL"
    echo "   → Cela peut causer des problèmes d'affichage"
else
    echo "Configuration semble correcte"
    echo "   → Vérifier les logs MapServer pour d'autres erreurs"
    echo "   → Vérifier la connexion réseau dans QGIS"
fi
echo ""
