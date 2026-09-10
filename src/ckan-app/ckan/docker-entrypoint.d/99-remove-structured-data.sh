#!/bin/bash
# Script pour supprimer structured_data de la configuration CKAN
# car il nécessite une configuration scheming spécifique qui n'est pas disponible

echo "Suppression de structured_data de la configuration CKAN..."

# Supprimer structured_data de la liste des plugins avec sed
sudo sed -i 's/ structured_data//' /srv/app/ckan.ini

echo "structured_data supprimé de la configuration"

