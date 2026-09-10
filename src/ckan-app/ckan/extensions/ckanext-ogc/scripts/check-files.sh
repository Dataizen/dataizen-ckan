#!/bin/bash

# Script de vérification des fichiers CKAN
# Vérifie la présence et l'intégrité des fichiers critiques

echo "Vérification des fichiers CKAN..."

# Vérifier les fichiers de configuration
if [ -f "/srv/app/ckan.ini" ]; then
    echo "ckan.ini trouvé"
else
    echo "ckan.ini manquant"
    exit 1
fi

# Vérifier les extensions
EXTENSIONS_DIR="/srv/app/src/ckan/ckan/plugins"
if [ -d "$EXTENSIONS_DIR" ]; then
    echo "Répertoire des extensions trouvé"
else
    echo "Répertoire des extensions manquant"
    exit 1
fi

# Vérifier pygeoapi
if [ -f "/srv/app/pygeoapi/local.config.yml" ]; then
    echo "Configuration pygeoapi trouvée"
else
    echo "Configuration pygeoapi manquante"
fi

echo "Vérification des fichiers terminée"

