#!/usr/bin/env python3
"""
Script pour générer automatiquement des mapfiles MapServer depuis les datasets CKAN

Ce script génère des fichiers .map MapServer pour chaque dataset géospatial CKAN,
en se connectant à PostGIS (via CKAN datastore).
"""

import os
import re
import sys
import logging
import traceback
import time
import requests
import subprocess
import shutil
import json
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_sld_style_for_resource(dataset: dict, resource_id: str, resource: dict,
                                resource_fallback: Optional[dict] = None) -> Optional[str]:
    """
    Extrait le SLD des extras de la ressource (clé sld_style) ou du dataset (sld_style_<resource_id>).
    resource_fallback: ressource complète (ex. depuis resource_show) si les extras ne sont pas dans resource.
    """
    if not resource_id:
        return None
    # Utiliser la ressource enrichie (extras) en priorité si fournie
    r = resource_fallback if resource_fallback is not None else resource
    # Extras ressource (liste ou dict)
    extras = r.get('extras') or []
    if isinstance(extras, dict):
        val = extras.get('sld_style')
        if val and str(val).strip():
            return str(val)
    else:
        for e in extras:
            if isinstance(e, dict) and e.get('key') == 'sld_style':
                val = e.get('value')
                if val and str(val).strip():
                    return str(val)
                break
    # Fallback: extras dataset (sld_style_<resource_id>)
    extras = dataset.get('extras') or []
    key = f'sld_style_{resource_id}'
    if isinstance(extras, dict):
        val = extras.get(key)
        if val and str(val).strip():
            return str(val)
    else:
        for e in extras:
            if isinstance(e, dict) and e.get('key') == key:
                val = e.get('value')
                if val and str(val).strip():
                    return str(val)
                break
    return None


try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    logger.warning("psycopg2 non disponible, création automatique de géométrie désactivée")


class MapfileGenerator:
    """Générateur de mapfiles MapServer pour datasets CKAN"""
    
    def _save_ogc_metadata_to_extras(self, dataset_id: str, source_type: str, 
                                     table_name: str = None, geom_column: str = None,
                                     srid: int = None, bbox: List[float] = None) -> bool:
        """
        Enregistre les métadonnées OGC dans les extras du dataset
        
        Args:
            dataset_id: ID du dataset CKAN
            source_type: Type de source ('datagis', 'datastore', 'ogr_local', 'ogr_remote')
            table_name: Nom de la table (pour datagis/datastore)
            geom_column: Nom de la colonne géométrie
            srid: SRID de la géométrie
            bbox: Bounding box [minx, miny, maxx, maxy]
            
        Returns:
            True si succès, False sinon
        """
        try:
            url = f"{self.ckan_url}/api/action/package_patch"

            # IMPORTANT : package_patch avec 'extras' REMPLACE toute la liste. On récupère
            # donc les extras existants et on ne remplace QUE les clés ogc:* gérées ici, afin
            # de préserver dolfin_mapping, les métadonnées de dépôt, la validité, etc.
            existing = []
            try:
                show = requests.get(f"{self.ckan_url}/api/action/package_show",
                                    params={'id': dataset_id}, headers=self.headers, timeout=10)
                if show.status_code == 200 and show.json().get('success'):
                    existing = show.json()['result'].get('extras', []) or []
            except Exception as e:
                logger.warning(f"   package_show extras (fusion) échec, on continue: {e}")

            OGC_KEYS = {'ogc:source', 'ogc:table', 'ogc:geom_column', 'ogc:srid', 'ogc:bbox'}
            extras = [e for e in existing if e.get('key') not in OGC_KEYS]

            # (Re)poser les métadonnées OGC
            extras.append({'key': 'ogc:source', 'value': source_type})
            if table_name:
                extras.append({'key': 'ogc:table', 'value': table_name})
            if geom_column:
                extras.append({'key': 'ogc:geom_column', 'value': geom_column})
            if srid:
                extras.append({'key': 'ogc:srid', 'value': str(srid)})
            if bbox and len(bbox) >= 4:
                extras.append({'key': 'ogc:bbox', 'value': json.dumps(bbox)})

            payload = {
                'id': dataset_id,
                'extras': extras
            }
            
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    logger.info(f"Métadonnées OGC enregistrées pour dataset {dataset_id}")
                    logger.info(f"   Source: {source_type}, Table: {table_name}, SRID: {srid}")
                    return True
                else:
                    logger.warning(f"Erreur API CKAN lors de l'enregistrement des métadonnées OGC: {result.get('error')}")
            else:
                logger.warning(f"Erreur HTTP {response.status_code} lors de l'enregistrement des métadonnées OGC")
            return False
        except Exception as e:
            logger.warning(f"Erreur lors de l'enregistrement des métadonnées OGC: {e}")
            return False
    
    def _validate_mapfile(self, mapfile_path: str, source_type: str = None, 
                         table_name: str = None, data_source: str = None) -> bool:
        """
        Valide qu'un mapfile est utilisable (existe, datasource accessible, table existe)
        
        Args:
            mapfile_path: Chemin du mapfile
            source_type: Type de source ('datagis', 'datastore', 'ogr_local', 'ogr_remote')
            table_name: Nom de la table (pour PostGIS)
            data_source: Source de données OGR (pour OGR)
            
        Returns:
            True si le mapfile est valide, False sinon
        """
        # 1. Vérifier que le mapfile existe
        if not os.path.exists(mapfile_path):
            logger.debug(f"Mapfile non trouvé: {mapfile_path}")
            return False
        
        # 2. Si c'est PostGIS (datagis ou datastore), vérifier que la table existe
        if source_type in {'datagis', 'datastore'} and table_name:
            if not HAS_PSYCOPG2:
                logger.debug(f"psycopg2 non disponible, impossible de valider la table")
                return False
            
            try:
                db_name = self.datagis_db if source_type == 'datagis' else self.postgis_db
                conn = psycopg2.connect(
                    host=self.postgis_host,
                    port=self.postgis_port,
                    dbname=db_name,
                    user=self.postgis_user,
                    password=self.postgis_password
                )
                cur = conn.cursor()
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 
                        FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = %s
                    );
                """, (table_name,))
                result = cur.fetchone()
                table_exists = result and len(result) > 0 and result[0] if result else False
                cur.close()
                conn.close()
                
                if not table_exists:
                    logger.debug(f"Table PostGIS non trouvée: {table_name}")
                    return False
            except Exception as e:
                logger.debug(f"Erreur validation table PostGIS: {e}")
                return False
        
        # 3. Si c'est OGR, vérifier que la datasource est accessible
        if source_type in {'ogr_local', 'ogr_remote'} and data_source:
            if data_source.startswith('/vsizip/'):
                # Pour /vsizip/, extraire le chemin du ZIP
                vsizip_part = data_source.replace('/vsizip/', '')
                if '/' in vsizip_part:
                    zip_path = vsizip_part.split('/', 1)[0]
                else:
                    zip_path = vsizip_part
                
                if not os.path.exists(zip_path):
                    logger.debug(f"ZIP OGR non trouvé: {zip_path}")
                    return False
                
                # Vérifier que le ZIP est valide
                try:
                    import zipfile
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.testzip()
                except Exception as e:
                    logger.debug(f"ZIP OGR invalide: {zip_path}, erreur: {e}")
                    return False
            elif ':' in data_source:
                # Pour les formats avec préfixe (GeoJSON:, GPKG:, KML:)
                file_path = data_source.split(':', 1)[1]
                if not os.path.exists(file_path):
                    logger.debug(f"Fichier OGR non trouvé: {file_path}")
                    return False
            else:
                # Chemin direct
                if not os.path.exists(data_source):
                    logger.debug(f"Fichier OGR non trouvé: {data_source}")
                    return False
            
            # Pour les URLs distantes, faire un test d'ouverture léger
            if source_type == 'ogr_remote' and data_source.startswith('http'):
                try:
                    response = requests.head(data_source, timeout=5, allow_redirects=True)
                    if response.status_code != 200:
                        logger.debug(f"URL OGR distante non accessible: {data_source} (status: {response.status_code})")
                        return False
                except Exception as e:
                    logger.debug(f"Erreur test URL OGR distante: {data_source}, erreur: {e}")
                    return False
        
        return True
    
    def _cleanup_invalid_ogr_mapfile(self, mapfile_path: str) -> bool:
        """
        Nettoie un mapfile OGR invalide (fichier datasource inexistant)
        
        Args:
            mapfile_path: Chemin du mapfile
            
        Returns:
            True si le mapfile a été supprimé, False sinon
        """
        if not os.path.exists(mapfile_path):
            return False
        
        try:
            # Lire le mapfile pour détecter si c'est OGR
            with open(mapfile_path, 'r', encoding='utf-8') as f:
                mapfile_content = f.read()
            
            # Vérifier si c'est un mapfile OGR
            if 'CONNECTIONTYPE OGR' not in mapfile_content:
                # Ce n'est pas un mapfile OGR, ne rien faire
                return False
            
            # Extraire la datasource OGR
            import re
            connection_match = re.search(r'CONNECTIONTYPE OGR\s+CONNECTION\s+"([^"]+)"', mapfile_content, re.MULTILINE)
            if not connection_match:
                # Impossible d'extraire la datasource, ne rien faire
                logger.warning(f"Impossible d'extraire la datasource OGR du mapfile: {mapfile_path}")
                return False
            
            data_source = connection_match.group(1)
            
            # Vérifier que le fichier existe
            file_to_check = None
            if data_source.startswith('/vsizip/'):
                # Pour /vsizip/, extraire le chemin du ZIP
                vsizip_part = data_source.replace('/vsizip/', '')
                if '/' in vsizip_part:
                    zip_path = vsizip_part.split('/', 1)[0]
                else:
                    zip_path = vsizip_part
                file_to_check = zip_path
            elif ':' in data_source:
                # Pour les formats avec préfixe (GeoJSON:, GPKG:, KML:)
                file_to_check = data_source.split(':', 1)[1]
            else:
                # Chemin direct
                file_to_check = data_source
            
            # Vérifier que le fichier existe
            if file_to_check and not os.path.exists(file_to_check):
                logger.warning(f"Fichier OGR datasource non trouvé: {file_to_check}")
                logger.warning(f"   Suppression du mapfile invalide: {mapfile_path}")
                try:
                    os.unlink(mapfile_path)
                    logger.info(f"Mapfile OGR invalide supprimé: {mapfile_path}")
                    return True
                except Exception as e:
                    logger.error(f"Erreur lors de la suppression du mapfile: {e}")
                    return False
            
            # Pour /vsizip/, vérifier aussi que le ZIP est valide
            if data_source.startswith('/vsizip/') and file_to_check:
                try:
                    import zipfile
                    with zipfile.ZipFile(file_to_check, 'r') as zip_ref:
                        zip_ref.testzip()
                except Exception as e:
                    logger.warning(f"ZIP OGR invalide: {file_to_check}, erreur: {e}")
                    logger.warning(f"   Suppression du mapfile invalide: {mapfile_path}")
                    try:
                        os.unlink(mapfile_path)
                        logger.info(f"Mapfile OGR invalide supprimé: {mapfile_path}")
                        return True
                    except Exception as e2:
                        logger.error(f"Erreur lors de la suppression du mapfile: {e2}")
                        return False
            
            # Le fichier existe, le mapfile est valide
            return False
            
        except Exception as e:
            logger.warning(f"Erreur lors de la vérification du mapfile OGR: {mapfile_path}, erreur: {e}")
            return False
    
    def __init__(self, ckan_url: str, ckan_api_key: str, 
                 postgis_host: str = 'db', postgis_port: int = 5432,
                 postgis_db: str = 'datastore', postgis_user: str = 'ckan',
                 postgis_password: str = '', mapfiles_dir: str = '/mapserver/mapfiles',
                 auto_create_geometry: bool = True,
                 datagis_db: str = 'datagis', use_datagis: bool = True,
                 ckan_storage_path: str = '/ckan_storage'):
        """
        Initialise le générateur de mapfiles
        
        Args:
            ckan_url: URL de l'API CKAN
            ckan_api_key: Clé API CKAN
            postgis_host: Host PostGIS
            postgis_port: Port PostGIS
            postgis_db: Base de données PostGIS
            postgis_user: Utilisateur PostGIS
            postgis_password: Mot de passe PostGIS
            mapfiles_dir: Répertoire où sauvegarder les mapfiles
            ckan_storage_path: Chemin vers ckan_storage
        """
        self.ckan_url = ckan_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.postgis_host = postgis_host
        self.postgis_port = postgis_port
        self.postgis_db = postgis_db
        self.postgis_user = postgis_user
        self.postgis_password = postgis_password
        self.mapfiles_dir = Path(mapfiles_dir)
        self.ckan_storage_path = Path(ckan_storage_path)
        # Ne créer le répertoire que s'il n'existe pas et si on a les permissions
        # (le répertoire peut être un volume monté depuis l'hôte)
        try:
            if not self.mapfiles_dir.exists():
                self.mapfiles_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, FileNotFoundError):
            # Le répertoire peut être un volume monté, on continue quand même
            # Si le répertoire n'est pas encore monté ou non accessible, on continuera
            # et l'erreur sera gérée lors de l'écriture du mapfile
            pass
        self.auto_create_geometry = auto_create_geometry
        self.datagis_db = datagis_db
        self.use_datagis = use_datagis
        
        # Headers pour les requêtes CKAN
        self.headers = {'Authorization': self.ckan_api_key} if self.ckan_api_key else {}
        
        # Cache pour mutualiser les résultats datastore_search (évite les appels redondants)
        self._datastore_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        
        # Cache persistant (fichier JSON) pour éviter les appels répétés entre instances
        self._cache_file = Path("/tmp/ckan_datastore_cache.json")
        self._cache_ttl = 3600  # TTL de 1 heure pour le cache persistant
        self._load_persistent_cache()
        
        # Métriques de performance
        self._metrics = {
            'cache_hits': 0,
            'cache_misses': 0,
            'api_calls': 0,
            'total_time': 0.0,
            'slow_queries': []  # Queries > 5s
        }
        
        logger.info(f"MapfileGenerator initialized. Auto-create geometry: {self.auto_create_geometry}")
    
    def _load_persistent_cache(self):
        """Charge le cache persistant depuis le fichier JSON"""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)
                    # Filtrer les entrées expirées
                    current_time = time.time()
                    for resource_id, entry in list(cache_data.items()):
                        if entry.get('timestamp', 0) + self._cache_ttl > current_time:
                            self._datastore_cache[resource_id] = entry.get('data')
                        else:
                            # Cache expiré, supprimer
                            del cache_data[resource_id]
                    # Sauvegarder le cache nettoyé
                    if cache_data != json.load(open(self._cache_file, 'r')):
                        with open(self._cache_file, 'w') as f:
                            json.dump(cache_data, f)
                    logger.debug(f"Cache persistant chargé: {len(self._datastore_cache)} entrées valides")
        except Exception as e:
            logger.warning(f"Erreur chargement cache persistant: {e}")
    
    def _save_persistent_cache(self, resource_id: str, data: Optional[Dict[str, Any]]):
        """Sauvegarde une entrée dans le cache persistant"""
        try:
            cache_data = {}
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)
            
            cache_data[resource_id] = {
                'data': data,
                'timestamp': time.time()
            }
            
            with open(self._cache_file, 'w') as f:
                json.dump(cache_data, f)
        except Exception as e:
            logger.debug(f"Erreur sauvegarde cache persistant: {e}")
    
    def _get_cached_fields(self, resource_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Récupère les champs depuis le cache (mémoire ou persistant)
        
        Returns:
            Liste des champs ou None si non trouvé
        """
        # Vérifier le cache mémoire d'abord
        if resource_id in self._datastore_cache:
            cached_result = self._datastore_cache[resource_id]
            if cached_result is not None:
                self._metrics['cache_hits'] += 1
                return cached_result.get('fields', [])
            return None
        
        # Vérifier le cache persistant
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)
                    if resource_id in cache_data:
                        entry = cache_data[resource_id]
                        current_time = time.time()
                        if entry.get('timestamp', 0) + self._cache_ttl > current_time:
                            data = entry.get('data')
                            if data is not None:
                                # Mettre à jour le cache mémoire
                                self._datastore_cache[resource_id] = data
                                self._metrics['cache_hits'] += 1
                                return data.get('fields', [])
        except Exception as e:
            logger.debug(f"Erreur lecture cache persistant: {e}")
        
        self._metrics['cache_misses'] += 1
        return None
    
    def _datastore_search_with_retry(self, resource_id: str, max_retries: int = 3, 
                                     initial_timeout: int = 5, backoff_factor: float = 1.5) -> Optional[Dict[str, Any]]:
        """
        Effectue un appel datastore_search avec retry/backoff pour gérer les CKAN en démarrage
        
        Args:
            resource_id: ID de la ressource
            max_retries: Nombre maximum de tentatives (défaut: 3)
            initial_timeout: Timeout initial en secondes (défaut: 5)
            backoff_factor: Facteur de backoff exponentiel (défaut: 1.5)
            
        Returns:
            Résultat de datastore_search ou None si échec
        """
        start_time = time.time()
        
        # Vérifier le cache mémoire d'abord
        if resource_id in self._datastore_cache:
            cached_result = self._datastore_cache[resource_id]
            if cached_result is not None:
                logger.debug(f"Cache hit (mémoire) pour datastore_search({resource_id})")
                self._metrics['cache_hits'] += 1
                return cached_result
        
        # Vérifier le cache persistant
        cached_fields = self._get_cached_fields(resource_id)
        if cached_fields is not None:
            # Reconstruire le résultat depuis le cache
            cached_result = {'fields': cached_fields}
            self._datastore_cache[resource_id] = cached_result
            logger.debug(f"Cache hit (persistant) pour datastore_search({resource_id})")
            return cached_result
        
        url = f"{self.ckan_url}/api/action/datastore_search"
        params = {'resource_id': resource_id, 'limit': 0}
        
        timeout = initial_timeout
        last_exception = None
        
        self._metrics['cache_misses'] += 1
        
        for attempt in range(max_retries):
            try:
                self._metrics['api_calls'] += 1
                response = requests.get(url, params=params, headers=self.headers, timeout=timeout)
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('success'):
                        # Mettre en cache le résultat (mémoire et persistant)
                        cache_result = result.get('result', {})
                        self._datastore_cache[resource_id] = cache_result
                        self._save_persistent_cache(resource_id, cache_result)
                        
                        # Enregistrer les métriques de performance
                        elapsed = time.time() - start_time
                        self._metrics['total_time'] += elapsed
                        if elapsed > 5.0:
                            self._metrics['slow_queries'].append({
                                'resource_id': resource_id,
                                'time': elapsed,
                                'timestamp': time.time()
                            })
                            logger.warning(f"Requête lente: datastore_search({resource_id}) a pris {elapsed:.2f}s")
                        
                        return cache_result
                    else:
                        # Erreur API mais réponse valide
                        logger.debug(f"datastore_search échoué pour {resource_id}: {result.get('error', 'Unknown error')}")
                        self._datastore_cache[resource_id] = None
                        return None
                elif response.status_code == 404:
                    # Datastore pas encore créé (normal pour les ressources en cours de traitement)
                    logger.debug(f"Datastore pas encore créé pour ressource {resource_id} (404)")
                    self._datastore_cache[resource_id] = None
                    return None
                elif response.status_code in [502, 503, 504]:
                    # Erreur serveur temporaire, retry
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor ** attempt
                        logger.debug(f"Erreur serveur {response.status_code} pour {resource_id}, retry {attempt + 1}/{max_retries} dans {wait_time:.1f}s...")
                        time.sleep(wait_time)
                        timeout = int(timeout * backoff_factor)  # Augmenter le timeout aussi
                        continue
                    else:
                        logger.warning(f"Erreur serveur {response.status_code} pour {resource_id} après {max_retries} tentatives")
                        self._datastore_cache[resource_id] = None
                        return None
                else:
                    # Autre erreur HTTP, ne pas retry
                    logger.debug(f"Erreur HTTP {response.status_code} pour {resource_id}")
                    self._datastore_cache[resource_id] = None
                    return None
                    
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.debug(f"Timeout pour {resource_id}, retry {attempt + 1}/{max_retries} dans {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    timeout = int(timeout * backoff_factor)
                    continue
                else:
                    logger.debug(f"Timeout pour {resource_id} après {max_retries} tentatives")
                    self._datastore_cache[resource_id] = None
                    return None
            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = backoff_factor ** attempt
                    logger.debug(f"Erreur connexion pour {resource_id}: {e}, retry {attempt + 1}/{max_retries} dans {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    timeout = int(timeout * backoff_factor)
                    continue
                else:
                    logger.debug(f"Erreur connexion pour {resource_id} après {max_retries} tentatives: {e}")
                    self._datastore_cache[resource_id] = None
                    return None
        
        # Si on arrive ici, toutes les tentatives ont échoué
        self._datastore_cache[resource_id] = None
        return None
    
    def get_package_details(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère les détails d'un dataset spécifique depuis CKAN
        (inclut les ressources ; les extras ressource peuvent nécessiter resource_show selon l'API).
        
        Args:
            dataset_id: Nom ou ID du dataset
            
        Returns:
            Dictionnaire du dataset ou None si non trouvé
        """
        try:
            url = f"{self.ckan_url}/api/action/package_show"
            params = {'id': dataset_id}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                logger.error(f"Erreur API CKAN: {result.get('error', {})}")
                return None
            
            return result.get('result')
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur récupération dataset {dataset_id}: {e}")
            return None
    
    def get_resource_details(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère les détails d'une ressource depuis l'API CKAN (resource_show),
        notamment les extras (ex. sld_style) qui ne sont pas toujours inclus dans package_show.
        
        Args:
            resource_id: ID de la ressource
            
        Returns:
            Dictionnaire de la ressource (avec extras) ou None si non trouvé
        """
        if not resource_id or not self.ckan_url:
            return None
        try:
            url = f"{self.ckan_url}/api/action/resource_show"
            params = {'id': resource_id}
            response = requests.get(url, params=params, headers=self.headers, timeout=5)
            if response.status_code != 200:
                return None
            result = response.json()
            if not result.get('success'):
                return None
            return result.get('result')
        except Exception as e:
            logger.debug(f"resource_show {resource_id}: {e}")
            return None
    
    def get_geospatial_datasets(self) -> List[Dict[str, Any]]:
        """
        Récupère tous les datasets géospatiaux depuis CKAN avec pagination
        
        Returns:
            Liste des datasets géospatiaux
        """
        try:
            # Récupérer TOUS les datasets avec pagination (CKAN limite à 1000 par page)
            url = f"{self.ckan_url}/api/action/package_search"
            all_datasets = []
            start = 0
            rows = 1000  # Maximum par page dans CKAN
            
            while True:
                params = {'rows': rows, 'start': start}
                logger.info(f"Récupération des datasets (start={start}, rows={rows})...")
                
                response = requests.get(url, params=params, headers=self.headers, timeout=60)
                response.raise_for_status()
                
                result = response.json()
                if not result.get('success'):
                    logger.error(f"Erreur API CKAN: {result}")
                    break
                
                page_datasets = result.get('result', {}).get('results', [])
                count = result.get('result', {}).get('count', 0)
                
                if not page_datasets:
                    break
                
                all_datasets.extend(page_datasets)
                logger.info(f"   {len(page_datasets)} dataset(s) récupéré(s) (total: {len(all_datasets)}/{count})")
                
                # Si on a récupéré tous les datasets, arrêter
                if len(all_datasets) >= count or len(page_datasets) < rows:
                    break
                
                start += rows
            
            logger.info(f"{len(all_datasets)} dataset(s) récupéré(s) depuis CKAN")
            
            geospatial_datasets = []
            datasets_checked = 0
            
            for dataset in all_datasets:
                datasets_checked += 1
                if datasets_checked % 100 == 0:
                    logger.info(f"   Vérification en cours: {datasets_checked}/{len(all_datasets)} datasets...")
                
                if self._is_geospatial_dataset(dataset):
                    geospatial_datasets.append(dataset)
                    logger.info(f"Dataset géospatial trouvé: {dataset.get('name')}")
            
            logger.info(f"Total: {len(geospatial_datasets)} datasets géospatiaux détectés sur {len(all_datasets)} datasets")
            return geospatial_datasets
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des datasets: {e}")
            return []
    
    def _is_geospatial_dataset(self, dataset: Dict[str, Any]) -> bool:
        """
        Vérifie si un dataset contient des données géospatiales
        Optimisé pour arrêter dès qu'un signal fort est trouvé et mutualiser les appels datastore_search
        
        Args:
            dataset: Dictionnaire du dataset
            
        Returns:
            True si géospatial, False sinon
        """
        dataset_name = dataset.get('name', '')
        
        # SIGNAL FORT 1: Vérifier d'abord les métadonnées spatiales dans les extras (priorité, pas d'appel API)
        extras = dataset.get('extras', [])
        for extra in extras:
            if isinstance(extra, dict):
                key = extra.get('key', '')
                value = extra.get('value', '')
                if key in ['spatial', 'spatial_text', 'spatial_uri'] and value:
                    logger.debug(f"Dataset {dataset_name} géospatial détecté via métadonnées spatiales (extras)")
                    return True
        
        # SIGNAL FORT 2: Vérifier aussi dans dataset directement (pas d'appel API)
        if dataset.get('spatial') or dataset.get('spatial_text') or dataset.get('spatial_uri'):
            logger.debug(f"Dataset {dataset_name} géospatial détecté via métadonnées spatiales (direct)")
            return True
        
        # FILTRE NON-GÉO: Exclure les datasets non-vectoriels (sauf rasters supportés)
        # Rasters supportés pour mapfiles: tif, tiff, jp2, img, asc → génèrent des LAYER TYPE RASTER
        resources = dataset.get('resources', [])
        non_vector_formats = {
            'ORTHOPHOTO', 'BD ORTHO', 'ORTHO', 'RASTER', 'WMS', 'WMTS', 'WFS',
            'ECW', 'GTFS', 'PNG', 'JPG', 'JPEG', 'GIF', 'BMP', 'WEBP'
        }
        # Formats qui excluent (non-vector ET non-raster-supporté)
        non_vector_exclude = non_vector_formats - self.RASTER_FORMATS_SET
        
        # SIGNAL RASTER: au moins une ressource raster supportée → dataset géospatial (rasters en dernier dans le mapfile)
        for resource in resources:
            format_ = resource.get('format', '').upper()
            if format_ in self.RASTER_FORMATS_SET:
                logger.debug(f"Dataset {dataset_name} géospatial détecté via format raster: {format_}")
                return True
        
        dataset_title = dataset.get('title', '').upper()
        dataset_name_upper = dataset_name.upper()
        
        # Vérifier dans le titre et le nom du dataset (exclure si termes non-géo, sauf si déjà inclus par raster)
        for non_vector_term in ['ORTHOPHOTO', 'BD ORTHO', 'ORTHO', 'WMS', 'WMTS', 'GTFS']:
            if non_vector_term in dataset_title or non_vector_term in dataset_name_upper:
                logger.debug(f"Dataset {dataset_name} exclu (format non-vectoriel détecté: {non_vector_term})")
                return False
        
        # Vérifier dans les formats des ressources (exclure seulement les formats non-géo et non-raster)
        for resource in resources:
            format_ = resource.get('format', '').upper()
            if format_ in non_vector_exclude:
                logger.debug(f"Dataset {dataset_name} exclu (format non-vectoriel: {format_})")
                return False
        
        # SIGNAL FORT 3: Vérifier les formats géospatiaux vectoriels explicites (pas d'appel API)
        geospatial_formats = {'GEOJSON', 'SHP', 'SHAPEFILE', 'KML', 'KMZ', 'GPKG', 'GEOPACKAGE'}
        for resource in resources:
            format_ = resource.get('format', '').upper()
            if format_ in geospatial_formats:
                logger.debug(f"Dataset {dataset_name} géospatial détecté via format: {format_}")
                return True  # Arrêter dès qu'un format géospatial est trouvé
        
        # SIGNAL MOYEN: Vérifier les ressources datastore avec géométrie (nécessite appel API)
        # Limiter le nombre de ressources testées pour optimiser
        # Arrêter dès qu'une ressource géospatiale est trouvée
        csv_resources = []
        datastore_resources = []
        
        for resource in resources:
            format_ = resource.get('format', '').upper()
            if format_ == 'CSV':
                csv_resources.append(resource)
            elif resource.get('datastore_active'):
                datastore_resources.append(resource)
        
        # Tester d'abord les ressources datastore actives (plus rapide, signal plus fort)
        for resource in datastore_resources[:3]:  # Limiter à 3 ressources max
            if self._has_geometry_column(resource):
                logger.debug(f"Dataset {dataset_name} géospatial détecté via ressource datastore active")
                return True
        
        # Ensuite tester les CSV (peut nécessiter plus de temps)
        for resource in csv_resources[:2]:  # Limiter à 2 CSV max
            resource_id = resource.get('id')
            if not resource_id:
                continue
            
            # Utiliser la fonction avec retry/backoff et cache
            datastore_result = self._datastore_search_with_retry(resource_id, max_retries=2, initial_timeout=3)
            if datastore_result:
                fields = datastore_result.get('fields', [])
                field_names = [f.get('id', '').lower() for f in fields]
                
                # Vérifier les colonnes géométrie PostGIS (utiliser le cache)
                if self._has_geometry_column_from_cache(resource_id, fields):
                    logger.debug(f"Dataset {dataset_name} géospatial détecté via CSV avec colonne géométrie")
                    return True
                
                # Vérifier les colonnes GeoJSON (même si ce sont des colonnes text)
                geojson_indicators = ['st_asgeojson', 'geo_point_2d', 'geopoint2d', 'geo_shape', 'geojson', 'geo_point', 'geopoint']
                if any(indicator in field_names for indicator in geojson_indicators):
                    logger.info(f"Dataset {dataset_name} géospatial détecté via colonne GeoJSON (st_asgeojson/geo_point_2d/geo_shape)")
                    return True
        
        # SIGNAL FAIBLE: Vérifier datagis (dernier recours, peut être lent)
        # IMPORTANT: Cette vérification est cruciale car beaucoup de datasets ont des tables dans datagis
        # mais n'ont pas de métadonnées spatiales ou de formats géospatiaux explicites
        if self.use_datagis:
            try:
                # Chercher par res_{resource_id} pour TOUTES les ressources
                # Ne pas utiliser _find_table_in_datagis() qui cherche par nom de dataset
                resources = dataset.get('resources', [])
                if not resources:
                    return False
                
                # Parcourir toutes les ressources (ne pas limiter)
                for resource in resources:
                    resource_id = resource.get('id')
                    if not resource_id:
                        continue
                    
                    resource_id_clean = resource_id.replace('-', '_')
                    resource_id_clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in resource_id_clean)
                    expected_table_name = f"res_{resource_id_clean}"
                    
                    if len(expected_table_name) > 63:
                        max_id_length = 63 - 4
                        expected_table_name = f"res_{resource_id_clean[:max_id_length]}"
                    
                    datagis_table = self._find_table_in_datagis_by_name(expected_table_name)
                    if datagis_table:
                        logger.info(f"Dataset {dataset_name} géospatial détecté via datagis (table: {datagis_table['table_name']}, ressource: {resource_id})")
                        return True
            except Exception as e:
                # Logger l'erreur mais ne pas bloquer la détection
                logger.debug(f"Erreur lors de la vérification datagis pour {dataset_name}: {e}")
        
        return False
    
    def _has_geometry_column_from_cache(self, resource_id: str, fields: List[Dict[str, Any]]) -> bool:
        """
        Vérifie si une ressource a une colonne géométrie en utilisant les champs déjà en cache
        (version optimisée qui évite un nouvel appel API)
        
        Args:
            resource_id: ID de la ressource
            fields: Liste des champs depuis datastore_search (déjà en cache)
            
        Returns:
            True si géométrie présente, False sinon
        """
        field_names = [f.get('id', '').lower() for f in fields]
        
        # Vérifier les colonnes géométrie PostGIS (inclure les variantes comme geo_point_2d, geopoint2d, st_asgeojson)
        geometry_fields = ['geometry', 'geom', 'the_geom', 'shape', 'coordinates', 'geo_shape', 'geo_point', 'geo_point_2d', 'geopoint2d', 'geopoint', 'coord', 'st_asgeojson', 'geojson']
        for field_name in field_names:
            # Vérifier si le nom du champ contient un mot-clé géométrique
            # Accepter aussi les champs qui contiennent 'geo' ou 'geom' (comme st_asgeojson, geo_point_2d, geopoint2d)
            if any(geom_field in field_name for geom_field in geometry_fields) or 'geo' in field_name or 'geom' in field_name:
                logger.debug(f"Colonne géospatiale détectée: {field_name} (ressource {resource_id})")
                return True
        
        # Vérifier les colonnes lat/lon (coordonnées)
        has_lat = any(field in field_names for field in ['latitude', 'lat', 'y', 'coord_y'])
        has_lon = any(field in field_names for field in ['longitude', 'lon', 'x', 'coord_x'])
        
        if has_lat and has_lon:
            logger.debug(f"Ressource {resource_id} a des colonnes lat/lon mais pas de colonne géométrie PostGIS")
            return True  # Considérer comme géospatial si lat/lon présents
        
        return False
            
    def _has_geometry_column(self, resource: Dict[str, Any]) -> bool:
        """
        Vérifie si une ressource datastore a une colonne géométrie ou des colonnes lat/lon
        Utilise le cache pour éviter les appels redondants
        
        Args:
            resource: Dictionnaire de la ressource
            
        Returns:
            True si géométrie présente, False sinon
        """
        resource_id = resource.get('id')
        if not resource_id:
            return False
        
        # Utiliser le cache si disponible
        if resource_id in self._datastore_cache:
            cached_result = self._datastore_cache[resource_id]
            if cached_result is not None:
                fields = cached_result.get('fields', [])
                return self._has_geometry_column_from_cache(resource_id, fields)
            else:
                # Cache indique que datastore n'existe pas ou erreur
                return False
        
        # Sinon, faire l'appel avec retry/backoff (et mettre en cache)
        datastore_result = self._datastore_search_with_retry(resource_id, max_retries=2, initial_timeout=5)
        if datastore_result:
            fields = datastore_result.get('fields', [])
            return self._has_geometry_column_from_cache(resource_id, fields)
        
            return False
    
    def get_datastore_table_name(self, resource_id: str) -> Optional[str]:
        """
        Récupère le nom de la table PostGIS pour une ressource datastore
        Vérifie réellement quelle table existe dans la base de données
        
        Args:
            resource_id: ID de la ressource CKAN
            
        Returns:
            Nom de la table ou None
        """
        if not HAS_PSYCOPG2:
            # Fallback: retourner le format moderne (avec tirets)
            logger.warning(f"psycopg2 non disponible, utilisation du format moderne pour {resource_id}")
            return resource_id
        
        conn = None
        cur = None
        try:
            # Le nom de la table PostGIS peut être:
            # 1. {resource_id} (avec tirets) - format moderne CKAN
            # 2. _table_{resource_id_with_underscores} - format ancien CKAN
            # Vérifier réellement quelle table existe dans la base de données
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Essayer d'abord le format moderne (avec tirets)
            table_name_with_dashes = resource_id
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                );
            """, (table_name_with_dashes,))
            
            result = cur.fetchone()
            if result and len(result) > 0 and result[0]:
                logger.info(f"Table trouvée (format moderne): {table_name_with_dashes}")
                return table_name_with_dashes
            
            # Essayer le format ancien (_table_{resource_id_with_underscores})
            resource_id_underscores = resource_id.replace('-', '_')
            table_name_old_format = f"_table_{resource_id_underscores}"
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                );
            """, (table_name_old_format,))
            
            result = cur.fetchone()
            if result and len(result) > 0 and result[0]:
                logger.info(f"Table trouvée (format ancien): {table_name_old_format}")
                return table_name_old_format
            
            # Aucune table trouvée (fallback: format moderne)
            logger.warning(f"Aucune table trouvée pour ressource {resource_id} (ni format moderne, ni format ancien)")
            logger.warning(f"   Formats testés: {table_name_with_dashes}, {table_name_old_format}")
            return table_name_with_dashes
            
        except psycopg2.OperationalError as e:
            logger.warning(f"Erreur de connexion PostGIS pour vérifier la table {resource_id}: {e}")
            return resource_id
        except Exception as e:
            logger.warning(f"Erreur récupération nom table pour ressource {resource_id}: {e}")
            return resource_id
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def get_geometry_column_name(self, resource_id: str) -> Optional[str]:
        """
        Récupère le nom de la colonne géométrie pour une ressource
        Utilise le cache pour éviter les appels API redondants
        
        Args:
            resource_id: ID de la ressource CKAN
            
        Returns:
            Nom de la colonne géométrie ou None
        """
        start_time = time.time()
        try:
            # Utiliser le cache au lieu de faire un appel API direct
            cached_fields = self._get_cached_fields(resource_id)
            if cached_fields is None:
                # Si pas dans le cache, utiliser _datastore_search_with_retry qui gère le cache
                datastore_result = self._datastore_search_with_retry(resource_id, max_retries=2, initial_timeout=5)
                if datastore_result:
                    fields = datastore_result.get('fields', [])
                else:
                    return None
            else:
                fields = cached_fields
            
            # Chercher la colonne géométrie (priorité aux noms PostGIS standards)
            # Ordre de priorité : geometry, geom, the_geom, shape, puis autres
            geometry_fields_priority = ['geometry', 'geom', 'the_geom', 'shape', 'geo_shape']
            
            # D'abord vérifier si une colonne geometry PostGIS existe déjà
            for priority_field in geometry_fields_priority:
                for field in fields:
                    field_name = field.get('id', '').lower()
                    field_type = field.get('type', '').lower()
                    if field_name == priority_field:
                        # Si c'est une vraie colonne PostGIS geometry, l'utiliser directement
                        if 'text' not in field_type and 'varchar' not in field_type:
                            return field.get('id')  # Retourner le nom original (avec casse)
            
            # Ensuite chercher les noms contenant ces mots-clés, mais exclure les colonnes texte
            for field in fields:
                field_name = field.get('id', '').lower()
                field_type = field.get('type', '').lower()
                # Exclure les colonnes texte (comme geom_wkt qui est du WKT en texte)
                if 'text' in field_type or 'varchar' in field_type:
                    continue
                if any(geom_field in field_name for geom_field in geometry_fields_priority):
                    return field.get('id')
            
            # NOUVEAU: Vérifier s'il y a une colonne 'geom' de type TEXT qui contient du GeoJSON
            # C'est le cas le plus courant avec les données importées depuis GeoJSON
            geom_text_fields = [f for f in fields if f.get('id', '').lower() == 'geom' and ('text' in f.get('type', '').lower() or 'varchar' in f.get('type', '').lower())]
            if geom_text_fields and self.auto_create_geometry:
                geom_field = geom_text_fields[0].get('id')
                logger.info(f"Ressource {resource_id} a une colonne 'geom' de type TEXT (probablement GeoJSON)")
                # Vérifier si la colonne geometry existe déjà
                if not self._check_geometry_column_exists(resource_id):
                    logger.info(f"   Création automatique de la colonne 'geometry' depuis '{geom_field}' (GeoJSON)")
                    if self._create_geometry_from_geojson(resource_id, geom_field):
                        logger.info(f"Colonne géométrie créée automatiquement depuis GeoJSON pour {resource_id}")
                        return 'geometry'
                else:
                    logger.info(f"   Colonne 'geometry' existe déjà, utilisation directe")
                    return 'geometry'
            
            # Vérifier aussi les colonnes de coordonnées (lat/lon) qui pourraient être converties
            coord_fields = ['latitude', 'longitude', 'lat', 'lon', 'x', 'y']
            has_coords = any(
                any(coord in field.get('id', '').lower() for coord in coord_fields)
                for field in fields
            )
            
            if has_coords:
                logger.info(f"Ressource {resource_id} a des coordonnées mais pas de colonne géométrie PostGIS")
                if self.auto_create_geometry:
                    # Essayer de créer automatiquement la colonne géométrie
                    if self._create_geometry_from_coords(resource_id):
                        logger.info(f"Colonne géométrie créée automatiquement pour {resource_id}")
                        return 'geometry'
                else:
                    logger.info(f"   Considérer l'utilisation d'une vue PostGIS pour convertir lat/lon en géométrie")
            
            # Vérifier s'il y a une colonne WKT (geom_wkt, geometry_wkt, etc.)
            wkt_fields = [f for f in fields if 'wkt' in f.get('id', '').lower()]
            if wkt_fields and self.auto_create_geometry:
                wkt_field = wkt_fields[0].get('id')
                logger.info(f"Ressource {resource_id} a une colonne WKT ({wkt_field}) mais pas de colonne géométrie PostGIS")
                if self._create_geometry_from_wkt(resource_id, wkt_field):
                    logger.info(f"Colonne géométrie créée automatiquement depuis WKT pour {resource_id}")
                    return 'geometry'
            
            # Vérifier s'il y a une colonne GeoJSON (geo_point_2d, geopoint2d, st_asgeojson, geo_shape, etc.)
            # Chercher aussi les colonnes texte qui pourraient contenir du GeoJSON
            geojson_keywords = ['geo_point', 'geopoint', 'geopoint2d', 'geo_point_2d', 'geo_shape', 'geojson', 'st_asgeojson', 'st_as_geojson', 'asgeojson']
            geojson_fields = []
            for field in fields:
                field_name_lower = field.get('id', '').lower()
                field_type = field.get('type', '').lower()
                # Inclure les colonnes texte qui correspondent aux mots-clés GeoJSON
                if any(keyword in field_name_lower for keyword in geojson_keywords):
                    if 'text' in field_type or 'varchar' in field_type:
                        geojson_fields.append(field)
            
            if geojson_fields and self.auto_create_geometry:
                geojson_field = geojson_fields[0].get('id')
                logger.info(f"Ressource {resource_id} a une colonne GeoJSON ({geojson_field}) mais pas de colonne géométrie PostGIS")
                if self._create_geometry_from_geojson(resource_id, geojson_field):
                    # Vérifier que la colonne geometry existe maintenant
                    if self._check_geometry_column_exists(resource_id):
                        logger.info(f"Colonne géométrie créée automatiquement depuis GeoJSON pour {resource_id}")
                        return 'geometry'
                    else:
                        logger.warning(f"Échec de la création de la colonne geometry depuis {geojson_field}")
                else:
                    logger.warning(f"Impossible de créer la colonne geometry depuis {geojson_field}")
            
            # Vérifier une dernière fois si la colonne geometry existe maintenant (peut avoir été créée ailleurs)
            if self._check_geometry_column_exists(resource_id):
                logger.info(f"Colonne geometry trouvée pour {resource_id}")
                return 'geometry'
            
            # Ne pas retourner 'geometry' par défaut si elle n'existe pas
            logger.error(f"Colonne géométrie non trouvée pour {resource_id} et impossible de la créer automatiquement")
            return None
            
        except Exception as e:
            logger.warning(f"Erreur récupération colonne géométrie pour ressource {resource_id}: {e}")
            return 'geometry'
    
    def _create_geometry_from_coords(self, resource_id: str) -> bool:
        """
        Crée automatiquement une colonne géométrie PostGIS à partir de colonnes lat/lon
        Utilise le cache pour éviter les appels API redondants
        
        Args:
            resource_id: ID de la ressource CKAN
            
        Returns:
            True si succès, False sinon
        """
        try:
            # Utiliser le cache au lieu de faire un appel API direct
            cached_fields = self._get_cached_fields(resource_id)
            if cached_fields is None:
                # Si pas dans le cache, utiliser _datastore_search_with_retry qui gère le cache
                datastore_result = self._datastore_search_with_retry(resource_id, max_retries=2, initial_timeout=5)
                if datastore_result:
                    fields = datastore_result.get('fields', [])
                else:
                    return False
            else:
                fields = cached_fields
            field_names = [f.get('id', '').lower() for f in fields]
            
            # Trouver les colonnes lat/lon
            lat_field = None
            lon_field = None
            
            for field in fields:
                field_name = field.get('id', '').lower()
                if field_name in ['latitude', 'lat', 'y', 'coord_y'] and not lat_field:
                    lat_field = field.get('id')
                if field_name in ['longitude', 'lon', 'x', 'coord_x'] and not lon_field:
                    lon_field = field.get('id')
            
            if not lat_field or not lon_field:
                logger.warning(f"Colonnes lat/lon non trouvées pour {resource_id}")
                return False
            
            # Créer la colonne géométrie via connexion PostGIS directe
            if not HAS_PSYCOPG2:
                logger.warning(f"psycopg2 non disponible, impossible de créer la géométrie automatiquement")
                return False
            
            try:
                table_name = resource_id
                conn = psycopg2.connect(
                    host=self.postgis_host,
                    port=self.postgis_port,
                    database=self.postgis_db,
                    user=self.postgis_user,
                    password=self.postgis_password
                )
                cur = conn.cursor()
                
                # Créer la colonne géométrie
                cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS geometry geometry(Point, 4326);')
                
                # Remplir la colonne géométrie
                cur.execute(f'''
                    UPDATE "{table_name}" 
                    SET geometry = ST_SetSRID(ST_MakePoint({lon_field}::numeric, {lat_field}::numeric), 4326)
                    WHERE {lat_field} IS NOT NULL 
                      AND {lon_field} IS NOT NULL 
                      AND {lat_field}::numeric BETWEEN -90 AND 90 
                      AND {lon_field}::numeric BETWEEN -180 AND 180
                      AND geometry IS NULL;
                ''')
                
                # Créer l'index spatial
                index_name = f'idx_{table_name.replace("-", "_")}_geometry'
                cur.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table_name}" USING GIST (geometry);')
                
                conn.commit()
                cur.close()
                conn.close()
                
                logger.info(f"Colonne géométrie créée depuis {lat_field}/{lon_field} pour {resource_id}")
                return True
                
            except Exception as e:
                logger.warning(f"Erreur création géométrie depuis coordonnées: {e}")
                return False
                
        except Exception as e:
            logger.warning(f"Erreur création géométrie depuis coordonnées: {e}")
            return False
    
    def _create_geometry_from_wkt(self, resource_id: str, wkt_field: str) -> bool:
        """
        Crée automatiquement une colonne géométrie PostGIS à partir d'une colonne WKT
        
        Args:
            resource_id: ID de la ressource CKAN
            wkt_field: Nom de la colonne WKT
            
        Returns:
            True si succès, False sinon
        """
        try:
            if not HAS_PSYCOPG2:
                logger.warning(f"psycopg2 non disponible, impossible de créer la géométrie automatiquement")
                return False
            
            try:
                table_name = resource_id
                conn = psycopg2.connect(
                    host=self.postgis_host,
                    port=self.postgis_port,
                    database=self.postgis_db,
                    user=self.postgis_user,
                    password=self.postgis_password
                )
                cur = conn.cursor()
                
                # Créer la colonne géométrie
                cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS geometry geometry(Geometry, 4326);')
                
                # Remplir la colonne géométrie depuis WKT
                cur.execute(f'''
                    UPDATE "{table_name}" 
                    SET geometry = ST_GeomFromText({wkt_field}, 4326)
                    WHERE {wkt_field} IS NOT NULL 
                      AND {wkt_field} != ''
                      AND geometry IS NULL;
                ''')
                
                # Créer l'index spatial
                index_name = f'idx_{table_name.replace("-", "_")}_geometry'
                cur.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table_name}" USING GIST (geometry);')
                
                conn.commit()
                cur.close()
                conn.close()
                
                logger.info(f"Colonne géométrie créée depuis {wkt_field} pour {resource_id}")
                return True
                
            except Exception as e:
                logger.warning(f"Erreur création géométrie depuis WKT: {e}")
                return False
                
        except Exception as e:
            logger.warning(f"Erreur création géométrie depuis WKT: {e}")
            return False
    
    def _check_geometry_column_exists(self, resource_id: str) -> bool:
        """
        Vérifie si la colonne geometry existe déjà dans la table
        
        Args:
            resource_id: ID de la ressource CKAN
            
        Returns:
            True si la colonne existe, False sinon
        """
        if not HAS_PSYCOPG2:
            return False
        
        try:
            table_name = resource_id
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Vérifier si la colonne geometry existe
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.columns 
                    WHERE table_name = %s 
                    AND column_name = 'geometry'
                    AND table_schema = 'public'
                );
            """, (table_name,))
            
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            # Sécuriser l'accès au résultat
            if result and len(result) > 0:
                return bool(result[0])
            return False
            
        except Exception as e:
            logger.warning(f"Erreur vérification colonne geometry pour {resource_id}: {e}")
            return False
    
    def _create_geometry_from_geojson(self, resource_id: str, geojson_field: str) -> bool:
        """
        Crée automatiquement une colonne géométrie PostGIS à partir d'une colonne GeoJSON
        
        Args:
            resource_id: ID de la ressource CKAN
            geojson_field: Nom de la colonne GeoJSON (peut être geo_point_2d, st_asgeojson, geom, etc.)
            
        Returns:
            True si succès, False sinon
        """
        try:
            if not HAS_PSYCOPG2:
                logger.warning(f"psycopg2 non disponible, impossible de créer la géométrie automatiquement")
                return False
            
            try:
                table_name = resource_id
                conn = psycopg2.connect(
                    host=self.postgis_host,
                    port=self.postgis_port,
                    database=self.postgis_db,
                    user=self.postgis_user,
                    password=self.postgis_password
                )
                cur = conn.cursor()
                
                # Créer la colonne géométrie
                cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS geometry geometry(Geometry, 4326);')
                
                # Remplir la colonne géométrie depuis GeoJSON
                # La colonne peut contenir du GeoJSON valide (format {"type": "Polygon", ...})
                # ou être au format "lon,lat" pour les points
                # Utiliser des guillemets simples pour éviter les problèmes avec les f-strings
                geojson_field_escaped = geojson_field.replace('"', '\\"')
                
                # Essayer plusieurs formats de GeoJSON
                # Utiliser des paramètres nommés pour éviter les erreurs de comptage
                update_query = f'''
                    UPDATE "{table_name}" 
                    SET geometry = CASE
                        -- Format 1: "lon,lat" (comme geo_point_2d de DataVinci)
                        WHEN "{geojson_field_escaped}" ~ '^[0-9.-]+,[0-9.-]+$' THEN
                            ST_SetSRID(ST_MakePoint(
                                CAST(SPLIT_PART("{geojson_field_escaped}", ',', 1) AS FLOAT),
                                CAST(SPLIT_PART("{geojson_field_escaped}", ',', 2) AS FLOAT)
                            ), 4326)
                        -- Format 2: GeoJSON valide (commence par {{ et contient "type")
                        WHEN "{geojson_field_escaped}" ~ '^\\s*\\{{' AND "{geojson_field_escaped}" ~ '"type"' THEN
                            ST_SetSRID(ST_GeomFromGeoJSON("{geojson_field_escaped}"), 4326)
                        -- Format 3: GeoJSON valide mais peut commencer par des espaces ou être un array
                        WHEN "{geojson_field_escaped}" ~ '\\{{.*"type"' OR "{geojson_field_escaped}" ~ '\\[.*\\{{.*"type"' THEN
                            ST_SetSRID(ST_GeomFromGeoJSON("{geojson_field_escaped}"), 4326)
                        -- Format 4: Essayer quand même ST_GeomFromGeoJSON (gère les cas non détectés)
                        WHEN "{geojson_field_escaped}" IS NOT NULL AND "{geojson_field_escaped}" != '' AND "{geojson_field_escaped}" != 'null' THEN
                            CASE
                                WHEN ST_GeomFromGeoJSON("{geojson_field_escaped}") IS NOT NULL THEN
                                    ST_SetSRID(ST_GeomFromGeoJSON("{geojson_field_escaped}"), 4326)
                                ELSE NULL
                            END
                        ELSE NULL
                    END
                    WHERE "{geojson_field_escaped}" IS NOT NULL 
                      AND "{geojson_field_escaped}" != ''
                      AND "{geojson_field_escaped}" != 'null'
                      AND geometry IS NULL;
                '''
                
                try:
                    cur.execute(update_query)
                    rows_updated = cur.rowcount
                    logger.info(f"{rows_updated} lignes mises à jour avec la colonne geometry depuis {geojson_field}")
                except Exception as e:
                    logger.warning(f"Erreur lors de la mise à jour de la colonne geometry: {e}")
                    # Essayer une approche plus simple : utiliser directement ST_GeomFromGeoJSON
                    try:
                        simple_update = f'''
                            UPDATE "{table_name}" 
                            SET geometry = ST_SetSRID(ST_GeomFromGeoJSON("{geojson_field_escaped}"), 4326)
                            WHERE "{geojson_field_escaped}" IS NOT NULL 
                              AND "{geojson_field_escaped}" != ''
                              AND "{geojson_field_escaped}" != 'null'
                              AND geometry IS NULL
                              AND ST_GeomFromGeoJSON("{geojson_field_escaped}") IS NOT NULL;
                        '''
                        cur.execute(simple_update)
                        rows_updated = cur.rowcount
                        logger.info(f"{rows_updated} lignes mises à jour avec approche simplifiée")
                    except Exception as e2:
                        logger.error(f"Échec de la création de geometry depuis {geojson_field}: {e2}")
                        conn.rollback()
                        cur.close()
                        conn.close()
                        return False
                
                # Vérifier que des géométries ont été créées
                cur.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE geometry IS NOT NULL;')
                result = cur.fetchone()
                geometry_count = result[0] if result and len(result) > 0 and result[0] is not None else 0
                
                if geometry_count == 0:
                    logger.warning(f"Aucune géométrie créée depuis {geojson_field} pour {resource_id}")
                    # Vérifier quelques exemples de valeurs pour debug
                    cur.execute(f'SELECT "{geojson_field}" FROM "{table_name}" LIMIT 3;')
                    samples = cur.fetchall()
                    logger.info(f"   Exemples de valeurs dans {geojson_field}: {samples}")
                    conn.rollback()
                    cur.close()
                    conn.close()
                    return False
                
                # Créer l'index spatial
                index_name = f'idx_{table_name.replace("-", "_")}_geometry'
                cur.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON "{table_name}" USING GIST (geometry);')
                
                # Enregistrer dans geometry_columns si nécessaire
                cur.execute(f"""
                    INSERT INTO geometry_columns (f_table_catalog, f_table_schema, f_table_name, f_geometry_column, coord_dimension, srid, type)
                    SELECT '', 'public', '{table_name}', 'geometry', 2, 4326, 'GEOMETRY'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM geometry_columns 
                        WHERE f_table_name = '{table_name}' AND f_geometry_column = 'geometry'
                    );
                """)
                
                conn.commit()
                cur.close()
                conn.close()
                
                logger.info(f"Colonne géométrie créée depuis {geojson_field} pour {resource_id} ({geometry_count} géométries)")
                return True
                
            except Exception as e:
                logger.warning(f"Erreur création géométrie depuis GeoJSON pour {resource_id}: {e}")
                import traceback
                logger.warning(traceback.format_exc())
                return False
                
        except Exception as e:
            logger.warning(f"Erreur création géométrie depuis GeoJSON pour {resource_id}: {e}")
            return False
    
    def _get_resource_file_path(self, resource: Dict[str, Any]) -> Optional[str]:
        """
        Récupère le chemin local d'un fichier de ressource CKAN de manière robuste
        
        Utilise la même logique que import-geospatial-to-datagis.py pour garantir
        la cohérence de la recherche de fichiers.
        
        Args:
            resource: Dictionnaire de la ressource
            
        Returns:
            Chemin local du fichier ou None si non trouvé (NE RETOURNE JAMAIS UN CHEMIN INEXISTANT)
        """
        resource_id = resource.get('id')
        if not resource_id:
            logger.warning(f"Pas de resource_id pour la ressource")
            return None
        
        url = resource.get('url', '')
        resource_name = resource.get('name', '')
        
        # Si c'est une URL locale absolue (hors CKAN), vérifier qu'elle existe
        if url.startswith('/') and not ('/dataset/' in url or '/resource/' in url):
            if os.path.exists(url) and os.path.isfile(url):
                logger.info(f"Fichier trouvé via URL locale: {url}")
                return url
            else:
                logger.warning(f"URL locale fournie mais fichier non trouvé: {url}")
                return None
        
        # Structure CKAN confirmée : {storage_path}/resources/{id[0:3]}/{id[3:6]}/{id[6:]}
        # Chercher dans plusieurs emplacements possibles (par ordre de probabilité)
        ckan_storage_base = Path('/var/lib/ckan')
        ckan_storage_base_default = Path('/var/lib/ckan/default')
        
        # Construire les chemins possibles (même logique que import-geospatial-to-datagis.py)
        if resource_id and len(resource_id) >= 6:
            # PRIORITÉ 1: Structure standard avec storage_path=/var/lib/ckan (confirmée)
            ckan_path_standard = ckan_storage_base / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            ckan_storage_path_standard = ckan_storage_base / 'storage' / 'resources' / resource_id[0:3] / resource_id[3:6] / resource_id[6:]
            
            # PRIORITÉ 2: Structure avec storage_path=/var/lib/ckan/default (ancienne config possible)
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
            ckan_path_standard if resource_id and len(resource_id) >= 6 else None,
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
            
            if exists and is_file:
                logger.info(f"Fichier trouvé: {path}")
                return str(path)
            
            # Chercher aussi les fichiers dans le répertoire parent si c'est un répertoire
            if exists and path.is_dir():
                try:
                    for file in path.iterdir():
                        if file.is_file() and not file.name.startswith('.'):
                            logger.info(f"Fichier trouvé dans répertoire: {file}")
                            return str(file)
                except Exception as e:
                    logger.debug(f"  Erreur lecture répertoire {path}: {e}")
            
            # Vérifier aussi le répertoire parent
            parent = path.parent
            if parent.exists() and parent.is_dir():
                try:
                    for file in parent.iterdir():
                        if file.is_file() and not file.name.startswith('.'):
                            logger.info(f"Fichier trouvé dans répertoire parent: {file}")
                            return str(file)
                except Exception as e:
                    logger.debug(f"  Erreur lecture répertoire parent {parent}: {e}")
        
        # Aucun fichier trouvé
        logger.warning(f"Impossible de déterminer le chemin du fichier pour {resource_id}")
        logger.info(f"Chemins vérifiés ({len(checked_paths)}):")
        for checked_path in checked_paths:
            exists = Path(checked_path).exists()
            status = 'existe' if exists else 'n\'existe pas'
            logger.info(f"   - {checked_path} {status}")
        
        # Logger aussi l'URL de la ressource pour debug
        if url:
            logger.info(f"URL de la ressource: {url}")
            if '/storage/' in url or '/resource/' in url:
                logger.info(f"L'URL semble pointer vers le storage CKAN, mais le fichier n'a pas été trouvé localement")
        
        return None
    
    # Extensions et formats reconnus comme rasters pour MapServer (traités en dernier dans le mapfile)
    RASTER_EXTENSIONS = ('.tif', '.tiff', '.jp2', '.j2k', '.img', '.asc')
    RASTER_FORMATS_SET = {'TIF', 'TIFF', 'JP2', 'JPEG2000', 'IMG', 'ASC', 'GEOTIFF'}
    
    def _is_raster_resource(self, resource: Dict[str, Any]) -> bool:
        """Indique si la ressource est un raster supporté (tif, tiff, jp2, img, asc)."""
        fmt = (resource.get('format') or '').upper()
        if fmt in self.RASTER_FORMATS_SET:
            return True
        url = resource.get('url') or ''
        name = resource.get('name') or ''
        for ext in self.RASTER_EXTENSIONS:
            if ext in url.lower() or ext in name.lower() or url.lower().endswith(ext):
                return True
        return False

    # Formats vectoriels que OGR peut lire (fallback quand pas de table datagis)
    VECTOR_OGR_FORMATS = {'SHP', 'SHAPEFILE', 'GEOJSON', 'KML', 'KMZ', 'GPKG', 'GEOPACKAGE'}
    VECTOR_OGR_EXTENSIONS = ('.shp', '.geojson', '.json', '.kml', '.kmz', '.gpkg')

    def _is_vector_ogr_resource(self, resource: Dict[str, Any]) -> bool:
        """Indique si la ressource est un vecteur lisible par OGR (SHP, GeoJSON, KML, GPKG, ZIP contenant .shp)."""
        fmt = (resource.get('format') or '').upper()
        if fmt in self.VECTOR_OGR_FORMATS:
            return True
        url = resource.get('url') or ''
        name = resource.get('name') or ''
        for ext in self.VECTOR_OGR_EXTENSIONS:
            if ext in url.lower() or ext in name.lower() or url.lower().endswith(ext):
                return True
        if '.zip' in url.lower() or url.lower().endswith('.zip') or 'zip' in (fmt or ''):
            # ZIP peut contenir un shapefile
            return True
        return False

    def _detect_geometry_type_ogr(self, data_path: str) -> str:
        """Détecte le type de géométrie d'un fichier OGR (ogrinfo). Retourne POINT, LINE, POLYGON ou POLYGON par défaut."""
        if not data_path or not os.path.isfile(data_path):
            return 'POLYGON'
        try:
            out = subprocess.run(
                ['ogrinfo', '-so', '-al', data_path],
                capture_output=True, text=True, timeout=15
            )
            if out.returncode != 0:
                return 'POLYGON'
            # Ex: "Geometry: Line String" / "Geometry: Polygon" / "Geometry: Point"
            for line in (out.stdout or '').splitlines():
                line = line.strip()
                if line.startswith('Geometry:'):
                    geom = line.split(':', 1)[-1].strip().upper()
                    if 'POINT' in geom:
                        return 'POINT'
                    if 'LINE' in geom or 'LINESTRING' in geom:
                        return 'LINE'
                    if 'POLYGON' in geom or 'MULTI' in geom:
                        return 'POLYGON'
                    break
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
            logger.debug(f"ogrinfo non disponible ou erreur pour {data_path}: {e}")
        return 'POLYGON'

    def _prepare_ogr_for_mapserver(self, resource: Dict[str, Any], dataset_name: str
                                   ) -> Optional[str]:
        """
        Copie le fichier vectoriel (SHP, GeoJSON, ZIP, etc.) vers un chemin lisible par MapServer
        (/mapserver/data/ogr/{dataset_name}/) pour que le mapfile fonctionne quand MapServer
        tourne dans un conteneur ou sur une autre machine.
        Retourne le chemin final (fichier ou répertoire) ou None.
        """
        local_path = self._get_resource_file_path(resource)
        if not local_path or not os.path.exists(local_path):
            return None

        # Cas zip : utiliser le pilote GDAL /vsizip/ directement plutot que de
        # copier dans /mapserver/data (qui n est pas forcement partage avec
        # MapServer). MapServer doit pouvoir lire le zip a son path original
        # (volume CKAN monte cote MapServer en read-only).
        # Syntaxe avec accolades : /vsizip/{<path>}/<entry> ; necessaire ici
        # parce que les fichiers CKAN stockes n ont pas d extension .zip
        # (le pilote GDAL la requiert pour l auto-detection sans accolades).
        try:
            with open(local_path, 'rb') as fp:
                magic = fp.read(4)
            if magic == b'PK\x03\x04':
                import zipfile
                with zipfile.ZipFile(local_path) as zf:
                    shp_in_zip = [n for n in zf.namelist()
                                  if n.lower().endswith('.shp')]
                if shp_in_zip:
                    vsizip_path = f"/vsizip/{{{local_path}}}/{shp_in_zip[0]}"
                    logger.info(
                        f"   ZIP detecte : utilisation OGR /vsizip/ -> {vsizip_path}"
                    )
                    return vsizip_path
                # Pas de .shp dans le zip : laisser OGR auto-detecter
                vsizip_path = f"/vsizip/{{{local_path}}}"
                logger.info(
                    f"   ZIP detecte (pas de .shp dedans) : auto-detect -> {vsizip_path}"
                )
                return vsizip_path
        except Exception as e:
            logger.debug(f"   Detection ZIP echouee pour {local_path}: {e}")

        resource_id = resource.get('id', '')
        path_obj = Path(local_path)
        suffix = path_obj.suffix.lower() or '.shp'
        try:
            ogr_base = self.mapfiles_dir.parent / 'data' / 'ogr' / dataset_name
        except Exception:
            ogr_base = Path(str(self.mapfiles_dir)) / '..' / 'data' / 'ogr' / dataset_name
            ogr_base = ogr_base.resolve()
        dest_dir = ogr_base / (resource_id.replace('-', '_')[:50])
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            can_write = os.access(str(ogr_base), os.W_OK)
        except Exception:
            can_write = False
        if not can_write:
            return local_path
        dest_path = dest_dir / f"data{suffix}"
        try:
            if path_obj.is_file():
                shutil.copy2(local_path, dest_path)
                # Pour un .shp, copier aussi .dbf, .shx, .prj (même répertoire source)
                if path_obj.suffix.lower() == '.shp':
                    parent = path_obj.parent
                    stem = path_obj.stem
                    for ext in ('.dbf', '.shx', '.prj', '.cpg', '.qpj'):
                        sibling = parent / (stem + ext)
                        if sibling.is_file():
                            shutil.copy2(sibling, dest_dir / (f"data{ext}"))
                logger.info(f"   Fichier OGR copié vers MapServer: {dest_path}")
                return str(dest_path)
            return local_path
        except Exception as e:
            logger.warning(f"   Copy OGR failed: {e}, utilisation du chemin source")
            return local_path

    def _prepare_raster_for_mapserver(self, resource: Dict[str, Any], dataset_name: str
                                     ) -> Optional[Dict[str, Any]]:
        """
        Prépare un fichier raster pour MapServer : chemin utilisable, optionnellement copie
        vers le répertoire MapServer + overviews (pyramides), CRS si absent, NoData.
        MapServer doit pouvoir lire data_path.
        
        Returns:
            {'data_path': str, 'nodata': float|None, 'srid': int|None} ou None si fichier introuvable.
        """
        local_path = self._get_resource_file_path(resource)
        if not local_path or not os.path.isfile(local_path):
            return None
        path_obj = Path(local_path)
        suffix = path_obj.suffix.lower() or '.tif'
        resource_id = resource.get('id', '')
        resource_name = (resource.get('name') or resource_id).replace('/', '_')[:80]
        
        # Répertoire de destination : /mapserver/data/rasters/{dataset_name}/
        try:
            raster_base = self.mapfiles_dir.parent / 'data' / 'rasters' / dataset_name
        except Exception:
            raster_base = Path(str(self.mapfiles_dir)) / '..' / 'data' / 'rasters' / dataset_name
            raster_base = raster_base.resolve()
        
        dest_dir = raster_base
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            can_write = os.access(str(dest_dir), os.W_OK)
        except Exception:
            can_write = False
        
        if can_write:
            dest_name = f"{resource_id.replace('-', '_')}{suffix}"
            dest_path = dest_dir / dest_name
            try:
                shutil.copy2(local_path, dest_path)
                data_path = str(dest_path)
                logger.info(f"   Raster copié vers MapServer: {data_path}")
            except Exception as e:
                logger.warning(f"   Copy raster failed: {e}, utilisation du chemin source")
                data_path = local_path
        else:
            logger.info(f"   Raster: utilisation du chemin CKAN (pas de copie ni GDAL)")
            data_path = local_path
        
        # Overviews (pyramides) uniquement si on a écrit dans le dir MapServer
        if can_write and data_path != local_path:
            try:
                subprocess.run(
                    ['gdaladdo', '-r', 'average', data_path, '2', '4', '8', '16'],
                    capture_output=True, text=True, timeout=300
                )
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                logger.debug(f"   gdaladdo skipped: {e}")

        # gdalinfo sur le raster (copié ou source) pour extraire SRID (EPSG) et NoData
        nodata_val = None
        srid_val = None
        try:
            out = subprocess.run(
                ['gdalinfo', data_path], capture_output=True, text=True, timeout=30
            )
            if out.returncode == 0 and out.stdout:
                stdout = out.stdout
                # NoData
                for line in stdout.splitlines():
                    if 'NoData Value=' in line:
                        try:
                            nodata_val = float(line.split('=', 1)[1].strip())
                        except ValueError:
                            pass
                        break
                # SRID (EPSG:xxxx) : prendre la DERNIÈRE occurrence (CRS projeté, ex. 2154)
                # car le WKT contient aussi le datum (ex. 4171 RGF93) ; MapServer a besoin du CRS projeté
                epsg_matches = re.findall(
                    r'(?:AUTHORITY\["EPSG","|ID\["EPSG",|EPSG["\s:,]*)(\d+)',
                    stdout, re.IGNORECASE
                )
                if epsg_matches:
                    srid_val = int(epsg_matches[-1])
                    logger.debug(f"   Raster SRID extrait (gdalinfo): EPSG:{srid_val}")
                # Si pas de CRS dans le fichier, assigner un par défaut uniquement après copie
                if can_write and data_path != local_path:
                    if 'Coordinate System is' not in stdout and 'PROJCS' not in stdout and 'GEOGCS' not in stdout:
                        for srs_cmd in [['gdal_edit.py', '-a_srs', 'EPSG:2154', data_path],
                                        ['gdal_edit.py', '-a_srs', 'EPSG:4326', data_path]]:
                            if subprocess.run(srs_cmd, capture_output=True, timeout=60).returncode == 0:
                                srid_val = 2154 if '2154' in str(srs_cmd[2]) else 4326
                                break
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f"   gdalinfo/gdal_edit skipped: {e}")

        return {'data_path': data_path, 'nodata': nodata_val, 'srid': srid_val}
    
    def _extract_shapefile_to_postgis(self, resource_id: str, zip_path: str) -> Optional[str]:
        """
        Extrait un Shapefile ZIP et l'importe dans PostGIS une seule fois
        
        Args:
            resource_id: ID de la ressource CKAN
            zip_path: Chemin vers le fichier ZIP
            
        Returns:
            Nom de la table PostGIS créée ou None si échec
        """
        if not HAS_PSYCOPG2:
            logger.warning("psycopg2 non disponible, impossible d'importer dans PostGIS")
            return None
        
        try:
            import zipfile
            import tempfile
            import subprocess
            
            # Vérifier si la table existe déjà
            table_name = f"extracted_{resource_id.replace('-', '_')}"
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Vérifier si la table existe déjà
            cur.execute(f"""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = '{table_name}'
                );
            """)
            result = cur.fetchone()
            table_exists = result and len(result) > 0 and result[0] if result else False
            
            if table_exists:
                logger.info(f"Table PostGIS existe déjà: {table_name}")
                cur.close()
                conn.close()
                return table_name
            
            # Extraire le ZIP dans un répertoire temporaire
            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    # Trouver le fichier .shp
                    shp_files = [f for f in zip_ref.namelist() if f.lower().endswith('.shp')]
                    if not shp_files:
                        logger.warning(f"Aucun fichier .shp trouvé dans le ZIP: {zip_path}")
                        return None
                    
                    # Extraire tous les fichiers du Shapefile
                    zip_ref.extractall(temp_dir)
                    shp_file = os.path.join(temp_dir, shp_files[0])
                    
                    # Importer dans PostGIS avec ogr2ogr (via shp2pgsql ou ogr2ogr)
                    # Utiliser ogr2ogr si disponible, sinon shp2pgsql
                    try:
                        # Essayer ogr2ogr d'abord (plus flexible)
                        cmd = [
                            'ogr2ogr',
                            '-f', 'PostgreSQL',
                            f'PG:host={self.postgis_host} port={self.postgis_port} dbname={self.postgis_db} user={self.postgis_user} password={self.postgis_password}',
                            shp_file,
                            '-nln', table_name,
                            '-lco', 'GEOMETRY_NAME=geometry',
                            '-lco', 'FID=_id',
                            '-overwrite'
                        ]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                        
                        if result.returncode == 0:
                            logger.info(f"Shapefile importé dans PostGIS: {table_name}")
                            cur.close()
                            conn.close()
                            return table_name
                        else:
                            logger.warning(f"Erreur ogr2ogr: {result.stderr}")
                    except FileNotFoundError:
                        logger.warning("ogr2ogr non disponible, tentative avec shp2pgsql")
                        # Fallback sur shp2pgsql
                        try:
                            cmd = [
                                'shp2pgsql',
                                '-s', '4326',
                                '-I',
                                '-D',
                                shp_file,
                                table_name
                            ]
                            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                            
                            if result.returncode == 0:
                                # Exécuter le SQL généré
                                cur.execute(result.stdout)
                                conn.commit()
                                logger.info(f"Shapefile importé dans PostGIS: {table_name}")
                                cur.close()
                                conn.close()
                                return table_name
                        except FileNotFoundError:
                            logger.error("ogr2ogr et shp2pgsql non disponibles")
                            return None
            
            cur.close()
            conn.close()
            return None
            
        except Exception as e:
            logger.error(f"Erreur import Shapefile dans PostGIS: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _detect_and_normalize_ogr_format(self, resource: Dict[str, Any], file_path: str) -> Tuple[Optional[str], Optional[str], bool]:
        """
        Détecte le format OGR depuis resource['format'] ou resource['url'] et normalise le chemin
        
        Args:
            resource: Dictionnaire de la ressource
            file_path: Chemin du fichier
            
        Returns:
            Tuple (format_normalized, file_path_normalized, is_zip) ou (None, None, False) si format non supporté
        """
        format_ = resource.get('format', '').upper().strip()
        url = resource.get('url', '').lower()
        
        # Mapping des formats vers extensions OGR
        format_to_extension = {
            'GEOJSON': '.geojson',
            'JSON': '.geojson',  # JSON peut être GeoJSON
            'SHP': '.shp',
            'SHAPEFILE': '.shp',
            'GPKG': '.gpkg',
            'GEOPACKAGE': '.gpkg',
            'KML': '.kml',
            'KMZ': '.kmz',
            'ZIP': '.zip'
        }
        
        # 1. Détecter depuis resource['format']
        detected_format = None
        if format_ in format_to_extension:
            detected_format = format_
        elif format_ in {'GEOJSON', 'JSON'}:
            detected_format = 'GEOJSON'
        elif format_ in {'SHP', 'SHAPEFILE'}:
            detected_format = 'SHP'
        elif format_ in {'GPKG', 'GEOPACKAGE'}:
            detected_format = 'GPKG'
        elif format_ in {'KML', 'KMZ'}:
            detected_format = format_
        
        # 2. Si format non détecté, essayer depuis l'URL (extension)
        if not detected_format:
            url_lower = url.lower()
            if url_lower.endswith('.geojson') or '.geojson' in url_lower:
                detected_format = 'GEOJSON'
            elif url_lower.endswith('.shp') or '.shp' in url_lower:
                detected_format = 'SHP'
            elif url_lower.endswith('.gpkg') or '.gpkg' in url_lower:
                detected_format = 'GPKG'
            elif url_lower.endswith('.kml') or '.kml' in url_lower:
                detected_format = 'KML'
            elif url_lower.endswith('.kmz') or '.kmz' in url_lower:
                detected_format = 'KMZ'
            elif url_lower.endswith('.zip') or '.zip' in url_lower:
                detected_format = 'ZIP'
        
        # 3. Si toujours non détecté, essayer depuis le chemin du fichier
        if not detected_format:
            file_path_lower = file_path.lower()
            if file_path_lower.endswith('.geojson'):
                detected_format = 'GEOJSON'
            elif file_path_lower.endswith('.shp'):
                detected_format = 'SHP'
            elif file_path_lower.endswith('.gpkg'):
                detected_format = 'GPKG'
            elif file_path_lower.endswith('.kml'):
                detected_format = 'KML'
            elif file_path_lower.endswith('.kmz'):
                detected_format = 'KMZ'
            elif file_path_lower.endswith('.zip'):
                detected_format = 'ZIP'
        
        if not detected_format:
            logger.warning(f"Format non détecté pour ressource {resource.get('id')} (format: {format_}, url: {url[:100]})")
            return None, None, False
        
        # 4. Normaliser le chemin du fichier avec la bonne extension
        is_zip = detected_format in {'ZIP', 'KMZ'} or (detected_format == 'SHP' and (file_path.lower().endswith('.zip') or '.zip' in url))
        
        # Si le fichier n'a pas la bonne extension, essayer de le normaliser
        expected_ext = format_to_extension.get(detected_format, '')
        file_path_normalized = file_path
        
        if expected_ext and not file_path.lower().endswith(expected_ext):
            # Si le fichier existe avec l'extension actuelle, le garder
            if os.path.exists(file_path):
                file_path_normalized = file_path
            else:
                # Essayer avec l'extension attendue
                base_path = os.path.splitext(file_path)[0]
                normalized_path = base_path + expected_ext
                if os.path.exists(normalized_path):
                    file_path_normalized = normalized_path
                    logger.info(f"Chemin normalisé: {normalized_path}")
                else:
                    # Garder le chemin original mais logger un avertissement
                    logger.warning(f"Extension attendue '{expected_ext}' non trouvée, utilisation du chemin original: {file_path}")
                    file_path_normalized = file_path
        
        return detected_format, file_path_normalized, is_zip
    
    def _create_ogr_mapfile_content(self, dataset_name: str, dataset_title: str,
                                   resource: Dict[str, Any]) -> Optional[str]:
        """
        Crée le contenu d'un mapfile MapServer utilisant OGR pour lire un fichier géospatial
        ou PostGIS si le Shapefile a été importé
        
        Args:
            dataset_name: Nom du dataset
            dataset_title: Titre du dataset
            resource: Dictionnaire de la ressource
            
        Returns:
            Contenu du mapfile ou None si échec
        """
        file_path = self._get_resource_file_path(resource)
        if not file_path:
            logger.warning(f"Impossible de déterminer le chemin du fichier pour {resource.get('id')}")
            return None
        
        # Détecter et normaliser le format
        format_result = self._detect_and_normalize_ogr_format(resource, file_path)
        if not format_result[0]:
            logger.warning(f"Format non supporté pour ressource {resource.get('id')}")
            return None
        
        format_, file_path_normalized, is_zip = format_result
        resource_id = resource.get('id')
        
        logger.info(f"Format détecté: {format_}, Chemin: {file_path_normalized}, ZIP: {is_zip}")
        
        # VALIDATION: Vérifier que le fichier existe (obligatoire pour éviter de casser le WMS)
        if not os.path.exists(file_path_normalized):
            logger.error(f"Fichier non trouvé: {file_path_normalized}")
            logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
            return None
        
        # Vérifier aussi le type réel du fichier si accessible
        try:
            import subprocess
            result = subprocess.run(['file', file_path_normalized], capture_output=True, text=True, timeout=5)
            if 'zip' in result.stdout.lower():
                is_zip = True
        except Exception:
            pass
        
        # Pour les Shapefiles ZIP, essayer d'importer dans PostGIS une seule fois
        use_postgis = False
        table_name = None
        if format_ in {'SHP', 'SHAPEFILE'} and is_zip:
            logger.info(f"Extraction et import du Shapefile ZIP dans PostGIS pour {resource_id}")
            table_name = self._extract_shapefile_to_postgis(resource_id, file_path)
            if table_name:
                use_postgis = True
                logger.info(f"Utilisation de PostGIS au lieu d'OGR pour {resource_id}")
        
        # Déterminer le type de layer et la source de données selon le format normalisé
            layer_type = 'POLYGON'  # Par défaut, peut être ajusté selon les données
        data_source = None  # Initialiser pour éviter les erreurs
        
        if format_ in {'SHP', 'SHAPEFILE'}:
            if use_postgis and table_name:
                # Utiliser PostGIS au lieu d'OGR
                data_source = None  # Sera défini dans le mapfile avec CONNECTIONTYPE POSTGIS
            elif is_zip or file_path_normalized.lower().endswith('.zip'):
                # Pour les ZIP contenant des Shapefiles, utiliser /vsizip/ avec le chemin normalisé
                try:
                    import zipfile
                    zip_path = file_path_normalized
                    if not zip_path.lower().endswith('.zip'):
                        # Si le format est SHP mais le fichier est un ZIP, utiliser le chemin tel quel
                        zip_path = file_path_normalized
                    
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        shp_files = [f for f in zip_ref.namelist() if f.lower().endswith('.shp')]
                        if shp_files:
                            # Spécifier le fichier .shp dans le ZIP avec /vsizip/
                            # Format: /vsizip/{chemin_zip}/{fichier.shp}
                            data_source = f"/vsizip/{zip_path}/{shp_files[0]}"
                            logger.info(f"Utilisation du Shapefile dans ZIP: {shp_files[0]}")
                            logger.info(f"   Chemin OGR: {data_source}")
                        else:
                            # Fallback: OGR peut parfois détecter automatiquement
                            data_source = f"/vsizip/{zip_path}"
                            logger.info(f"Utilisation du ZIP (auto-détection OGR): {data_source}")
                except Exception as e:
                    logger.error(f"Erreur lecture ZIP pour Shapefile: {e}")
                    logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                    return None
            else:
                # Shapefile direct (non compressé)
                data_source = file_path_normalized
                logger.info(f"Utilisation du Shapefile direct: {data_source}")
        
        elif format_ == 'GEOJSON':
            # GeoJSON: utiliser le préfixe GeoJSON: ou le chemin direct
            if not file_path_normalized.lower().endswith('.geojson'):
                # Normaliser avec extension .geojson si nécessaire
                if os.path.exists(file_path_normalized + '.geojson'):
                    file_path_normalized = file_path_normalized + '.geojson'
            data_source = f"GeoJSON:{file_path_normalized}"
            logger.info(f"Utilisation du GeoJSON: {data_source}")
        
        elif format_ in {'GPKG', 'GEOPACKAGE'}:
            # GPKG: utiliser le préfixe GPKG: ou le chemin direct
            if not file_path_normalized.lower().endswith('.gpkg'):
                # Normaliser avec extension .gpkg si nécessaire
                if os.path.exists(file_path_normalized + '.gpkg'):
                    file_path_normalized = file_path_normalized + '.gpkg'
            data_source = f"GPKG:{file_path_normalized}"
            logger.info(f"Utilisation du GPKG: {data_source}")
        
        elif format_ == 'KML':
            # KML: utiliser le préfixe KML: ou le chemin direct
            if not file_path_normalized.lower().endswith('.kml'):
                # Normaliser avec extension .kml si nécessaire
                if os.path.exists(file_path_normalized + '.kml'):
                    file_path_normalized = file_path_normalized + '.kml'
            data_source = f"KML:{file_path_normalized}"
            logger.info(f"Utilisation du KML: {data_source}")
        
        elif format_ == 'KMZ':
            # KMZ: utiliser /vsizip/ car c'est un ZIP contenant un KML
            if not file_path_normalized.lower().endswith('.kmz'):
                # Normaliser avec extension .kmz si nécessaire
                if os.path.exists(file_path_normalized + '.kmz'):
                    file_path_normalized = file_path_normalized + '.kmz'
            # KMZ est un ZIP, donc utiliser /vsizip/
            try:
                import zipfile
                with zipfile.ZipFile(file_path_normalized, 'r') as zip_ref:
                    kml_files = [f for f in zip_ref.namelist() if f.lower().endswith('.kml')]
                    if kml_files:
                        data_source = f"/vsizip/{file_path_normalized}/{kml_files[0]}"
                        logger.info(f"Utilisation du KML dans KMZ: {kml_files[0]}")
                    else:
                        data_source = f"/vsizip/{file_path_normalized}"
                        logger.info(f"Utilisation du KMZ (auto-détection OGR): {data_source}")
            except Exception as e:
                logger.error(f"Erreur lecture KMZ: {e}")
                logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                return None
        
        else:
            logger.error(f"Format {format_} non supporté pour OGR")
            logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
            return None
        
        # VALIDATION FINALE: Vérifier que data_source est défini (sauf pour PostGIS)
        if not use_postgis and not data_source:
            logger.error(f"Source de données OGR non déterminée pour {resource_id}")
            logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
            return None
        
        # VALIDATION CRITIQUE: Vérifier que le fichier existe vraiment avant de générer le mapfile
        # (sauf pour PostGIS qui est géré différemment)
        if not use_postgis and data_source:
            # Extraire le chemin réel du fichier depuis data_source
            file_to_check = None
            
            if data_source.startswith('/vsizip/'):
                # Pour /vsizip/, extraire le chemin du ZIP
                # Format: /vsizip/{chemin_zip}/{fichier_interne} ou /vsizip/{chemin_zip}
                vsizip_part = data_source.replace('/vsizip/', '')
                # Le chemin du ZIP est la première partie (avant le / suivant s'il y a un fichier interne)
                if '/' in vsizip_part:
                    zip_path = vsizip_part.split('/', 1)[0]
                else:
                    zip_path = vsizip_part
                file_to_check = zip_path
            elif ':' in data_source:
                # Pour les formats avec préfixe (GeoJSON:, GPKG:, KML:)
                # Format: GeoJSON:{chemin} ou GPKG:{chemin} ou KML:{chemin}
                file_to_check = data_source.split(':', 1)[1]
            else:
                # Chemin direct
                file_to_check = data_source
            
            # Vérifier que le fichier existe
            if file_to_check and not os.path.exists(file_to_check):
                logger.error(f"Fichier OGR non trouvé: {file_to_check}")
                logger.error(f"   Source de données: {data_source}")
                logger.error(f"   Format détecté: {format_}")
                logger.error(f"   Chemin normalisé: {file_path_normalized}")
                logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                return None
            
            # Pour /vsizip/, vérifier aussi que le ZIP est valide et contient le fichier attendu
            if data_source.startswith('/vsizip/'):
                try:
                    import zipfile
                    zip_path = file_to_check
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        # Vérifier que le ZIP est valide
                        bad_file = zip_ref.testzip()
                        if bad_file:
                            logger.error(f"ZIP corrompu (fichier invalide: {bad_file}): {zip_path}")
                            logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                            return None
                        
                        # Si data_source contient un fichier interne, vérifier qu'il existe dans le ZIP
                        vsizip_part = data_source.replace('/vsizip/', '')
                        if '/' in vsizip_part:
                            internal_file = vsizip_part.split('/', 1)[1]
                            if internal_file not in zip_ref.namelist():
                                logger.error(f"Fichier interne non trouvé dans ZIP: {internal_file}")
                                logger.error(f"   ZIP: {zip_path}")
                                logger.error(f"   Fichiers disponibles: {', '.join(zip_ref.namelist()[:5])}...")
                                logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                                return None
                except zipfile.BadZipFile:
                    logger.error(f"ZIP invalide (format incorrect): {zip_path}")
                    logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
                    return None
                except Exception as e:
                    logger.error(f"Erreur lecture ZIP: {zip_path}")
                    logger.error(f"   Erreur: {e}")
                    logger.error(f"   SKIPPED: Pas de mapfile OGR généré pour éviter de casser le WMS")
            return None
        
        # Construire le contenu du mapfile selon le type de source
        if use_postgis and table_name:
            # Utiliser PostGIS (Shapefile importé)
            connection_string = (
                f"host={self.postgis_host} "
                f"port={self.postgis_port} "
                f"dbname={self.postgis_db} "
                f"user={self.postgis_user} "
                f"password={self.postgis_password}"
            )
            layer_connection = f"""        # Connexion PostGIS
        CONNECTIONTYPE POSTGIS
        CONNECTION "{connection_string}"
        DATA "geometry FROM \\"{table_name}\\" USING UNIQUE _id USING SRID=4326"
"""
        else:
            # Utiliser OGR (fichier direct)
            layer_connection = f"""        # Connexion OGR
        CONNECTIONTYPE OGR
        CONNECTION "{data_source}"
        DATA "0"  # Utiliser la première couche du fichier
"""
        
        mapfile = f"""MAP
    NAME "{dataset_name}"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT -180 -90 180 90
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255

    # Sortie GeoJSON pour le WFS (GetFeature OUTPUTFORMAT=geojson) et clients web
    OUTPUTFORMAT
        NAME "geojson"
        DRIVER "OGR/GEOJSON"
        MIMETYPE "application/json; subtype=geojson"
        FORMATOPTION "STORAGE=stream"
        FORMATOPTION "FORM=SIMPLE"
    END
    
    # Configuration
    CONFIG "PROJ_LIB" "/usr/share/proj"
    CONFIG "MS_ERRORFILE" "/mapserver/logs/{dataset_name}_error.log"
    
    # Projection par défaut (WGS84)
    PROJECTION
        "init=epsg:4326"
    END
    
    # Métadonnées WMS
    WEB
        METADATA
            "wms_title" "{dataset_title} (ogr)"
            "wms_onlineresource" "/wms?map=/mapserver/mapfiles/{dataset_name}.map"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wms_feature_info_mime_type" "text/html"
            "wms_getlegendgraphic_formatlist" "image/png,image/gif,image/jpeg"
            "wfs_enable_request" "*"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_title" "{dataset_title} (ogr)"
            "ows_enable_request" "*"
        END
    END
    
    # Définir un symbole simple inline pour les points
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    
    # Layer principal
    LAYER
        NAME "{dataset_name}"
        TYPE {layer_type}
        STATUS ON
        
{layer_connection}
        
        # Projection
        PROJECTION
            "init=epsg:4326"
        END
        
        # Style par défaut
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
                SYMBOL "circle_point"
                SIZE 6
            END
        END
        
        # Métadonnées du layer
        METADATA
            "wms_title" "{dataset_title} (ogr)"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wfs_title" "{dataset_title} (ogr)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
        END
    END
END
"""
        return mapfile
    
    def _validate_datagis_table_basic(self, table_name: str, geom_column: str, cur) -> bool:
        """
        Valide qu'une table datagis a les preuves minimales (lignes > 0, extent plausible)
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            cur: Curseur PostgreSQL
            
        Returns:
            True si la table est valide, False sinon
        """
        try:
            # PREUVE MINIMUM 1: Vérifier que la table a un nombre de lignes > 0
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}" WHERE "{geom_column}" IS NOT NULL')
            result = cur.fetchone()
            row_count = result[0] if result and len(result) > 0 and result[0] is not None else 0
            
            if row_count == 0:
                logger.debug(f"Table datagis '{table_name}' ignorée (0 lignes avec géométrie)")
                return False
            
            # PREUVE MINIMUM 2: Vérifier un extent plausible
            cur.execute(f"""
                SELECT ST_Extent(ST_Transform("{geom_column}", 4326))::text
                FROM "{table_name}"
                WHERE "{geom_column}" IS NOT NULL
            """)
            extent_result = cur.fetchone()
            # Sécuriser l'accès au résultat : vérifier None, tuple vide, ou valeur None
            if not extent_result or len(extent_result) == 0 or extent_result[0] is None:
                logger.debug(f"Table datagis '{table_name}' ignorée (extent NULL ou vide)")
                return False
            
            # Parser le BBOX PostGIS pour vérifier qu'il est valide
            import re
            bbox_str = str(extent_result[0])
            match = re.search(r'BOX\(([-\d.]+)\s+([-\d.]+),([-\d.]+)\s+([-\d.]+)\)', bbox_str)
            if not match:
                logger.debug(f"Table datagis '{table_name}' ignorée (extent non parsable)")
                return False
            
            table_bbox = [float(match.group(1)), float(match.group(2)), 
                         float(match.group(3)), float(match.group(4))]
            
            # Vérifier que l'extent est plausible (pas trop petit, pas trop grand)
            bbox_width = abs(table_bbox[2] - table_bbox[0])
            bbox_height = abs(table_bbox[3] - table_bbox[1])
            
            # Extent invalide si trop petit (< 0.0001 degrés ≈ 11 mètres) ou trop grand (> 360 degrés)
            if bbox_width < 0.0001 or bbox_height < 0.0001:
                logger.debug(f"Table datagis '{table_name}' ignorée (extent trop petit: {bbox_width:.6f} x {bbox_height:.6f})")
                return False
            
            if bbox_width > 360 or bbox_height > 180:
                logger.debug(f"Table datagis '{table_name}' ignorée (extent invalide: {bbox_width:.2f} x {bbox_height:.2f})")
                return False
            
            return True
        except Exception as e:
            logger.debug(f"Table datagis '{table_name}' ignorée (erreur validation: {e})")
            return False
    
    def _find_table_in_datagis_by_name(self, table_name: str) -> Optional[Dict[str, Any]]:
        """
        Cherche une table par son nom exact dans la base datagis
        
        Args:
            table_name: Nom exact de la table (pattern: {nom}_{hash})
            
        Returns:
            Dictionnaire avec table_name, geom_column, srid ou None
        """
        if not HAS_PSYCOPG2:
            return None
        
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Chercher la table par nom exact
            cur.execute("""
                SELECT f_table_name, f_geometry_column, srid
                FROM geometry_columns
                WHERE f_table_schema = 'public'
                AND f_table_name = %s
                LIMIT 1;
            """, (table_name,))
            
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            if result:
                srid = result[2]
                # Valider le SRID avant de le retourner
                if not self._validate_srid(srid):
                    logger.warning(f"SRID invalide détecté pour table '{table_name}': {srid}, utilisation de 4326 par défaut")
                    srid = 4326  # Utiliser 4326 (WGS84) par défaut pour les SRID invalides
                
                logger.debug(f"Table '{table_name}' trouvée dans datagis: geom={result[1]}, srid={srid}")
                return {
                    'table_name': result[0],
                    'geom_column': result[1],
                    'srid': srid
                }
            logger.debug(f"Table '{table_name}' non trouvée dans geometry_columns")
            return None
        except Exception as e:
            logger.warning(f"Erreur recherche table {table_name} dans datagis: {e}")
            return None
    
    def _find_table_in_datagis(self, dataset_name: str) -> Optional[Dict[str, Any]]:
        """
        DÉPRÉCIÉ : Cette fonction ne fait plus de recherche approximative.
        Elle cherche uniquement par nom exact pour compatibilité avec l'ancien format.
        
        Pour les nouvelles ressources, utilisez toujours _find_table_in_datagis_by_name() avec
        le format res_{resource_id} qui est garanti unique et sans ambiguïté.
        
        Cherche une table correspondant au dataset dans la base datagis (RECHERCHE EXACTE UNIQUEMENT)
        
        Args:
            dataset_name: Nom du dataset CKAN
            
        Returns:
            Dictionnaire avec table_name, geom_column, srid ou None
        """
        logger.warning(f"_find_table_in_datagis() appelée pour '{dataset_name}' - Cette fonction est DÉPRÉCIÉE")
        logger.warning(f"   Utilisez plutôt _find_table_in_datagis_by_name() avec res_{{resource_id}} pour une recherche exacte")
        logger.warning(f"   RECHERCHE APPROXIMATIVE DÉSACTIVÉE - Seule la recherche exacte par nom est utilisée")
        
        if not HAS_PSYCOPG2:
            return None
        
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # RECHERCHE EXACTE UNIQUEMENT - Plus de recherche approximative
            # Chercher par nom exact (avec underscores) - pour compatibilité ancien format uniquement
            clean_name = dataset_name.replace('-', '_')
            cur.execute("""
                SELECT f_table_name, f_geometry_column, srid
                FROM geometry_columns
                WHERE f_table_schema = 'public'
                AND f_table_name = %s
                LIMIT 1;
            """, (clean_name,))
            
            result = cur.fetchone()
            if result:
                # Valider la table avant de la retourner (preuves minimales)
                table_name_found = result[0]
                geom_column = result[1]
                srid = result[2]
                
                if self._validate_datagis_table_basic(table_name_found, geom_column, cur):
                    logger.info(f"Table trouvée par nom exact (ancien format): {table_name_found}")
                    cur.close()
                    conn.close()
                    return {
                        'table_name': table_name_found,
                        'geom_column': geom_column,
                        'srid': srid
                    }
                else:
                    logger.warning(f"Table datagis '{table_name_found}' trouvée par nom exact mais validation échouée")
                    cur.close()
                    conn.close()
                    return None
            
            # RECHERCHE APPROXIMATIVE COMPLÈTEMENT SUPPRIMÉE
            # Plus aucune recherche par mots-clés ou pattern - uniquement recherche exacte
            
            logger.warning(f"Aucune table datagis trouvée pour '{dataset_name}' avec recherche exacte")
            logger.warning(f"   Vérifiez que les ressources utilisent le format res_{{resource_id}}")
            logger.warning(f"   Ou que les anciennes tables utilisent un nom exact correspondant")
            logger.warning(f"   Recherche approximative désactivée pour éviter les faux positifs")
            
            cur.close()
            conn.close()
            return None
            
        except Exception as e:
            logger.warning(f"Erreur recherche dans datagis pour {dataset_name}: {e}")
            import traceback
            logger.debug(f"   Traceback: {traceback.format_exc()}")
            return None
    
    def _validate_datagis_table_match(self, candidate_tables: List[Tuple], dataset_name: str, dataset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        DÉPRÉCIÉ ET DÉSACTIVÉ : Cette fonction utilisait du scoring (recherche approximative).
        
        Cette fonction n'est plus utilisée et ne doit plus être appelée.
        Utilisez uniquement _find_table_in_datagis_by_name() avec le format res_{resource_id}
        pour une recherche exacte garantie.
        
        Cette fonction est conservée uniquement pour référence historique et sera supprimée
        dans une future version.
        """
        logger.error(f"ERREUR: _validate_datagis_table_match() appelée - Cette fonction est DÉSACTIVÉE")
        logger.error(f"   RECHERCHE APPROXIMATIVE COMPLÈTEMENT SUPPRIMÉE")
        logger.error(f"   Utilisez uniquement _find_table_in_datagis_by_name() avec res_{{resource_id}}")
        return None
    
    def _detect_unique_column_datastore(self, table_name: str) -> str:
        """
        Détecte la colonne unique pour une table datastore
        
        Args:
            table_name: Nom de la table
            
        Returns:
            Nom de la colonne unique (_id, id, gid, ou _id par défaut)
        """
        if not HAS_PSYCOPG2:
            return '_id'  # Fallback
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=15
            )
            cur = conn.cursor()
            cur.execute("SET statement_timeout TO '30s';")
            # Vérifier l'existence de différentes colonnes uniques (par ordre de priorité)
            unique_columns = ['_id', 'id', 'gid', 'oid']
            for col in unique_columns:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_schema = 'public' 
                        AND table_name = %s
                        AND column_name = %s
                    );
                """, (table_name, col))
                
                result = cur.fetchone()
                if result and len(result) > 0 and result[0]:
                    logger.info(f"Colonne unique trouvée pour {table_name}: {col}")
                    return col
            
            logger.warning(f"Aucune colonne unique standard trouvée pour {table_name}, utilisation de _id par défaut")
            return '_id'  # Fallback
            
        except Exception as e:
            logger.warning(f"Erreur détection colonne unique pour {table_name}: {e}")
            return '_id'  # Fallback
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _detect_unique_column(self, table_name: str) -> str:
        """
        Détecte la colonne d'identifiant unique d'une table datagis
        
        Args:
            table_name: Nom de la table
            
        Returns:
            Nom de la colonne d'identifiant unique (_id, fid, id, etc.)
        """
        if not HAS_PSYCOPG2:
            return "_id"  # Par défaut
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=15
            )
            cur = conn.cursor()
            cur.execute("SET statement_timeout TO '30s';")
            # Ordre de priorité pour les colonnes d'identifiant unique
            possible_ids = ['_id', 'fid', 'id', 'gid', 'oid']
            
            # Vérifier si une de ces colonnes existe
            for col_name in possible_ids:
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = %s 
                    AND column_name = %s;
                """, (table_name, col_name))
                
                if cur.fetchone():
                    return col_name
            
            # Si aucune colonne standard trouvée, chercher la clé primaire
            cur.execute("""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass
                AND i.indisprimary
                LIMIT 1;
            """, (table_name,))
            
            result = cur.fetchone()
            if result:
                return result[0]
            
            # Par défaut, utiliser _id (même si elle n'existe pas, MapServer peut fonctionner sans)
            return "_id"
            
        except Exception as e:
            logger.warning(f"Erreur détection colonne unique pour {table_name}: {e}")
            return "_id"  # Par défaut
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _detect_geometry_type(self, table_name: str, geom_column: str) -> str:
        """
        Détecte le type de géométrie d'une table datagis
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            
        Returns:
            Type MapServer (POINT, LINE, POLYGON)
        """
        if not HAS_PSYCOPG2:
            return "POLYGON"  # Par défaut
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=15
            )
            cur = conn.cursor()
            cur.execute("SET statement_timeout TO '30s';")
            # Détecter le type de géométrie (uniquement lignes à géométrie valide)
            cur.execute(f"""
                SELECT ST_GeometryType({geom_column})
                FROM "{table_name}"
                WHERE {geom_column} IS NOT NULL AND ST_IsValid({geom_column})
                LIMIT 1;
            """)
            
            result = cur.fetchone()
            if result:
                geom_type = str(result[0]).lower()
                # MultiPoint/Point -> POINT (MapServer utilise TYPE POINT pour les deux)
                if 'multipoint' in geom_type or 'point' in geom_type:
                    return "POINT"
                elif 'multilinestring' in geom_type or 'linestring' in geom_type or 'line' in geom_type:
                    return "LINE"
                elif 'multipolygon' in geom_type or 'polygon' in geom_type:
                    return "POLYGON"
            
            return "POLYGON"  # Par défaut
            
        except Exception as e:
            logger.warning(f"Erreur détection type géométrie pour {table_name}: {e}")
            return "POLYGON"  # Par défaut
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
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
    
    def get_srid_from_table(self, table_name: str, geom_column: str) -> Optional[int]:
        """
        Récupère le SRID réel d'une table depuis geometry_columns
        Valide que le SRID est un code EPSG valide
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            
        Returns:
            SRID (int) valide ou None si non trouvé/invalide
        """
        if not HAS_PSYCOPG2:
            return None
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Récupérer le SRID depuis geometry_columns
            cur.execute("""
                SELECT srid 
                FROM geometry_columns 
                WHERE f_table_name = %s 
                AND f_geometry_column = %s
                AND f_table_schema = 'public'
                LIMIT 1;
            """, (table_name, geom_column))
            
            result = cur.fetchone()
            
            if result:
                srid = result[0]
                if self._validate_srid(srid):
                    return srid
                logger.warning(f"SRID invalide détecté pour {table_name}.{geom_column}: {srid}, utilisation de 4326 par défaut")
                return None
            
            # Fallback: détecter depuis les données (fermer 1re connexion puis ouvrir 2e)
            try:
                cur.close()
                conn.close()
            except Exception:
                pass
            cur = None
            conn = None
            
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            cur.execute(f"""
                SELECT ST_SRID({geom_column}) 
                FROM "{table_name}" 
                WHERE {geom_column} IS NOT NULL 
                LIMIT 1;
            """)
            result = cur.fetchone()
            if result:
                srid = result[0]
                if self._validate_srid(srid):
                    return srid
                logger.warning(f"SRID invalide détecté via ST_SRID() pour {table_name}.{geom_column}: {srid}, utilisation de 4326 par défaut")
            return None
            
        except Exception as e:
            logger.warning(f"Erreur récupération SRID pour {table_name}.{geom_column}: {e}")
            return None
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _detect_geometry_type_datastore(self, table_name: str, geom_column: str) -> str:
        """
        Détecte le type de géométrie d'une table datastore
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            
        Returns:
            Type MapServer (POINT, LINE, POLYGON)
        """
        if not HAS_PSYCOPG2:
            return "POLYGON"  # Par défaut
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.postgis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=15
            )
            cur = conn.cursor()
            cur.execute("SET statement_timeout TO '30s';")
            # Détecter le type de géométrie (uniquement lignes à géométrie valide)
            cur.execute(f"""
                SELECT ST_GeometryType({geom_column})
                FROM "{table_name}"
                WHERE {geom_column} IS NOT NULL AND ST_IsValid({geom_column})
                LIMIT 1;
            """)
            
            result = cur.fetchone()
            # Sécuriser l'accès au résultat : vérifier None, tuple vide, ou valeur None
            if result and len(result) > 0 and result[0] is not None:
                geom_type = str(result[0]).lower()
                # MultiPoint/Point -> POINT (MapServer utilise TYPE POINT pour les deux)
                if 'multipoint' in geom_type or 'point' in geom_type:
                    return "POINT"
                elif 'multilinestring' in geom_type or 'linestring' in geom_type or 'line' in geom_type:
                    return "LINE"
                elif 'multipolygon' in geom_type or 'polygon' in geom_type:
                    return "POLYGON"
            
            return "POLYGON"  # Par défaut (aucune géométrie valide ou vide)
            
        except Exception as e:
            logger.warning(f"Erreur détection type géométrie pour {table_name}: {e}")
            return "POLYGON"  # Par défaut
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _detect_geometry_type_datagis(self, table_name: str, geom_column: str) -> str:
        """
        Détecte le type de géométrie d'une table datagis
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            
        Returns:
            Type MapServer (POINT, LINE, POLYGON)
        """
        if not HAS_PSYCOPG2:
            return "POLYGON"  # Par défaut
        
        conn = None
        cur = None
        try:
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                database=self.datagis_db,
                user=self.postgis_user,
                password=self.postgis_password,
                connect_timeout=15
            )
            cur = conn.cursor()
            cur.execute("SET statement_timeout TO '30s';")
            # Détecter le type de géométrie (uniquement lignes à géométrie valide)
            cur.execute(f"""
                SELECT ST_GeometryType({geom_column})
                FROM "{table_name}"
                WHERE {geom_column} IS NOT NULL AND ST_IsValid({geom_column})
                LIMIT 1;
            """)
            
            result = cur.fetchone()
            # Sécuriser l'accès au résultat : vérifier None, tuple vide, ou valeur None
            if result and len(result) > 0 and result[0] is not None:
                geom_type = str(result[0]).lower()
                # MultiPoint/Point -> POINT (MapServer utilise TYPE POINT pour les deux)
                if 'multipoint' in geom_type or 'point' in geom_type:
                    if 'multipoint' in geom_type:
                        logger.info(f"      type géométrie datagis: MULTIPOINT → POINT (table {table_name})")
                    return "POINT"
                elif 'multilinestring' in geom_type or 'linestring' in geom_type or 'line' in geom_type:
                    return "LINE"
                elif 'multipolygon' in geom_type or 'polygon' in geom_type:
                    return "POLYGON"
            
            # Aucune ligne avec géométrie valide (table vide, ou toutes invalides)
            logger.info(f"      type géométrie datagis: aucune géométrie valide pour {table_name}, défaut POLYGON")
            return "POLYGON"
            
        except Exception as e:
            logger.warning(f"Erreur détection type géométrie datagis pour {table_name}: {e}")
            return "POLYGON"  # Par défaut
        finally:
            if cur is not None:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def _create_datagis_mapfile_content(self, dataset_name: str, dataset_title: str,
                                        table_name: str, geom_column: str, srid: int) -> str:
        """
        Crée le contenu d'un mapfile MapServer pour une table datagis
        Suit la même structure que generate-mapfiles-from-datagis.sh
        
        Args:
            dataset_name: Nom du dataset
            dataset_title: Titre du dataset
            table_name: Nom de la table dans datagis
            geom_column: Nom de la colonne géométrie (généralement the_geom)
            srid: SRID de la géométrie (généralement 4171 pour datagis)
            
        Returns:
            Contenu du mapfile
        """
        # Valider le SRID avant de l'utiliser
        if not self._validate_srid(srid):
            logger.warning(f"SRID invalide passé à _create_datagis_mapfile_content pour {dataset_name}: {srid}, utilisation de 4326 par défaut")
            srid = 4326
        
        # Détecter le type de géométrie
        geom_type = self._detect_geometry_type(table_name, geom_column)
        
        # Détecter la colonne d'identifiant unique
        unique_column = self._detect_unique_column(table_name)
        
        connection_string = (
            f"host={self.postgis_host} "
            f"port={self.postgis_port} "
            f"dbname={self.datagis_db} "
            f"user={self.postgis_user} "
            f"password={self.postgis_password}"
        )
        
        # Calculer la BBOX réelle depuis les données PostGIS
        # Format MapServer: minx miny maxx maxy (espaces, pas de virgules)
        bbox = self._calculate_bbox_from_postgis(table_name, geom_column, db_name=self.datagis_db, srid=4326)
        if not bbox:
            # Fallback: utiliser une BBOX par défaut (France)
            logger.warning(f"Impossible de calculer le BBOX pour {table_name}, utilisation du BBOX par défaut (France)")
            bbox = "-5.0 41.0 10.0 51.0"
        else:
            logger.info(f"BBOX calculé pour {table_name}: {bbox}")
        
        # Log le SRID natif de la table pour information
        logger.info(f"SRID natif de la table: {srid}, mapfile généré en EPSG:4326 (BBOX transformé)")
        
        # Déterminer le style selon le type
        if geom_type == "POINT":
            style_content = """            STYLE
                SYMBOL "circle_point"
                SIZE 6
                COLOR 0 0 255
                OUTLINECOLOR 255 255 255
                WIDTH 1
            END"""
        elif geom_type == "LINE":
            style_content = """            STYLE
                COLOR 0 0 255
                WIDTH 2
            END"""
        else:  # POLYGON
            style_content = """            STYLE
                COLOR 200 200 255
                OUTLINECOLOR 0 0 255
                WIDTH 1
            END"""
        
        mapfile = f"""MAP
    NAME "{dataset_name}"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT {bbox}
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255

    # Sortie GeoJSON pour le WFS (GetFeature OUTPUTFORMAT=geojson) et clients web
    OUTPUTFORMAT
        NAME "geojson"
        DRIVER "OGR/GEOJSON"
        MIMETYPE "application/json; subtype=geojson"
        FORMATOPTION "STORAGE=stream"
        FORMATOPTION "FORM=SIMPLE"
    END
    FONTSET "/mapserver/fonts/fonts.list"
    
    # Configuration
    CONFIG "PROJ_LIB" "/usr/share/proj"
    CONFIG "MS_ERRORFILE" "/mapserver/logs/{dataset_name}_error.log"
    
    # Projection - Toujours utiliser EPSG:4326 (WGS84) car BBOX calculé en 4326
    # PostGIS transforme automatiquement via USING SRID=4326 dans la clause DATA
    PROJECTION
        "init=epsg:4326"
    END
    
    # Métadonnées WMS/WFS (cohérent avec generate-mapfiles-from-datagis.sh)
    WEB
        METADATA
            "wms_title" "{dataset_title} (datagis)"
            "wms_onlineresource" "http://localhost:8081/wms?map=/mapserver/mapfiles/{dataset_name}.map"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wms_getlegendgraphic_formatlist" "image/png,image/gif,image/jpeg"
            "wfs_title" "{dataset_title} (datagis)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
            "ows_enable_request" "*"
        END
    END
    
    # Symboles
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    
    # Layer principal - même structure que generate-mapfiles-from-datagis.sh
    # USING SRID=4326 force PostGIS à transformer les données vers WGS84 à la volée
    LAYER
        NAME "{dataset_name}"
        TYPE {geom_type}
        STATUS ON
        CONNECTIONTYPE postgis
        CONNECTION "{connection_string}"
        DATA "{geom_column} FROM \\"{table_name}\\" USING SRID=4326 USING UNIQUE {unique_column}"
        PROJECTION
            "init=epsg:4326"
        END
        CLASS
            NAME "default"
{style_content}
        END
        
        # Métadonnées du layer
        METADATA
            "wms_title" "{dataset_title} (datagis)"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wfs_title" "{dataset_title} (datagis)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
        END
    END
END
"""
        return mapfile
    
    def _get_ogc_metadata_from_extras(self, dataset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Récupère les métadonnées OGC stockées dans les extras du dataset
        
        Args:
            dataset: Dictionnaire du dataset CKAN
            
        Returns:
            Dictionnaire avec source_type, table_name, geom_column, srid, bbox ou None
        """
        extras = dataset.get('extras', [])
        if not isinstance(extras, list):
            return None
        
        metadata = {}
        for extra in extras:
            if isinstance(extra, dict):
                key = extra.get('key', '')
                value = extra.get('value', '')
                
                if key == 'ogc:source':
                    metadata['source_type'] = value
                elif key == 'ogc:table':
                    metadata['table_name'] = value
                elif key == 'ogc:geom_column':
                    metadata['geom_column'] = value
                elif key == 'ogc:srid':
                    try:
                        metadata['srid'] = int(value)
                    except (ValueError, TypeError):
                        pass
                elif key == 'ogc:bbox':
                    try:
                        metadata['bbox'] = json.loads(value) if isinstance(value, str) else value
                    except (json.JSONDecodeError, TypeError):
                        pass
        
        # Retourner seulement si on a au moins source_type et table_name (pour datagis/datastore)
        if metadata.get('source_type') and metadata.get('table_name'):
            return metadata
        
        return None
    
    def _calculate_bbox_from_postgis(self, table_name: str, geom_column: str, 
                                     db_name: str = None, srid: int = None) -> Optional[str]:
        """
        Calcule le BBOX réel depuis une table PostGIS
        
        Args:
            table_name: Nom de la table
            geom_column: Nom de la colonne géométrie
            db_name: Nom de la base de données (None = utiliser self.postgis_db)
            srid: SRID cible pour la transformation (None = utiliser 4326)
            
        Returns:
            BBOX au format MapServer "minx miny maxx maxy" ou None
        """
        if not HAS_PSYCOPG2:
            return None
        
        try:
            target_srid = srid if srid else 4326
            db_to_use = db_name if db_name else self.postgis_db
            
            conn = psycopg2.connect(
                host=self.postgis_host,
                port=self.postgis_port,
                dbname=db_to_use,
                user=self.postgis_user,
                password=self.postgis_password
            )
            cur = conn.cursor()
            
            # Calculer l'EXTENT directement depuis PostGIS (table déjà en 4326)
            # La table est supposée être en 4326 grâce à l'import avec -s_srs/-t_srs
            cur.execute(f"""
                SELECT 
                    ST_XMin(ST_Extent("{geom_column}")) as minx,
                    ST_YMin(ST_Extent("{geom_column}")) as miny,
                    ST_XMax(ST_Extent("{geom_column}")) as maxx,
                    ST_YMax(ST_Extent("{geom_column}")) as maxy
                FROM "{table_name}"
                WHERE "{geom_column}" IS NOT NULL
            """)
            
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            if result and len(result) >= 4:
                minx, miny, maxx, maxy = result[0], result[1], result[2], result[3]
                if all(v is not None for v in [minx, miny, maxx, maxy]):
                    # Format MapServer: minx miny maxx maxy (espaces)
                    bbox_str = f"{minx} {miny} {maxx} {maxy}"
                    logger.info(f"BBOX calculé pour {table_name}: {bbox_str}")
                    return bbox_str
            
            return None
        except Exception as e:
            logger.warning(f"Erreur calcul BBOX pour {table_name}: {e}")
            return None
    
    def generate_mapfile(self, dataset: Dict[str, Any]) -> bool:
        """
        Génère un mapfile MapServer pour un dataset
        
        Args:
            dataset: Dictionnaire du dataset CKAN
            
        Returns:
            True si succès, False sinon
        """
        try:
            dataset_name = dataset.get('name')
            dataset_title = dataset.get('title', dataset_name)
            dataset_id = dataset.get('id', 'N/A')
            
            logger.info("=" * 80)
            logger.info(f"DÉBUT GÉNÉRATION MAPFILE")
            logger.info(f"   Dataset: {dataset_name} (ID: {dataset_id})")
            logger.info(f"   Titre: {dataset_title}")
            logger.info(f"   Recherche dans datagis: {self.use_datagis}")
            logger.info(f"   Auto-création géométrie: {self.auto_create_geometry}")
            logger.info("=" * 80)
            
            # NETTOYAGE PRÉALABLE: Vérifier et supprimer les mapfiles OGR invalides existants
            mapfile_path = self.mapfiles_dir / f"{dataset_name}.map"
            if mapfile_path.exists():
                logger.info(f"Vérification du mapfile existant: {mapfile_path}")
                if self._cleanup_invalid_ogr_mapfile(str(mapfile_path)):
                    logger.info(f"   Mapfile OGR invalide supprimé, génération d'un nouveau mapfile...")
                else:
                    logger.info(f"   Mapfile existant valide ou non-OGR, vérification si régénération nécessaire...")
            
            # Toujours passer par toutes les étapes (datagis + datastore + rasters) pour générer
            # un mapfile multi-couches cohérent. Pas de raccourci via les extras OGC.
            
            # ÉTAPE PRÉLIMINAIRE: Créer automatiquement les colonnes geometry depuis st_asgeojson/geo_point_2d
            # si auto_create_geometry est activé et que les ressources ont datastore_active
            if self.auto_create_geometry:
                logger.info("Vérification et création automatique des colonnes geometry...")
                for resource in dataset.get('resources', []):
                    if resource.get('datastore_active'):
                        resource_id = resource.get('id')
                        if resource_id:
                            try:
                                # Vérifier si la colonne geometry existe déjà
                                if not self._check_geometry_column_exists(resource_id):
                                    # Vérifier s'il y a des colonnes GeoJSON (st_asgeojson, geo_point_2d)
                                    url = f"{self.ckan_url}/api/action/datastore_search"
                                    params = {'resource_id': resource_id, 'limit': 0}
                                    response = requests.get(url, params=params, headers=self.headers, timeout=10)
                                    if response.status_code == 200:
                                        result = response.json()
                                        if result.get('success'):
                                            fields = result.get('result', {}).get('fields', [])
                                            field_names = [f.get('id', '').lower() for f in fields]
                                            
                                            # Chercher st_asgeojson ou geo_point_2d ou geopoint2d
                                            geojson_fields = []
                                            for field in fields:
                                                field_name_lower = field.get('id', '').lower()
                                                field_type = field.get('type', '').lower()
                                                if field_name_lower in ['st_asgeojson', 'geo_point_2d', 'geopoint2d', 'geopoint'] and ('text' in field_type or 'varchar' in field_type):
                                                    geojson_fields.append(field.get('id'))
                                            
                                            # Créer la colonne geometry depuis la première colonne GeoJSON trouvée
                                            if geojson_fields:
                                                geojson_field = geojson_fields[0]
                                                logger.info(f"Création automatique de la colonne geometry depuis '{geojson_field}' pour ressource {resource_id}...")
                                                if self._create_geometry_from_geojson(resource_id, geojson_field):
                                                    logger.info(f"Colonne geometry créée automatiquement depuis '{geojson_field}' pour ressource {resource_id}")
                                                else:
                                                    logger.warning(f"Échec de la création de la colonne geometry depuis '{geojson_field}' pour ressource {resource_id}")
                            except Exception as e:
                                logger.warning(f"Erreur lors de la création automatique de geometry pour ressource {resource_id}: {e}")
            
            # NOUVELLE LOGIQUE: Collecter TOUTES les tables datagis ET tous les layers datastore
            # Puis générer UN SEUL mapfile multi-layer avec les deux catégories
            datagis_layers = []  # Liste de toutes les tables datagis trouvées
            datastore_layers = []  # Liste de tous les layers datastore valides
            
            logger.info("=" * 80)
            logger.info(f"COLLECTE DE TOUTES LES SOURCES DE DONNÉES GÉOSPATIALES")
            logger.info(f"   Objectif: Générer UN SEUL mapfile multi-layer")
            logger.info(f"   - Tables DATAGIS (format res_{{resource_id}})")
            logger.info(f"   - Layers DATASTORE valides")
            logger.info("=" * 80)
            
            # ÉTAPE 1: Collecter TOUTES les tables datagis (une par ressource importée)
            # Pour chaque ressource on lit les extras (sld_style ou sld_style_<resource_id>) pour inclure le SLD dans le mapfile
            if self.use_datagis:
                logger.info("=" * 80)
                logger.info(f"ÉTAPE 1: Collecte de TOUTES les tables DATAGIS...")
                logger.info(f"   Dataset: {dataset_name} ({dataset_title})")
                logger.info(f"   Nombre de ressources: {len(dataset.get('resources', []))}")
                logger.info("=" * 80)
                
                # Parcourir TOUTES les ressources pour chercher les tables datagis (déduplication par table)
                logger.info(f"   Parcours de {len(dataset.get('resources', []))} ressource(s) pour collecter les tables datagis...")
                datagis_tables_seen = set()  # évite doublons si même table/ressource en double
                for idx, resource in enumerate(dataset.get('resources', []), 1):
                    resource_id = resource.get('id')
                    resource_name = resource.get('name', 'N/A')
                    resource_format = resource.get('format', 'N/A')
                    
                    logger.info(f"   [{idx}/{len(dataset.get('resources', []))}] Ressource: {resource_name} (ID: {resource_id}, format: {resource_format})")
                    
                    if not resource_id:
                        logger.warning(f"      Ressource sans ID, ignorée")
                        continue
                    
                    # Si la ressource est dans le datastore (ex. CSV remplacé), ignorer datagis pour cette ressource :
                    # la table datagis peut être obsolète (ancien ZIP/shapefile) et le datastore a les données à jour.
                    if resource.get('datastore_active', False):
                        logger.info(f"      Ressource datastore_active=True → ignorer datagis (sera utilisée via datastore)")
                        continue
                    
                    # Format: res_{resource_id_complet_nettoye}
                    resource_id_clean = resource_id.replace('-', '_')
                    resource_id_clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in resource_id_clean)
                    expected_table_name = f"res_{resource_id_clean}"
                    
                    # Limiter si nécessaire (PostgreSQL limite à 63 caractères)
                    if len(expected_table_name) > 63:
                        max_id_length = 63 - 4  # 4 caractères pour "res_"
                        expected_table_name = f"res_{resource_id_clean[:max_id_length]}"
                        logger.info(f"      Nom de table tronqué à 63 caractères: {expected_table_name}")
                    
                    logger.info(f"      Recherche table datagis: {expected_table_name}")
                    datagis_table = self._find_table_in_datagis_by_name(expected_table_name)
                    
                    if datagis_table:
                        table_name = datagis_table['table_name']
                        if table_name in datagis_tables_seen:
                            logger.info(f"      Table déjà collectée (doublon ignoré): {table_name}")
                            continue
                        datagis_tables_seen.add(table_name)
                        logger.info(f"      Table trouvée dans datagis: {table_name}")
                        logger.info(f"      Colonne géométrie: {datagis_table['geom_column']}, SRID: {datagis_table['srid']}")
                        # SLD : extras ressource puis dataset ; si absent (API sans extras), resource_show
                        sld_xml = _get_sld_style_for_resource(dataset, resource_id, resource)
                        if sld_xml is None and getattr(self, 'ckan_url', None):
                            res_full = self.get_resource_details(resource_id)
                            if res_full:
                                sld_xml = _get_sld_style_for_resource(dataset, resource_id, resource, resource_fallback=res_full)
                        if sld_xml:
                            logger.info(f"      SLD trouvé pour ressource {resource_id}")
                        datagis_layers.append({
                            'resource_id': resource_id,
                            'resource_name': resource_name,
                            'table_name': table_name,
                            'geom_column': datagis_table['geom_column'],
                            'srid': datagis_table['srid'],
                            'source_type': 'datagis',
                            'sld_style': sld_xml,
                        })
                    else:
                        logger.info(f"      Table non trouvée dans datagis: {expected_table_name}")
                
                logger.info(f"   {len(datagis_layers)} table(s) datagis collectée(s)")
            
            # ÉTAPE 2: Collecter TOUS les layers datastore valides
            # Pour chaque ressource on lit les extras (sld_style ou sld_style_<resource_id>) pour inclure le SLD dans le mapfile
            logger.info("=" * 80)
            logger.info(f"ÉTAPE 2: Collecte de TOUS les layers DATASTORE valides...")
            logger.info(f"   Nombre de ressources dans le dataset: {len(dataset.get('resources', []))}")
            logger.info("=" * 80)
            datastore_tables_seen = set()  # (table_name, geom_column) pour éviter doublons
            for resource in dataset.get('resources', []):
                resource_id = resource.get('id', 'N/A')
                resource_name = resource.get('name', 'N/A')
                datastore_active = resource.get('datastore_active', False)
                logger.info(f"   Vérification ressource: {resource_name} (ID: {resource_id}, datastore_active: {datastore_active})")
                
                if datastore_active:
                    has_geom = self._has_geometry_column(resource)
                    logger.info(f"      → _has_geometry_column={has_geom}")
                    if has_geom:
                        logger.info(f"      → Appel de get_geometry_column_name pour {resource_id}...")
                        geom_col = self.get_geometry_column_name(resource_id)
                        logger.info(f"      → get_geometry_column_name retourne: {geom_col}")
                        if geom_col:
                            table_name = self.get_datastore_table_name(resource_id)
                            if not table_name:
                                logger.warning(f"      Impossible de déterminer le nom de table pour ressource {resource_id}, ignorée")
                                continue
                            key = (table_name, geom_col)
                            if key in datastore_tables_seen:
                                logger.info(f"      Layer déjà collecté (doublon ignoré): table {table_name}, colonne {geom_col}")
                                continue
                            datastore_tables_seen.add(key)
                            
                            # Détecter le SRID réel depuis geometry_columns
                            srid = self.get_srid_from_table(table_name, geom_col)
                            if srid is None:
                                logger.warning(f"      Impossible de détecter le SRID pour {table_name}, utilisation de 4326 par défaut")
                                srid = 4326
                            elif not self._validate_srid(srid):
                                logger.warning(f"      SRID invalide détecté pour {table_name}: {srid}, utilisation de 4326 par défaut")
                                srid = 4326
                            else:
                                logger.info(f"      SRID détecté pour {table_name}: {srid}")
                            
                            # SLD : extras ressource puis dataset ; si absent (API sans extras), resource_show
                            sld_xml = _get_sld_style_for_resource(dataset, resource_id, resource)
                            if sld_xml is None and getattr(self, 'ckan_url', None):
                                res_full = self.get_resource_details(resource_id)
                                if res_full:
                                    sld_xml = _get_sld_style_for_resource(dataset, resource_id, resource, resource_fallback=res_full)
                            if sld_xml:
                                logger.info(f"      SLD trouvé pour ressource {resource_id}")
                            datastore_layers.append({
                                'resource_id': resource_id,
                                'resource_name': resource_name,
                                'table_name': table_name,
                                'geom_column': geom_col,
                                'srid': srid,
                                'source_type': 'datastore',
                                'sld_style': sld_xml,
                            })
                            logger.info(f"      Layer datastore ajouté: {resource_name} → Table: {table_name}, Colonne: {geom_col}, SRID: {srid}")
            
            logger.info(f"   {len(datastore_layers)} layer(s) datastore collecté(s)")
            
            # ÉTAPE 2b: Collecter les rasters (tif, tiff, jp2, img, asc) — en dernier dans le mapfile
            raster_layers = []
            raster_data_paths_seen = set()  # évite doublons (même fichier / même ressource)
            for resource in dataset.get('resources', []):
                if not self._is_raster_resource(resource):
                    continue
                resource_id = resource.get('id', '')
                resource_name = resource.get('name', 'N/A')
                logger.info(f"   Raster: {resource_name} (ID: {resource_id})")
                prep = self._prepare_raster_for_mapserver(resource, dataset_name)
                if not prep:
                    logger.warning(f"      Fichier raster introuvable ou inutilisable, ignoré")
                    continue
                data_path = prep['data_path']
                if data_path in raster_data_paths_seen:
                    logger.info(f"      Raster déjà collecté (doublon ignoré): {data_path}")
                    continue
                raster_data_paths_seen.add(data_path)
                raster_layers.append({
                    'resource_id': resource_id,
                    'resource_name': resource_name,
                    'source_type': 'raster',
                    'data_path': data_path,
                    'nodata': prep.get('nodata'),
                    'srid': prep.get('srid'),
                })
            logger.info(f"   {len(raster_layers)} layer(s) raster collecté(s)")
            
            # ÉTAPE 2c: Fallback OGR pour ressources vectorielles (SHP, GeoJSON, etc.) sans table datagis
            # Permet d'inclure dans le mapfile les ressources "fichier" dont la table datagis n'existe pas encore,
            # pour éviter l'erreur WMS "Invalid layer(s) given" quand on demande LAYERS=res_xxx.
            resource_ids_in_map = {lay.get('resource_id') for lay in datagis_layers + datastore_layers if lay.get('resource_id')}
            ogr_fallback_layers = []
            for resource in dataset.get('resources', []):
                resource_id = resource.get('id', '')
                if not resource_id or resource_id in resource_ids_in_map:
                    continue
                if resource.get('datastore_active', False) or self._is_raster_resource(resource):
                    continue
                if not self._is_vector_ogr_resource(resource):
                    continue
                resource_name = resource.get('name', 'N/A')
                local_path = self._get_resource_file_path(resource)
                if not local_path or not os.path.exists(local_path):
                    logger.debug(f"   OGR fallback: pas de fichier local pour {resource_name} ({resource_id})")
                    continue
                data_path_ogr = self._prepare_ogr_for_mapserver(resource, dataset_name)
                if not data_path_ogr:
                    data_path_ogr = local_path
                geom_type = self._detect_geometry_type_ogr(local_path)
                logger.info(f"   OGR fallback: {resource_name} (ID: {resource_id}) → {data_path_ogr} (type: {geom_type})")
                resource_ids_in_map.add(resource_id)
                ogr_fallback_layers.append({
                    'resource_id': resource_id,
                    'resource_name': resource_name,
                    'source_type': 'ogr_local',
                    'data_path': data_path_ogr,
                    'geometry_type': geom_type,
                    'srid': 4326,
                })
            logger.info(f"   {len(ogr_fallback_layers)} layer(s) OGR fallback collecté(s)")
            
            # ÉTAPE 3: Vérifier qu'on a au moins une source (vecteurs ou rasters)
            all_layers = datagis_layers + datastore_layers + ogr_fallback_layers + raster_layers
            if not all_layers:
                logger.warning("=" * 80)
                logger.warning(f"AUCUNE SOURCE DE DONNÉES GÉOSPATIALES TROUVÉE")
                logger.warning(f"   Dataset: {dataset_name}")
                logger.warning(f"   → DATAGIS: {len(datagis_layers)} table(s) trouvée(s)")
                logger.warning(f"   → DATASTORE: {len(datastore_layers)} layer(s) trouvé(s)")
                logger.warning(f"   → OGR FALLBACK: {len(ogr_fallback_layers)} layer(s) trouvé(s)")
                logger.warning(f"   → RASTER: {len(raster_layers)} layer(s) trouvé(s)")
                logger.warning(f"   → Aucun mapfile ne sera généré pour ce dataset")
                logger.warning("=" * 80)
                self._log_performance_metrics()
                return False
            
            # ÉTAPE 4: Générer UN SEUL mapfile multi-layer avec toutes les sources (vecteurs puis rasters)
            logger.info("=" * 80)
            logger.info(f"ÉTAPE 4: Génération d'UN SEUL mapfile multi-layer")
            logger.info(f"   Total: {len(all_layers)} layer(s)")
            logger.info(f"   - {len(datagis_layers)} table(s) datagis")
            logger.info(f"   - {len(datastore_layers)} layer(s) datastore")
            logger.info(f"   - {len(ogr_fallback_layers)} layer(s) OGR fallback")
            logger.info(f"   - {len(raster_layers)} raster(s)")
            logger.info("=" * 80)
            
            # Générer le mapfile avec tous les layers (datagis + datastore)
            mapfile_content = self._create_mapfile_content_multilayer(
                dataset_name=dataset_name,
                dataset_title=dataset_title,
                layers_info=all_layers
            )
            if mapfile_content is None:
                logger.warning(f"Aucune couche valide pour {dataset_name}, mapfile non généré")
                return False
            
            # Sauvegarder le mapfile avec gestion améliorée des erreurs
            mapfile_path = self.mapfiles_dir / f"{dataset_name}.map"
            logger.info(f"Sauvegarde du mapfile: {mapfile_path}")
            
            try:
                    # Vérifier les permissions avant d'écrire
                    # self.mapfiles_dir est un Path, utiliser .exists() au lieu de os.path.exists()
                    if self.mapfiles_dir.exists():
                        # Convertir en string pour os.access et os.stat
                        mapfiles_dir_str = str(self.mapfiles_dir)
                        if not os.access(mapfiles_dir_str, os.W_OK):
                            logger.error(f"Erreur de permission: Le répertoire {self.mapfiles_dir} n'est pas accessible en écriture.")
                            stat_info = os.stat(mapfiles_dir_str)
                            logger.error(f"   Permissions actuelles: {oct(stat_info.st_mode)}")
                            logger.error(f"   Propriétaire: {stat_info.st_uid}:{stat_info.st_gid}")
                            return False
                        
                        # Vérifier l'espace disque (shutil.disk_usage accepte Path ou string)
                        total, used, free = shutil.disk_usage(self.mapfiles_dir)
                        if free < 10 * 1024 * 1024:  # Moins de 10MB libre
                            logger.error(f"Espace disque insuffisant dans {self.mapfiles_dir}. Libre: {free / (1024 * 1024):.2f} MB")
                            return False
                    else:
                        logger.error(f"Le répertoire des mapfiles {self.mapfiles_dir} n'existe pas.")
                        return False
                    
                    # Garde anti course : un job de génération a pu être lancé AVANT une
                    # suppression/purge du jeu et n'écrire le mapfile qu'APRÈS (le sous-processus
                    # a lu le package quand il existait encore, puis écrit tardivement). On
                    # revérifie l'existence du jeu juste avant d'écrire : s'il a disparu entre
                    # temps, on n'écrit pas de mapfile fantôme (sinon il survit à la suppression).
                    if self.ckan_url and self.get_package_details(dataset_name) is None:
                        logger.warning(f"Jeu '{dataset_name}' introuvable au moment d'écrire le mapfile (supprimé entre temps) : écriture annulée.")
                        return False

                    # mapfile_path est un Path, utiliser str() pour open() ou utiliser .write_text()
                    # Utiliser un fichier temporaire puis déplacement atomique pour éviter les problèmes de verrouillage
                    mapfile_path_str = str(mapfile_path)
                    import tempfile
                    
                    # Créer un fichier temporaire dans le même répertoire
                    temp_fd, temp_path = tempfile.mkstemp(
                        dir=str(self.mapfiles_dir),
                        prefix=f'.{dataset_name}.',
                        suffix='.map.tmp'
                    )
                    
                    try:
                        # Écrire le contenu dans le fichier temporaire
                        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                            f.write(mapfile_content)
                        
                        # Changer les permissions du fichier temporaire
                        os.chmod(temp_path, 0o644)  # rw-r--r--
                        
                        # Déplacer atomiquement le fichier temporaire vers le fichier final
                        # Cela évite les problèmes de verrouillage si plusieurs processus écrivent en même temps
                        if os.path.exists(mapfile_path_str):
                            # Si le fichier existe déjà, le remplacer atomiquement
                            os.replace(temp_path, mapfile_path_str)
                        else:
                            # Si le fichier n'existe pas, simplement renommer
                            os.rename(temp_path, mapfile_path_str)
                    except Exception as write_e:
                        # Nettoyer le fichier temporaire en cas d'erreur
                        try:
                            if os.path.exists(temp_path):
                                os.unlink(temp_path)
                        except Exception:
                            pass
                        raise write_e
                    
                    # Vérifier que le fichier existe bien
                    if os.path.exists(mapfile_path_str):
                        file_size = os.path.getsize(mapfile_path_str)
                        logger.info(f"Fichier mapfile créé avec succès")
                        logger.info(f"   Taille: {file_size} octets")
                    else:
                        logger.error(f"Le fichier mapfile n'existe pas après écriture")
                        return False
                
                    logger.info("=" * 80)
                    logger.info(f"MAPFILE MULTI-LAYER GÉNÉRÉ AVEC SUCCÈS")
                    logger.info(f"   Fichier: {mapfile_path}")
                    logger.info(f"   Dataset: {dataset_name}")
                    logger.info(f"   Nombre de layers: {len(all_layers)}")
                    logger.info(f"   - {len(datagis_layers)} table(s) datagis")
                    logger.info(f"   - {len(datastore_layers)} layer(s) datastore")
                    logger.info(f"   - {len(raster_layers)} raster(s)")
                    logger.info("=" * 80)
                    
                    # Enregistrer les métadonnées OGC dans les extras
                    # Utiliser la première source trouvée (priorité datagis si disponible).
                    # Les rasters n'ont pas de table_name/geom_column : on les saute pour
                    # eviter un KeyError, ils utiliseront le mapfile pour SLD/style brut.
                    if all_layers:
                        first_layer = next(
                            (lyr for lyr in all_layers if 'table_name' in lyr),
                            None,
                        )
                        if first_layer is not None:
                            self._save_ogc_metadata_to_extras(
                                dataset_id=dataset_id,
                                source_type=first_layer.get('source_type', 'datastore'),
                                table_name=first_layer['table_name'],
                                geom_column=first_layer.get('geom_column'),
                                srid=first_layer.get('srid'),
                            )
                        else:
                            logger.debug(
                                "Aucune couche vecteur (datagis/datastore) trouvée pour enregistrer "
                                "les extras OGC ; uniquement des rasters."
                            )
                    
                    self._log_performance_metrics()
                    return True
            except OSError as e:
                    # Logging détaillé de l'erreur OSError
                    logger.error(f"OSError lors de l'écriture du mapfile {mapfile_path}")
                    logger.error(f"   Type d'erreur: {type(e).__name__}")
                    logger.error(f"   Message: {str(e)}")
                    logger.error(f"   Errno: {e.errno if hasattr(e, 'errno') else 'N/A'}")
                    logger.error(f"   Strerror: {e.strerror if hasattr(e, 'strerror') else 'N/A'}")
                    logger.error(f"   Filename: {e.filename if hasattr(e, 'filename') else 'N/A'}")
                    logger.error(f"   Vérifiez les permissions du répertoire {self.mapfiles_dir} et l'espace disque.")
                    
                    # Diagnostic détaillé
                    try:
                        mapfiles_dir_str = str(self.mapfiles_dir)
                        if self.mapfiles_dir.exists():
                            stat_info = os.stat(mapfiles_dir_str)
                            logger.error(f"   Permissions du répertoire: {oct(stat_info.st_mode)}")
                            logger.error(f"   Propriétaire: {stat_info.st_uid}:{stat_info.st_gid}")
                            logger.error(f"   Accessible en écriture: {os.access(mapfiles_dir_str, os.W_OK)}")
                            
                            # Vérifier l'espace disque
                            total, used, free = shutil.disk_usage(self.mapfiles_dir)
                            logger.error(f"   Espace disque - Total: {total / (1024**3):.2f} GB, Utilisé: {used / (1024**3):.2f} GB, Libre: {free / (1024**3):.2f} GB")
                            
                            # Vérifier les permissions du fichier parent si le fichier existe
                            if os.path.exists(mapfile_path):
                                try:
                                    file_stat = os.stat(mapfile_path)
                                    logger.error(f"   Fichier existe - Permissions: {oct(file_stat.st_mode)}, Propriétaire: {file_stat.st_uid}:{file_stat.st_gid}")
                                except Exception as file_stat_e:
                                    logger.error(f"   Erreur lors de la vérification du fichier: {file_stat_e}")
                        else:
                            logger.error(f"   Le répertoire {self.mapfiles_dir} n'existe pas")
                    except Exception as diag_e:
                        logger.error(f"   Erreur lors du diagnostic: {diag_e}")
                        import traceback
                        logger.error(f"   Traceback diagnostic: {traceback.format_exc()}")
                    
                    return False
            except Exception as e:
                logger.error(f"Erreur inattendue lors de la sauvegarde du mapfile: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False
            
        except Exception as e:
            logger.error("=" * 80)
            logger.error(f"ERREUR LORS DE LA GÉNÉRATION DU MAPFILE")
            logger.error(f"   Dataset: {dataset.get('name', 'N/A')}")
            logger.error(f"   Erreur: {e}")
            logger.error("=" * 80)
            import traceback
            logger.error(traceback.format_exc())
            self._log_performance_metrics()
            return False
    
    def _log_performance_metrics(self):
        """Affiche les métriques de performance"""
        total_queries = self._metrics['cache_hits'] + self._metrics['cache_misses']
        if total_queries > 0:
            cache_hit_rate = (self._metrics['cache_hits'] / total_queries) * 100
            avg_time = self._metrics['total_time'] / self._metrics['api_calls'] if self._metrics['api_calls'] > 0 else 0
            logger.info("=" * 80)
            logger.info("MÉTRIQUES DE PERFORMANCE")
            logger.info(f"   Cache hits: {self._metrics['cache_hits']}")
            logger.info(f"   Cache misses: {self._metrics['cache_misses']}")
            logger.info(f"   Taux de cache: {cache_hit_rate:.1f}%")
            logger.info(f"   Appels API: {self._metrics['api_calls']}")
            logger.info(f"   Temps total: {self._metrics['total_time']:.2f}s")
            logger.info(f"   Temps moyen par requête: {avg_time:.3f}s")
            if self._metrics['slow_queries']:
                logger.warning(f"   Requêtes lentes (>5s): {len(self._metrics['slow_queries'])}")
                for slow_query in self._metrics['slow_queries'][-5:]:  # Afficher les 5 dernières
                    logger.warning(f"      - {slow_query['resource_id']}: {slow_query['time']:.2f}s")
            logger.info("=" * 80)
    
    def _create_mapfile_content(self, dataset_name: str, dataset_title: str,
                               table_name: str, geom_column: str) -> str:
        """
        Crée le contenu d'un mapfile MapServer
        
        Args:
            dataset_name: Nom du dataset
            dataset_title: Titre du dataset
            table_name: Nom de la table PostGIS (peut contenir des tirets)
            geom_column: Nom de la colonne géométrie
            
        Returns:
            Contenu du mapfile
        """
        # Connexion PostGIS (format pour MapServer)
        # Note: MapServer utilise le format: host=... port=... dbname=... user=... password=...
        connection_string = (
            f"host={self.postgis_host} "
            f"port={self.postgis_port} "
            f"dbname={self.postgis_db} "
            f"user={self.postgis_user} "
            f"password={self.postgis_password}"
        )
        
        # Le nom de table doit être entre guillemets pour PostGIS si il contient des tirets
        # ou des caractères spéciaux
        if '-' in table_name or not table_name.replace('_', '').replace('-', '').isalnum():
            table_name_quoted = f'"{table_name}"'
        else:
            table_name_quoted = table_name
        
        # Détecter le type de géométrie automatiquement
        geometry_type = self._detect_geometry_type_datastore(table_name, geom_column)
        
        # Note: Le SRID peut varier selon les données (4326 pour WGS84, 2154 pour Lambert-93)
        # On utilise 4326 par défaut, mais cela peut être ajusté selon les données
        
        # Style selon le type de géométrie
        if geometry_type == "POINT":
            style_content = """        # Style par défaut (points rouges)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
                SYMBOL "circle_point"
                SIZE 6
            END
        END"""
            symbol_content = """    # Définir un symbole simple inline pour les points
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
"""
        elif geometry_type == "LINE":
            style_content = """        # Style par défaut (lignes rouges)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                WIDTH 2
            END
        END"""
            symbol_content = ""
        else:  # POLYGON par défaut
            style_content = """        # Style par défaut (polygones)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END"""
            symbol_content = ""
        
        mapfile = f"""MAP
    NAME "{dataset_name}"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT -180 -90 180 90
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255

    # Sortie GeoJSON pour le WFS (GetFeature OUTPUTFORMAT=geojson) et clients web
    OUTPUTFORMAT
        NAME "geojson"
        DRIVER "OGR/GEOJSON"
        MIMETYPE "application/json; subtype=geojson"
        FORMATOPTION "STORAGE=stream"
        FORMATOPTION "FORM=SIMPLE"
    END
    
    # Configuration PostGIS
    CONFIG "PROJ_LIB" "/usr/share/proj"
    CONFIG "MS_ERRORFILE" "/mapserver/logs/{dataset_name}_error.log"
    
    # Projection par défaut (WGS84)
    PROJECTION
        "init=epsg:4326"
    END
    
    # Métadonnées WMS
    WEB
        METADATA
            "wms_title" "{dataset_title} (datastore)"
            "wms_onlineresource" "/wms?map=/mapserver/mapfiles/{dataset_name}.map"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wms_feature_info_mime_type" "text/html"
            "wms_getlegendgraphic_formatlist" "image/png,image/gif,image/jpeg"
            "wfs_enable_request" "*"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_title" "{dataset_title} (datastore)"
            "ows_enable_request" "*"
        END
    END
    
{symbol_content}    # Layer principal
    LAYER
        NAME "{dataset_name}"
        TYPE {geometry_type}
        STATUS ON
        
        # Connexion PostGIS
        CONNECTIONTYPE POSTGIS
        CONNECTION "{connection_string}"
        DATA "{geom_column} FROM \\"{table_name}\\" USING UNIQUE _id USING SRID=4326"
        
        # Projection
        PROJECTION
            "init=epsg:4326"
        END
        
{style_content}
        
        # Métadonnées du layer
        METADATA
            "wms_title" "{dataset_title} (datastore)"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wfs_title" "{dataset_title} (datastore)"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
            "gml_featureid" "_id"
        END
    END
END
"""
        return mapfile
    
    def _create_mapfile_content_multilayer(self, dataset_name: str, dataset_title: str,
                                          layers_info: list) -> Optional[str]:
        """
        Crée le contenu d'un mapfile MapServer avec plusieurs LAYER (une par ressource)
        Supporte les tables datagis et datastore avec des connexions différentes
        
        Returns:
            Contenu du mapfile, ou None si aucune couche valide (toutes en erreur).
        
        Args:
            dataset_name: Nom du dataset
            dataset_title: Titre du dataset
            layers_info: Liste de dictionnaires contenant les infos de chaque layer:
                [{
                    'resource_id': '...',
                    'resource_name': '...',
                    'table_name': '...',
                    'geom_column': '...',
                    'srid': 4326,
                    'source_type': 'datagis' ou 'datastore'
                }, ...]
            
        Returns:
            Contenu du mapfile
        """
        # Connexions PostGIS (format pour MapServer)
        # Pour datastore: base ckan/datastore
        connection_string_datastore = (
            f"host={self.postgis_host} "
            f"port={self.postgis_port} "
            f"dbname={self.postgis_db} "
            f"user={self.postgis_user} "
            f"password={self.postgis_password}"
        )
        # Pour datagis: base datagis
        connection_string_datagis = (
            f"host={self.postgis_host} "
            f"port={self.postgis_port} "
            f"dbname={self.datagis_db} "
            f"user={self.postgis_user} "
            f"password={self.postgis_password}"
        )
        
        # Construire les layers (nom = res_{resource_id} pour correspondre à layer_name_for_resource et au proxy WMS)
        # Un mapfile par dataset, un layer par ressource ; le proxy résout LAYERS=res_xxx vers le dataset via ce NAME.
        layers_content = []
        symbols_content = []
        used_layer_names = set()
        
        def _layer_name_for_resource(resource_id: str) -> str:
            """Même convention que ckanext/ogc/helpers.layer_name_for_resource (res_xxx, max 63 car)."""
            if not resource_id:
                return ""
            clean = resource_id.replace('-', '_')
            clean = ''.join(c if c.isalnum() or c == '_' else '_' for c in clean)
            name = f"res_{clean}"
            if len(name) > 63:
                name = f"res_{clean[:59]}"
            return name
        
        def _sanitize_layer_name(name: str, idx: int) -> str:
            base = re.sub(r'[^a-zA-Z0-9_-]+', '_', (name or '').strip()).strip('_')
            base = base or f'layer_{idx + 1}'
            maxlen = 63
            out = base[:maxlen]
            n = 1
            while out in used_layer_names:
                n += 1
                suffix = f"_{n}"
                out = (base[:maxlen - len(suffix)] + suffix)
            used_layer_names.add(out)
            return out
        
        for idx, layer_info in enumerate(layers_info):
            resource_name = layer_info.get('resource_name', f'resource_{idx+1}')
            resource_id = layer_info.get('resource_id', '')
            source_type = layer_info.get('source_type', 'datastore')
            # Nom de layer = res_xxx pour que le proxy WMS (_find_dataset_for_layer_in_org) et SLD trouvent le dataset
            layer_name = _layer_name_for_resource(resource_id) if resource_id else ""
            if not layer_name:
                layer_name = _sanitize_layer_name(resource_name, idx)
            if layer_name:
                used_layer_names.add(layer_name)
            
            # LAYER RASTER (tif, tiff, jp2, img, asc) — traités en dernier dans la liste
            if source_type == 'raster':
                data_path = layer_info.get('data_path', '')
                if not data_path:
                    continue
                nodata = layer_info.get('nodata')
                srid = layer_info.get('srid') or 4326
                proc_line = f'\n        PROCESSING "NODATA={nodata}"' if nodata is not None else ''
                proj_block = f'''        PROJECTION
            "init=epsg:{srid}"
        END'''
                layer_content = f"""    # Layer raster: {resource_name} (ID: {resource_id})
    LAYER
        NAME "{layer_name}"
        TYPE RASTER
        DATA "{data_path}"
        STATUS ON{proc_line}
{proj_block}
        METADATA
            "wms_title" "{resource_name} ({dataset_title})"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wcs_label" "{resource_name}"
            "wcs_description" "{resource_name} ({dataset_title})"
            "wcs_rangeset_name" "{layer_name}"
            "wcs_rangeset_label" "{resource_name}"
            "wcs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wcs_formats" "GEOTIFF"
            "wcs_nativeformat" "GEOTIFF"
            "wcs_enable_request" "*"
        END
    END
"""
                layers_content.append(layer_content)
                continue
            
            # LAYER VECTOR OGR (fichier local : SHP, GeoJSON, etc. sans table datagis)
            if source_type == 'ogr_local':
                data_path = layer_info.get('data_path', '')
                if not data_path:
                    continue
                geometry_type = layer_info.get('geometry_type', 'POLYGON')
                srid = layer_info.get('srid', 4326)
                # Échapper les backslashes et guillemets pour MapServer
                data_path_esc = data_path.replace('\\', '\\\\').replace('"', '\\"')
                if geometry_type == 'POINT':
                    style_content = """        # Style par défaut (points rouges)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
                SYMBOL "circle_point"
                SIZE 6
            END
        END"""
                elif geometry_type == 'LINE':
                    style_content = """        # Style par défaut (lignes)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                WIDTH 2
            END
        END"""
                else:
                    style_content = """        # Style par défaut (polygones)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END"""
                if not symbols_content and geometry_type == 'POINT':
                    symbols_content.append("""    # Symbole pour les points (SLD PointSymbolizer)
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
""")
                layer_content = f"""    # Layer OGR fallback: {resource_name} (ID: {resource_id})
    LAYER
        NAME "{layer_name}"
        TYPE {geometry_type}
        STATUS ON
        
        # Connexion OGR (fichier local)
        CONNECTIONTYPE OGR
        CONNECTION "{data_path_esc}"
        DATA "0"
        
        PROJECTION
            "init=epsg:{srid}"
        END
        
{style_content}
        
        METADATA
            "wms_title" "{resource_name} ({dataset_title})"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wfs_title" "{resource_name} ({dataset_title})"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
        END
    END
"""
                layers_content.append(layer_content)
                logger.info(f"   ▶ Layer OGR fallback: {resource_name} (ID: {resource_id})")
                continue
            
            # LAYER VECTOR (datagis / datastore)
            table_name = layer_info['table_name']
            logger.info(f"   ▶ Début traitement layer {idx+1}/{len(layers_info)}: {resource_name} (table {table_name})")
            try:
                logger.info(f"   Layer {idx+1}/{len(layers_info)}: {resource_name} (table {table_name})")
                
                logger.info(f"      détection geometry column: starting")
                geom_column = layer_info['geom_column']
                logger.info(f"      détection geometry column: finished")
                
                logger.info(f"      détection SRID: starting")
                srid = layer_info.get('srid', 4326)  # Fallback à 4326 si non défini
                if not self._validate_srid(srid):
                    logger.warning(f"SRID invalide dans layer_info pour {table_name}: {srid}, utilisation de 4326 par défaut")
                    srid = 4326
                logger.info(f"      détection SRID: finished")
                
                # Choisir la connexion selon le type de source
                if source_type == 'datagis':
                    connection_string = connection_string_datagis
                else:
                    connection_string = connection_string_datastore
                
                # Le nom de table doit être entre guillemets pour PostGIS si il contient des tirets
                if '-' in table_name or not table_name.replace('_', '').replace('-', '').isalnum():
                    table_name_quoted = f'"{table_name}"'
                else:
                    table_name_quoted = table_name
                
                logger.info(f"      calcul extent: starting")
                # Détecter le type de géométrie automatiquement
                if source_type == 'datagis':
                    geometry_type = self._detect_geometry_type_datagis(table_name, geom_column)
                else:
                    geometry_type = self._detect_geometry_type_datastore(table_name, geom_column)
                logger.info(f"      calcul extent: finished")
                
                logger.info(f"      building layer content: starting")
                # Style : SLD compilé si présent, sinon défaut selon géométrie
                style_content = None
                sld_xml = layer_info.get('sld_style')
                if sld_xml and isinstance(sld_xml, str) and sld_xml.strip():
                    try:
                        from ckanext.ogc.sld_to_mapfile import sld_to_mapserver_class
                        style_content = sld_to_mapserver_class(sld_xml, geometry_type=geometry_type)
                        logger.info(f"      SLD compilé pour layer {layer_name} (geometry_type={geometry_type})")
                        # SLD peut utiliser GraphicFill (hatch) ou PointSymbolizer : s'assurer que les symboles sont dans le mapfile
                        if style_content and not symbols_content:
                            if 'hatch-line' in style_content or 'circle_point' in style_content:
                                symbols_content.append("""    # Symbole pour les points (SLD PointSymbolizer)
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    # Symbole pour les hachures (SLD PolygonSymbolizer avec GraphicFill / horline, etc.)
    SYMBOL
        NAME "hatch-line"
        TYPE HATCH
    END
""")
                                logger.info(f"      Symboles hatch-line / circle_point ajoutés pour SLD")
                    except Exception as e:
                        logger.warning(f"      Compilation SLD ignorée pour {layer_name}: {e}, style par défaut")
                if style_content is None:
                    # Style par défaut selon le type de géométrie
                    if geometry_type == "POINT":
                        style_content = """        # Style par défaut (points rouges)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
                SYMBOL "circle_point"
                SIZE 6
            END
        END"""
                        # Ajouter les symboles (points + hachures pour SLD GraphicFill) une seule fois
                        if not symbols_content:
                            symbols_content.append("""    # Symbole pour les points (SLD PointSymbolizer)
    SYMBOL
        NAME "circle_point"
        TYPE ellipse
        FILLED true
        POINTS
            1 1
        END
    END
    # Symbole pour les hachures (SLD PolygonSymbolizer avec GraphicFill / horline, etc.)
    SYMBOL
        NAME "hatch-line"
        TYPE HATCH
    END
""")
                    elif geometry_type == "LINE":
                        style_content = """        # Style par défaut (lignes rouges)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                WIDTH 2
            END
        END"""
                    else:  # POLYGON par défaut
                        style_content = """        # Style par défaut (polygones)
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END"""
                
                # Colonne unique : pour datagis utiliser "_id" (évite appels DB qui bloquent), sinon datastore
                if source_type == 'datagis':
                    unique_column = "_id"  # défaut; éviter _detect_unique_column (blocage)
                else:
                    unique_column = self._detect_unique_column_datastore(table_name)
                
                # EXTENT du layer depuis PostGIS (WMS GetCapabilities + zoom client)
                extent_line = ""
                if source_type == 'datagis':
                    try:
                        layer_bbox = self._calculate_bbox_from_postgis(
                            table_name, geom_column, db_name=self.datagis_db, srid=4326
                        )
                        if layer_bbox:
                            extent_line = f"        EXTENT {layer_bbox}\n        "
                    except Exception as e:
                        logger.debug(f"      BBOX layer non calculé pour {table_name}: {e}")
                
                # Clustering SERVEUR pour les couches de POINTS : au dézoom, MapServer agrège
                # les points proches en amas (évite d'afficher des millions de marqueurs, allège
                # le rendu des tuiles WMS). Sans effet sur les lignes/polygones.
                cluster_content = ""
                if (geometry_type or "").upper() == "POINT":
                    cluster_content = (
                        "        # Agrégation des points proches au dézoom\n"
                        "        CLUSTER\n"
                        "            MAXDISTANCE 30\n"
                        "            REGION \"ellipse\"\n"
                        "        END\n"
                    )

                layer_content = f"""    # Layer pour ressource: {resource_name} (ID: {resource_id})
    LAYER
        NAME "{layer_name}"
        TYPE {geometry_type}
        STATUS ON
        {extent_line}
        # Connexion PostGIS
        CONNECTIONTYPE POSTGIS
        CONNECTION "{connection_string}"
        DATA "{geom_column} FROM \\"{table_name}\\" USING UNIQUE {unique_column} USING SRID={srid}"

        # Projection (utiliser le SRID détecté)
        PROJECTION
            "init=epsg:{srid}"
        END
{cluster_content}
{style_content}
        
        # Métadonnées du layer
        METADATA
            "wms_title" "{resource_name} ({dataset_title})"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wfs_title" "{resource_name} ({dataset_title})"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_enable_request" "*"
            "wfs_getfeature_formatlist" "geojson,application/json; subtype=geojson,application/json,application/gml+xml; version=3.2,text/xml; subtype=gml/3.2.1,text/xml; subtype=gml/3.1.1,text/xml; subtype=gml/2.1.2"
            "gml_include_items" "all"
            "gml_featureid" "_id"
        END
    END
"""
                logger.info(f"      building layer content: finished")
                layers_content.append(layer_content)
                logger.info(f"      layer {idx+1}/{len(layers_info)} appended")
            except Exception as e:
                logger.error(f"ERREUR layer {idx+1}/{len(layers_info)} (resource {resource_name}, table {table_name}): {e}")
                logger.error("Traceback:\n%s", traceback.format_exc())
                # ne pas bloquer : on ignore cette couche et on continue avec les autres
                continue
        
        # Construire le mapfile complet
        mapfile = f"""MAP
    NAME "{dataset_name}"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT -180 -90 180 90
    UNITS DD
    SHAPEPATH "/mapserver/data"
    IMAGECOLOR 255 255 255

    # Sortie GeoJSON pour le WFS (GetFeature OUTPUTFORMAT=geojson) et clients web
    OUTPUTFORMAT
        NAME "geojson"
        DRIVER "OGR/GEOJSON"
        MIMETYPE "application/json; subtype=geojson"
        FORMATOPTION "STORAGE=stream"
        FORMATOPTION "FORM=SIMPLE"
    END
    
    # Configuration PostGIS
    CONFIG "PROJ_LIB" "/usr/share/proj"
    CONFIG "MS_ERRORFILE" "/mapserver/logs/{dataset_name}_error.log"
    
    # Projection par défaut (WGS84)
    PROJECTION
        "init=epsg:4326"
    END
    
    # Métadonnées WMS
    WEB
        METADATA
            "wms_title" "{dataset_title} ({len(layers_info)} layer(s))"
            "wms_onlineresource" "/wms?map=/mapserver/mapfiles/{dataset_name}.map"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wms_enable_request" "*"
            "wms_feature_info_mime_type" "text/html"
            "wms_getlegendgraphic_formatlist" "image/png,image/gif,image/jpeg"
            "wfs_enable_request" "*"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_title" "{dataset_title} ({len(layers_info)} layer(s))"
            "ows_enable_request" "*"
        END
    END
    
{''.join(symbols_content)}{''.join(layers_content)}END
"""
        if not layers_content:
            logger.warning(f"Aucune couche valide pour {dataset_name} (toutes en erreur ou ignorées)")
            return None
        return mapfile
    
    def cleanup_all_invalid_ogr_mapfiles(self) -> int:
        """
        Nettoie tous les mapfiles OGR invalides (fichiers datasource inexistants)
        
        Returns:
            Nombre de mapfiles supprimés
        """
        if not self.mapfiles_dir.exists():
            return 0
        
        removed_count = 0
        mapfiles = list(self.mapfiles_dir.glob("*.map"))
        
        logger.info(f"Nettoyage des mapfiles OGR invalides...")
        logger.info(f"   Nombre de mapfiles à vérifier: {len(mapfiles)}")
        
        for mapfile_path in mapfiles:
            if self._cleanup_invalid_ogr_mapfile(str(mapfile_path)):
                removed_count += 1
        
        logger.info(f"Nettoyage terminé: {removed_count} mapfile(s) OGR invalide(s) supprimé(s)")
        return removed_count
    
    def generate_all_mapfiles(self) -> int:
        """
        Génère les mapfiles pour tous les datasets géospatiaux
        
        Returns:
            Nombre de mapfiles générés avec succès
        """
        # Nettoyer d'abord les mapfiles OGR invalides
        self.cleanup_all_invalid_ogr_mapfiles()
        
        datasets = self.get_geospatial_datasets()
        success_count = 0
        failed_datasets = []
        
        total = len(datasets)
        logger.info(f"Génération de mapfiles pour {total} datasets géospatiaux...")
        # Ligne parseable pour l'UI (job écrit en Redis)
        print(f"MAPFILE_PROGRESS: 0/{total}", flush=True)
        
        for i, dataset in enumerate(datasets):
            current = i + 1
            dataset_name = dataset.get('name', 'unknown')
            print(f"MAPFILE_PROGRESS: {current}/{total}", flush=True)
            try:
                if self.generate_mapfile(dataset):
                    success_count += 1
                    logger.info(f"[{current}/{total}] Mapfile généré pour {dataset_name}")
                else:
                    failed_datasets.append(dataset_name)
                    logger.warning(f"[{current}/{total}] Échec pour {dataset_name}")
            except Exception as e:
                failed_datasets.append(dataset_name)
                logger.error(f"[{current}/{total}] Exception pour {dataset_name}: {e}")
                logger.error("Traceback:\n%s", traceback.format_exc())
                # on continue avec les autres mapfiles (ne pas bloquer toute la génération)
        
        # Résumé final
        logger.info("=" * 80)
        logger.info(f"RÉSUMÉ DE LA GÉNÉRATION DES MAPFILES")
        logger.info(f"   Total datasets géospatiaux: {len(datasets)}")
        logger.info(f"   Mapfiles générés avec succès: {success_count}")
        if failed_datasets:
            logger.warning(f"   Mapfiles échoués: {len(failed_datasets)}")
            logger.warning(f"   Datasets en échec:")
            for failed_name in failed_datasets:
                logger.warning(f"      - {failed_name}")
            logger.warning(f"   Utilisez le script de diagnostic pour plus de détails:")
            logger.warning(f"      /srv/app/scripts/diagnose-mapfile.sh <dataset_name> --fix")
        else:
            logger.info(f"   Tous les mapfiles ont été générés avec succès!")
        logger.info("=" * 80)
        
        return success_count


def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Génère des mapfiles MapServer depuis CKAN')
    parser.add_argument('--ckan-url', default=os.getenv('CKAN_URL', 'http://localhost:5000'),
                       help='URL de l\'API CKAN')
    parser.add_argument('--ckan-api-key', default=os.getenv('CKAN_API_KEY', ''),
                       help='Clé API CKAN')
    parser.add_argument('--postgis-host', default=os.getenv('POSTGIS_HOST', 'db'),
                       help='Host PostGIS')
    parser.add_argument('--postgis-port', type=int, default=int(os.getenv('POSTGIS_PORT', '5432')),
                       help='Port PostGIS')
    parser.add_argument('--postgis-db', default=os.getenv('POSTGIS_DB', 'datastore'),
                       help='Base de données PostGIS (généralement "datastore" pour CKAN)')
    parser.add_argument('--postgis-user', default=os.getenv('POSTGIS_USER', 'ckan'),
                       help='Utilisateur PostGIS')
    parser.add_argument('--postgis-password', default=os.getenv('POSTGIS_PASSWORD', ''),
                       help='Mot de passe PostGIS')
    parser.add_argument('--mapfiles-dir', default=os.getenv('MAPFILES_DIR', '/mapserver/mapfiles'),
                       help='Répertoire pour les mapfiles')
    parser.add_argument('--dataset', help='Générer uniquement pour un dataset spécifique (nom)')
    parser.add_argument('--auto-create-geometry', action='store_true', default=True,
                       help='Créer automatiquement les colonnes géométrie PostGIS depuis lat/lon ou WKT')
    parser.add_argument('--datagis-db', default=os.getenv('DATAGIS_DB', 'datagis'),
                       help='Base de données datagis')
    parser.add_argument('--use-datagis', action='store_true', default=True,
                       help='Chercher dans datagis si pas de datastore')
    parser.add_argument('--no-auto-create-geometry', dest='auto_create_geometry', action='store_false',
                       help='Ne pas créer automatiquement les colonnes géométrie')
    parser.add_argument('--cleanup-invalid-ogr', action='store_true',
                       help='Nettoyer uniquement les mapfiles OGR invalides (fichiers datasource inexistants)')
    parser.add_argument('--ckan-storage-path', default=os.getenv('CKAN_STORAGE_PATH', '/ckan_storage'),
                       help='Chemin vers ckan_storage')
    
    args = parser.parse_args()
    
    generator = MapfileGenerator(
                ckan_url=args.ckan_url,
                ckan_api_key=args.ckan_api_key,
                postgis_host=args.postgis_host,
                postgis_port=args.postgis_port,
                postgis_db=args.postgis_db,
                postgis_user=args.postgis_user,
                postgis_password=args.postgis_password,
                mapfiles_dir=args.mapfiles_dir,
                auto_create_geometry=args.auto_create_geometry,
                datagis_db=args.datagis_db,
                use_datagis=args.use_datagis,
                ckan_storage_path=args.ckan_storage_path
            )
    
    # Option pour nettoyer uniquement les mapfiles OGR invalides
    if args.cleanup_invalid_ogr:
        removed_count = generator.cleanup_all_invalid_ogr_mapfiles()
        sys.exit(0 if removed_count >= 0 else 1)
    
    if args.dataset:
        # Générer pour un seul dataset
        dataset = generator.get_package_details(args.dataset)
        if dataset:
            if generator._is_geospatial_dataset(dataset):
                try:
                    success = generator.generate_mapfile(dataset)
                    sys.exit(0 if success else 1)
                except Exception as e:
                    logger.error(f"Exception lors de la génération du mapfile pour '{args.dataset}': {e}")
                    logger.error("Traceback:\n%s", traceback.format_exc())
                    sys.exit(1)
            else:
                logger.error(f"Dataset '{args.dataset}' n'est pas géospatial.")
                sys.exit(1)
        else:
            logger.error(f"Dataset '{args.dataset}' non trouvé.")
            sys.exit(1)
    else:
        # Générer pour tous les datasets
        count = generator.generate_all_mapfiles()
        sys.exit(0 if count > 0 else 1)


if __name__ == '__main__':
    main()

