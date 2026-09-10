#!/bin/bash

# Configure CKAN plugins for LOCAL DEVELOPMENT to match production capabilities
echo "Configuration des plugins CKAN pour le développement local..."

# Définir CKAN_INI si non défini
CKAN_INI="${CKAN_INI:-/srv/app/ckan.ini}"

# Liste des plugins pour le développement local (sans envvars car pas de configmap)
# Extensions installées et fonctionnelles: showcase, composite
# scheming_datasets désactivé car nécessite une configuration de schéma YAML (provoque des erreurs avec dcat)
# structured_data désactivé car nécessite une configuration scheming spécifique
# Extensions non disponibles ou nécessitant configuration spéciale: restricted (nécessite version neogeo-technologies)
# TEMPORAIREMENT DÉSACTIVÉ: keycloak (pour réactiver, décommenter dans la liste ci-dessous)
LOCAL_DEV_PLUGINS="datastore image_view text_view harvest ckan_harvester csw_harvester dcat_patch dcat dcat_json_harvester dcat_rdf_harvester xloader harvest_xloader dataload_router dolfin rag datatables_view geo_view geojson_view wmts_view shp_view spatial_metadata spatial_query spatial_harvest_metadata_api activity tracking ogc showcase composite custom_licenses admin_tools"
# IMPORTANT: ogc doit être chargé AVANT admin_tools pour que le template data_type.html soit utilisé
# Pour réactiver Keycloak, ajouter "keycloak" dans la liste ci-dessus

# Remplacer la ligne des plugins par la configuration de développement local
sudo sed -i "s/^ckan.plugins = .*/ckan.plugins = $LOCAL_DEV_PLUGINS/" /srv/app/ckan.ini

echo "Plugins CKAN configurés pour le développement local"
echo "Plugins actifs: $LOCAL_DEV_PLUGINS"
echo "Note: 'envvars' omis car utilisé uniquement en production avec configmap"

# Configuration des vues géospatiales automatiques
echo "Configuration des vues géospatiales..."

# Activer la détection automatique des colonnes géographiques pour CSV
sudo sed -i '/^ckanext\.geoview\.ol_viewer\.formats/d' /srv/app/ckan.ini
echo "ckanext.geoview.ol_viewer.formats = csv geojson wms wfs kml" | sudo tee -a /srv/app/ckan.ini

# Configuration basemaps.json pour geoview
sudo sed -i '/^ckanext\.geoview\.basemaps/d' /srv/app/ckan.ini
echo "ckanext.geoview.basemaps = /srv/app/config_files/common/basemaps.json" | sudo tee -a /srv/app/ckan.ini

# Configuration OGC forward params (comme en production)
sudo sed -i '/^ckanext\.geoview\.ol_viewer\.forward_ogc_request_params/d' /srv/app/ckan.ini
echo "ckanext.geoview.ol_viewer.forward_ogc_request_params = True" | sudo tee -a /srv/app/ckan.ini

# Configuration feature hover (comme en production)
sudo sed -i '/^ckanext\.geoview\.ol_viewer\.default_feature_hoveron/d' /srv/app/ckan.ini
echo "ckanext.geoview.ol_viewer.default_feature_hoveron = True" | sudo tee -a /srv/app/ckan.ini

# Configurer la taille max pour GeoJSON
sudo sed -i '/^ckanext\.geoview\.geojson\.max_file_size/d' /srv/app/ckan.ini
echo "ckanext.geoview.geojson.max_file_size = 100000000" | sudo tee -a /srv/app/ckan.ini

# Configurer le SRID par défaut
sudo sed -i '/^ckanext\.geoview\.shp_viewer\.srid/d' /srv/app/ckan.ini
echo "ckanext.geoview.shp_viewer.srid = 4326" | sudo tee -a /srv/app/ckan.ini

# Activer la détection automatique des coordonnées dans les CSV
sudo sed -i '/^ckanext\.spatial\.common_map\.type/d' /srv/app/ckan.ini
echo "ckanext.spatial.common_map.type = stamen" | sudo tee -a /srv/app/ckan.ini

# Activer la création automatique de vues géographiques
sudo sed -i '/^ckan\.views\.default_views/d' /srv/app/ckan.ini
echo "ckan.views.default_views = image_view text_view datatables_view geojson_view geo_view shp_view" | sudo tee -a /srv/app/ckan.ini

# Configuration pour shp_view (Shapefile viewer)
sudo sed -i '/^ckanext\.geoview\.shp_viewer\.max_file_size/d' /srv/app/ckan.ini
echo "ckanext.geoview.shp_viewer.max_file_size = 100000000" | sudo tee -a /srv/app/ckan.ini

# Note: shp_view dans ckanext-geoview peut afficher les Shapefiles directement,
# mais pour les fichiers ZIP, il faut soit les extraire ou utiliser pygeoapi.
# Le plugin OGC créera automatiquement une vue GeoJSON pour les Shapefiles ZIP.

echo "Vues géospatiales configurées pour création automatique des cartes"

# Configuration FTS française pour Datastore (comme en production)
echo "Configuration de la recherche full-text en français..."
sudo sed -i '/^ckan\.datastore\.default_fts_lang/d' /srv/app/ckan.ini
echo "ckan.datastore.default_fts_lang = french" | sudo tee -a /srv/app/ckan.ini

sudo sed -i '/^ckan\.datastore\.default_fts_index_method/d' /srv/app/ckan.ini
echo "ckan.datastore.default_fts_index_method = gist" | sudo tee -a /srv/app/ckan.ini

echo "Configuration FTS française appliquée"

# Supprimer structured_data car il nécessite une configuration scheming spécifique
echo "Suppression de structured_data (nécessite configuration scheming)..."
sudo sed -i 's/ structured_data//' /srv/app/ckan.ini
echo "structured_data supprimé"

# Configuration du storage CKAN pour l'upload de fichiers
echo "Configuration du storage CKAN..."
# Configurer ckan.storage_path comme en production (/var/lib/ckan)
# CKAN créera automatiquement les répertoires nécessaires au premier upload
# Utiliser la syntaxe avec espaces autour du = pour compatibilité
ckan config-tool "$CKAN_INI" "ckan.storage_path = /var/lib/ckan"
# IMPORTANT: Activer explicitement les uploads (requis pour afficher le bouton)
ckan config-tool "$CKAN_INI" "ckan.uploads_enabled = true"

# S'assurer que le répertoire parent existe et est accessible
# CKAN vérifie l'accessibilité du répertoire parent pour afficher le bouton d'upload
mkdir -p /var/lib/ckan/storage/resources
mkdir -p /var/lib/ckan/storage/uploads
chmod 755 /var/lib/ckan 2>/dev/null || true
chmod -R 755 /var/lib/ckan/storage 2>/dev/null || true

# Vérifier que la configuration a été appliquée (avec plusieurs tentatives de syntaxe)
STORAGE_PATH=$(ckan config-tool "$CKAN_INI" --get ckan.storage_path 2>/dev/null || echo "")
if [ -z "$STORAGE_PATH" ]; then
    # Essayer de lire directement depuis le fichier
    STORAGE_PATH=$(grep "^ckan\.storage_path" "$CKAN_INI" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d ' ' || echo "")
fi

if [ -n "$STORAGE_PATH" ]; then
    echo "Storage CKAN configuré: $STORAGE_PATH"
    echo "Répertoires créés: /var/lib/ckan/storage/resources et /var/lib/ckan/storage/uploads"
else
    echo " Vérification de la configuration storage_path échouée"
    echo " Tentative de correction manuelle..."
    # Essayer d'ajouter directement dans la section [app:main] si elle existe
    if grep -q "^\[app:main\]" "$CKAN_INI" 2>/dev/null; then
        # Supprimer l'ancienne ligne si elle existe
        sed -i '/^ckan\.storage_path/d' "$CKAN_INI" 2>/dev/null || true
        # Ajouter après [app:main]
        sed -i '/^\[app:main\]/a ckan.storage_path = /var/lib/ckan' "$CKAN_INI" 2>/dev/null || true
        echo "Configuration ajoutée manuellement dans [app:main]"
    fi
fi

# Configuration du logging pour filtrer les logs de rendu Flask
# NOTE: Le filtre DatastoreSearch404Filter dans le plugin OGC filtrera automatiquement
# les 404 de /api/action/datastore_search tout en conservant les autres logs INFO utiles
# (import, mapfiles, etc.). Pas besoin de changer le niveau du logger ici.
echo "Configuration du logging pour filtrer les logs de rendu..."
echo "Le filtre DatastoreSearch404Filter sera appliqué automatiquement par le plugin OGC"
echo "Les 404 de /api/action/datastore_search seront filtrés, les autres logs INFO conservés"

