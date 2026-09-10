#!/usr/bin/env python3
"""
Script pour forcer la génération du mapfile pour randonnees_de_la_ccmt
Usage: python3 force-generate-mapfile-randonnees.py
"""

import os
import sys
import requests
import json
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

CKAN_URL = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
CKAN_API_KEY = os.getenv('CKAN_API_KEY', '')
DATASET_NAME = 'randonnees_de_la_ccmt'

def get_dataset():
    """Récupère le dataset"""
    url = f"{CKAN_URL}/api/3/action/package_show"
    params = {'id': DATASET_NAME}
    headers = {'Authorization': CKAN_API_KEY} if CKAN_API_KEY else {}
    
    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    result = response.json()
    
    if not result.get('success'):
        raise Exception(f"Erreur API: {result.get('error')}")
    
    return result.get('result')

def check_mapfile():
    """Vérifie si le mapfile existe"""
    mapfile_path = f"/mapserver/mapfiles/{DATASET_NAME}.map"
    exists = os.path.exists(mapfile_path)
    if exists:
        size = os.path.getsize(mapfile_path)
        logger.info(f"Mapfile existe: {mapfile_path} ({size} octets)")
        return True
    else:
        logger.warning(f"Mapfile n'existe pas: {mapfile_path}")
        return False

def force_generate():
    """Force la génération du mapfile"""
    script_path = '/srv/app/src/ckanext-ogc/ckanext/ogc/scripts/generate-mapfile.py'
    if not os.path.exists(script_path):
        script_path = '/srv/app/ckanext-ogc/scripts/generate-mapfile.py'
    
    if not os.path.exists(script_path):
        logger.error(f"Script generate-mapfile.py non trouvé")
        return False
    
    cmd = [
        'python3', script_path,
        '--dataset', DATASET_NAME,
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
    
    logger.info("Génération du mapfile...")
    logger.info(f"   Dataset: {DATASET_NAME}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    
    if result.returncode == 0:
        logger.info("Génération réussie")
        if result.stdout:
            # Afficher les dernières lignes importantes
            lines = result.stdout.split('\n')
            important_lines = [l for l in lines if any(keyword in l.lower() 
                              for keyword in ['', '', '', 'error', 'success', 'mapfile'])]
            if important_lines:
                logger.info("Résumé:\n" + '\n'.join(important_lines[-20:]))
        return True
    else:
        logger.error(f"Génération échouée (code: {result.returncode})")
        if result.stderr:
            logger.error(f"Erreur:\n{result.stderr[-2000:]}")
        if result.stdout:
            logger.info(f"Sortie:\n{result.stdout[-2000:]}")
        return False

def main():
    logger.info("=" * 80)
    logger.info(f"FORCE GÉNÉRATION MAPFILE: {DATASET_NAME}")
    logger.info("=" * 80)
    
    # 1. Vérifier le dataset
    logger.info("\nÉtape 1: Vérification du dataset")
    try:
        dataset = get_dataset()
        logger.info(f"Dataset trouvé: {dataset.get('title', DATASET_NAME)}")
        
        # Afficher les métadonnées OGC
        extras = dataset.get('extras', [])
        ogc_metadata = {}
        for extra in extras:
            if isinstance(extra, dict) and extra.get('key', '').startswith('ogc:'):
                ogc_metadata[extra['key']] = extra.get('value')
        
        if ogc_metadata:
            logger.info("Métadonnées OGC:")
            for key, value in ogc_metadata.items():
                logger.info(f"   {key}: {value}")
        
        # Afficher les ressources
        resources = dataset.get('resources', [])
        logger.info(f"\nRessources: {len(resources)}")
        for res in resources:
            logger.info(f"   - {res.get('name', 'Sans nom')} ({res.get('format', 'N/A')})")
            logger.info(f"     ID: {res.get('id')}")
            logger.info(f"     Datastore actif: {res.get('datastore_active', False)}")
    except Exception as e:
        logger.error(f"Erreur: {e}")
        sys.exit(1)
    
    # 2. Vérifier le mapfile actuel
    logger.info("\nÉtape 2: Vérification du mapfile")
    mapfile_exists = check_mapfile()
    
    # 3. Forcer la génération
    logger.info("\nÉtape 3: Génération du mapfile")
    if force_generate():
        # 4. Vérifier après génération
        logger.info("\nÉtape 4: Vérification après génération")
        if check_mapfile():
            logger.info("\nSUCCÈS! Le mapfile a été généré.")
            logger.info(f"Fichier: /mapserver/mapfiles/{DATASET_NAME}.map")
        else:
            logger.error("\nLe mapfile n'a pas été créé malgré la génération réussie")
            logger.error("Vérifiez les permissions d'écriture dans /mapserver/mapfiles/")
            sys.exit(1)
    else:
        logger.error("\nÉchec de la génération")
        logger.info("Consultez les logs ci-dessus pour plus de détails")
        sys.exit(1)

if __name__ == '__main__':
    main()
