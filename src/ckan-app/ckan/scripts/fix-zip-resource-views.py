#!/usr/bin/env python3
"""
Script pour corriger les vues cartographiques qui essaient de parser des fichiers ZIP
Remplace geojson_view par geo_view avec WMS pour les ressources ZIP
"""

import sys
import os

# Ajouter le chemin de CKAN au PYTHONPATH
sys.path.insert(0, '/srv/app')

from ckan.config.environment import load_environment
from ckan import model
from ckan.common import config
from ckan.plugins import toolkit
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def fix_zip_resource_views():
    """Corrige toutes les vues geojson_view qui pointent vers des fichiers ZIP"""
    
    load_environment(config)
    context = {'ignore_auth': True}
    
    # Trouver toutes les ressources ZIP
    try:
        # Rechercher tous les datasets
        datasets = toolkit.get_action('package_list')(context, {})
        log.debug(f"Trouvé {len(datasets)} datasets")
        
        fixed_count = 0
        error_count = 0
        
        for dataset_name in datasets:
            try:
                dataset = toolkit.get_action('package_show')(context, {'id': dataset_name})
                resources = dataset.get('resources', [])
                
                org_name = None
                if dataset.get('organization'):
                    org_name = dataset['organization'].get('name')
                
                # Vérifier si le dataset a un mapfile
                mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
                has_mapfile = os.path.exists(mapfile_path)
                
                if not has_mapfile or not org_name:
                    continue
                
                for resource in resources:
                    resource_id = resource.get('id')
                    format_ = resource.get('format', '').upper()
                    url = resource.get('url', '')
                    
                    # Vérifier si c'est un ZIP
                    is_zip = format_ == 'ZIP' or url.lower().endswith('.zip')
                    
                    if not is_zip:
                        continue
                    
                    # Vérifier les vues existantes
                    try:
                        views = toolkit.get_action('resource_view_list')(
                            context,
                            {'id': resource_id}
                        )
                        
                        for view in views:
                            view_type = view.get('view_type')
                            
                            # Supprimer les vues geojson_view qui causent des erreurs
                            if view_type == 'geojson_view':
                                try:
                                    toolkit.get_action('resource_view_delete')(
                                        context,
                                        {'id': view['id']}
                                    )
                                    log.info(f"Vue geojson_view supprimée pour {resource_id} (dataset: {dataset_name})")
                                    
                                    # Créer une vue geo_view avec WMS
                                    ckan_site_url = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
                                    wms_url = f"{ckan_site_url}/wms?map=/mapserver/mapfiles/{dataset_name}.map&LAYERS={dataset_name}&SERVICE=WMS&VERSION=1.3.0"
                                    
                                    view_data = {
                                        'resource_id': resource_id,
                                        'title': 'Carte',
                                        'view_type': 'geo_view',
                                        'description': 'Vue cartographique générée automatiquement depuis le Shapefile',
                                        'wms_url': wms_url,
                                        'wms_layer': dataset_name
                                    }
                                    
                                    toolkit.get_action('resource_view_create')(
                                        context,
                                        view_data
                                    )
                                    log.info(f"Vue geo_view créée pour {resource_id} (dataset: {dataset_name})")
                                    fixed_count += 1
                                    
                                except Exception as e:
                                    log.error(f"Erreur lors de la correction de la vue {view['id']}: {e}")
                                    error_count += 1
                            
                            # Vérifier si une vue geo_view existe déjà
                            elif view_type == 'geo_view':
                                log.debug(f"Vue geo_view existe déjà pour {resource_id}")
                                
                    except Exception as e:
                        log.warning(f"Erreur lors de la vérification des vues pour {resource_id}: {e}")
                        error_count += 1
                        
            except Exception as e:
                log.error(f"Erreur lors du traitement du dataset {dataset_name}: {e}")
                error_count += 1
        
        log.info(f"\nCorrection terminée:")
        log.info(f"   - Vues corrigées: {fixed_count}")
        log.info(f"   - Erreurs: {error_count}")
        
    except Exception as e:
        log.error(f"Erreur lors de la recherche des datasets: {e}")
        return False
    
    return True


if __name__ == '__main__':
    success = fix_zip_resource_views()
    sys.exit(0 if success else 1)
