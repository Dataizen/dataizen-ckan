#!/bin/bash

# Script pour créer un mapfile MapServer directement depuis une table datagis

set -e

TABLE_NAME="${1}"
GEOM_COLUMN="${2:-the_geom}"
DB_NAME="${DB_NAME:-datagis}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_PASSWORD="${POSTGRES_PASSWORD:-ckan}"

if [ -z "$TABLE_NAME" ]; then
    echo "Usage: $0 <table_name> [geometry_column]"
    echo ""
    echo "Exemples:"
    echo "  $0 adresse_recap_92a66e8"
    echo "  $0 _110_communes_adherentes_f98c07e the_geom"
    echo ""
    echo "Pour lister les tables disponibles:"
    echo "  docker exec db psql -U $DB_USER -d $DB_NAME -c \"\\dt\" | head -30"
    exit 1
fi

MAPFILES_DIR="${MAPFILES_DIR:-./mapserver/mapfiles}"
MAPFILE_PATH="${MAPFILES_DIR}/${TABLE_NAME}.map"

echo " Création d'un mapfile MapServer depuis datagis"
echo "=================================================="
echo ""
echo "Table: $TABLE_NAME"
echo "Colonne géométrie: $GEOM_COLUMN"
echo "Base: $DB_NAME"
echo "Mapfile: $MAPFILE_PATH"
echo ""

# Vérifier que la table existe
if ! docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT 1 FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_name = '$TABLE_NAME';
" | grep -q 1; then
    echo "Erreur: Table '$TABLE_NAME' non trouvée dans la base '$DB_NAME'"
    exit 1
fi

# Vérifier que la colonne géométrie existe
if ! docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT 1 FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = '$TABLE_NAME' 
    AND column_name = '$GEOM_COLUMN';
" | grep -q 1; then
    echo "Erreur: Colonne géométrie '$GEOM_COLUMN' non trouvée dans la table '$TABLE_NAME'"
    echo ""
    echo "Colonnes disponibles:"
    docker exec db psql -U "$DB_USER" -d "$DB_NAME" -c "\d $TABLE_NAME" | grep -i "geometry\|geom" || echo "  Aucune colonne géométrie trouvée"
    exit 1
fi

# Récupérer le SRID de la géométrie
SRID=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT Find_SRID('public', '$TABLE_NAME', '$GEOM_COLUMN');
" | tr -d ' ')

if [ -z "$SRID" ] || [ "$SRID" = "-1" ]; then
    echo " SRID non détecté, utilisation de 4326 par défaut"
    SRID=4326
else
    echo "SRID détecté: $SRID"
fi

# Récupérer la bbox
BBOX=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT 
        ST_XMin(ST_Extent($GEOM_COLUMN)) || ',' ||
        ST_YMin(ST_Extent($GEOM_COLUMN)) || ',' ||
        ST_XMax(ST_Extent($GEOM_COLUMN)) || ',' ||
        ST_YMax(ST_Extent($GEOM_COLUMN))
    FROM $TABLE_NAME;
" | tr -d ' ')

if [ -z "$BBOX" ] || [ "$BBOX" = "" ]; then
    echo " BBOX non calculable, utilisation de valeurs par défaut (France)"
    BBOX="-5.0,41.0,10.0,51.0"
else
    echo "BBOX: $BBOX"
fi

# Créer le répertoire si nécessaire
mkdir -p "$MAPFILES_DIR"

# Générer le mapfile
echo ""
echo "Génération du mapfile..."
cat > "$MAPFILE_PATH" <<EOF
MAP
    NAME "${TABLE_NAME}"
    STATUS ON
    SIZE 800 600
    EXTENT $BBOX
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255
    FONTSET "/mapserver/fonts/fonts.list"
    
    # Projection
    PROJECTION
        "init=epsg:$SRID"
    END
    
    # Symboles
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    
    # Couche
    LAYER
        NAME "${TABLE_NAME}"
        TYPE POINT
        STATUS ON
        CONNECTIONTYPE postgis
        CONNECTION "host=${DB_HOST} port=5432 dbname=${DB_NAME} user=${DB_USER} password=${DB_PASSWORD}"
        DATA "${GEOM_COLUMN} FROM ${TABLE_NAME} USING UNIQUE _id"
        PROJECTION
            "init=epsg:$SRID"
        END
        CLASS
            NAME "default"
            STYLE
                SYMBOL "circle_point"
                SIZE 6
                COLOR 0 0 255
                OUTLINECOLOR 255 255 255
                WIDTH 1
            END
        END
        METADATA
            "wms_title" "${TABLE_NAME}"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
        END
    END
    
    # Métadonnées WMS
    WEB
        METADATA
            "wms_title" "WMS Service for ${TABLE_NAME}"
            "wms_onlineresource" "http://localhost:8081/wms?map=${MAPFILE_PATH}"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
        END
    END
END
EOF

echo "Mapfile créé: $MAPFILE_PATH"
echo ""
echo "Prochaines étapes:"
echo "   1. Vérifier le mapfile: cat $MAPFILE_PATH"
echo "   2. Tester avec MapServer:"
echo "      http://localhost:8081/wms?map=$MAPFILE_PATH&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
echo "   3. Si SRID=$SRID n'est pas 4326, considérer créer une vue avec conversion:"
echo "      CREATE VIEW ${TABLE_NAME}_4326 AS SELECT *, ST_Transform($GEOM_COLUMN, 4326) as ${GEOM_COLUMN}_4326 FROM $TABLE_NAME;"
echo ""



