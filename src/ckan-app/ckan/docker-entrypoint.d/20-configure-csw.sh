#!/bin/bash
set -euo pipefail

echo "Configuration du service CSW (Catalogue Service for the Web)..."

# Créer le répertoire de configuration pycsw (avec sudo si nécessaire)
if ! mkdir -p /srv/app/pycsw 2>/dev/null; then
    sudo mkdir -p /srv/app/pycsw || true
fi
if [ -d /srv/app/pycsw ]; then
    sudo chown -R ckan:ckan-sys /srv/app/pycsw 2>/dev/null || chown -R ckan:ckan-sys /srv/app/pycsw 2>/dev/null || true
fi

# Configuration pycsw
PYCSW_CONFIG="/srv/app/pycsw/default.cfg"

# Vérifier si la configuration existe déjà
if [ ! -f "$PYCSW_CONFIG" ]; then
    echo "Création de la configuration pycsw..."
    
    # Créer la configuration pycsw de base
    cat > "$PYCSW_CONFIG" <<EOF
[server]
home=/srv/app/pycsw
url=${CKAN_SITE_URL:-http://localhost:8080}/csw
mimetype=application/xml; charset=UTF-8
encoding=utf-8
maxrecords=10
loglevel=INFO
logfile=/srv/app/pycsw/pycsw.log
federatedcatalogues=
pretty_print=true

[manager]
transactions=true
allowed_ips=127.0.0.1

[metadata:main]
identification_title=Dataizen Catalogue Service
identification_abstract=Catalogue Service for the Web (CSW) for Dataizen CKAN instance
identification_keywords=CSW,ISO 19115,ISO 19139,Dataizen,CKAN
identification_keywords_type=theme
identification_fees=NONE
identification_accessconstraints=NONE
provider_name=Dataizen
provider_url=${CKAN_SITE_URL:-http://localhost:8080}
contact_name=Dataizen
contact_position=Administrator
contact_address=
contact_city=
contact_stateorprovince=
contact_postalcode=
contact_country=France
contact_phone=
contact_fax=
contact_email=
contact_url=${CKAN_SITE_URL:-http://localhost:8080}
contact_hours=
contact_instructions=
role=pointOfContact

[repository]
database=postgresql://${POSTGRES_USER:-ckan}:${POSTGRES_PASSWORD:-ckan}@${POSTGRES_HOST:-db}:5432/${POSTGRES_DB:-ckan}
table=records
EOF

    chown ckan:ckan-sys "$PYCSW_CONFIG"
    echo "Configuration pycsw créée: $PYCSW_CONFIG"
else
    echo "Configuration pycsw existe déjà: $PYCSW_CONFIG"
fi

# Initialiser la base de données pycsw si nécessaire
echo "Initialisation de la base de données pycsw..."
export PGUSER="${POSTGRES_USER:-ckan}"
export PGPASSWORD="${POSTGRES_PASSWORD:-ckan}"
export PGHOST="${POSTGRES_HOST:-db}"
export PGDATABASE="${POSTGRES_DB:-ckan}"

# Vérifier si la table records existe
TABLE_EXISTS=$(psql -t -c "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'records');" 2>/dev/null | tr -d ' \n')

if [ "$TABLE_EXISTS" != "t" ]; then
    echo "Création de la table records pour pycsw..."
    # Utiliser pycsw-admin pour créer la base de données
    if command -v pycsw-admin.py &> /dev/null; then
        pycsw-admin.py -c setup_db -f "$PYCSW_CONFIG" || echo "Erreur lors de la création de la table records (peut déjà exister)"
    else
        # Alternative: utiliser Python directement
        python -c "
from pycsw.core import config, admin
import os
pycsw_config = config.StaticContext()
pycsw_config.load('$PYCSW_CONFIG')
database = pycsw_config.get('repository', 'database')
table_name = pycsw_config.get('repository', 'table', 'records')
admin.setup_db(database, table_name, '', create_plpythonu_functions=False)
print('Table records créée')
" || echo "Erreur lors de la création de la table records"
    fi
else
    echo "Table records existe déjà"
fi

# Configurer l'URL CSW dans ckan.ini
if ! sudo grep -q '^ckanext.spatial.csw.url' /srv/app/ckan.ini; then
    CSW_URL="${CKAN_SITE_URL:-http://localhost:8080}/csw"
    echo "Configuration de l'URL CSW dans ckan.ini: $CSW_URL"
    echo "ckanext.spatial.csw.url = $CSW_URL" | sudo tee -a /srv/app/ckan.ini
else
    echo "URL CSW déjà configurée dans ckan.ini"
fi

echo "Configuration CSW terminée"
echo "Le service CSW sera accessible à: ${CKAN_SITE_URL:-http://localhost:8080}/csw"
echo "Pour synchroniser les données CKAN avec pycsw, utilisez: ckan-pycsw load -p $PYCSW_CONFIG -u ${CKAN_SITE_URL:-http://localhost:8080}"

