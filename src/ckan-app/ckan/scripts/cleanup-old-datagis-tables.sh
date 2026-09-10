#!/bin/bash
#
# Script pour nettoyer les anciennes tables datagis au format {clean_name}_{hash}
# (sans le préfixe res_)
#
# Usage:
#   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh
#   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh --dry-run
#   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh --force
#

set -uo pipefail
# Note: on n'utilise pas 'set -e' pour ne pas s'arrêter à la première erreur dans la boucle

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Variables d'environnement (avec valeurs par défaut)
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-ckan}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
DATAGIS_DB="${DATAGIS_DB:-datagis}"

# Options
DRY_RUN=false
FORCE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--force]"
            echo ""
            echo "Options:"
            echo "  --dry-run    Affiche les tables qui seraient supprimées sans les supprimer"
            echo "  --force      Supprime les tables sans demander confirmation"
            echo ""
            echo "Variables d'environnement:"
            echo "  POSTGRES_HOST      Host PostgreSQL (défaut: db)"
            echo "  POSTGRES_PORT      Port PostgreSQL (défaut: 5432)"
            echo "  POSTGRES_USER      Utilisateur PostgreSQL (défaut: ckan)"
            echo "  POSTGRES_PASSWORD  Mot de passe PostgreSQL"
            echo "  DATAGIS_DB         Base de données datagis (défaut: datagis)"
            exit 0
            ;;
        *)
            echo "Option inconnue: $1"
            echo "Utilisez --help pour voir l'aide"
            exit 1
            ;;
    esac
done

echo "================================================================================"
echo "Nettoyage des anciennes tables datagis (format {clean_name}_{hash})"
echo "================================================================================"
echo ""
echo "Configuration:"
echo "   Host: ${POSTGRES_HOST}"
echo "   Port: ${POSTGRES_PORT}"
echo "   User: ${POSTGRES_USER}"
echo "   Database: ${DATAGIS_DB}"
echo "   Mode: $([ "$DRY_RUN" = true ] && echo "DRY-RUN (simulation)" || echo "EXÉCUTION")"
echo ""

# Vérifier que psql est disponible
if ! command -v psql &> /dev/null; then
    echo "psql n'est pas disponible. Installez postgresql-client."
    exit 1
fi

# Fonction pour exécuter une requête SQL
run_sql() {
    local sql="$1"
    PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" \
        -p "${POSTGRES_PORT}" \
        -U "${POSTGRES_USER}" \
        -d "${DATAGIS_DB}" \
        -t -A \
        -c "$sql" 2>/dev/null || echo ""
}

# Vérifier que la base datagis existe
if ! PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -h "${POSTGRES_HOST}" \
    -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" \
    -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "${DATAGIS_DB}"; then
    echo "La base de données '${DATAGIS_DB}' n'existe pas."
    echo "   Créez-la avec: CREATE DATABASE ${DATAGIS_DB} OWNER ${POSTGRES_USER};"
    exit 1
fi

echo "Connexion à la base '${DATAGIS_DB}' réussie"
echo ""

# Récupérer toutes les tables géospatiales (depuis geometry_columns)
echo "Recherche des tables géospatiales dans datagis..."
ALL_TABLES=$(run_sql "
    SELECT f_table_name
    FROM geometry_columns
    WHERE f_table_schema = 'public'
    ORDER BY f_table_name;
")

if [ -z "$ALL_TABLES" ]; then
    echo "Aucune table géospatiale trouvée dans datagis."
    exit 0
fi

# Filtrer les tables qui ne commencent PAS par "res_" (ancien format)
OLD_TABLES=()
while IFS= read -r table; do
    # Nettoyer les espaces en début/fin
    table=$(echo "$table" | xargs)
    if [ -n "$table" ] && [[ ! "$table" =~ ^res_ ]]; then
        OLD_TABLES+=("$table")
    fi
done <<< "$ALL_TABLES"

# Debug: afficher le nombre de tables trouvées
if [ ${#OLD_TABLES[@]} -gt 0 ]; then
    echo "${#OLD_TABLES[@]} table(s) à l'ancien format identifiée(s)"
fi

# Afficher le résumé
TOTAL_TABLES=$(echo "$ALL_TABLES" | grep -c . || echo "0")
OLD_COUNT=${#OLD_TABLES[@]}
NEW_COUNT=$((TOTAL_TABLES - OLD_COUNT))

echo "Statistiques:"
echo "   Total de tables géospatiales: ${TOTAL_TABLES}"
echo "   Tables au nouveau format (res_*): ${NEW_COUNT}"
echo "   Tables à l'ancien format ({name}_{hash}): ${OLD_COUNT}"
echo ""

if [ $OLD_COUNT -eq 0 ]; then
    echo "Aucune ancienne table à nettoyer. Toutes les tables utilisent le nouveau format (res_*)."
    exit 0
fi

# Afficher les tables à supprimer
echo " Tables à supprimer (format ancien):"
for table in "${OLD_TABLES[@]}"; do
    # Récupérer des infos sur la table
    ROW_COUNT=$(run_sql "SELECT COUNT(*) FROM \"${table}\";" 2>/dev/null || echo "?")
    GEOM_COL=$(run_sql "SELECT f_geometry_column FROM geometry_columns WHERE f_table_name = '${table}' LIMIT 1;" 2>/dev/null || echo "?")
    SRID=$(run_sql "SELECT srid FROM geometry_columns WHERE f_table_name = '${table}' LIMIT 1;" 2>/dev/null || echo "?")
    
    echo "   - ${table}"
    echo "     → Géométrie: ${GEOM_COL}, SRID: ${SRID}, Lignes: ${ROW_COUNT}"
done
echo ""

# Mode dry-run
if [ "$DRY_RUN" = true ]; then
    echo "MODE DRY-RUN: Aucune table ne sera supprimée."
    echo "   Pour supprimer réellement, exécutez sans --dry-run"
    exit 0
fi

# Demander confirmation (sauf si --force)
if [ "$FORCE" = false ]; then
    echo " ATTENTION: Cette opération va supprimer ${OLD_COUNT} table(s) et leurs métadonnées."
    echo "   Les tables suivantes seront supprimées:"
    for table in "${OLD_TABLES[@]}"; do
        echo "      - ${table}"
    done
    echo ""
    read -p "   Continuer ? (oui/non): " -r
    echo ""
    if [[ ! $REPLY =~ ^[Oo][Uu][Ii]$ ]]; then
        echo "Opération annulée."
        exit 0
    fi
fi

# Supprimer les tables
echo " Suppression des tables..."
SUCCESS_COUNT=0
ERROR_COUNT=0
ERROR_DETAILS=()

# Debug: afficher le nombre de tables à supprimer
echo "   ${#OLD_TABLES[@]} table(s) à supprimer"
echo ""

TOTAL_TABLES=${#OLD_TABLES[@]}
CURRENT_INDEX=0

for table in "${OLD_TABLES[@]}"; do
    CURRENT_INDEX=$((CURRENT_INDEX + 1))
    echo -n "   [${CURRENT_INDEX}/${TOTAL_TABLES}] Suppression de '${table}'... "
    
    # Supprimer la table (cascade supprime aussi les index, contraintes, etc.)
    SQL_DROP="DROP TABLE IF EXISTS \"${table}\" CASCADE;"
    
    # Capturer stdout et stderr pour afficher les erreurs
    DROP_OUTPUT=$(PGPASSWORD="${POSTGRES_PASSWORD}" psql \
        -h "${POSTGRES_HOST}" \
        -p "${POSTGRES_PORT}" \
        -U "${POSTGRES_USER}" \
        -d "${DATAGIS_DB}" \
        -c "$SQL_DROP" 2>&1)
    DROP_EXIT_CODE=$?
    
    if [ $DROP_EXIT_CODE -eq 0 ]; then
        # Supprimer les métadonnées dans geometry_columns (devrait être automatique avec CASCADE, mais on vérifie)
        run_sql "DELETE FROM geometry_columns WHERE f_table_name = '${table}';" >/dev/null 2>&1 || true
        
        # Supprimer les métadonnées dans datagis_import_metadata si la table existe
        run_sql "DELETE FROM datagis_import_metadata WHERE table_name = '${table}';" >/dev/null 2>&1 || true
        
        # Vérifier que la table a bien été supprimée
        TABLE_STILL_EXISTS=$(run_sql "SELECT COUNT(*) FROM geometry_columns WHERE f_table_name = '${table}';" 2>/dev/null || echo "1")
        if [ "$TABLE_STILL_EXISTS" = "0" ] || [ -z "$TABLE_STILL_EXISTS" ]; then
            echo -e "${GREEN}${NC}"
            SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        else
            echo -e "${YELLOW}${NC} (table toujours présente dans geometry_columns)"
            ERROR_DETAILS+=("${table}: table toujours présente après suppression")
            ERROR_COUNT=$((ERROR_COUNT + 1))
        fi
    else
        echo -e "${RED}${NC}"
        # Afficher l'erreur si elle n'est pas vide
        if [ -n "$DROP_OUTPUT" ]; then
            echo "      Erreur: ${DROP_OUTPUT}"
        fi
        ERROR_DETAILS+=("${table}: ${DROP_OUTPUT}")
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
    
    # Afficher la progression toutes les 50 tables
    if [ $((CURRENT_INDEX % 50)) -eq 0 ]; then
        echo ""
        echo "   Progression: ${CURRENT_INDEX}/${TOTAL_TABLES} tables traitées (${SUCCESS_COUNT} , ${ERROR_COUNT} )"
        echo ""
    fi
done

echo ""
echo "================================================================================"
echo "Résumé:"
echo "   Tables supprimées avec succès: ${SUCCESS_COUNT}"
if [ $ERROR_COUNT -gt 0 ]; then
    echo -e "   ${RED}Erreurs: ${ERROR_COUNT}${NC}"
    echo ""
    echo "   Détails des erreurs:"
    for detail in "${ERROR_DETAILS[@]}"; do
        echo -e "   ${RED}   - ${detail}${NC}"
    done
fi
echo "================================================================================"

if [ $ERROR_COUNT -eq 0 ]; then
    echo ""
    echo "Nettoyage terminé avec succès !"
    echo ""
    echo "Vérification:"
    echo "   Pour vérifier les tables restantes:"
    echo "   docker exec db psql -U ${POSTGRES_USER} -d ${DATAGIS_DB} -c \"SELECT f_table_name FROM geometry_columns WHERE f_table_schema = 'public' ORDER BY f_table_name;\""
    exit 0
else
    echo ""
    echo " Certaines tables n'ont pas pu être supprimées. Vérifiez les erreurs ci-dessus."
    exit 1
fi
