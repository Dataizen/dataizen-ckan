#!/usr/bin/env python3
"""
Script de diagnostic et correction pour la génération de mapfiles

Ce script vérifie :
1. Si PostGIS est activé dans datastore
2. Si le dataset est détecté comme géospatial
3. Si la colonne geometry existe ou peut être créée
4. Génère le mapfile si nécessaire
"""

import os
import sys
import logging
import requests
import psycopg2
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration depuis les variables d'environnement
CKAN_URL = os.getenv('CKAN_URL', 'http://ckan:5000')
CKAN_API_KEY = os.getenv('CKAN_API_KEY', '')
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'db')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.getenv('POSTGRES_DB', 'datastore')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'ckan')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'ckan')


def check_postgis_enabled() -> bool:
    """Vérifie si PostGIS est activé dans la base datastore"""
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cur = conn.cursor()
        
        # Vérifier la version PostGIS
        cur.execute("SELECT PostGIS_version();")
        version = cur.fetchone()[0]
        logger.info(f"PostGIS est activé dans datastore (version: {version})")
        
        # Vérifier les extensions installées
        cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'postgis%';")
        extensions = cur.fetchall()
        logger.info(f"Extensions PostGIS installées: {[e[0] for e in extensions]}")
        
        cur.close()
        conn.close()
        return True
        
    except psycopg2.OperationalError as e:
        logger.error(f"Erreur de connexion à la base de données: {e}")
        return False
    except psycopg2.ProgrammingError as e:
        if 'function postgis_version() does not exist' in str(e):
            logger.error("PostGIS n'est PAS activé dans la base datastore")
            logger.info("Pour activer PostGIS, exécutez:")
            logger.info(f"   docker compose exec db psql -U postgres -d datastore -c 'CREATE EXTENSION IF NOT EXISTS postgis;'")
            return False
        else:
            logger.error(f"Erreur SQL: {e}")
            return False
    except Exception as e:
        logger.error(f"Erreur inattendue: {e}")
        return False


def enable_postgis() -> bool:
    """Active PostGIS dans la base datastore"""
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user='postgres',  # Utiliser postgres pour créer les extensions
            password=os.getenv('POSTGRES_PASSWORD', 'ckan')
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        extensions = [
            'postgis',
            'postgis_topology',
            'postgis_raster',
            'fuzzystrmatch',
            'postgis_tiger_geocoder'
        ]
        
        for ext in extensions:
            try:
                cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext};")
                logger.info(f"Extension {ext} activée")
            except Exception as e:
                logger.warning(f"Impossible d'activer {ext}: {e}")
        
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"Erreur lors de l'activation de PostGIS: {e}")
        return False


def get_dataset_info(dataset_name: str) -> Optional[Dict[str, Any]]:
    """Récupère les informations d'un dataset depuis CKAN"""
    try:
        url = f"{CKAN_URL}/api/action/package_show"
        params = {'id': dataset_name}
        headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        if not result.get('success'):
            logger.error(f"Erreur API CKAN: {result.get('error', {})}")
            return None
        
        return result.get('result')
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du dataset: {e}")
        return None


def check_resource_geometry(resource_id: str) -> Dict[str, Any]:
    """Vérifie les colonnes géométriques d'une ressource"""
    result = {
        'has_geometry': False,
        'has_geojson': False,
        'geojson_fields': [],
        'geometry_column': None
    }
    
    try:
        url = f"{CKAN_URL}/api/action/datastore_search"
        params = {'resource_id': resource_id, 'limit': 0}
        headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.warning(f"Impossible de récupérer les métadonnées de la ressource {resource_id}")
            return result
        
        data = response.json()
        if not data.get('success'):
            logger.warning(f"Erreur API pour la ressource {resource_id}")
            return result
        
        fields = data.get('result', {}).get('fields', [])
        
        # Chercher la colonne geometry PostGIS
        for field in fields:
            field_name = field.get('id', '').lower()
            field_type = field.get('type', '').lower()
            
            if field_name == 'geometry' and 'text' not in field_type:
                result['has_geometry'] = True
                result['geometry_column'] = field.get('id')
                logger.info(f"Colonne geometry PostGIS trouvée: {field.get('id')}")
            
            # Chercher les colonnes GeoJSON (texte)
            geojson_keywords = ['st_asgeojson', 'geo_point_2d', 'geojson', 'geom']
            if any(keyword in field_name for keyword in geojson_keywords):
                if 'text' in field_type or 'varchar' in field_type:
                    result['has_geojson'] = True
                    result['geojson_fields'].append(field.get('id'))
                    logger.info(f"Colonne GeoJSON trouvée: {field.get('id')} (type: {field_type})")
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur lors de la vérification de la ressource: {e}")
        return result


def generate_mapfile(dataset_name: str) -> bool:
    """Génère le mapfile pour un dataset"""
    try:
        import subprocess
        
        script_path = '/usr/local/bin/generate-mapfile.py'
        if not os.path.exists(script_path):
            script_path = '/srv/app/ckanext-ogc/scripts/generate-mapfile.py'
        
        if not os.path.exists(script_path):
            logger.error(f"Script generate-mapfile.py non trouvé")
            return False
        
        logger.info(f"Génération du mapfile pour {dataset_name}...")
        
        cmd = [
            'python3', script_path,
            '--dataset', dataset_name,
            '--ckan-url', CKAN_URL,
            '--ckan-api-key', CKAN_API_KEY,
            '--mapfiles-dir', '/mapserver/mapfiles',
            '--postgis-host', POSTGRES_HOST,
            '--postgis-port', str(POSTGRES_PORT),
            '--postgis-db', POSTGRES_DB,
            '--postgis-user', POSTGRES_USER,
            '--postgis-password', POSTGRES_PASSWORD,
            '--auto-create-geometry'  # Activer la création automatique
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logger.info(f"Mapfile généré avec succès pour {dataset_name}")
            if result.stdout:
                # Afficher les logs importants (rechercher les lignes avec , , , )
                important_lines = [line for line in result.stdout.split('\n') 
                                   if any(marker in line for marker in ['', '', '', '', '', '', ''])]
                if important_lines:
                    logger.info("Logs de génération:")
                    for line in important_lines[-30:]:  # Dernières 30 lignes importantes
                        logger.info(f"   {line}")
                else:
                    logger.info(f"Sortie: {result.stdout[-500:]}")
            return True
        else:
            logger.error(f"Échec de la génération du mapfile")
            if result.stderr:
                logger.error(f"Erreur: {result.stderr[-500:]}")
            if result.stdout:
                # Afficher les lignes importantes même en cas d'erreur
                important_lines = [line for line in result.stdout.split('\n') 
                                   if any(marker in line for marker in ['', '', '', '', '', '', '', 'ERROR', 'WARNING'])]
                if important_lines:
                    logger.error("Logs de génération (erreurs):")
                    for line in important_lines[-30:]:
                        logger.error(f"   {line}")
                else:
                    logger.info(f"Sortie: {result.stdout[-500:]}")
            return False
            
    except Exception as e:
        logger.error(f"Erreur lors de la génération du mapfile: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def check_mapfile_exists(dataset_name: str) -> bool:
    """Vérifie si le mapfile existe"""
    mapfile_path = f"/mapserver/mapfiles/{dataset_name}.map"
    exists = os.path.exists(mapfile_path)
    if exists:
        size = os.path.getsize(mapfile_path)
        logger.info(f"Mapfile existe: {mapfile_path} ({size} octets)")
    else:
        logger.warning(f"Mapfile n'existe pas: {mapfile_path}")
    return exists


def main():
    """Point d'entrée principal"""
    if len(sys.argv) < 2:
        logger.error("Usage: python3 diagnose-and-fix-mapfile.py <dataset_name> [--enable-postgis] [--generate]")
        logger.error("Options:")
        logger.error("  --enable-postgis  Active PostGIS si nécessaire")
        logger.error("  --generate        Génère le mapfile après le diagnostic")
        sys.exit(1)
    
    dataset_name = sys.argv[1]
    enable_postgis_flag = '--enable-postgis' in sys.argv
    generate_flag = '--generate' in sys.argv
    
    logger.info(f"Diagnostic pour le dataset: {dataset_name}")
    logger.info("=" * 60)
    
    # Étape 1: Vérifier PostGIS
    logger.info("\nÉtape 1: Vérification de PostGIS dans datastore")
    postgis_enabled = check_postgis_enabled()
    
    if not postgis_enabled:
        if enable_postgis_flag:
            logger.info("\nActivation de PostGIS...")
            if enable_postgis():
                logger.info("PostGIS activé avec succès")
                postgis_enabled = True
            else:
                logger.error("Échec de l'activation de PostGIS")
                sys.exit(1)
        else:
            logger.error("\nPostGIS n'est pas activé. Utilisez --enable-postgis pour l'activer automatiquement.")
            sys.exit(1)
    
    # Étape 2: Récupérer les informations du dataset
    logger.info("\nÉtape 2: Récupération des informations du dataset")
    dataset = get_dataset_info(dataset_name)
    
    if not dataset:
        logger.error(f"Dataset '{dataset_name}' non trouvé")
        sys.exit(1)
    
    logger.info(f"Dataset trouvé: {dataset.get('title', dataset_name)}")
    logger.info(f"   ID: {dataset.get('id')}")
    logger.info(f"   Nom: {dataset.get('name')}")
    
    # Étape 3: Vérifier les ressources
    logger.info("\nÉtape 3: Vérification des ressources géospatiales")
    resources = dataset.get('resources', [])
    
    if not resources:
        logger.warning("Aucune ressource trouvée dans le dataset")
        sys.exit(1)
    
    geospatial_resources = []
    for resource in resources:
        resource_id = resource.get('id')
        resource_name = resource.get('name', 'Sans nom')
        datastore_active = resource.get('datastore_active', False)
        
        logger.info(f"\n   Ressource: {resource_name} (ID: {resource_id})")
        logger.info(f"   Format: {resource.get('format', 'N/A')}")
        logger.info(f"   Datastore actif: {datastore_active}")
        
        if datastore_active:
            geom_info = check_resource_geometry(resource_id)
            
            if geom_info['has_geometry']:
                logger.info(f"   Colonne geometry PostGIS présente")
                geospatial_resources.append(resource)
            elif geom_info['has_geojson']:
                logger.info(f"   Colonnes GeoJSON trouvées: {geom_info['geojson_fields']}")
                logger.info(f"   La colonne geometry sera créée automatiquement lors de la génération du mapfile")
                geospatial_resources.append(resource)
            else:
                logger.warning(f"   Aucune colonne géométrique trouvée")
        else:
            logger.info(f"   Datastore non actif, ignoré pour la génération de mapfile")
    
    if not geospatial_resources:
        logger.warning("\nAucune ressource géospatiale trouvée dans le dataset")
        logger.info("Assurez-vous que:")
        logger.info("   1. La ressource a datastore_active=True")
        logger.info("   2. La ressource contient des colonnes géométriques (geometry, st_asgeojson, geo_point_2d, etc.)")
        sys.exit(1)
    
    # Étape 4: Vérifier si le mapfile existe déjà
    logger.info("\nÉtape 4: Vérification de l'existence du mapfile")
    mapfile_exists = check_mapfile_exists(dataset_name)
    
    if mapfile_exists and not generate_flag:
        logger.info("\nMapfile existe déjà!")
        logger.info("Pour forcer la régénération, utilisez: --generate")
        sys.exit(0)
    
    # Étape 5: Générer le mapfile (si demandé ou s'il n'existe pas)
    if generate_flag or not mapfile_exists:
        logger.info("\nÉtape 5: Génération du mapfile")
        if generate_mapfile(dataset_name):
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
            logger.info("\nRésumé du diagnostic:")
            logger.info(f"   - PostGIS activé: {postgis_enabled}")
            logger.info(f"   - Dataset trouvé: {dataset is not None}")
            logger.info(f"   - Ressources géospatiales: {len(geospatial_resources)}")
            if geospatial_resources:
                for res in geospatial_resources:
                    geom_info = check_resource_geometry(res.get('id'))
                    logger.info(f"     * {res.get('name', 'Sans nom')}: geometry={geom_info['has_geometry']}, geojson={geom_info['has_geojson']}")
            sys.exit(1)
    else:
        logger.info("\nDiagnostic terminé - mapfile existe déjà")


if __name__ == '__main__':
    main()

