#!/bin/bash
# Active le plugin keycloak dans ckan.plugins quand la config OIDC est fournie
# (CKAN_OIDC_CLIENT_ID défini). Ajout Dataizen : le script 60 configure les
# clés ckanext.keycloak.* mais n'activait pas le plugin.
set -euo pipefail

CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

if [ -z "${CKAN_OIDC_CLIENT_ID:-}" ]; then
    echo "CKAN_OIDC_CLIENT_ID absent : plugin keycloak non activé"
    exit 0
fi

PLUGINS=$(grep -E "^ckan\.plugins\s*=" "$CKAN_INI" | head -1 | sed 's/^ckan\.plugins\s*=\s*//')
if echo "$PLUGINS" | grep -qw keycloak; then
    echo "plugin keycloak déjà actif"
else
    ckan config-tool "$CKAN_INI" "ckan.plugins = $PLUGINS keycloak"
    echo "plugin keycloak activé"
fi
