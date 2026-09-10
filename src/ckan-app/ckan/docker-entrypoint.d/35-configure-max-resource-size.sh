#!/bin/bash
set -e

echo "Configuration de la taille maximale des ressources..."

# Définir CKAN_INI si non défini
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Configurer la taille maximale des ressources (en MB)
# Valeur par défaut: 2000 MB (2 GB) pour permettre les fichiers volumineux
# Vous pouvez modifier cette valeur selon vos besoins
MAX_RESOURCE_SIZE="${CKAN_MAX_RESOURCE_SIZE:-2000}"

echo "Configuration de ckan.max_resource_size à ${MAX_RESOURCE_SIZE} MB..."

# Configurer la taille maximale des ressources
ckan config-tool "$CKAN_INI" "ckan.max_resource_size=${MAX_RESOURCE_SIZE}" || echo "Impossible de configurer max_resource_size"

# Vérifier que la valeur a bien été appliquée
CONFIGURED_SIZE=$(ckan config-tool "$CKAN_INI" --get ckan.max_resource_size 2>/dev/null || echo "")
if [ -z "$CONFIGURED_SIZE" ]; then
    # Essayer de lire directement depuis le fichier
    CONFIGURED_SIZE=$(grep "^ckan\.max_resource_size" "$CKAN_INI" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d ' ' || echo "")
fi

if [ -n "$CONFIGURED_SIZE" ]; then
    echo "Taille maximale des ressources configurée: ${CONFIGURED_SIZE} MB"
else
    echo " Impossible de vérifier la configuration de max_resource_size"
fi

echo "Note: Assurez-vous que nginx (client_max_body_size) et CKAN (ckan.max_resource_size) sont tous deux configurés pour accepter des fichiers de cette taille"


