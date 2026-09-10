#!/usr/bin/env python3
"""
Script de diagnostic et génération de mapfile pour un dataset spécifique
Usage: python3 diagnose-dataset-mapfile.py randonnees_de_la_ccmt
"""

import os
import sys
import requests
import json
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration depuis les variables d'environnement
CKAN_URL = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
CKAN_API_KEY = os.getenv('CKAN_API_KEY', '')

def get_dataset_info(dataset_name: str) -> Optional[Dict[str, Any]]:
    """Récupère les informations du dataset"""
    try:
        url = f"{CKAN_URL}/api/3/action/package_show"
        params = {'id': dataset_name}
        headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
        
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if result.get('success'):
            return result.get('result')
        else:
            logger.error(f"Erreur API: {result.get('error')}")
            return None
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du dataset: {e}")
        return None

def check_mapfile_exists(dataset_name: str) -> bool:
    """Vérifie si le mapfile existe"""
    mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
    exists = os.path.exists(mapfile_path)
    if exists:
        size = os.path.getsize(mapfile_path)
        logger.info(f"Mapfile existe: {mapfile_path} ({size} octets)")
        # Lire les premières lignes pour vérifier le contenu
        try:
            with open(mapfile_path, 'r') as f:
                first_lines = ''.join(f.readlines()[:10])
                logger.info(f"Contenu (premières lignes):\n{first_lines}")
        except Exception as e:
            logger.warning(f"Impossible de lire le mapfile: {e}")
    else:
        logger.warning(f"Mapfile n'existe pas: {mapfile_path}")
    return exists

def check_datastore_table(table_name: str) -> Dict[str, Any]:
    """Vérifie la table datastore"""
    try:
        import psycopg2
        
        conn = psycopg2.connect(
            host=os.getenv('POSTGRES_HOST', 'db'),
            port=int(os.getenv('POSTGRES_PORT', '5432')),
            database=os.getenv('POSTGRES_DB', 'ckan'),
            user=os.getenv('POSTGRES_USER', 'ckan'),
            password=os.getenv('POSTGRES_PASSWORD', 'ckan')
        )
        cur = conn.cursor()
        
        # Vérifier si la table existe
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = %s
            );
        """, (table_name,))
        
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            cur.close()
            conn.close()
            return {'exists': False, 'error': 'Table does not exist'}
        
        # Vérifier la colonne geometry
        cur.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = %s
            AND column_name = 'geometry';
        """, (table_name,))
        
        geom_col = cur.fetchone()
        
        # Vérifier le SRID
        srid = None
        if geom_col:
            cur.execute(f"""
                SELECT ST_SRID(geometry) 
                FROM "{table_name}" 
                WHERE geometry IS NOT NULL 
                LIMIT 1;
            """)
            result = cur.fetchone()
            if result:
                srid = result[0]
        
        # Compter les lignes avec geometry
        cur.execute(f"""
            SELECT COUNT(*) 
            FROM "{table_name}" 
            WHERE geometry IS NOT NULL;
        """)
        geom_count = cur.fetchone()[0]
        
        # Compter le total de lignes
        cur.execute(f'SELECT COUNT(*) FROM "{table_name}";')
        total_count = cur.fetchone()[0]
        
        cur.close()
        conn.close()
        
        return {
            'exists': True,
            'has_geometry_column': geom_col is not None,
            'geometry_type': geom_col[1] if geom_col else None,
            'srid': srid,
            'geometry_count': geom_count,
            'total_count': total_count
        }
    except ImportError:
        return {'exists': False, 'error': 'psycopg2 not available'}
    except Exception as e:
        return {'exists': False, 'error': str(e)}

def check_resource_datastore(resource_id: str) -> Dict[str, Any]:
    """Vérifie les colonnes d'une ressource datastore"""
    try:
        url = f"{CKAN_URL}/api/3/action/datastore_search"
        params = {'resource_id': resource_id, 'limit': 0}
        headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
        
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if result.get('success'):
            fields = result.get('result', {}).get('fields', [])
            field_names = [f.get('id', '') for f in fields]
            
            # Vérifier les colonnes géospatiales
            geo_fields = [f for f in fields if any(keyword in f.get('id', '').lower() 
                          for keyword in ['geometry', 'geom', 'geo', 'st_asgeojson', 'geo_point'])]
            
            return {
                'has_datastore': True,
                'fields': field_names,
                'geo_fields': [f.get('id') for f in geo_fields],
                'field_details': fields
            }
        else:
            return {'has_datastore': False, 'error': result.get('error')}
    except Exception as e:
        return {'has_datastore': False, 'error': str(e)}

def generate_mapfile_for_dataset(dataset_name: str) -> bool:
    """Génère le mapfile pour un dataset"""
    try:
        script_path = '/srv/app/src/ckanext-ogc/ckanext/ogc/scripts/generate-mapfile.py'
        if not os.path.exists(script_path):
            script_path = '/srv/app/ckanext-ogc/scripts/generate-mapfile.py'
        
        if not os.path.exists(script_path):
            logger.error(f"Script generate-mapfile.py non trouvé")
            return False
        
        import subprocess
        
        cmd = [
            'python3', script_path,
            '--dataset', dataset_name,
            '--ckan-url', CKAN_URL,
            '--ckan-api-key', CKAN_API_KEY or '',
            '--mapfiles-dir', '/mapserver/mapfiles',
            '--postgis-host', os.getenv('POSTGRES_HOST', 'db'),
            '--postgis-port', os.getenv('POSTGRES_PORT', '5432'),
            '--postgis-db', os.getenv('POSTGRES_DB', 'ckan'),
            '--postgis-user', os.getenv('POSTGRES_USER', 'ckan'),
            '--postgis-password', os.getenv('POSTGRES_PASSWORD', 'ckan'),
            '--auto-create-geometry'
        ]
        
        logger.info(f"Génération du mapfile...")
        logger.info(f"   Commande: {' '.join(cmd[:5])} ...")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            logger.info("Génération réussie")
            if result.stdout:
                logger.info(f"Sortie:\n{result.stdout[-1000:]}")
            return True
        else:
            logger.error(f"Génération échouée (code: {result.returncode})")
            if result.stderr:
                logger.error(f"Erreur:\n{result.stderr[-2000:]}")
            if result.stdout:
                logger.info(f"Sortie:\n{result.stdout[-2000:]}")
            return False
    except Exception as e:
        logger.error(f"Erreur lors de la génération: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    if len(sys.argv) < 2:
        logger.error("Usage: python3 diagnose-dataset-mapfile.py <dataset_name> [--generate]")
        sys.exit(1)
    
    dataset_name = sys.argv[1]
    generate = '--generate' in sys.argv
    
    logger.info("=" * 80)
    logger.info(f"DIAGNOSTIC MAPFILE POUR DATASET: {dataset_name}")
    logger.info("=" * 80)
    
    # 1. Vérifier si le mapfile existe
    logger.info("\nÉtape 1: Vérification du mapfile")
    mapfile_exists = check_mapfile_exists(dataset_name)
    
    if mapfile_exists and not generate:
        logger.info("\nMapfile existe déjà!")
        logger.info("Pour forcer la régénération, utilisez: --generate")
        sys.exit(0)
    
    # 2. Récupérer les informations du dataset
    logger.info("\nÉtape 2: Récupération des informations du dataset")
    dataset = get_dataset_info(dataset_name)
    
    if not dataset:
        logger.error("Dataset non trouvé")
        sys.exit(1)
    
    logger.info(f"Dataset trouvé: {dataset.get('title', dataset_name)}")
    logger.info(f"   ID: {dataset.get('id')}")
    
    # 3. Vérifier les métadonnées OGC
    logger.info("\nÉtape 3: Vérification des métadonnées OGC")
    extras = dataset.get('extras', [])
    ogc_metadata = {}
    for extra in extras:
        if isinstance(extra, dict):
            key = extra.get('key', '')
            value = extra.get('value', '')
            if key.startswith('ogc:'):
                ogc_metadata[key] = value
                logger.info(f"   {key}: {value}")
    
    if not ogc_metadata:
        logger.warning("Aucune métadonnée OGC trouvée")
    else:
        logger.info("Métadonnées OGC présentes")
    
    # 4. Vérifier les ressources
    logger.info("\nÉtape 4: Vérification des ressources")
    resources = dataset.get('resources', [])
    logger.info(f"   Nombre de ressources: {len(resources)}")
    
    datastore_resources = []
    for resource in resources:
        resource_id = resource.get('id')
        resource_name = resource.get('name', 'Sans nom')
        datastore_active = resource.get('datastore_active', False)
        format_ = resource.get('format', '').upper()
        
        logger.info(f"\n   Ressource: {resource_name}")
        logger.info(f"      ID: {resource_id}")
        logger.info(f"      Format: {format_}")
        logger.info(f"      Datastore actif: {datastore_active}")
        
        if datastore_active:
            datastore_resources.append(resource)
            # Vérifier les colonnes
            logger.info(f"      Vérification des colonnes datastore...")
            datastore_info = check_resource_datastore(resource_id)
            if datastore_info.get('has_datastore'):
                logger.info(f"      Colonnes trouvées: {len(datastore_info.get('fields', []))}")
                logger.info(f"      Colonnes géospatiales: {datastore_info.get('geo_fields', [])}")
                
                # Afficher les détails des colonnes géospatiales
                for field in datastore_info.get('field_details', []):
                    field_name = field.get('id', '')
                    field_type = field.get('type', '')
                    if any(keyword in field_name.lower() for keyword in ['geometry', 'geom', 'geo', 'st_asgeojson', 'geo_point']):
                        logger.info(f"         - {field_name} ({field_type})")
            else:
                logger.warning(f"      Erreur datastore: {datastore_info.get('error')}")
    
    if not datastore_resources:
        logger.warning("Aucune ressource datastore active trouvée")
    
    # 5. Vérifier la table datastore si métadonnées OGC présentes
    if ogc_metadata.get('ogc:table'):
        logger.info("\nÉtape 5: Vérification de la table datastore")
        table_name = ogc_metadata.get('ogc:table')
        logger.info(f"   Table: {table_name}")
        
        table_info = check_datastore_table(table_name)
        if table_info.get('exists'):
            logger.info(f"   Table existe")
            logger.info(f"   Colonne geometry: {table_info.get('has_geometry_column')}")
            logger.info(f"   Type geometry: {table_info.get('geometry_type')}")
            logger.info(f"   SRID: {table_info.get('srid')}")
            logger.info(f"   Lignes avec geometry: {table_info.get('geometry_count')}/{table_info.get('total_count')}")
            
            if not table_info.get('has_geometry_column'):
                logger.error("La table n'a pas de colonne geometry!")
                logger.info("La colonne geometry doit être créée automatiquement")
        else:
            logger.error(f"Table non trouvée: {table_info.get('error')}")
    
    # 6. Générer le mapfile si demandé ou s'il n'existe pas
    if generate or not mapfile_exists:
        logger.info("\nÉtape 6: Génération du mapfile")
        if generate_mapfile_for_dataset(dataset_name):
            # Vérifier à nouveau après génération
            if check_mapfile_exists(dataset_name):
                logger.info("\nDiagnostic terminé avec succès!")
                logger.info(f"Le mapfile est disponible à: /mapserver/mapfiles/{dataset_name}.map")
            else:
                logger.warning("\nLe mapfile n'a pas été créé malgré la génération réussie")
                logger.warning("Vérifiez les permissions d'écriture dans /mapserver/mapfiles/")
                sys.exit(1)
        else:
            logger.error("\nÉchec de la génération du mapfile")
            logger.info("Vérifiez les logs ci-dessus pour plus de détails")
            sys.exit(1)
    else:
        logger.info("\nDiagnostic terminé - mapfile existe déjà")

if __name__ == '__main__':
    main()
