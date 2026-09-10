#!/bin/bash

# Script pour importer le dump datagis dans une base PostgreSQL dédiée

set -e

DUMP_FILE="${1:-dumps/geo/datagis_20250124.sql}"
DB_NAME="${DB_NAME:-datagis}"
DB_HOST="${DB_HOST:-db}"
DB_USER="${DB_USER:-ckan}"
DB_PASSWORD="${POSTGRES_PASSWORD:-ckan}"

echo " Import du dump datagis dans PostgreSQL"
echo "=========================================="
echo ""

# Vérifier que le dump existe
if [ ! -f "$DUMP_FILE" ]; then
    echo "Erreur: Fichier dump non trouvé: $DUMP_FILE"
    echo ""
    echo "Usage: $0 [chemin_vers_dump.sql]"
    exit 1
fi

DUMP_SIZE=$(du -h "$DUMP_FILE" | awk '{print $1}')
echo "Fichier: $DUMP_FILE"
echo "Taille: $DUMP_SIZE"
echo ""

# Vérifier que le conteneur db est accessible
if ! docker exec db psql -U "$DB_USER" -d postgres -c "SELECT 1;" > /dev/null 2>&1; then
    echo "Erreur: Impossible de se connecter au conteneur PostgreSQL 'db'"
    echo "   Vérifiez que le conteneur est démarré: docker ps | grep db"
    exit 1
fi

echo "Connexion à PostgreSQL réussie"
echo ""

# Supprimer la base existante si demandé (via variable d'environnement)
if [ "${DROP_EXISTING:-false}" = "true" ]; then
    echo " Suppression de la base de données '$DB_NAME' existante..."
    docker exec db psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null || true
    echo "Base de données '$DB_NAME' supprimée"
    echo ""
fi

# Créer la base de données datagis si elle n'existe pas
echo "Création de la base de données '$DB_NAME'..."
EXISTS=$(docker exec db psql -U "$DB_USER" -d postgres -t -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME';" | tr -d ' ')
if [ -z "$EXISTS" ]; then
    docker exec db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'utf-8';"
    echo "Base de données '$DB_NAME' créée"
else
    echo " Base de données '$DB_NAME' existe déjà"
    echo ""
    read -p "Voulez-vous la supprimer et réimporter ? [y/N]: " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo " Suppression de la base existante..."
        docker exec db psql -U "$DB_USER" -d postgres -c "DROP DATABASE $DB_NAME;"
        docker exec db psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'utf-8';"
        echo "Base de données '$DB_NAME' recréée"
    else
        echo "Import annulé"
        exit 1
    fi
fi

# Activer PostGIS dans la base datagis
echo "Activation de PostGIS dans '$DB_NAME'..."
docker exec db psql -U "$DB_USER" -d "$DB_NAME" <<EOF
-- Activer PostGIS si pas déjà activé
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
EOF

echo "Base de données '$DB_NAME' prête"
echo ""

# Vérifier si la base contient déjà des données
TABLE_COUNT=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(*) 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE';
" | tr -d ' ')

if [ "$TABLE_COUNT" -gt 0 ]; then
    echo " La base '$DB_NAME' contient déjà $TABLE_COUNT tables"
    echo ""
    read -p "Voulez-vous continuer l'import ? (cela peut ajouter des doublons) [y/N]: " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Import annulé"
        exit 1
    fi
fi

# Créer le rôle datagis si nécessaire (le dump peut le référencer)
echo "Vérification des rôles nécessaires..."
docker exec db psql -U "$DB_USER" -d postgres -c "CREATE ROLE datagis NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN;" 2>/dev/null || echo "   Rôle datagis existe déjà ou sera créé par le dump"

# Importer le dump
echo "Import du dump (cela peut prendre du temps pour 41G)..."
echo "   Cela peut prendre plusieurs heures selon la taille du dump"
echo "    Les erreurs de rôle seront ignorées (normal si le dump référence des rôles inexistants)"
echo ""

# Utiliser pg_restore si c'est un dump custom, sinon psql
if file "$DUMP_FILE" | grep -q "PostgreSQL custom database dump"; then
    echo "   Format détecté: PostgreSQL custom dump (pg_restore)"
    docker exec -i db pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-acl < "$DUMP_FILE" 2>&1 | grep -v "ERROR.*role.*does not exist" || {
        echo " pg_restore a échoué, tentative avec psql..."
        docker exec -i db psql -U "$DB_USER" -d "$DB_NAME" < "$DUMP_FILE" 2>&1 | grep -v "ERROR.*role.*does not exist"
    }
else
    echo "   Format détecté: SQL plain (psql)"
    # Utiliser PGPASSWORD pour éviter les prompts
    # Filtrer les erreurs de rôle (normales si le dump référence des rôles inexistants)
    export PGPASSWORD="$DB_PASSWORD"
    docker exec -i -e PGPASSWORD="$DB_PASSWORD" db psql -U "$DB_USER" -d "$DB_NAME" < "$DUMP_FILE" 2>&1 | grep -v "ERROR.*role.*does not exist" || {
        echo " Certaines erreurs peuvent être normales (rôles inexistants)"
        echo "   Vérification de l'import en cours..."
    }
    unset PGPASSWORD
fi

echo ""
echo "Import terminé"
echo ""

# Afficher des statistiques
echo "Statistiques de la base '$DB_NAME':"
echo "-----------------------------------"
TABLE_COUNT=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(*) 
    FROM information_schema.tables 
    WHERE table_schema = 'public' 
    AND table_type = 'BASE TABLE';
" | tr -d ' ')

GEOM_TABLE_COUNT=$(docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT COUNT(DISTINCT table_name)
    FROM information_schema.columns
    WHERE table_schema = 'public'
    AND data_type = 'USER-DEFINED'
    AND udt_name = 'geometry';
" | tr -d ' ')

echo "  - Tables: $TABLE_COUNT"
echo "  - Tables avec géométrie: $GEOM_TABLE_COUNT"
echo ""

# Lister quelques tables géospatiales
echo " Exemples de tables géospatiales:"
docker exec db psql -U "$DB_USER" -d "$DB_NAME" -t -c "
    SELECT DISTINCT table_name
    FROM information_schema.columns
    WHERE table_schema = 'public'
    AND data_type = 'USER-DEFINED'
    AND udt_name = 'geometry'
    ORDER BY table_name
    LIMIT 10;
" | sed 's/^/    - /'

echo ""
echo "Base '$DB_NAME' prête à être utilisée"
echo ""
echo "Prochaines étapes:"
echo "   1. Explorer les tables: docker exec -it db psql -U $DB_USER -d $DB_NAME"
echo "   2. Analyser les relations: ./scripts/explore-datagis-relations.sh"
echo "   3. Utiliser avec MapServer: modifier generate-mapfile.py pour pointer vers datagis"
echo ""

