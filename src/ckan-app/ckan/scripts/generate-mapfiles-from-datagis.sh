#!/bin/bash

# Script pour générer tous les mapfiles MapServer depuis la base datagis
# Ce script crée des mapfiles pour toutes les tables géospatiales dans datagis

set -e

DB_NAME_DATAGIS="${DB_NAME_DATAGIS:-datagis}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_PASSWORD="${POSTGRES_PASSWORD:-ckan}"
MAPFILES_DIR="${MAPFILES_DIR:-./mapserver/mapfiles}"

echo " Génération des mapfiles depuis datagis"
echo "=========================================="
echo ""
echo "Base: $DB_NAME_DATAGIS"
echo "Répertoire mapfiles: $MAPFILES_DIR"
echo ""

# Créer le répertoire si nécessaire
mkdir -p "$MAPFILES_DIR"

# Récupérer toutes les tables avec géométrie
echo "Récupération des tables géospatiales..."
TABLES=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
    SELECT DISTINCT f_table_name, f_geometry_column, srid
    FROM geometry_columns
    WHERE f_table_schema = 'public'
    ORDER BY f_table_name;
")

if [ -z "$TABLES" ]; then
    echo "Aucune table géospatiale trouvée dans datagis"
    exit 1
fi

TABLE_COUNT=$(echo "$TABLES" | grep -c "^" || echo "0")
echo "$TABLE_COUNT tables géospatiales trouvées"
echo ""

# Compteurs
SUCCESS=0
FAILED=0
SKIPPED=0

# Générer un mapfile pour chaque table
while IFS='|' read -r table_name geom_col srid; do
    # Nettoyer les espaces
    table_name=$(echo "$table_name" | tr -d ' ')
    geom_col=$(echo "$geom_col" | tr -d ' ')
    srid=$(echo "$srid" | tr -d ' ')
    
    if [ -z "$table_name" ]; then
        continue
    fi
    
    MAPFILE_PATH="${MAPFILES_DIR}/datagis_${table_name}.map"
    
    # Vérifier si le mapfile existe déjà
    if [ -f "$MAPFILE_PATH" ]; then
        echo " Mapfile existe déjà: datagis_${table_name}.map"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    
    echo "Génération mapfile pour: $table_name (SRID: $srid)"
    
    # Récupérer la bbox
    BBOX=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
        SELECT 
            ST_XMin(ST_Extent($geom_col)) || ',' ||
            ST_YMin(ST_Extent($geom_col)) || ',' ||
            ST_XMax(ST_Extent($geom_col)) || ',' ||
            ST_YMax(ST_Extent($geom_col))
        FROM \"$table_name\";
    " 2>/dev/null | tr -d ' ' || echo "")
    
    if [ -z "$BBOX" ] || [ "$BBOX" = "" ]; then
        echo "   BBOX non calculable, utilisation de valeurs par défaut (France)"
        BBOX="-5.0,41.0,10.0,51.0"
    fi
    
    # Détecter le type de géométrie
    GEOM_TYPE=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME_DATAGIS" -t -c "
        SELECT ST_GeometryType($geom_col) 
        FROM \"$table_name\" 
        WHERE $geom_col IS NOT NULL 
        LIMIT 1;
    " 2>/dev/null | tr -d ' ' | tr '[:upper:]' '[:lower:]' || echo "geometry")
    
    # Déterminer le TYPE MapServer
    if echo "$GEOM_TYPE" | grep -q "point"; then
        MAPSERVER_TYPE="POINT"
    elif echo "$GEOM_TYPE" | grep -q "linestring\|multilinestring"; then
        MAPSERVER_TYPE="LINE"
    elif echo "$GEOM_TYPE" | grep -q "polygon\|multipolygon"; then
        MAPSERVER_TYPE="POLYGON"
    else
        MAPSERVER_TYPE="POLYGON"  # Par défaut
    fi
    
    # Générer le mapfile
    cat > "$MAPFILE_PATH" <<EOF
MAP
    NAME "datagis_${table_name}"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT $BBOX
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255
    FONTSET "/mapserver/fonts/fonts.list"
    
    # Configuration
    CONFIG "PROJ_LIB" "/usr/share/proj"
    CONFIG "MS_ERRORFILE" "/mapserver/logs/datagis_${table_name}_error.log"
    
    # Projection
    PROJECTION
        "init=epsg:${srid}"
    END
    
    # Métadonnées WMS/WFS
    WEB
        METADATA
            "wms_title" "WMS Service for ${table_name} (datagis)"
            "wms_onlineresource" "http://localhost:8081/wms?map=/mapserver/mapfiles/datagis_${table_name}.map"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154 EPSG:${srid}"
            "wms_enable_request" "*"
            "wfs_title" "WFS Service for ${table_name} (datagis)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154 EPSG:${srid}"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
            "ows_enable_request" "*"
        END
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
        NAME "${table_name}"
        TYPE ${MAPSERVER_TYPE}
        STATUS ON
        CONNECTIONTYPE postgis
        CONNECTION "host=${DB_HOST} port=5432 dbname=${DB_NAME_DATAGIS} user=${DB_USER} password=${DB_PASSWORD}"
        DATA "${geom_col} FROM \"${table_name}\" USING UNIQUE _id"
        PROJECTION
            "init=epsg:${srid}"
        END
        CLASS
            NAME "default"
EOF
    
    # Ajouter le style selon le type
    if [ "$MAPSERVER_TYPE" = "POINT" ]; then
        cat >> "$MAPFILE_PATH" <<EOF
            STYLE
                SYMBOL "circle_point"
                SIZE 6
                COLOR 0 0 255
                OUTLINECOLOR 255 255 255
                WIDTH 1
            END
EOF
    elif [ "$MAPSERVER_TYPE" = "LINE" ]; then
        cat >> "$MAPFILE_PATH" <<EOF
            STYLE
                COLOR 0 0 255
                WIDTH 2
            END
EOF
    else
        cat >> "$MAPFILE_PATH" <<EOF
            STYLE
                COLOR 200 200 255
                OUTLINECOLOR 0 0 255
                WIDTH 1
            END
EOF
    fi
    
    cat >> "$MAPFILE_PATH" <<EOF
        END
        METADATA
            "wms_title" "${table_name} (datagis)"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154 EPSG:${srid}"
            "wms_enable_request" "*"
            "wfs_title" "${table_name} (datagis)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154 EPSG:${srid}"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
        END
    END
END
EOF
    
    if [ -f "$MAPFILE_PATH" ]; then
        echo "  Mapfile créé: datagis_${table_name}.map"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "  Erreur lors de la création du mapfile"
        FAILED=$((FAILED + 1))
    fi
    
done <<< "$TABLES"

echo ""
echo "Résumé:"
echo "  Succès: $SUCCESS"
echo "   Ignorés (déjà existants): $SKIPPED"
echo "  Échecs: $FAILED"
echo "  Total: $TABLE_COUNT tables"
echo ""
echo "Les mapfiles sont disponibles dans: $MAPFILES_DIR"
echo ""

