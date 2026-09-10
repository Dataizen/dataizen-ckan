#!/bin/bash
# Script de monitoring en temps réel des OSError et performances

LOG_FILE="${LOG_FILE:-/var/log/ckan/ckan.log}"
CHECK_INTERVAL="${CHECK_INTERVAL:-5}"  # secondes

echo "Monitoring en temps réel CKAN"
echo "   Log file: ${LOG_FILE}"
echo "   Intervalle: ${CHECK_INTERVAL}s"
echo "   Appuyez sur Ctrl+C pour arrêter"
echo ""

# Compteurs
oserror_count=0
error_count=0
last_line_count=0

# Vérifier si on est dans Docker
if [ -f /.dockerenv ] || [ -n "${DOCKER_CONTAINER}" ]; then
    echo "Mode Docker détecté"
    echo "   Utilisez: docker compose logs -f ckan | grep -i 'oserror\|write error'"
    echo ""
fi

# Fonction pour compter les erreurs dans les dernières lignes
count_recent_errors() {
    local lines_to_check="${1:-100}"
    
    if [ -f "${LOG_FILE}" ]; then
        # Compter les OSError
        local oserrors=$(tail -n "${lines_to_check}" "${LOG_FILE}" | grep -ci "oserror\|write error" || echo "0")
        
        # Compter les autres erreurs
        local errors=$(tail -n "${lines_to_check}" "${LOG_FILE}" | grep -ciE "\b(ERROR|CRITICAL|Exception)\b" || echo "0")
        
        echo "${oserrors}|${errors}"
    else
        echo "0|0"
    fi
}

# Boucle de monitoring
while true; do
    # Obtenir les compteurs
    IFS='|' read -r oserrors errors <<< "$(count_recent_errors 100)"
    
    # Afficher seulement si changement
    if [ "${oserrors}" -gt 0 ] || [ "${errors}" -gt 0 ]; then
        timestamp=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[${timestamp}] OSError: ${oserrors} | Erreurs: ${errors}"
        
        # Si beaucoup d'OSError, alerter
        if [ "${oserrors}" -gt 10 ]; then
            echo "   ATTENTION: ${oserrors} OSError détectés dans les 100 dernières lignes!"
        fi
    fi
    
    sleep "${CHECK_INTERVAL}"
done
