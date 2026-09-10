#!/bin/bash
set -euo pipefail

echo "Configuration du harvester pour continuer malgré les erreurs de validation..."

CKAN_INI="/srv/app/ckan.ini"

# Activer continue_on_validation_errors dans ckan.ini
if ! sudo grep -q '^ckanext.spatial.harvest.continue_on_validation_errors' "$CKAN_INI"; then
    echo "Activation de continue_on_validation_errors dans ckan.ini..."
    echo "ckanext.spatial.harvest.continue_on_validation_errors = true" | sudo tee -a "$CKAN_INI" > /dev/null
    echo "Option continue_on_validation_errors activée"
else
    # Mettre à jour la valeur si elle existe déjà
    sudo sed -i 's/^ckanext.spatial.harvest.continue_on_validation_errors.*/ckanext.spatial.harvest.continue_on_validation_errors = true/' "$CKAN_INI"
    echo "Option continue_on_validation_errors mise à jour"
fi

echo "Configuration du harvester terminée"
echo "Les erreurs de validation ne bloqueront plus l'import des datasets"


