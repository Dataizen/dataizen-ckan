#!/bin/bash
set -e

echo "Configuration des licences CKAN..."

LICENSES_FILE="/srv/app/config_files/common/licenses.json"

if [ ! -f "$LICENSES_FILE" ]; then
  echo " Fichier de licences non trouvé: ${LICENSES_FILE}"
  echo "CKAN utilisera les licences par défaut"
  exit 0
fi

# Exécuter le script Python pour charger et afficher les licences
export CKAN_LICENSES_FILE="${LICENSES_FILE}"
python3 /docker-entrypoint.d/load_licenses.py || echo " Erreur lors du chargement des licences"

# Configurer licenses_group_url pour que CKAN charge les licences depuis le fichier JSON
# CKAN supporte file:// pour charger depuis un fichier local
# IMPORTANT: LicenseRegister cherche 'licenses_group_url' sans préfixe ckan.
echo "Configuration de licenses_group_url pour charger depuis le fichier JSON..."
if ckan config-tool /srv/app/ckan.ini --exists licenses_group_url 2>/dev/null; then
  ckan config-tool /srv/app/ckan.ini "licenses_group_url = file://${LICENSES_FILE}"
  echo "licenses_group_url mis à jour"
else
  ckan config-tool /srv/app/ckan.ini "licenses_group_url = file://${LICENSES_FILE}"
  echo "licenses_group_url ajouté"
fi
# Supprimer aussi ckan.licenses_group_url s'il existe (format incorrect)
ckan config-tool /srv/app/ckan.ini --delete ckan.licenses_group_url 2>/dev/null || true

echo ""
echo "Configuration des licences terminée"
echo "Fichier de licences: ${LICENSES_FILE}"
echo "Vous pouvez éditer ce fichier pour modifier les licences disponibles"
echo "CKAN chargera automatiquement les licences depuis ce fichier via licenses_group_url"

