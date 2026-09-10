#!/usr/bin/env python3
"""
Script pour importer automatiquement les fichiers géospatiaux dans datagis

Ce script :
1. Scanne les ressources CKAN avec formats géospatiaux (SHP, GeoJSON, GPKG, etc.)
2. Importe les fichiers non-datastore dans la base datagis
3. Crée les mapfiles correspondants
"""

import os
import sys
import logging
import requests
import subprocess
import tempfile
import zipfile
import hashlib
import shutil
import re
from typing import Dict, List, Optional, Any
from pathlib import Path

try:
    import psycopg2
    from psycopg2 import OperationalError, DatabaseError
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    OperationalError = None
    DatabaseError = None
    print("psycopg2 non disponible, installation requise")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DatagisImporter:
    """Importe les fichiers géospatiaux dans datagis"""
    GEOSPATIAL_FORMATS = {'SHP', 'SHAPEFILE', 'GEOJSON', 'GPKG', 'GEOPACKAGE', 'KML', 'KMZ', 'ZIP'}
    
    def __init__(self, ckan_url: str, ckan_api_key: str,
                 postgis_host: str = 'db', postgis_port: int = 5432,
                 datagis_db: str = 'datagis', postgis_user: str = 'ckan',
                 postgis_password: str = '', ckan_storage_path: str = '/ckan_storage'):
        """
        Initialise l'importeur datagis
        
        Args:
            ckan_url: URL de l'API CKAN
            ckan_api_key: Clé API CKAN
            postgis_host: Host PostGIS
            postgis_port: Port PostGIS
            datagis_db: Base de données datagis
            postgis_user: Utilisateur PostGIS
            postgis_password: Mot de passe PostGIS
            ckan_storage_path: Chemin vers ckan_storage
        """
        self.ckan_url = ckan_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.postgis_host = postgis_host
        self.postgis_port = postgis_port
        self.datagis_db = datagis_db
        self.postgis_user = postgis_user
        self.postgis_password = postgis_password
        self.ckan_storage_path = Path(ckan_storage_path)
        
        self.headers = {'Authorization': self.ckan_api_key} if self.ckan_api_key else {}

    def _is_geospatial_resource_candidate(self, resource: Dict[str, Any]) -> bool:
        """Détection souple des ressources géospatiales importables."""
        format_ = (resource.get('format') or '').strip().upper()
        if format_ in self.GEOSPATIAL_FORMATS:
            return True

        mimetype = (resource.get('mimetype') or '').lower()
        if any(token in mimetype for token in ['zip', 'shp', 'geojson', 'geopackage', 'gpkg', 'kml', 'kmz']):
            return True

        name = (resource.get('name') or '').lower()
        url = (resource.get('url') or '').lower()
        candidates = [name, url]
        exts = ['.zip', '.shp', '.geojson', '.json', '.gpkg', '.kml', '.kmz']
        if any(any(ext in value for ext in exts) for value in candidates):
            return True

        if any(token in name for token in ['shp', 'shape', 'geojson', 'gpkg', 'geopackage', 'kml', 'kmz']):
            return True

        return False
    
    def get_geospatial_resources(self) -> List[Dict[str, Any]]:
        """
        Récupère toutes les ressources géospatiales non-datastore depuis CKAN.
        Pagination pour ne pas avoir de limite (traitement de tous les datasets).
        
        Returns:
            Liste des ressources géospatiales
        """
        try:
            url = f"{self.ckan_url}/api/action/package_search"
            resources = []
            start = 0
            rows = 100
            fq = 'res_format:(SHP OR GeoJSON OR GPKG OR KML OR KMZ OR Shapefile OR ZIP)'
            
            while True:
                params = {
                    'rows': rows,
                    'start': start,
                    'fq': fq,
                    'include_private': False
                }
                response = requests.get(url, params=params, headers=self.headers, timeout=60)
                response.raise_for_status()
                result = response.json()
                if not result.get('success'):
                    logger.error(f"Erreur API CKAN: {result.get('error', {})}")
                    break
                
                data = result.get('result', {})
                datasets = data.get('results', [])
                count = data.get('count', 0)
                
                for dataset in datasets:
                    for resource in dataset.get('resources', []):
                        if resource.get('datastore_active'):
                            continue
                        if self._is_geospatial_resource_candidate(resource):
                            res_url = resource.get('url', '')
                            url_type = resource.get('url_type', '')
                            if url_type == 'upload' or res_url:
                                resource['dataset_name'] = dataset.get('name')
                                resource['dataset_title'] = dataset.get('title')
                                resources.append(resource)
                            else:
                                logger.debug(f"  Ressource {resource.get('id', 'N/A')} ignorée (pas d'URL exploitable)")
                
                logger.info(f"Pagination: {len(resources)} ressources récupérées (start={start}, count total CKAN={count})")
                if start + rows >= count or len(datasets) == 0:
                    break
                start += rows
            
            logger.info(f"{len(resources)} ressources géospatiales non-datastore à traiter (sans limite)")
            return resources
            
        except Exception as e:
            logger.error(f"Erreur récupération ressources: {e}")
            return []
    
    def get_resource_file_path(self, resource: Dict[str, Any]) -> Optional[Path]:
        """
        Détermine le chemin du fichier pour une ressource
        
        Args:
            resource: Dictionnaire de la ressource
            
        Returns:
            Chemin du fichier ou None
        """
        url = resource.get('url', '')
        resource_id = resource.get('id')
        resource_name = resource.get('name', '')
        url_type = resource.get('url_type', '')
        
        logger.info(f"Recherche fichier pour ressource {resource_id} (nom: {resource_name}, URL: {url[:100] if url else 'N/A'}...)")
        
        # Les URLs externes seront gérées dans la section de téléchargement ci-dessous
        # Ne pas les ignorer ici
        
        # Ignorer les ressources déjà dans datastore
        if resource.get('datastore_active'):
            logger.debug(f"  Ressource déjà dans datastore, ignorée")
            return None
        
        # Structure CKAN pour les fichiers uploadés :
        # /var/lib/ckan/resources/{id[0:3]}/{id[3:6]}/{id[6:9]}/{id}
        # ou /var/lib/ckan/default/storage/resources/{id[0:3]}/{id[3:6]}/{id[6:9]}/{id}
        
        # Vérifier d'abord le chemin standard CKAN
        # D'après la configuration, storage_path est /var/lib/ckan (sans /default)
        # Mais on vérifie les deux emplacements possibles
        ckan_storage_base = Path('/var/lib/ckan')
        ckan_storage_base_default = Path('/var/lib/ckan/default')
        
        # Déterminer quel storage_path est utilisé
        # On essaiera les deux structures
        
        # Construire le chemin selon la structure CKAN
        # Structure CKAN confirmée : {storage_path}/resources/{id[0:3]}/{id[3:6]}/{id[6:]}
        # D'après la documentation et les tests, CKAN utilise :
        # - get_directory() retourne: {storage_path}/{id[0:3]}/{id[3:6]}
        # - get_path() retourne: {storage_path}/resources/{id[0:3]}/{id[3:6]}/{id[6:]}
        # Le nom du fichier est {id[6:]} (le reste de l'ID avec les tirets)
        if resource_id and len(resource_id) >= 6:
            # Structure standard CKAN (confirmée par les tests)
            # Format: /var/lib/ckan/resources/{id[0:3]}/{id[3:6]}/{id[6:]}
            # PRIORITÉ 1: Structure standard avec storage_path=/var/lib/ckan
            ckan_path_standard = ckan_storage_base / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            ckan_storage_path_standard = ckan_storage_base / 'storage' / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            
            # PRIORITÉ 2: Structure avec storage_path=/var/lib/ckan/default
            ckan_path_default = ckan_storage_base_default / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            ckan_storage_path_default = ckan_storage_base_default / 'storage' / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            
            # Variante possible : ID complet comme nom de fichier (moins probable mais à vérifier)
            ckan_path_full_id = ckan_storage_base / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id
            ckan_storage_path_full_id = ckan_storage_base / 'storage' / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id
        else:
            # Fallback si l'ID est trop court
            ckan_path_standard = ckan_storage_base / 'resources' / resource_id
            ckan_storage_path_standard = ckan_storage_base / 'storage' / 'resources' / resource_id
            ckan_path_default = ckan_storage_base_default / 'resources' / resource_id
            ckan_storage_path_default = ckan_storage_base_default / 'storage' / 'resources' / resource_id
            ckan_path_full_id = None
            ckan_storage_path_full_id = None
        
        # Chercher dans plusieurs emplacements possibles (par ordre de probabilité)
        # PRIORITÉ: Structure standard CKAN confirmée par les tests
        possible_paths = [
            # PRIORITÉ 1: Structure standard avec storage_path=/var/lib/ckan (confirmée)
            # Format: /var/lib/ckan/resources/{id[0:3]}/{id[3:6]}/{id[6:]}
            ckan_path_standard,
            ckan_storage_path_standard,
            # PRIORITÉ 2: Structure avec storage_path=/var/lib/ckan/default (ancienne config possible)
            ckan_path_default if resource_id and len(resource_id) >= 6 else None,
            ckan_storage_path_default if resource_id and len(resource_id) >= 6 else None,
            # PRIORITÉ 3: Variante avec ID complet comme nom de fichier (moins probable)
            ckan_path_full_id,
            ckan_storage_path_full_id,
            # PRIORITÉ 4: Chemins depuis ckan_storage_path configuré (structure standard)
            self.ckan_storage_path / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:] if resource_id and len(resource_id) >= 6 else None,
            self.ckan_storage_path / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id if resource_id and len(resource_id) >= 6 else None,
            # PRIORITÉ 5: Chemins alternatifs (anciens formats ou structures différentes)
            ckan_storage_base / 'storage' / 'resources' / resource_id,
            ckan_storage_base / 'storage' / 'resources' / resource_id / resource_name,
            self.ckan_storage_path / 'resources' / resource_id,
            self.ckan_storage_path / 'resources' / resource_id / resource_name,
            self.ckan_storage_path / 'storage' / 'uploads' / resource_id,
            # PRIORITÉ 6: Chemin depuis l'URL si c'est un chemin local
            Path(url) if url.startswith('/') and not url.startswith('//') and Path(url).exists() else None,
        ]
        
        # Logger tous les chemins vérifiés
        checked_paths = []
        for path in possible_paths:
            if path is None:
                continue
            
            checked_paths.append(str(path))
            exists = path.exists()
            is_file = path.is_file() if exists else False
            
            logger.debug(f"  Vérification: {path} - existe: {exists}, est fichier: {is_file}")
            
            if exists and is_file:
                logger.info(f"Fichier trouvé: {path}")
                return path
            
            # Chercher aussi les fichiers dans le répertoire parent si c'est un répertoire
            if exists and path.is_dir():
                logger.debug(f"  {path} est un répertoire, recherche de fichiers...")
                try:
                    for file in path.iterdir():
                        if file.is_file() and not file.name.startswith('.'):
                            logger.info(f"Fichier trouvé dans répertoire: {file}")
                            return file
                except Exception as e:
                    logger.debug(f"  Erreur lecture répertoire {path}: {e}")
            
            # Vérifier aussi le répertoire parent
            parent = path.parent
            if parent.exists() and parent.is_dir():
                logger.debug(f"  Vérification répertoire parent: {parent}")
                try:
                    for file in parent.iterdir():
                        if file.is_file() and not file.name.startswith('.'):
                            logger.info(f"Fichier trouvé dans répertoire parent: {file}")
                            return file
                except Exception as e:
                    logger.debug(f"  Erreur lecture répertoire parent {parent}: {e}")
        
        logger.warning(f"Fichier non trouvé pour ressource {resource_id}")
        logger.info(f"Chemins vérifiés ({len(checked_paths)}):")
        for checked_path in checked_paths:
            exists = Path(checked_path).exists()
            status = 'existe' if exists else 'n\'existe pas'
            logger.info(f"   - {checked_path} {status}")
        
        # Logger aussi l'URL de la ressource pour debug
        if url:
            logger.info(f"URL de la ressource: {url}")
            # Télécharger aussi les URLs externes publiques pour les datasets moissonnés.
            if url.startswith('http://') or url.startswith('https://'):
                is_ckan_internal = False
                if '/resource/' in url or '/dataset/' in url:
                    if any(domain in url for domain in ['localhost', '127.0.0.1', 'ckan2', 'qualif-data.example.org', 'data.example.org']):
                        is_ckan_internal = True

                if is_ckan_internal:
                    logger.info(f"URL CKAN interne détectée, tentative de téléchargement...")
                else:
                    logger.info(f"URL externe détectée, tentative de téléchargement...")

                downloaded_path = self._download_resource_file(resource_id, url, resource_name)
                if downloaded_path:
                    logger.info(f"Fichier téléchargé depuis l'URL: {downloaded_path}")
                    return downloaded_path
                else:
                    logger.warning(f"Échec du téléchargement depuis l'URL")
            elif '/storage/' in url or '/resource/' in url:
                logger.info(f"L'URL semble pointer vers le storage CKAN, mais le fichier n'a pas été trouvé localement")
        
        return None
    
    def _download_resource_file(self, resource_id: str, url: str, resource_name: str) -> Optional[Path]:
        """
        Télécharge un fichier depuis une URL et le sauvegarde temporairement
        
        Args:
            resource_id: ID de la ressource
            url: URL du fichier à télécharger
            resource_name: Nom de la ressource (pour déterminer l'extension)
            
        Returns:
            Chemin du fichier téléchargé ou None
        """
        try:
            logger.info(f"Téléchargement depuis: {url[:100]}...")
            
            # Déterminer l'extension du fichier
            file_ext = ''
            if resource_name:
                # Extraire l'extension du nom de ressource
                if '.' in resource_name:
                    file_ext = '.' + resource_name.rsplit('.', 1)[1].lower()
            
            # Si pas d'extension dans le nom, essayer de la deviner depuis l'URL
            if not file_ext and '.' in url:
                file_ext = '.' + url.rsplit('.', 1)[1].lower()
                # Limiter aux extensions géospatiales connues
                if file_ext not in ['.shp', '.zip', '.geojson', '.gpkg', '.kml', '.kmz']:
                    file_ext = ''
            
            # Créer un fichier temporaire
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext, prefix=f'ckan_resource_{resource_id}_') as tmp_file:
                tmp_path = Path(tmp_file.name)
            
            # Télécharger le fichier
            response = requests.get(url, headers=self.headers, timeout=300, stream=True)
            response.raise_for_status()
            
            # Écrire le fichier
            total_size = 0
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total_size += len(chunk)
            
            file_size_mb = total_size / (1024 * 1024)
            logger.info(f"Fichier téléchargé: {tmp_path} ({file_size_mb:.2f} MB)")
            
            return tmp_path
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors du téléchargement: {e}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue lors du téléchargement: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _normalize_input_file(self, file_path: Path, resource: Dict[str, Any]) -> Path:
        """
        CKAN stocke souvent les uploads sans extension.
        On crée une copie temporaire avec extension pour aider GDAL/OGR.
        """
        if file_path.suffix:
            return file_path

        # 1) ZIP détecté par contenu
        if zipfile.is_zipfile(file_path):
            tmp = Path(tempfile.mkstemp(prefix="ckan_res_", suffix=".zip")[1])
            shutil.copy2(file_path, tmp)
            logger.info(f"Fichier CKAN sans extension détecté comme ZIP -> copie: {tmp}")
            return tmp

        # 2) Sinon, on essaie d'inférer via le nom de ressource (si dispo)
        name = (resource.get("name") or "").strip()
        if "." in name:
            ext = "." + name.rsplit(".", 1)[1].lower()
            if ext in [".shp", ".geojson", ".gpkg", ".kml", ".kmz", ".json"]:
                tmp = Path(tempfile.mkstemp(prefix="ckan_res_", suffix=ext)[1])
                shutil.copy2(file_path, tmp)
                logger.info(f"Fichier CKAN sans extension -> copie avec extension {ext}: {tmp}")
                return tmp

        # 3) Utiliser le format déclaré (ex. "SHP" -> .shp) si le nom n'a pas d'extension
        format_ = (resource.get("format") or "").upper()
        format_to_ext = {
            "SHP": ".shp",
            "GPKG": ".gpkg",
            "GEOJSON": ".geojson",
            "GEO JSON": ".geojson",
            "KML": ".kml",
            "KMZ": ".kmz",
            "JSON": ".json",
        }
        if format_ in format_to_ext:
            ext = format_to_ext[format_]
            tmp = Path(tempfile.mkstemp(prefix="ckan_res_", suffix=ext)[1])
            shutil.copy2(file_path, tmp)
            logger.info(f"Fichier CKAN sans extension -> copie avec extension {ext} (format {format_}): {tmp}")
            return tmp

        return file_path
    
    def import_to_datagis(self, resource: Dict[str, Any], file_path: Path) -> Optional[str]:
        """
        Importe un fichier géospatial dans datagis
        
        Args:
            resource: Dictionnaire de la ressource
            file_path: Chemin vers le fichier
            
        Returns:
            Nom de la table créée ou None
        """
        # Normaliser le fichier (ajouter extension si manquante)
        file_path = self._normalize_input_file(file_path, resource)
        logger.info(f"Fichier normalisé pour import: {file_path}")
        if not HAS_PSYCOPG2:
            logger.error("psycopg2 non disponible")
            return None
        
        format_ = resource.get('format', '').upper()
        resource_id = resource.get('id')
        dataset_name = resource.get('dataset_name', 'unknown')
        resource_name = resource.get('name', '')
        
        logger.info("=" * 80)
        logger.info(f"DÉBUT IMPORT RESSOURCE DANS DATAGIS")
        logger.info(f"   Ressource ID: {resource_id}")
        logger.info(f"   Nom ressource: {resource_name}")
        logger.info(f"   Dataset: {dataset_name}")
        logger.info(f"   Format: {format_}")
        logger.info(f"   Fichier: {file_path}")
        logger.info("=" * 80)
        
        # Générer un nom de table basé sur l'ID complet de la ressource (garantit l'unicité)
        # NOUVEAU PATTERN: res_{resource_id_complet_nettoye}
        # Avantages:
        # - Nom unique (basé sur l'ID UUID/hash complet de CKAN)
        # - Pas de collision possible
        # - Facile à retrouver depuis CKAN
        # - Plus simple et plus sûr que de tronquer
        
        if not resource_id:
            logger.error("ID de ressource manquant, impossible de générer un nom de table unique")
            return None
        
        # Utiliser l'ID complet de la ressource (UUID ou hash)
        # Nettoyer l'ID pour qu'il soit valide comme nom de table PostgreSQL
        # (seulement alphanumériques et underscores, remplacer les tirets par underscores)
        resource_id_clean = resource_id.replace('-', '_')
        resource_id_clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in resource_id_clean)
        
        table_name = f"res_{resource_id_clean}"
        
        # Limiter la longueur totale (PostgreSQL limite à 63 caractères)
        # Format: res_ + ID (généralement UUID = 36 caractères, donc total = 40 caractères)
        if len(table_name) > 63:
            # Si jamais l'ID est très long, tronquer en gardant le préfixe "res_"
            max_id_length = 63 - 4  # 4 caractères pour "res_"
            table_name = f"res_{resource_id_clean[:max_id_length]}"
            logger.warning(f"ID de ressource tronqué (limite PostgreSQL 63 caractères): {table_name}")
        
        logger.info(f"Nom de table généré depuis l'ID complet de ressource: {table_name}")
        logger.info(f"   ID ressource: {resource_id}")
        logger.info(f"   Format: res_{{resource_id_complet_nettoye}}")
        
        try:
            # Vérifier si la table existe déjà
            try:
                conn = psycopg2.connect(
                    host=self.postgis_host,
                    port=self.postgis_port,
                    dbname=self.datagis_db,
                    user=self.postgis_user,
                    password=self.postgis_password,
                    connect_timeout=10
                )
            except psycopg2.OperationalError as e:
                error_msg = str(e)
                if 'database' in error_msg.lower() and 'does not exist' in error_msg.lower():
                    logger.error(f"Base de données '{self.datagis_db}' n'existe pas")
                    logger.info(f"Pour créer la base datagis:")
                    logger.info(f"   1. Se connecter à PostgreSQL: docker exec -it db psql -U {self.postgis_user}")
                    logger.info(f"   2. Créer la base: CREATE DATABASE {self.datagis_db} OWNER {self.postgis_user} ENCODING 'utf-8';")
                    logger.info(f"   3. Activer PostGIS: \\c {self.datagis_db}; CREATE EXTENSION IF NOT EXISTS postgis;")
                elif 'password authentication failed' in error_msg.lower() or 'authentication failed' in error_msg.lower():
                    logger.error(f"Erreur d'authentification pour utilisateur '{self.postgis_user}'")
                    logger.info(f"Vérifiez les identifiants: POSTGRES_USER={self.postgis_user}, POSTGRES_PASSWORD=...")
                else:
                    logger.error(f"Erreur de connexion à datagis: {e}")
                    logger.info(f"Vérifiez: host={self.postgis_host}, port={self.postgis_port}, db={self.datagis_db}, user={self.postgis_user}")
                return None
            except Exception as e:
                logger.error(f"Erreur inattendue lors de la connexion à datagis: {e}")
                return None
            
            cur = conn.cursor()
            
            # Créer la table de métadonnées si elle n'existe pas
            cur.execute("""
                CREATE TABLE IF NOT EXISTS datagis_import_metadata (
                    table_name VARCHAR(255) PRIMARY KEY,
                    resource_id VARCHAR(255) NOT NULL,
                    file_hash VARCHAR(64) NOT NULL,
                    file_size BIGINT,
                    file_mtime TIMESTAMP,
                    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(resource_id)
                );
            """)
            conn.commit()
            
            # Vérifier si la table existe déjà
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = '{table_name}'
                );
            """)
            table_exists = cur.fetchone()[0]
            
            if table_exists:
                logger.info(f"Table existe déjà dans datagis: {table_name}")
                
                # Calculer le hash du fichier actuel
                try:
                    file_hash = self._calculate_file_hash(file_path)
                    file_size = file_path.stat().st_size
                    file_mtime = file_path.stat().st_mtime
                    
                    logger.info(f"Vérification des changements du fichier...")
                    logger.info(f"   Hash actuel: {file_hash}")
                    logger.info(f"   Taille: {file_size} bytes")
                    
                    # Récupérer le hash stocké
                    cur.execute("""
                        SELECT file_hash, file_size, file_mtime 
                        FROM datagis_import_metadata 
                        WHERE table_name = %s
                    """, (table_name,))
                    metadata = cur.fetchone()
                    
                    if metadata:
                        stored_hash, stored_size, stored_mtime = metadata
                        logger.info(f"   Hash stocké: {stored_hash}")
                        logger.info(f"   Taille stockée: {stored_size} bytes")
                        
                        # Comparer les hashs
                        if file_hash == stored_hash and file_size == stored_size:
                            logger.info(f"Fichier inchangé (hash et taille identiques)")
                            logger.info(f"   Pas besoin de réimporter")
                            cur.close()
                            conn.close()
                            return table_name
                        else:
                            logger.info(f"Fichier modifié détecté!")
                            if file_hash != stored_hash:
                                logger.info(f"   → Hash différent: {stored_hash} → {file_hash}")
                            if file_size != stored_size:
                                logger.info(f"   → Taille différente: {stored_size} → {file_size}")
                            logger.info(f"   Réimport nécessaire avec -overwrite")
                            # Continuer pour réimporter
                    else:
                        logger.info(f"Pas de métadonnées trouvées pour {table_name}")
                        logger.info(f"   Réimport pour créer les métadonnées")
                        # Continuer pour réimporter et créer les métadonnées
                except Exception as e:
                    logger.warning(f"Erreur lors de la vérification du hash: {e}")
                    logger.info(f"   Réimport par précaution")
                    # En cas d'erreur, réimporter par précaution
            
            # Si on arrive ici, soit la table n'existe pas, soit elle existe mais le fichier a changé
            if table_exists:
                logger.info(f"Table existe mais fichier modifié, réimport nécessaire")
            else:
                logger.info(f"Table n'existe pas, import nécessaire")
            logger.info(f"   Nom de table calculé: {table_name}")
            
            # Importer selon le format (format_ peut être un libellé court ou un MIME type, ex. APPLICATION/GEOPACKAGE+SQLITE3)
            logger.info(f"Détermination de la méthode d'import selon le format: {format_}")
            
            # Détecter ZIP par contenu si pas d'extension
            is_zip = (file_path.suffix.lower() == '.zip') or zipfile.is_zipfile(file_path)
            
            if format_ in {'SHP', 'SHAPEFILE'} or 'SHAPEFILE' in (format_ or '') or is_zip:
                logger.info(f"   → Format Shapefile détecté")
                out = self._import_shapefile(file_path, table_name, cur, conn, resource_id)
                if out is None:
                    logger.error(f"_import_shapefile a retourné None (voir logs ci-dessus: .shp dans ZIP, ogr2ogr, ou connexion datagis)")
                return out
            elif format_ == 'GEOJSON' or 'GEOJSON' in (format_ or '') or file_path.suffix.lower() == '.geojson':
                logger.info(f"   → Format GeoJSON détecté")
                return self._import_geojson(file_path, table_name, cur, conn, resource_id)
            elif format_ in {'GPKG', 'GEOPACKAGE'} or ('GEOPACKAGE' in (format_ or '') or 'GPKG' in (format_ or '')) or file_path.suffix.lower() == '.gpkg':
                logger.info(f"   → Format GPKG détecté")
                return self._import_gpkg(file_path, table_name, cur, conn, resource_id)
            else:
                logger.warning(f"Format {format_} non supporté pour l'import automatique")
                logger.info(f"   Formats supportés: SHP, SHAPEFILE, GEOJSON, GPKG, GEOPACKAGE (ou MIME ex. APPLICATION/GEOPACKAGE+SQLITE3)")
                cur.close()
                conn.close()
                return None
                
        except Exception as e:
            logger.error(f"Erreur import dans datagis: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _import_shapefile(self, file_path: Path, table_name: str, cur, conn, resource_id: str = None) -> Optional[str]:
        """Importe un Shapefile (ZIP ou fichiers) dans datagis"""
        logger.info(f"Import Shapefile: {file_path}")
        try:
            # Détecter ZIP par extension OU par contenu (CKAN stocke souvent sans extension)
            is_zip = (file_path.suffix.lower() == '.zip') or zipfile.is_zipfile(file_path)
            
            if is_zip:
                logger.info(f"   → Fichier ZIP détecté, extraction nécessaire")
                # Extraire le ZIP
                with tempfile.TemporaryDirectory() as temp_dir:
                    logger.info(f"   Répertoire temporaire: {temp_dir}")
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        names = zip_ref.namelist()
                        shp_files = [f for f in names if f.lower().endswith('.shp')]
                        geojson_files = [f for f in names if f.lower().endswith('.geojson') or (f.lower().endswith('.json') and not f.lower().endswith('.min.json'))]
                        if shp_files:
                            logger.info(f"   {len(shp_files)} fichier(s) .shp trouvé(s)")
                            logger.info(f"   Extraction du ZIP...")
                            zip_ref.extractall(temp_dir)
                            first_geom = os.path.join(temp_dir, shp_files[0])
                            logger.info(f"   Fichier .shp extrait: {shp_files[0]}")
                            return self._run_ogr2ogr(first_geom, table_name, cur, conn, resource_id)
                        if geojson_files:
                            logger.info(f"   {len(geojson_files)} fichier(s) GeoJSON trouvé(s) dans le ZIP (pas de .shp)")
                            logger.info(f"   Extraction du ZIP...")
                            zip_ref.extractall(temp_dir)
                            first_geojson = os.path.join(temp_dir, geojson_files[0])
                            logger.info(f"   Fichier GeoJSON extrait: {geojson_files[0]}")
                            return self._run_ogr2ogr(first_geojson, table_name, cur, conn, resource_id)
                        logger.warning(f"Aucun fichier .shp ni .geojson/.json dans le ZIP")
                        logger.info(f"   Fichiers dans le ZIP: {names[:15]}")
                        return None
            elif file_path.is_dir():
                # Si c'est un répertoire, chercher le fichier .shp à l'intérieur
                logger.info(f"   → Répertoire détecté, recherche du fichier .shp...")
                shp_files = list(file_path.glob('*.shp'))
                if not shp_files:
                    logger.error(f"Aucun fichier .shp trouvé dans le répertoire: {file_path}")
                    logger.info(f"   Fichiers dans le répertoire: {list(file_path.iterdir())[:10]}")
                    return None
                logger.info(f"   {len(shp_files)} fichier(s) .shp trouvé(s)")
                shp_file = shp_files[0]
                logger.info(f"   Utilisation du fichier: {shp_file}")
                return self._run_ogr2ogr(str(shp_file), table_name, cur, conn, resource_id)
            else:
                # Vérifier que c'est bien un fichier .shp
                if file_path.suffix.lower() != '.shp':
                    logger.warning(f"Le fichier ne se termine pas par .shp: {file_path}")
                    logger.info(f"   Tentative d'import quand même...")
                logger.info(f"   → Fichier .shp direct (pas de ZIP)")
                return self._run_ogr2ogr(str(file_path), table_name, cur, conn, resource_id)
                
        except zipfile.BadZipFile:
            logger.error(f"Fichier ZIP corrompu: {file_path}")
            return None
        except Exception as e:
            logger.error(f"Erreur import Shapefile: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _import_geojson(self, file_path: Path, table_name: str, cur, conn, resource_id: str = None) -> Optional[str]:
        """Importe un GeoJSON dans datagis"""
        logger.info(f"Import GeoJSON: {file_path}")
        return self._run_ogr2ogr(str(file_path), table_name, cur, conn, resource_id)
    
    def _import_gpkg(self, file_path: Path, table_name: str, cur, conn, resource_id: str = None) -> Optional[str]:
        """Importe un GPKG dans datagis.
        Si le fichier n'a pas d'extension .gpkg (ex. stockage CKAN sans extension), on préfixe par GPKG:
        pour forcer le driver OGR (sinon ogr2ogr ne reconnaît pas le format).
        """
        logger.info(f"Import GPKG: {file_path}")
        input_path = str(file_path)
        if not input_path.lower().endswith('.gpkg'):
            input_path = "GPKG:" + input_path
            logger.info(f"   → Pas d'extension .gpkg: utilisation du préfixe GPKG: pour forcer le driver OGR")
        return self._run_ogr2ogr(input_path, table_name, cur, conn, resource_id)
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """
        Calcule le hash MD5 d'un fichier
        
        Args:
            file_path: Chemin vers le fichier
            
        Returns:
            Hash MD5 en hexadécimal
        """
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                # Lire par chunks pour gérer les gros fichiers
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.warning(f"Erreur calcul hash pour {file_path}: {e}")
            # Retourner un hash basé sur la taille et la date de modification en fallback
            try:
                stat = file_path.stat()
                fallback = f"{stat.st_size}_{stat.st_mtime}"
                return hashlib.md5(fallback.encode()).hexdigest()
            except Exception:
                return "unknown"
    
    def _validate_srid(self, srid: int) -> bool:
        """
        Valide qu'un SRID est un code EPSG valide
        
        Args:
            srid: Code SRID à valider
            
        Returns:
            True si valide, False sinon
        """
        if srid is None:
            return False
        
        # Codes EPSG valides sont généralement entre 2000 et 99999
        # Codes courants: 4326 (WGS84), 3857 (Web Mercator), 2154 (Lambert-93), etc.
        if srid < 2000 or srid > 99999:
            return False
        
        # Codes EPSG invalides connus (concaténations, erreurs, etc.)
        invalid_srids = [900914, 999999, 0, -1]
        if srid in invalid_srids:
            return False
        
        return True
    
    def _detect_source_epsg(self, input_file: str) -> Optional[int]:
        """
        Détecte l'EPSG source d'un fichier géospatial avec ogrinfo
        
        Args:
            input_file: Chemin vers le fichier géospatial
            
        Returns:
            Code EPSG (int) si détecté, None sinon
        """
        try:
            # gdalsrsinfo est plus fiable qu'un simple parsing de ogrinfo
            # pour éviter de confondre le CRS projeté (ex. EPSG:2154)
            # avec le CRS géographique imbriqué (ex. EPSG:4326).
            try:
                srs_result = subprocess.run(
                    ['gdalsrsinfo', '-o', 'epsg', input_file],
                    capture_output=True, text=True, timeout=15,
                    encoding='utf-8', errors='replace'
                )
                if srs_result.returncode == 0:
                    srs_output = (srs_result.stdout or '') + (srs_result.stderr or '')
                    match = re.search(r'EPSG[:\s]+(\d+)', srs_output, re.IGNORECASE)
                    if match:
                        epsg_code = int(match.group(1))
                        if self._validate_srid(epsg_code):
                            logger.info(f"   EPSG source détecté via gdalsrsinfo: {epsg_code}")
                            return epsg_code
            except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
                pass

            # Exécuter ogrinfo pour obtenir les informations sur le fichier
            cmd = ['ogrinfo', '-al', '-so', input_file]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            
            if result.returncode != 0:
                logger.warning(f"ogrinfo a échoué pour {input_file}: {result.stderr[:200]}")
                return None
            
            # Parser la sortie pour trouver l'EPSG
            # Chercher des patterns comme "EPSG:4326", "AUTHORITY["EPSG","4326"]", etc.
            output = result.stdout + result.stderr
            
            # Pattern 1: EPSG:4326
            match = re.search(r'EPSG[:\s]+(\d+)', output, re.IGNORECASE)
            if match:
                epsg_code = int(match.group(1))
                if self._validate_srid(epsg_code):
                    logger.info(f"   EPSG source détecté: {epsg_code}")
                    return epsg_code
            
            # Pattern 2: AUTHORITY["EPSG","4326"]
            match = re.search(r'AUTHORITY\["EPSG",\s*"(\d+)"\]', output, re.IGNORECASE)
            if match:
                epsg_code = int(match.group(1))
                if self._validate_srid(epsg_code):
                    logger.info(f"   EPSG source détecté: {epsg_code}")
                    return epsg_code
            
            # Pattern 3: PROJCS["...",GEOGCS["...",AUTHORITY["EPSG","4326"]]]
            match = re.search(r'AUTHORITY\["EPSG",\s*"(\d+)"\]', output, re.IGNORECASE)
            if match:
                epsg_code = int(match.group(1))
                if self._validate_srid(epsg_code):
                    logger.info(f"   EPSG source détecté: {epsg_code}")
                    return epsg_code
            
            logger.info(f"   Aucun EPSG source détecté pour {input_file}")
            return None
            
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout lors de la détection EPSG pour {input_file}")
            return None
        except Exception as e:
            logger.warning(f"Erreur lors de la détection EPSG pour {input_file}: {e}")
            return None

    def _get_geometry_extent(self, cur, table_name: str, geom_column: str) -> Optional[tuple]:
        """Retourne l'emprise (xmin, ymin, xmax, ymax) d'une table PostGIS."""
        try:
            cur.execute(f"""
                SELECT
                    ST_XMin(ext),
                    ST_YMin(ext),
                    ST_XMax(ext),
                    ST_YMax(ext)
                FROM (
                    SELECT ST_Extent({geom_column})::box2d AS ext
                    FROM "{table_name}"
                    WHERE {geom_column} IS NOT NULL
                ) s
                WHERE ext IS NOT NULL;
            """)
            result = cur.fetchone()
            if not result or any(v is None for v in result):
                return None
            return tuple(float(v) for v in result)
        except Exception as e:
            logger.debug(f"Impossible de calculer l'emprise de {table_name}.{geom_column}: {e}")
            return None

    def _looks_like_geographic_bbox(self, bbox: Optional[tuple]) -> bool:
        """Indique si l'emprise ressemble à des coordonnées lon/lat WGS84."""
        if not bbox:
            return False
        xmin, ymin, xmax, ymax = bbox
        return (
            -180 <= xmin <= 180 and -180 <= xmax <= 180 and
            -90 <= ymin <= 90 and -90 <= ymax <= 90
        )

    def _looks_like_lambert93_bbox(self, bbox: Optional[tuple]) -> bool:
        """Heuristique simple pour reconnaître des coordonnées Lambert-93 en mètres."""
        if not bbox:
            return False
        xmin, ymin, xmax, ymax = bbox
        return (
            -100000 <= xmin <= 1400000 and -100000 <= xmax <= 1400000 and
            5800000 <= ymin <= 7300000 and 5800000 <= ymax <= 7300000
        )

    def _guess_source_srid_from_extent(self, cur, table_name: str, geom_column: str) -> Optional[int]:
        """Déduit un SRID source probable à partir des coordonnées stockées."""
        bbox = self._get_geometry_extent(cur, table_name, geom_column)
        if self._looks_like_geographic_bbox(bbox):
            return 4326
        if self._looks_like_lambert93_bbox(bbox):
            return 2154
        return None

    def _repair_suspicious_wgs84_coordinates(self, cur, conn, table_name: str, geom_column: str, srid: int) -> int:
        """
        Corrige le cas où la table est étiquetée 4326 mais contient en réalité
        des coordonnées projetées Lambert-93 (en mètres).
        """
        if srid != 4326:
            return srid
        bbox = self._get_geometry_extent(cur, table_name, geom_column)
        if not self._looks_like_lambert93_bbox(bbox):
            return srid
        try:
            logger.warning(f"Coordonnées suspectes pour {table_name}.{geom_column}: SRID=4326 mais emprise Lambert-93 probable")
            logger.info("   Reprojection corrective EPSG:2154 -> EPSG:4326...")
            cur.execute(f"""
                UPDATE "{table_name}"
                SET {geom_column} = ST_Transform(ST_SetSRID({geom_column}, 2154), 4326)
                WHERE {geom_column} IS NOT NULL;
            """)
            conn.commit()
            logger.info("   Reprojection corrective terminée")
            return 4326
        except Exception as e:
            conn.rollback()
            logger.error(f"   Échec de la reprojection corrective: {e}")
            return srid
    
    def _validate_and_fix_srid(self, cur, conn, table_name: str, geom_column: str, detected_srid: int) -> int:
        """
        Valide et corrige le SRID d'une table après import ogr2ogr
        
        Args:
            cur: Curseur PostgreSQL
            conn: Connexion PostgreSQL
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            detected_srid: SRID détecté par ogr2ogr
            
        Returns:
            SRID validé (corrigé si nécessaire)
        """
        if self._validate_srid(detected_srid):
            return detected_srid
        
        # SRID invalide détecté, le corriger
        logger.warning(f"SRID invalide détecté pour {table_name}.{geom_column}: {detected_srid}")
        guessed_source_srid = self._guess_source_srid_from_extent(cur, table_name, geom_column)
        logger.info(f"   SRID source probable déduit des coordonnées: {guessed_source_srid or 'inconnu'}")
        
        default_srid = 4326  # WGS84 par défaut
        
        try:
            if guessed_source_srid == 2154:
                cur.execute(f"""
                    UPDATE "{table_name}"
                    SET {geom_column} = ST_Transform(ST_SetSRID({geom_column}, %s), %s)
                    WHERE {geom_column} IS NOT NULL;
                """, (2154, default_srid))
                logger.info(f"   Coordonnées reprojetées de EPSG:2154 vers EPSG:{default_srid}")
            else:
                cur.execute(f"""
                    UPDATE "{table_name}"
                    SET {geom_column} = ST_SetSRID({geom_column}, %s)
                    WHERE {geom_column} IS NOT NULL;
                """, (default_srid,))
                logger.warning(f"    Réinterprétation simple avec ST_SetSRID({default_srid})")
                logger.warning(f"   Utilisé seulement faute de meilleur SRID source détecté")

            # Mettre à jour geometry_columns pour refléter le SRID final
            cur.execute("""
                UPDATE geometry_columns
                SET srid = %s
                WHERE f_table_schema = 'public'
                AND f_table_name = %s
                AND f_geometry_column = %s;
            """, (default_srid, table_name, geom_column))
            
            # Vérifier le résultat
            cur.execute(f"""
                SELECT COUNT(*) 
                FROM "{table_name}"
                WHERE {geom_column} IS NOT NULL;
            """)
            geom_count = cur.fetchone()[0]
            
            conn.commit()
            logger.info(f"   SRID corrigé dans geometry_columns: {detected_srid} → {default_srid}")
            logger.info(f"   SRID final appliqué à {geom_count} géométrie(s)")
            
            return default_srid
            
        except Exception as e:
            conn.rollback()
            logger.error(f"   Erreur lors de la correction du SRID: {e}")
            # Retourner quand même le SRID par défaut pour éviter les erreurs
            return default_srid
    
    def _run_ogr2ogr(self, input_file: str, table_name: str, cur, conn, resource_id: str = None) -> Optional[str]:
        """Exécute ogr2ogr pour importer dans datagis.
        input_file peut être un chemin simple ou un préfixe OGR (ex. GPKG:/path) pour forcer le driver.
        """
        logger.info("=" * 80)
        logger.info(f" DÉBUT IMPORT DATAGIS VIA OGR2OGR")
        logger.info(f"   Fichier source: {input_file}")
        logger.info(f"   Table cible: {table_name}")
        logger.info(f"   Base de données: {self.datagis_db}")
        logger.info(f"   Host: {self.postgis_host}:{self.postgis_port}")
        logger.info("=" * 80)
        
        # Chemin physique pour exists/isfile/getsize (sans préfixe DRIVER:)
        physical_path = input_file
        if ":" in input_file and not input_file.startswith("/") and not (len(input_file) > 1 and input_file[1] == ":"):
            # Préfixe OGR type "GPKG:/path" ou "GeoJSON:/path"
            idx = input_file.index(":")
            physical_path = input_file[idx + 1:]
        
        try:
            # Vérifier que le fichier existe
            if not os.path.exists(physical_path):
                logger.error(f"Fichier source introuvable: {physical_path}")
                cur.close()
                conn.close()
                return None
            
            # Vérifier que c'est un fichier (pas un répertoire)
            if not os.path.isfile(physical_path):
                logger.error(f"Le chemin n'est pas un fichier (c'est un répertoire?): {physical_path}")
                logger.info(f"   Type: {type(input_file)}")
                if os.path.isdir(input_file):
                    logger.info(f"   C'est un répertoire. Liste des fichiers:")
                    try:
                        for item in os.listdir(input_file):
                            logger.info(f"      - {item}")
                    except Exception as e:
                        logger.error(f"   Erreur lecture répertoire: {e}")
                cur.close()
                conn.close()
                return None
            
            file_size = os.path.getsize(physical_path)
            logger.info(f"Taille du fichier: {file_size / (1024*1024):.2f} MB")
            logger.info(f"Fichier source vérifié: {physical_path} (existe: {os.path.exists(physical_path)}, est fichier: {os.path.isfile(physical_path)})")
            
            # Détecter l'EPSG source (ogrinfo accepte le préfixe GPKG: si présent)
            logger.info(f"Détection de l'EPSG source...")
            source_epsg = self._detect_source_epsg(input_file)
            
            # Construire la commande ogr2ogr de base (sans SHAPE_ENCODING: UTF-8 ou .cpg par défaut)
            cmd_base = [
                'ogr2ogr',
                '-f', 'PostgreSQL',
                f'PG:host={self.postgis_host} port={self.postgis_port} dbname={self.datagis_db} user={self.postgis_user} password={self.postgis_password}',
                input_file,
                '-nln', table_name,
                '-lco', 'GEOMETRY_NAME=the_geom',  # Utiliser the_geom pour datagis
                '-lco', 'FID=_id',
                '-lco', 'PRECISION=NO',  # Float8 au lieu de NUMERIC pour éviter overflow (ex. shape_area > 10^8)
                '-overwrite',
                '-skipfailures',  # Ignorer les entités en erreur (reproject, géométrie invalide) pour importer le reste
                '-nlt', 'PROMOTE_TO_MULTI'  # Promouvoir les géométries simples en multi
            ]
            
            cmd_display_base = [
                'ogr2ogr',
                '-f', 'PostgreSQL',
                f'PG:host={self.postgis_host} port={self.postgis_port} dbname={self.datagis_db} user={self.postgis_user} password=***',
                input_file,
                '-nln', table_name,
                '-lco', 'GEOMETRY_NAME=the_geom',
                '-lco', 'FID=_id',
                '-lco', 'PRECISION=NO',
                '-overwrite',
                '-skipfailures',
                '-nlt', 'PROMOTE_TO_MULTI'
            ]
            
            # Ajouter l'option SRID selon le cas
            if source_epsg is not None:
                # Cas A : Fichier a déjà un CRS correct (détecté)
                if source_epsg != 4326:
                    # Transformation réelle : toujours préciser source + cible
                    logger.info(f"   Cas A : Transformation depuis EPSG:{source_epsg} vers EPSG:4326")
                    cmd_base.extend(['-s_srs', f'EPSG:{source_epsg}', '-t_srs', 'EPSG:4326'])
                    cmd_display_base.extend(['-s_srs', f'EPSG:{source_epsg}', '-t_srs', 'EPSG:4326'])
                else:
                    # Déjà en 4326 : aucune reprojection. On ne passe PAS -s_srs seul,
                    # car GDAL exige alors -t_srs (« ERROR 5: if -s_srs is specified,
                    # -t_srs and/or -spat_srs must also be specified »). On n'ajoute donc
                    # aucune option SRS.
                    logger.info(f"   Cas A : Fichier déjà en EPSG:4326, pas de transformation nécessaire")
            else:
                # Cas B : Fichier n'a pas de CRS (ou CRS non détecté)
                # Assumer Lambert-93 (EPSG:2154) et transformer vers 4326
                # IMPORTANT : Utiliser -s_srs ET -t_srs, pas -a_srs (qui assigne juste la métadonnée)
                logger.info(f"    Cas B : EPSG source inconnu, assumption Lambert-93 (EPSG:2154) avec transformation vers EPSG:4326")
                logger.info(f"   Utilisation de -s_srs EPSG:2154 -t_srs EPSG:4326 (transformation réelle)")
                cmd_base.extend(['-s_srs', 'EPSG:2154', '-t_srs', 'EPSG:4326'])
                cmd_display_base.extend(['-s_srs', 'EPSG:2154', '-t_srs', 'EPSG:4326'])
            
            cmd = cmd_base
            cmd_display = cmd_display_base
            logger.info(f"Commande ogr2ogr complète:")
            logger.info(f"   {' '.join(cmd_display)}")
            logger.info(f"Exécution de ogr2ogr (timeout: 600s)...")
            
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                encoding='utf-8', errors='replace'
            )
            
            # Si échec avec "Non UTF-8 content" sur un shapefile, réessayer avec encodage CP1252 (Latin-1/Windows)
            if result.returncode != 0 and (result.stderr or '').lower().find('non utf-8') >= 0:
                if physical_path.lower().endswith('.shp'):
                    logger.info(f"   Réessai avec SHAPE_ENCODING=CP1252 (attributs .dbf probablement en Latin-1/Windows)")
                    cmd_retry = ['ogr2ogr', '--config', 'SHAPE_ENCODING', 'CP1252'] + cmd_base[1:]
                    result = subprocess.run(
                        cmd_retry, capture_output=True, text=True, timeout=600,
                        encoding='utf-8', errors='replace'
                    )
            
            if result.returncode == 0:
                logger.info(f"ogr2ogr terminé avec succès (code: {result.returncode})")
                
                # Reconnexion après ogr2ogr (peut prendre plusieurs minutes) pour éviter
                # "server closed the connection unexpectedly" / "cursor already closed"
                try:
                    try:
                        cur.close()
                        conn.close()
                    except Exception:
                        pass
                    conn = psycopg2.connect(
                        host=self.postgis_host,
                        port=self.postgis_port,
                        dbname=self.datagis_db,
                        user=self.postgis_user,
                        password=self.postgis_password,
                        connect_timeout=10
                    )
                    cur = conn.cursor()
                except Exception as reconnect_e:
                    logger.warning(f"Reconnexion après ogr2ogr: {reconnect_e}")
                    return table_name
                
                # Vérifier que la table a bien été créée
                try:
                    cur.execute(f"""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = 'public' 
                            AND table_name = '{table_name}'
                        );
                    """)
                    table_exists = cur.fetchone()[0]
                    
                    if table_exists:
                        # Récupérer des informations sur la table créée
                        cur.execute(f"""
                            SELECT 
                                f_geometry_column,
                                srid,
                                type
                            FROM geometry_columns
                            WHERE f_table_schema = 'public'
                            AND f_table_name = '{table_name}';
                        """)
                        geom_info = cur.fetchone()
                        
                        if geom_info:
                            geom_col, srid, geom_type = geom_info
                            logger.info(f"Informations de la table créée:")
                            logger.info(f"   Table: {table_name}")
                            logger.info(f"   Colonne géométrie: {geom_col}")
                            logger.info(f"   SRID: {srid}")
                            logger.info(f"   Type géométrie: {geom_type}")
                            
                            # Vérifier/corriger le SRID et détecter les faux 4326.
                            if not self._validate_srid(srid):
                                logger.warning(f"   SRID invalide détecté après import: {srid}")
                                logger.info(f"   Correction du SRID vers 4326...")
                                srid_validated = self._validate_and_fix_srid(
                                    cur, conn, table_name, geom_col, srid
                                )
                                if srid_validated != srid:
                                    logger.info(f"   SRID corrigé: {srid} → {srid_validated}")
                                    srid = srid_validated
                            else:
                                logger.info(f"   SRID valide: {srid}")
                                srid = self._repair_suspicious_wgs84_coordinates(
                                    cur, conn, table_name, geom_col, srid
                                )
                            
                            # Compter le nombre d'enregistrements
                            cur.execute(f"SELECT COUNT(*) FROM {table_name};")
                            record_count = cur.fetchone()[0]
                            logger.info(f"   Nombre d'enregistrements: {record_count}")
                        else:
                            logger.warning(f"Table créée mais aucune information de géométrie trouvée")
                    else:
                        logger.error(f"Table {table_name} n'existe pas après l'import")
                        cur.close()
                        conn.close()
                        return None
                except Exception as verify_e:
                    logger.warning(f"Erreur lors de la vérification de la table: {verify_e}")
                
                logger.info("=" * 80)
                logger.info(f"IMPORT DATAGIS RÉUSSI")
                logger.info(f"   Table: {table_name}")
                logger.info("=" * 80)
                
                # Enregistrer les métadonnées après import réussi (utiliser physical_path: pas de préfixe GPKG:/)
                if resource_id:
                    try:
                        file_path_obj = Path(physical_path)
                        file_hash = self._calculate_file_hash(file_path_obj)
                        file_size = file_path_obj.stat().st_size
                        file_mtime = file_path_obj.stat().st_mtime
                        
                        cur.execute("""
                            INSERT INTO datagis_import_metadata 
                            (table_name, resource_id, file_hash, file_size, file_mtime, import_date)
                            VALUES (%s, %s, %s, %s, to_timestamp(%s), CURRENT_TIMESTAMP)
                            ON CONFLICT (table_name) 
                            DO UPDATE SET 
                                file_hash = EXCLUDED.file_hash,
                                file_size = EXCLUDED.file_size,
                                file_mtime = EXCLUDED.file_mtime,
                                import_date = CURRENT_TIMESTAMP
                        """, (table_name, resource_id, file_hash, file_size, file_mtime))
                        conn.commit()
                        logger.info(f"Métadonnées enregistrées: hash={file_hash[:16]}..., size={file_size} bytes")
                    except Exception as e:
                        logger.warning(f"Erreur enregistrement métadonnées: {e}")
                        # Ne pas bloquer (connexion fermée après gros import, ou chemin invalide)
                
                try:
                    cur.close()
                    conn.close()
                except Exception:
                    pass
                return table_name
            else:
                logger.error(f"ogr2ogr a échoué (code: {result.returncode})")
                logger.error(f"   Commande exécutée: {' '.join(cmd_display)}")
                if result.stderr:
                    logger.error(f"   Erreur stderr (complet):")
                    # Logger toutes les lignes de stderr (pas seulement les 500 premiers caractères)
                    for line in result.stderr.split('\n'):
                        if line.strip():
                            logger.error(f"      {line}")
                if result.stdout:
                    logger.error(f"   Sortie stdout:")
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            logger.error(f"      {line}")
                cur.close()
                conn.close()
                return None
                
        except FileNotFoundError:
            logger.error("ogr2ogr non disponible dans le PATH")
            logger.info("Vérifiez que GDAL/OGR est installé: apt-get install gdal-bin")
            cur.close()
            conn.close()
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout lors de l'import ogr2ogr (délai: 600s)")
            logger.info(f"Le fichier est peut-être trop volumineux ou la connexion est lente")
            cur.close()
            conn.close()
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'exécution de ogr2ogr: {e}")
            import traceback
            logger.error(traceback.format_exc())
            cur.close()
            conn.close()
            return None
    
    def import_all(self) -> int:
        """
        Importe toutes les ressources géospatiales non-datastore dans datagis.
        Progression N/Total affichée dans les logs et via ligne parseable DATAGIS_PROGRESS: N/Total
        """
        resources = self.get_geospatial_resources()
        total = len(resources)
        imported = 0
        
        if total == 0:
            logger.info("Aucune ressource géospatiale à importer")
            return 0
        
        logger.info(f"Début import: {total} ressource(s) à traiter")
        # Ligne parseable pour l'UI (job écrit en Redis)
        print(f"DATAGIS_PROGRESS: 0/{total}", flush=True)
        
        for i, resource in enumerate(resources):
            current = i + 1
            resource_id = resource.get('id')
            resource_name = resource.get('name', 'N/A')
            resource_format = resource.get('format', 'N/A')
            
            logger.info(f"[{current}/{total}] Traitement ressource {resource_id}: {resource_name} ({resource_format})")
            print(f"DATAGIS_PROGRESS: {current}/{total}", flush=True)
            
            file_path = self.get_resource_file_path(resource)
            if not file_path:
                logger.warning(f"  [{current}/{total}] Fichier non trouvé pour {resource_name}, skip...")
                continue
            
            logger.info(f"[{current}/{total}] Import de {resource_name} ({resource_format}) depuis {file_path}")
            table_name = self.import_to_datagis(resource, file_path)
            if table_name:
                imported += 1
                logger.info(f"[{current}/{total}] Importé: {table_name}")
            else:
                logger.warning(f"  [{current}/{total}] Échec import pour {resource_name}")
        
        logger.info(f"Terminé: {imported}/{total} ressources importées dans datagis")
        print(f"DATAGIS_PROGRESS: {total}/{total}", flush=True)
        return imported


def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Importe les fichiers géospatiaux dans datagis')
    parser.add_argument('--resource-id', help='ID de la ressource spécifique à importer (optionnel)')
    parser.add_argument('--package-id', help='ID ou nom du dataset : importer uniquement les ressources géospatiales de ce dataset (optionnel)')
    parser.add_argument('--ckan-url', default=os.getenv('CKAN_URL', 'http://localhost:5000'),
                       help='URL de l\'API CKAN')
    parser.add_argument('--ckan-api-key', default=os.getenv('CKAN_API_KEY', ''),
                       help='Clé API CKAN')
    parser.add_argument('--postgis-host', default=os.getenv('POSTGIS_HOST', 'db'),
                       help='Host PostGIS')
    parser.add_argument('--postgis-port', type=int, default=int(os.getenv('POSTGIS_PORT', '5432')),
                       help='Port PostGIS')
    parser.add_argument('--datagis-db', default=os.getenv('DATAGIS_DB', 'datagis'),
                       help='Base de données datagis')
    parser.add_argument('--postgis-user', default=os.getenv('POSTGIS_USER', 'ckan'),
                       help='Utilisateur PostGIS')
    parser.add_argument('--postgis-password', default=os.getenv('POSTGIS_PASSWORD', ''),
                       help='Mot de passe PostGIS')
    parser.add_argument('--ckan-storage-path', default=os.getenv('CKAN_STORAGE_PATH', '/ckan_storage'),
                       help='Chemin vers ckan_storage')
    
    args = parser.parse_args()
    
    importer = DatagisImporter(
        ckan_url=args.ckan_url,
        ckan_api_key=args.ckan_api_key,
        postgis_host=args.postgis_host,
        postgis_port=args.postgis_port,
        datagis_db=args.datagis_db,
        postgis_user=args.postgis_user,
        postgis_password=args.postgis_password,
        ckan_storage_path=args.ckan_storage_path
    )
    
    # Si --resource-id est spécifié, importer seulement cette ressource
    if args.resource_id:
        logger.info(f"Import de la ressource spécifique: {args.resource_id}")
        # Récupérer la ressource depuis CKAN
        try:
            url = f"{args.ckan_url}/api/action/resource_show"
            headers = {'Authorization': args.ckan_api_key} if args.ckan_api_key else {}
            headers.setdefault('Content-Type', 'application/json')
            # CKAN API exige POST pour les actions (GET renvoie 400 Bad Request)
            response = requests.post(url, json={'id': args.resource_id}, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            if result.get('success') and result.get('result'):
                resource = result['result']
                # Récupérer le dataset pour avoir le nom
                package_url = f"{args.ckan_url}/api/action/package_show"
                package_response = requests.post(
                    package_url, json={'id': resource.get('package_id')}, headers=headers, timeout=30
                )
                if package_response.status_code == 200:
                    package_result = package_response.json()
                    if package_result.get('success'):
                        resource['dataset_name'] = package_result['result'].get('name')
                        resource['dataset_title'] = package_result['result'].get('title')
                
                # Importer cette ressource
                file_path = importer.get_resource_file_path(resource)
                if file_path and file_path.exists():
                    logger.info(f"Fichier trouvé: {file_path}")
                    table_name = importer.import_to_datagis(resource, file_path)
                    if table_name:
                        logger.info(f"Ressource {args.resource_id} importée dans datagis (table: {table_name})")
                        sys.exit(0)
                    else:
                        logger.error(f"Échec import ressource {args.resource_id}")
                        sys.exit(1)
                else:
                    logger.error(f"Fichier non trouvé pour ressource {args.resource_id}")
                    logger.info(f"Chemins vérifiés: {[str(p) for p in [importer.ckan_storage_path / 'resources' / args.resource_id, importer.ckan_storage_path / 'storage' / 'uploads' / args.resource_id]]}")
                    sys.exit(1)
            else:
                logger.error(f"Ressource {args.resource_id} non trouvée dans CKAN")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Erreur import ressource {args.resource_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)
    elif args.package_id:
        # Importer uniquement les ressources géospatiales de ce dataset
        logger.info(f"Import des ressources du dataset: {args.package_id}")
        try:
            package_url = f"{args.ckan_url}/api/action/package_show"
            headers = {'Authorization': args.ckan_api_key} if args.ckan_api_key else {}
            headers.setdefault('Content-Type', 'application/json')
            response = requests.post(
                package_url, json={'id': args.package_id}, headers=headers, timeout=30
            )
            response.raise_for_status()
            result = response.json()
            if not result.get('success') or not result.get('result'):
                logger.error(f"Dataset {args.package_id} non trouvé")
                sys.exit(1)
            package = result['result']
            resources = package.get('resources', [])
            dataset_name = package.get('name', args.package_id)
            # Filtrer ressources géospatiales non-datastore (même critères que get_geospatial_resources)
            to_import = []
            for res in resources:
                if res.get('datastore_active'):
                    continue
                if not importer._is_geospatial_resource_candidate(res):
                    continue
                res_url = res.get('url', '')
                url_type = res.get('url_type', '')
                if url_type == 'upload' or res_url:
                    res['dataset_name'] = dataset_name
                    res['dataset_title'] = package.get('title', dataset_name)
                    to_import.append(res)
            if not to_import:
                logger.info(f"Aucune ressource géospatiale à importer dans le dataset {args.package_id}")
                for res in resources[:10]:
                    logger.info(
                        "   - ressource=%s format=%r mimetype=%r url_type=%r url=%r datastore_active=%r",
                        res.get('name', 'N/A'),
                        res.get('format'),
                        res.get('mimetype'),
                        res.get('url_type'),
                        (res.get('url') or '')[:120],
                        res.get('datastore_active')
                    )
                sys.exit(0)
            logger.info(f"{len(to_import)} ressource(s) à importer pour le dataset {args.package_id}")
            imported = 0
            for i, resource in enumerate(to_import):
                current, total = i + 1, len(to_import)
                logger.info(f"[{current}/{total}] {resource.get('name', 'N/A')} ({resource.get('id', '')})")
                print(f"DATAGIS_PROGRESS: {current}/{total}", flush=True)
                file_path = importer.get_resource_file_path(resource)
                if not file_path or not file_path.exists():
                    logger.warning(f"  Fichier non trouvé, skip")
                    continue
                table_name = importer.import_to_datagis(resource, file_path)
                if table_name:
                    imported += 1
                    logger.info(f"[{current}/{total}] Importé: {table_name}")
                else:
                    logger.warning(f"  Échec import")
            logger.info(f"Terminé: {imported}/{len(to_import)} ressources importées pour le dataset {args.package_id}")
            print(f"DATAGIS_PROGRESS: {len(to_import)}/{len(to_import)}", flush=True)
            sys.exit(0 if imported > 0 else 1)
        except Exception as e:
            logger.error(f"Erreur import dataset {args.package_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            sys.exit(1)
    else:
        # Importer toutes les ressources géospatiales
        count = importer.import_all()
        sys.exit(0 if count > 0 else 1)


if __name__ == '__main__':
    main()

