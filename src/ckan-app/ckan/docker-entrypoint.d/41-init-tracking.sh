#!/bin/bash
# Script pour initialiser les tables du plugin tracking (comptage des vues).
# Permet d'afficher le nombre de consultations par ressource sur la page dataset.

set -euo pipefail

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"
echo "Initialisation du plugin tracking..."

# Vérifier si le plugin tracking est activé
if ! ckan -c "$CKAN_INI" config-tool 2>/dev/null | grep -q "tracking"; then
    echo "Plugin tracking non activé, initialisation ignorée"
    exit 0
fi

# Exécuter les migrations tracking
echo "Exécution des migrations tracking..."
ckan -c "$CKAN_INI" db upgrade -p tracking 2>&1 || true

echo "Initialisation tracking terminée"
echo "Pour mettre à jour les statistiques régulièrement, ajoutez un cron:"
echo "   @hourly ckan -c $CKAN_INI tracking update && ckan -c $CKAN_INI search-index rebuild -r"
