#!/bin/bash
# Script pour démarrer pygeoapi sur le port 5001

export MPLCONFIGDIR=/tmp/matplotlib-cache
export FONTCONFIG_CACHE=/tmp/fontconfig-cache
cd /srv/app/pygeoapi

# Vérifier que openapi.yml existe
if [ ! -f openapi.yml ]; then
    echo " openapi.yml manquant, génération..."
    PYGEOAPI_CONFIG=local.config.yml pygeoapi openapi generate -o openapi.yml 2>&1 || echo " Échec génération openapi.yml"
fi

# Utiliser Python pour démarrer Flask sur le port 5001
PYGEOAPI_CONFIG=local.config.yml \
PYGEOAPI_OPENAPI=/srv/app/pygeoapi/openapi.yml \
python3 -c "
import os
os.environ['PYGEOAPI_CONFIG'] = '/srv/app/pygeoapi/local.config.yml'
os.environ['PYGEOAPI_OPENAPI'] = '/srv/app/pygeoapi/openapi.yml'
from pygeoapi.flask_app import APP
APP.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)
"




