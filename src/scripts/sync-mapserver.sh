#!/bin/bash
# Script pour lancer la synchronisation MapServer via l'API CKAN

set -euo pipefail

CKAN_URL="${CKAN_URL:-https://ckan2.qualif-data.example.org}"
CKAN_TOKEN="${CKAN_TOKEN:-}"

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Fonction d'aide
show_help() {
    cat << EOF
Usage: $0 [OPTIONS]

Lance la synchronisation MapServer (génération des mapfiles) via l'API CKAN.

OPTIONS:
    -u, --url URL          URL du serveur CKAN (défaut: https://ckan2.qualif-data.example.org)
    -t, --token TOKEN      Token d'authentification CKAN (requis)
    -h, --help             Affiche cette aide

VARIABLES D'ENVIRONNEMENT:
    CKAN_URL               URL du serveur CKAN
    CKAN_TOKEN             Token d'authentification CKAN

EXEMPLES:
    # Avec variables d'environnement
    export CKAN_TOKEN="your-token-here"
    $0

    # Avec options en ligne de commande
    $0 --url https://ckan.example.com --token your-token-here

    # Depuis Docker
    docker exec ckan $0

EOF
}

# Parsing des arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -u|--url)
            CKAN_URL="$2"
            shift 2
            ;;
        -t|--token)
            CKAN_TOKEN="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}Option inconnue: $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# Vérifier que le token est défini
if [ -z "$CKAN_TOKEN" ]; then
    echo -e "${RED}Erreur: CKAN_TOKEN non défini${NC}"
    echo ""
    echo "   Définir avec:"
    echo "   export CKAN_TOKEN=your-token-here"
    echo ""
    echo "   Ou utiliser l'option:"
    echo "   $0 --token your-token-here"
    exit 1
fi

# Afficher les informations
echo -e "${BLUE} Lancement de la synchronisation MapServer...${NC}"
echo -e "   URL: ${CKAN_URL}"
echo ""

# Lancer la requête
RESPONSE=$(curl -s -X POST "${CKAN_URL}/api/action/admin_sync_mapserver" \
  -H "Authorization: ${CKAN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}' \
  -w "\n%{http_code}" \
  --max-time 600) || {
    echo -e "${RED}Erreur de connexion au serveur CKAN${NC}"
    exit 1
}

# Extraire le code HTTP et le corps de la réponse
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

# Vérifier le code HTTP
if [ "$HTTP_CODE" != "200" ]; then
    echo -e "${RED}Erreur HTTP: $HTTP_CODE${NC}"
    echo ""
    # Essayer d'afficher le message d'erreur JSON si disponible
    if command -v jq &> /dev/null; then
        echo "$BODY" | jq -r '.error // .' 2>/dev/null || echo "$BODY"
    else
        echo "$BODY"
    fi
    exit 1
fi

# Parser la réponse JSON
if ! command -v jq &> /dev/null; then
    echo -e "${YELLOW} jq non disponible, affichage brut de la réponse${NC}"
    echo "$BODY"
    exit 0
fi

SUCCESS=$(echo "$BODY" | jq -r '.success // false')
MESSAGE=$(echo "$BODY" | jq -r '.message // "N/A"')
SUCCESS_COUNT=$(echo "$BODY" | jq -r '.success_count // 0')
ERROR_COUNT=$(echo "$BODY" | jq -r '.error_count // 0')

# Afficher les résultats
if [ "$SUCCESS" = "true" ]; then
    echo -e "${GREEN}$MESSAGE${NC}"
    if [ "$SUCCESS_COUNT" != "0" ] && [ "$SUCCESS_COUNT" != "null" ]; then
        echo -e "   ${GREEN}Mapfiles générés: $SUCCESS_COUNT${NC}"
    fi
    if [ "$ERROR_COUNT" != "0" ] && [ "$ERROR_COUNT" != "null" ]; then
        echo -e "   ${YELLOW} Erreurs: $ERROR_COUNT${NC}"
    fi
    
    # Afficher un extrait de la sortie si disponible
    OUTPUT=$(echo "$BODY" | jq -r '.output // ""')
    if [ -n "$OUTPUT" ] && [ "$OUTPUT" != "null" ]; then
        echo ""
        echo -e "${BLUE}Sortie du script (dernières lignes):${NC}"
        echo "$OUTPUT" | tail -n 20 | sed 's/^/   /'
    fi
else
    ERROR=$(echo "$BODY" | jq -r '.error // "Erreur inconnue"')
    echo -e "${RED}Erreur: $ERROR${NC}"
    
    # Afficher un extrait de la sortie si disponible
    OUTPUT=$(echo "$BODY" | jq -r '.output // ""')
    if [ -n "$OUTPUT" ] && [ "$OUTPUT" != "null" ]; then
        echo ""
        echo -e "${BLUE}Sortie du script (dernières lignes):${NC}"
        echo "$OUTPUT" | tail -n 20 | sed 's/^/   /'
    fi
    exit 1
fi


