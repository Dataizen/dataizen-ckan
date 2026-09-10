#!/bin/bash
set -e

echo "Configuration XLoader + OGC au démarrage..."

# Définir CKAN_INI si non défini
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Configuration du storage CKAN pour l'upload de fichiers (CRITIQUE pour les ressources uploadées)
echo "Configuration du storage CKAN..."
# IMPORTANT: storage_path doit pointer vers /var/lib/ckan (sans /default)
# car les fichiers sont stockés dans /var/lib/ckan/resources/... directement
ckan config-tool "$CKAN_INI" "ckan.storage_path=/var/lib/ckan"
ckan config-tool "$CKAN_INI" "ckan.uploads_enabled=true"

# Vérifier que la valeur a bien été appliquée (et corriger si nécessaire)
STORAGE_PATH_CONFIGURED=$(ckan config-tool "$CKAN_INI" --get ckan.storage_path 2>/dev/null || echo "")
if [ -z "$STORAGE_PATH_CONFIGURED" ]; then
    # Essayer de lire directement depuis le fichier
    STORAGE_PATH_CONFIGURED=$(grep "^ckan\.storage_path" "$CKAN_INI" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d ' ' || echo "")
fi

# Si storage_path contient /default, le corriger
if [[ "$STORAGE_PATH_CONFIGURED" == *"/default"* ]]; then
    echo " Correction: storage_path contient '/default', suppression..."
    ckan config-tool "$CKAN_INI" "ckan.storage_path=/var/lib/ckan"
    echo "storage_path corrigé: /var/lib/ckan"
fi

# Vérifier que la configuration a été appliquée
STORAGE_PATH=$(ckan config-tool "$CKAN_INI" --get ckan.storage_path 2>/dev/null || echo "")
if [ -z "$STORAGE_PATH" ]; then
    # Essayer de lire directement depuis le fichier
    STORAGE_PATH=$(grep "^ckan\.storage_path" "$CKAN_INI" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d ' ' || echo "")
fi

if [ -n "$STORAGE_PATH" ]; then
    echo "Storage CKAN configuré: $STORAGE_PATH"
    
    # CRITIQUE: S'assurer que les répertoires nécessaires existent
    if [ ! -d "$STORAGE_PATH/storage/resources" ]; then
        echo "Création du répertoire storage/resources: $STORAGE_PATH/storage/resources"
        mkdir -p "$STORAGE_PATH/storage/resources"
        chown -R ckan:ckan-sys "$STORAGE_PATH/storage" 2>/dev/null || echo " Impossible de changer le propriétaire (peut nécessiter sudo)"
        chmod -R 755 "$STORAGE_PATH/storage" 2>/dev/null || true
        echo "Répertoire créé: $STORAGE_PATH/storage/resources"
    else
        echo "Répertoire existe déjà: $STORAGE_PATH/storage/resources"
    fi
    
    if [ ! -d "$STORAGE_PATH/storage/uploads" ]; then
        echo "Création du répertoire storage/uploads: $STORAGE_PATH/storage/uploads"
        mkdir -p "$STORAGE_PATH/storage/uploads"
        chown -R ckan:ckan-sys "$STORAGE_PATH/storage" 2>/dev/null || echo " Impossible de changer le propriétaire (peut nécessiter sudo)"
        chmod -R 755 "$STORAGE_PATH/storage" 2>/dev/null || true
        echo "Répertoire créé: $STORAGE_PATH/storage/uploads"
    fi
else
    echo " ATTENTION: storage_path n'a pas été configuré correctement"
    echo " Les uploads de fichiers ne fonctionneront pas sans cette configuration"
fi

# XLoader
ckan config-tool "$CKAN_INI" "ckanext.xloader.formats=csv tsv application/csv application/vnd.ms-excel application/vnd.openxmlformats-officedocument.spreadsheetml.sheet geojson application/geo+json json application/json"
ckan config-tool "$CKAN_INI" "ckanext.xloader.api_token=${XLOADER_API_TOKEN:-${CKAN_API_KEY}}"
# XLoader doit utiliser l'URL interne pour télécharger les fichiers depuis le conteneur
# Utiliser localhost:5000 (port interne) pour que le worker puisse télécharger les fichiers
# Ne pas utiliser l'URL externe (localhost:8080/8083) car elle n'est pas accessible depuis le conteneur
ckan config-tool "$CKAN_INI" "ckanext.xloader.site_url=http://ckan:5000"
ckan config-tool "$CKAN_INI" "ckanext.xloader.auto_index_dates=True"
ckan config-tool "$CKAN_INI" "ckanext.xloader.auto_index_threshold=3"
ckan config-tool "$CKAN_INI" "ckanext.xloader.auto_unique_index=True"
ckan config-tool "$CKAN_INI" "ckanext.xloader.calculate_record_count=True"
ckan config-tool "$CKAN_INI" "ckanext.xloader.chunk_size=16384"
ckan config-tool "$CKAN_INI" "ckanext.xloader.max_content_length=10737418240"
ckan config-tool "$CKAN_INI" "ckanext.xloader.ssl_verify=False"
ckan config-tool "$CKAN_INI" "ckanext.xloader.jobs_db.uri=postgresql://datapusher:${DATAPUSHER_PWD:-datapusher}@db/datapusher_jobs"
ckan config-tool "$CKAN_INI" "ckanext.xloader.debug=True"
# Configuration du logging pour xloader - capturer toutes les erreurs
ckan config-tool "$CKAN_INI" "ckanext.xloader.log_level=DEBUG"
ckan config-tool "$CKAN_INI" "ckan.datastore.write_url=postgresql://datapusher:${DATAPUSHER_PWD:-datapusher}@db/datastore"
ckan config-tool "$CKAN_INI" "ckan.datastore.read_url=postgresql://datapusher:${DATAPUSHER_PWD:-datapusher}@db/datastore"

# Configuration des queues RQ pour éviter les warnings
# Par défaut, CKAN utilise la queue "default" si ces options ne sont pas définies
ckan config-tool "$CKAN_INI" "ckan.jobs.queue_name=default"
ckan config-tool "$CKAN_INI" "ckan.jobs.queues=default"

# OGC
ckan config-tool "$CKAN_INI" "ckanext.ogc.site_url=${CKAN_SITE_URL:-http://ckan:5000}"
ckan config-tool "$CKAN_INI" "ckanext.ogc.pygeoapi_url=${PYGEOAPI_URL:-http://localhost:5001}"
# IMPORTANT: pygeoapi (conteneur séparé) LIT /srv/app/pygeoapi-conf/local.config.yml
# (volume partagé). L'auto-sync du plugin ogc doit écrire au MÊME endroit, sinon les
# collections ne parviennent jamais à pygeoapi (constaté le 11/08 : 1 seule collection).
ckan config-tool "$CKAN_INI" "ckanext.ogc.pygeoapi_config_path=/srv/app/pygeoapi-conf/local.config.yml"
ckan config-tool "$CKAN_INI" "ckanext.ogc.ckan_api_key=${CKAN_API_KEY:-${XLOADER_API_TOKEN}}"
ckan config-tool "$CKAN_INI" "ckanext.ogc.auto_sync=true"
ckan config-tool "$CKAN_INI" "ckanext.ogc.collections_base_path=/srv/app/pygeoapi-conf"

# Mise en avant de l'organisation des référentiels sur la home du catalogue
ckan config-tool "$CKAN_INI" "ckan.featured_orgs=referentiels"

echo "XLoader + OGC configurés"

