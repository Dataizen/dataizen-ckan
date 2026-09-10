#!/bin/bash
#
# Script pour synchroniser/relancer la génération des mapfiles MapServer
#
# Usage:
#   ./sync-mapfiles.sh                    # Génère tous les mapfiles
#   ./sync-mapfiles.sh <dataset_name>      # Génère le mapfile pour un dataset spécifique
#   ./sync-mapfiles.sh --force            # Force la régénération de tous les mapfiles
#   ./sync-mapfiles.sh <dataset_name> --force  # Force la régénération d'un dataset
#

set -euo pipefail

# Configuration depuis les variables d'environnement
CKAN_URL="${CKAN_URL:-http://ckan:5000}"
CKAN_API_KEY="${CKAN_API_KEY:-}"
POSTGRES_HOST="${POSTGRES_HOST:-db}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-datastore}"
POSTGRES_USER="${POSTGRES_USER:-ckan}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-ckan}"
MAPFILES_DIR="${MAPFILES_DIR:-/mapserver/mapfiles}"

# Script de génération
GENERATE_SCRIPT="/usr/local/bin/generate-mapfile.py"
if [ ! -f "$GENERATE_SCRIPT" ]; then
    GENERATE_SCRIPT="/srv/app/ckanext-ogc/scripts/generate-mapfile.py"
fi

# Couleurs pour les logs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}$1${NC}"
}

log_success() {
    echo -e "${GREEN}$1${NC}"
}

log_warning() {
    echo -e "${YELLOW} $1${NC}"
}

log_error() {
    echo -e "${RED}$1${NC}"
}

# Vérifier que le script existe
if [ ! -f "$GENERATE_SCRIPT" ]; then
    log_error "Script generate-mapfile.py non trouvé: $GENERATE_SCRIPT"
    exit 1
fi

# Vérifier les arguments
FORCE=false
DATASET_NAME=""

if [ $# -eq 0 ]; then
    # Aucun argument: générer tous les mapfiles
    log_info "Génération de tous les mapfiles..."
    MODE="all"
elif [ "$1" = "--force" ]; then
    # --force seul: forcer la régénération de tous
    FORCE=true
    MODE="all"
    log_info "Régénération forcée de tous les mapfiles..."
elif [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    echo "Usage: $0 [dataset_name] [--force]"
    echo ""
    echo "Options:"
    echo "  dataset_name    Nom du dataset à synchroniser (optionnel)"
    echo "  --force         Force la régénération même si le mapfile existe déjà"
    echo ""
    echo "Exemples:"
    echo "  $0                              # Génère tous les mapfiles"
    echo "  $0 testlmocsv                   # Génère le mapfile pour testlmocsv"
    echo "  $0 --force                      # Régénère tous les mapfiles"
    echo "  $0 testlmocsv --force           # Régénère le mapfile pour testlmocsv"
    exit 0
else
    DATASET_NAME="$1"
    if [ $# -gt 1 ] && [ "$2" = "--force" ]; then
        FORCE=true
    fi
    MODE="single"
    log_info "Génération du mapfile pour: $DATASET_NAME"
fi

# Construire la commande
CMD=(
    python3 "$GENERATE_SCRIPT"
    --ckan-url "$CKAN_URL"
    --ckan-api-key "$CKAN_API_KEY"
    --mapfiles-dir "$MAPFILES_DIR"
    --postgis-host "$POSTGRES_HOST"
    --postgis-port "$POSTGRES_PORT"
    --postgis-db "$POSTGRES_DB"
    --postgis-user "$POSTGRES_USER"
    --postgis-password "$POSTGRES_PASSWORD"
    --auto-create-geometry
)

if [ "$MODE" = "single" ]; then
    CMD+=(--dataset "$DATASET_NAME")
fi

if [ "$FORCE" = true ]; then
    log_warning "Mode FORCE activé: les mapfiles existants seront écrasés"
fi

# Afficher la configuration
log_info "Configuration:"
log_info "  CKAN URL: $CKAN_URL"
log_info "  PostGIS: $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
log_info "  Mapfiles dir: $MAPFILES_DIR"
log_info "  Script: $GENERATE_SCRIPT"
echo ""

# Exécuter la commande
log_info "Exécution de la génération des mapfiles..."
echo ""

# Capturer la sortie pour analyser les erreurs
OUTPUT=$("${CMD[@]}" 2>&1)
EXIT_CODE=$?

# Afficher la sortie
echo "$OUTPUT"

echo ""

# Analyser le résultat
if [ $EXIT_CODE -ne 0 ]; then
    log_error "Échec de la synchronisation des mapfiles (code retour: $EXIT_CODE)"
    log_error "Causes possibles:"
    log_error "  - PostGIS non activé dans datastore"
    log_error "  - Colonne geometry non créée (vérifier les colonnes GeoJSON)"
    log_error "  - Erreur lors de la création de la colonne geometry"
    log_error "  - Aucune ressource géospatiale trouvée"
    log_error "  - Erreur de connexion à la base de données"
    
    if [ "$MODE" = "single" ]; then
        log_error "Utilisez le script de diagnostic:"
        log_error "  docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh ${DATASET_NAME} --fix"
    fi
    
    # Extraire les erreurs spécifiques de la sortie
    ERROR_LINES=$(echo "$OUTPUT" | grep -E "|ERROR|Erreur|Échec|Traceback" | head -10 || true)
    if [ -n "$ERROR_LINES" ]; then
        log_error "Dernières erreurs détectées:"
        echo "$ERROR_LINES" | while IFS= read -r line; do
            log_error "  $line"
        done
    fi
    
    exit $EXIT_CODE
fi

# Vérifier s'il y a des erreurs dans la sortie même si exit code == 0
ERROR_COUNT=$(echo "$OUTPUT" | grep -cE "|ERROR|Erreur|Échec" || true)
WARNING_COUNT=$(echo "$OUTPUT" | grep -cE "|WARNING" || true)

if [ "$ERROR_COUNT" -gt 0 ]; then
    log_warning "ATTENTION: Des erreurs ont été détectées dans la génération des mapfiles"
    log_warning "Nombre d'erreurs détectées: $ERROR_COUNT"
    
    if [ "$MODE" = "single" ]; then
        log_warning "Utilisez le script de diagnostic:"
        log_warning "  docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh ${DATASET_NAME} --fix"
    fi
    
    # Extraire les erreurs spécifiques
    ERROR_LINES=$(echo "$OUTPUT" | grep -E "|ERROR|Erreur|Échec" | head -10 || true)
    if [ -n "$ERROR_LINES" ]; then
        log_warning "Erreurs détectées:"
        echo "$ERROR_LINES" | while IFS= read -r line; do
            log_warning "  $line"
        done
    fi
elif [ "$WARNING_COUNT" -gt 0 ]; then
    log_warning "Génération terminée avec $WARNING_COUNT avertissement(s)"
else
    log_success "Synchronisation des mapfiles terminée sans erreurs détectées!"
fi

# Afficher les mapfiles générés
if [ "$MODE" = "single" ]; then
    MAPFILE_PATH="$MAPFILES_DIR/${DATASET_NAME}.map"
    if [ -f "$MAPFILE_PATH" ]; then
        log_success "Mapfile créé: $MAPFILE_PATH"
        log_info "Taille: $(du -h "$MAPFILE_PATH" | cut -f1)"
    else
        log_warning "Mapfile non trouvé: $MAPFILE_PATH"
        log_warning "La génération a peut-être échoué silencieusement"
    fi
else
    MAPFILE_COUNT=$(find "$MAPFILES_DIR" -name "*.map" -type f 2>/dev/null | wc -l)
    SUCCESS_COUNT=$(echo "$OUTPUT" | grep -cE "MAPFILE GÉNÉRÉ|Mapfile généré" || true)
    if [ "$SUCCESS_COUNT" -gt 0 ]; then
        log_success "Mapfiles générés avec succès: $SUCCESS_COUNT"
    fi
    log_info "Total de mapfiles dans le répertoire: $MAPFILE_COUNT"
fi

