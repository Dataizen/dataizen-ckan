#!/bin/bash
# Script pour surveiller les logs du worker xloader

set -euo pipefail

RESOURCE_ID="${1:-}"

echo "=== Surveillance des logs xloader ==="
echo ""

# Vérifier le statut du worker
echo "=== Statut du worker ==="
supervisorctl status xloader
echo ""

# Afficher les dernières lignes
echo "=== Dernières 30 lignes ==="
supervisorctl tail -30 xloader
echo ""

# Si une ressource est spécifiée, filtrer pour cette ressource
if [ -n "$RESOURCE_ID" ]; then
    echo "=== Logs pour ressource $RESOURCE_ID ==="
    supervisorctl tail -200 xloader | grep "$RESOURCE_ID" | tail -20
    echo ""
fi

# Afficher les erreurs récentes
echo "=== Erreurs récentes (dernières 50 lignes) ==="
supervisorctl tail -200 xloader | grep -E "ERROR|Exception|Traceback" | tail -20 || echo "Aucune erreur récente"
echo ""

# Afficher les logs du patch (initialisation CKAN)
echo "=== Logs du patch CKAN (initialisation) ==="
supervisorctl tail -200 xloader | grep -E "\[xloader\].*CKAN|Session.bind|make_app|CKAN initialized" | tail -20 || echo "Aucun log de patch récent"
echo ""

# Mode interactif
echo "=== Mode surveillance (Ctrl+C pour arrêter) ==="
echo "Filtres actifs: [xloader], ERROR, Exception, CKAN, Session.bind"
echo ""

if [ -n "$RESOURCE_ID" ]; then
    supervisorctl tail -f xloader | grep -E "\[xloader\]|$RESOURCE_ID|ERROR|Exception|CKAN|Session.bind|make_app"
else
    supervisorctl tail -f xloader | grep -E "\[xloader\]|ERROR|Exception|CKAN|Session.bind|make_app"
fi



