#!/bin/bash
# Script pour configurer SECRET_KEY AVANT que supervisor démarre les consumers
# Ce script doit s'exécuter en premier (00-)

set -euo pipefail

echo "Configuration de SECRET_KEY avant démarrage des consumers..."

# Charger les variables d'environnement du .env si le fichier existe
if [ -f "/srv/app/.env" ]; then
    echo "Chargement des variables depuis .env..."
    export $(grep -v '^#' /srv/app/.env | xargs)
fi

# Vérifier si SECRET_KEY est déjà configuré dans ckan.ini
if ! sudo grep -qE "^SECRET_KEY\s*=" /srv/app/ckan.ini || sudo grep -qE "^SECRET_KEY\s*=\s*$" /srv/app/ckan.ini; then
    echo "Configuration de SECRET_KEY dans ckan.ini..."
    
    # Générer SECRET_KEY si non défini dans l'environnement
    if [ -z "${SECRET_KEY:-}" ]; then
        SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')
        echo "SECRET_KEY généré automatiquement"
    else
        echo "Utilisation de SECRET_KEY depuis l'environnement"
    fi
    
    # Configurer SECRET_KEY dans ckan.ini
    sudo ckan config-tool /srv/app/ckan.ini "SECRET_KEY=${SECRET_KEY}"
    sudo ckan config-tool /srv/app/ckan.ini "WTF_CSRF_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe())')"
    
    # Secrets JWT : depuis l'environnement si fourni (CKAN_JWT_SECRET), sinon générés.
    # Un secret stable est indispensable pour que les api_tokens survivent à la
    # recréation du conteneur (le ckan.ini n'est pas persisté).
    if [ -n "${CKAN_JWT_SECRET:-}" ]; then
        JWT_SECRET="string:${CKAN_JWT_SECRET}"
        echo "Utilisation de CKAN_JWT_SECRET depuis l'environnement"
    else
        JWT_SECRET=$(python3 -c 'import secrets; print("string:" + secrets.token_urlsafe())')
        echo "ATTENTION: JWT secret généré aléatoirement, les api_tokens ne survivront pas à une recréation (définir CKAN_JWT_SECRET)"
    fi
    sudo ckan config-tool /srv/app/ckan.ini "api_token.jwt.encode.secret=${JWT_SECRET}"
    sudo ckan config-tool /srv/app/ckan.ini "api_token.jwt.decode.secret=${JWT_SECRET}"
    
    echo "SECRET_KEY configuré dans ckan.ini"
else
    echo "SECRET_KEY déjà configuré dans ckan.ini"
fi

echo "Configuration SECRET_KEY terminée"


