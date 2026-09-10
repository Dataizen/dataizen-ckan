#!/usr/bin/env python3
"""
Script pour importer toutes les données depuis un CKAN source vers un CKAN cible

Ce script importe :
- Organisations
- Utilisateurs
- Groupes
- Datasets (packages)
- Ressources
- Relations et métadonnées

Usage:
    python import-from-ckan.py --source-url https://source.ckan.fr --source-token TOKEN --target-url https://target.ckan.fr --target-token TOKEN

Ou avec variables d'environnement:
    SOURCE_CKAN_URL=https://source.ckan.fr SOURCE_CKAN_TOKEN=TOKEN python import-from-ckan.py
"""

import os
import sys
import json
import logging
import argparse
import requests
import re
import tempfile
import secrets
import string
import time
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from urllib.parse import urljoin
from pathlib import Path

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'import-ckan-{datetime.now().strftime("%Y%m%d-%H%M%S")}.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class CKANImporter:
    """Importe les données depuis un CKAN source vers un CKAN cible"""
    
    def __init__(self, source_url: str, source_token: str, 
                 target_url: str, target_token: str,
                 dry_run: bool = False, skip_users: bool = False,
                 skip_organizations: bool = False, skip_datasets: bool = False,
                 skip_showcases: bool = False,
                 source_public_url: Optional[str] = None,
                 namespace_url_mapping: Optional[Dict[str, str]] = None,
                 import_activities: bool = True,
                 test_mode: bool = False,
                 request_delay: float = 0.5,
                 retry_dataset_ids: Optional[List[str]] = None,
                 retry_resource_ids: Optional[List[str]] = None):
        """
        Initialise l'importeur CKAN
        
        Args:
            source_url: URL du CKAN source
            source_token: Token admin du CKAN source
            target_url: URL du CKAN cible
            target_token: Token admin du CKAN cible
            dry_run: Mode simulation (ne fait rien)
            skip_users: Ignorer l'import des utilisateurs
            skip_organizations: Ignorer l'import des organisations
            skip_datasets: Ignorer l'import des datasets
            skip_showcases: Ignorer l'import des showcases (réutilisations)
            source_public_url: URL publique du CKAN source (pour transformer les URLs internes)
            namespace_url_mapping: Mapping des namespaces Kubernetes vers URLs publiques
                                  Ex: {'dijon-bfc': 'https://data.metropole-dijon.fr', 
                                       'besancon-bfc': 'https://besancon-bfc.data.example.org'}
            import_activities: Importer l'historique d'activité des objets
            test_mode: Mode test (limite les imports à quelques éléments)
            request_delay: Délai en secondes entre les requêtes de création (datasets et ressources)
                          pour ne pas surcharger les serveurs CKAN (défaut: 0.5s)
        """
        self.source_url = source_url.rstrip('/')
        self.target_url = target_url.rstrip('/')
        self.source_token = source_token
        self.target_token = target_token
        self.dry_run = dry_run
        
        # URL publique du source (pour transformer les URLs internes)
        self.source_public_url = source_public_url or source_url.rstrip('/')
        
        # Mapping des namespaces vers URLs publiques
        # Par défaut: dijon-bfc -> data.metropole-dijon.fr
        # Autres: {namespace} -> {namespace}.data.example.org
        self.namespace_url_mapping = namespace_url_mapping or {}
        
        # Ajouter les mappings par défaut s'ils ne sont pas déjà présents
        default_mappings = {
            'dijon-bfc': 'https://data.metropole-dijon.fr'
        }
        for namespace, url in default_mappings.items():
            if namespace not in self.namespace_url_mapping:
                self.namespace_url_mapping[namespace] = url
        
        # Headers pour les requêtes API
        self.source_headers = {'Authorization': source_token} if source_token else {}
        self.target_headers = {'Authorization': target_token} if target_token else {}
        
        # Mapping des IDs importés (source_id -> target_id)
        self.org_mapping: Dict[str, str] = {}
        self.user_mapping: Dict[str, str] = {}
        self.group_mapping: Dict[str, str] = {}
        self.dataset_mapping: Dict[str, str] = {}
        self.showcase_mapping: Dict[str, str] = {}  # Mapping showcase_id_source -> showcase_id_target
        
        # Compteurs
        self.stats = {
            'organizations': {'imported': 0, 'skipped': 0, 'errors': 0},
            'users': {'imported': 0, 'skipped': 0, 'errors': 0},
            'groups': {'imported': 0, 'skipped': 0, 'errors': 0},
            'datasets': {'imported': 0, 'skipped': 0, 'updated': 0, 'errors': 0, 'harvested': 0},
            'resources': {'imported': 0, 'updated': 0, 'skipped': 0, 'errors': 0},
            'showcases': {'imported': 0, 'updated': 0, 'skipped': 0, 'errors': 0},
            'harvest_sources': {'imported': 0, 'updated': 0, 'skipped': 0, 'errors': 0},
            'dataset_relationships': {'created': 0, 'errors': 0},
            'activities': {'imported': 0, 'skipped': 0, 'errors': 0}
        }
        
        # Stocker les datasets pour le rapport final
        self._imported_datasets: List[Dict[str, Any]] = []
        
        # Stocker les erreurs pour le rapport final
        self._error_datasets: List[Dict[str, Any]] = []  # Datasets en erreur
        self._error_resources: List[Dict[str, Any]] = []  # Ressources en erreur
        
        # Mapping showcase_id -> liste de package_ids (pour récupérer les associations depuis les datasets)
        self._showcase_packages_mapping: Dict[str, List[str]] = {}
        
        # Liste de filtres pour réessayer uniquement certains éléments
        self._retry_dataset_ids: Optional[Set[str]] = set(retry_dataset_ids) if retry_dataset_ids else None
        self._retry_resource_ids: Optional[Set[str]] = set(retry_resource_ids) if retry_resource_ids else None
        
        # Option pour importer l'historique
        self.import_activities = import_activities
        
        # Options de skip
        self.skip_users = skip_users
        self.skip_organizations = skip_organizations
        self.skip_datasets = skip_datasets
        self.skip_showcases = skip_showcases
        
        # Mode test (limite les imports)
        self.test_mode = test_mode
        if test_mode:
            logger.info("Mode TEST activé - import limité à quelques éléments")
        
        # Délai entre les requêtes pour ne pas surcharger les serveurs
        self.request_delay = request_delay
        if request_delay > 0:
            logger.info(f" Délai entre requêtes: {request_delay}s")
        
        # Tracker le temps de la dernière ressource importée pour garantir un délai minimum
        self._last_resource_import_time = None
        self._min_resource_interval = 10.0  # Minimum 10 secondes entre le début de chaque import de ressource
        
        # Stocker la dernière erreur API pour traitement ultérieur (pour les 409 notamment)
        self._last_api_error = None
        self._last_api_status_code = None
        
        # Dossier pour sauvegarder les activités
        self.activities_dir = Path(f'import-activities-{datetime.now().strftime("%Y%m%d-%H%M%S")}')
        if self.import_activities:
            self.activities_dir.mkdir(exist_ok=True)
            logger.info(f"Dossier activités: {self.activities_dir}")
        
        # Dossier temporaire pour les fichiers téléchargés
        self.temp_dir = tempfile.mkdtemp(prefix='ckan-import-')
        logger.info(f"Dossier temporaire: {self.temp_dir}")
        
        logger.info(f"CKAN Importer initialisé")
        logger.info(f"   Source: {self.source_url}")
        logger.info(f"   Source public URL: {self.source_public_url}")
        if self.namespace_url_mapping:
            logger.info(f"   Namespace mapping: {self.namespace_url_mapping}")
        logger.info(f"   Target: {self.target_url}")
        logger.info(f"   Dry run: {self.dry_run}")
    
    def _transform_internal_url(self, url: str) -> str:
        """
        Transforme une URL interne (cluster Kubernetes) en URL publique
        
        Détecte le pattern: http://data4citizen.{namespace}.svc.cluster.local:8080/...
        et le transforme selon le mapping des namespaces.
        
        Args:
            url: URL à transformer
            
        Returns:
            URL transformée
        """
        if not url:
            return url
        
        # Pattern spécifique: data4citizen.{namespace}.svc.cluster.local:8080
        # Exemple: http://data4citizen.dijon-bfc.svc.cluster.local:8080/sites/default/files/...
        data4citizen_pattern = r'http://data4citizen\.([^.]+)\.svc\.cluster\.local(?::\d+)?(/.*)'
        match = re.search(data4citizen_pattern, url)
        
        if match:
            namespace = match.group(1)
            path = match.group(2)
            
            # Chercher dans le mapping personnalisé
            if namespace in self.namespace_url_mapping:
                public_domain = self.namespace_url_mapping[namespace]
                # S'assurer que le domaine n'a pas de protocole
                public_domain = public_domain.replace('https://', '').replace('http://', '').rstrip('/')
                public_url = f"https://{public_domain}{path}"
                logger.info(f"URL transformée ({namespace}): {url[:80]}... → {public_url[:80]}...")
                return public_url
            else:
                # Règle par défaut: {namespace}.data.example.org
                public_domain = f"{namespace}.data.example.org"
                public_url = f"https://{public_domain}{path}"
                logger.info(f"URL transformée (défaut {namespace}): {url[:80]}... → {public_url[:80]}...")
                return public_url
        
        # Autres patterns internes Kubernetes (fallback)
        internal_patterns = [
            r'http://[^/]+\.svc\.cluster\.local(?::\d+)?',
            r'http://[^/]+\.cluster\.local(?::\d+)?',
            r'http://[^/]+\.svc(?::\d+)?',
            r'http://[^/]+:8080',  # Port interne commun
        ]
        
        for pattern in internal_patterns:
            if re.search(pattern, url):
                # Extraire le chemin de l'URL interne
                path_match = re.search(r'://[^/]+(/.*)', url)
                if path_match:
                    path = path_match.group(1)
                    # Utiliser l'URL publique par défaut
                    protocol = 'https' if self.source_public_url.startswith('https') else 'http'
                    public_domain = self.source_public_url.replace('https://', '').replace('http://', '').rstrip('/')
                    public_url = f"{protocol}://{public_domain}{path}"
                    logger.info(f"URL transformée (fallback): {url[:80]}... → {public_url[:80]}...")
                    return public_url
        
        return url
    
    def _call_api(self, url: str, method: str = 'GET', data: Optional[Dict] = None,
                   params: Optional[Dict] = None, headers: Optional[Dict] = None, 
                   is_source: bool = True, files: Optional[Dict] = None, 
                   max_retries: int = 3, retry_delay: int = 5) -> Optional[Dict]:
        """
        Appelle l'API CKAN avec gestion des retries pour les erreurs temporaires
        
        Args:
            url: URL de l'API
            method: Méthode HTTP
            data: Données à envoyer (pour POST/PUT)
            params: Paramètres de requête (pour GET)
            headers: Headers HTTP
            is_source: True si appel au CKAN source, False si cible
            files: Fichiers à uploader (pour multipart/form-data)
            max_retries: Nombre maximum de tentatives en cas d'erreur temporaire (502, 503, 504)
            retry_delay: Délai en secondes entre les tentatives
            
        Returns:
            Réponse JSON ou None en cas d'erreur
        """
        if headers is None:
            headers = self.source_headers if is_source else self.target_headers
        
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                # Pour les uploads de fichiers, ne pas utiliser json
                if files:
                    # Pour multipart/form-data, data doit être un dict simple (pas json)
                    logger.debug(f"  Upload multipart/form-data vers: {url} (tentative {attempt + 1}/{max_retries})")
                    # Logger la taille du fichier si disponible
                    for key, file_tuple in files.items():
                        if isinstance(file_tuple, tuple) and len(file_tuple) >= 2:
                            file_obj = file_tuple[1]
                            if hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
                                current_pos = file_obj.tell()
                                file_obj.seek(0, 2)  # Aller à la fin
                                file_size = file_obj.tell()
                                file_obj.seek(current_pos)  # Revenir à la position initiale
                                logger.debug(f"  Taille du fichier à uploader: {file_size} bytes")
                    # Pour les uploads, réinitialiser le fichier à chaque tentative
                    if attempt > 0:
                        for key, file_tuple in files.items():
                            if isinstance(file_tuple, tuple) and len(file_tuple) >= 2:
                                file_obj = file_tuple[1]
                                if hasattr(file_obj, 'seek'):
                                    file_obj.seek(0)
                    response = requests.post(url, data=data, files=files, headers=headers, timeout=300)
                elif method == 'GET':
                    response = requests.get(url, params=params, headers=headers, timeout=60)
                elif method == 'POST':
                    response = requests.post(url, json=data, headers=headers, timeout=60)
                elif method == 'PUT':
                    response = requests.put(url, json=data, headers=headers, timeout=60)
                else:
                    logger.error(f"Méthode HTTP non supportée: {method}")
                    return None
                
                # Ne pas lever d'exception pour les 404 (Not Found) - c'est normal si l'objet n'existe pas
                if response.status_code == 404:
                    return None
                
                # Gérer les erreurs temporaires (502 Bad Gateway, 503 Service Unavailable, 504 Gateway Timeout)
                if response.status_code in [502, 503, 504]:
                    if attempt < max_retries - 1:
                        error_msg = {
                            502: "Bad Gateway",
                            503: "Service Unavailable",
                            504: "Gateway Timeout"
                        }.get(response.status_code, f"HTTP {response.status_code}")
                        logger.warning(f"   Erreur temporaire {error_msg} (tentative {attempt + 1}/{max_retries}), nouvelle tentative dans {retry_delay}s...")
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.error(f"  Erreur {response.status_code} après {max_retries} tentatives")
                        response.raise_for_status()
                
                response.raise_for_status()
                result = response.json()
                
                if not result.get('success', True):
                    error = result.get('error', {})
                    # Ne pas logger les erreurs "Not Found" comme des erreurs critiques
                    if error.get('__type') == 'Not Found Error':
                        return None
                    # Ne pas logger les erreurs pour activity_list si l'API n'est pas disponible
                    error_msg = str(error) if isinstance(error, dict) else str(error)
                    if 'activity_list' in url and ('inconnue' in error_msg.lower() or 'unknown' in error_msg.lower() or 'action' in error_msg.lower()):
                        # API activity_list non disponible - c'est normal pour certaines installations CKAN
                        return None
                    # Stocker l'erreur pour traitement ultérieur (pour les 409 notamment)
                    self._last_api_error = result
                    
                    # Logger avec plus de détails pour les uploads
                    if files:
                        logger.error(f"Erreur API CKAN lors de l'upload vers {url}")
                        if isinstance(error, dict):
                            error_type = error.get('__type', 'Unknown')
                            error_message = error.get('message', str(error))
                            logger.error(f"  Type: {error_type}")
                            logger.error(f"  Message: {error_message}")
                            # Logger les autres champs d'erreur
                            for key, value in error.items():
                                if key not in ['__type', 'message']:
                                    logger.error(f"  {key}: {value}")
                        else:
                            logger.error(f"  Erreur: {error}")
                    else:
                        logger.error(f"Erreur API CKAN: {error}")
                    return None
                
                return result
                
            except requests.exceptions.HTTPError as e:
                # Gérer les erreurs temporaires avec retry
                if e.response.status_code in [502, 503, 504]:
                    if attempt < max_retries - 1:
                        error_msg = {
                            502: "Bad Gateway",
                            503: "Service Unavailable",
                            504: "Gateway Timeout"
                        }.get(e.response.status_code, f"HTTP {e.response.status_code}")
                        logger.warning(f"   Erreur temporaire {error_msg} (tentative {attempt + 1}/{max_retries}), nouvelle tentative dans {retry_delay}s...")
                        time.sleep(retry_delay)
                        last_exception = e
                        continue
                    else:
                        logger.error(f"  Erreur {e.response.status_code} après {max_retries} tentatives")
                        # Continuer avec le traitement d'erreur normal
                
                # Ne pas logger les 404 comme des erreurs (c'est normal si l'objet n'existe pas)
                if e.response.status_code == 404:
                    return None
                
                # Ne pas logger les 400 pour activity_list si l'API n'est pas disponible
                if e.response.status_code == 400 and 'activity_list' in url:
                    try:
                        error_detail = e.response.text[:500]
                        if 'inconnue' in error_detail.lower() or 'unknown' in error_detail.lower() or 'action' in error_detail.lower():
                            # API activity_list non disponible - c'est normal pour certaines installations CKAN
                            return None
                    except Exception:
                        pass
                
                # Pour les autres erreurs HTTP, logger et stocker l'erreur
                try:
                    error_detail = e.response.text[:500]
                    # Stocker l'erreur pour traitement ultérieur (pour les 409 notamment)
                    self._last_api_status_code = e.response.status_code
                    try:
                        error_json = e.response.json()
                        self._last_api_error = error_json
                        if error_json.get('error', {}).get('__type') == 'Not Found Error':
                            return None
                        # Vérifier si c'est une erreur "action inconnue" pour activity_list
                        error_msg = str(error_json.get('error', ''))
                        if 'activity_list' in url and ('inconnue' in error_msg.lower() or 'unknown' in error_msg.lower()):
                            return None
                    except Exception:
                        self._last_api_error = error_detail
                    logger.error(f"Erreur HTTP lors de l'appel API {url}: {e}")
                    logger.error(f"Détails de l'erreur: {error_detail}")
                except Exception:
                    logger.error(f"Erreur HTTP lors de l'appel API {url}: {e}")
                return None
            except requests.exceptions.RequestException as e:
                # Pour les erreurs de connexion/timeout, retry si possible
                if attempt < max_retries - 1:
                    logger.warning(f"   Erreur de connexion (tentative {attempt + 1}/{max_retries}): {e}")
                    logger.warning(f"   Nouvelle tentative dans {retry_delay}s...")
                    time.sleep(retry_delay)
                    last_exception = e
                    continue
                else:
                    logger.error(f"Erreur lors de l'appel API {url} après {max_retries} tentatives: {e}")
                    return None
        
        # Si on arrive ici, toutes les tentatives ont échoué
        if last_exception:
            logger.error(f"  Échec après {max_retries} tentatives pour {url}")
        return None
    
    def get_all_organizations(self) -> List[Dict[str, Any]]:
        """Récupère toutes les organisations du CKAN source"""
        logger.info("Récupération des organisations...")
        organizations = []
        
        # Utiliser organization_list avec all_fields pour obtenir tous les détails
        url = f"{self.source_url}/api/action/organization_list"
        params = {'all_fields': True, 'include_dataset_count': True}
        
        result = self._call_api(url, params=params, is_source=True)
        if not result:
            return []
        
        org_list = result.get('result', [])
        
        # Si organization_list retourne des objets complets, les utiliser directement
        # Sinon, récupérer les détails avec organization_show
        for org_item in org_list:
            if isinstance(org_item, dict) and 'id' in org_item:
                # C'est déjà un objet complet
                org_id = org_item.get('id') or org_item.get('name')
                # Récupérer les détails complets quand même pour être sûr
                org_url = f"{self.source_url}/api/action/organization_show"
                org_params = {'id': org_id, 'include_users': True, 'include_groups': True}
                org_result = self._call_api(org_url, params=org_params, is_source=True)
                if org_result and org_result.get('result'):
                    organizations.append(org_result['result'])
            else:
                # C'est juste un nom (string)
                org_name = org_item if isinstance(org_item, str) else org_item.get('name', '')
                org_url = f"{self.source_url}/api/action/organization_show"
                org_params = {'id': org_name, 'include_users': True, 'include_groups': True}
                org_result = self._call_api(org_url, params=org_params, is_source=True)
                if org_result and org_result.get('result'):
                    organizations.append(org_result['result'])
        
        logger.info(f"{len(organizations)} organisations trouvées")
        
        # En mode test, limiter à quelques organisations
        if self.test_mode and len(organizations) > 0:
            organizations = organizations[:min(3, len(organizations))]
            logger.info(f"Mode TEST: limitation à {len(organizations)} organisations")
        
        return organizations
    
    def import_organization(self, org: Dict[str, Any]) -> Optional[str]:
        """
        Importe une organisation dans le CKAN cible
        
        Args:
            org: Données de l'organisation source
            
        Returns:
            ID de l'organisation créée ou None
        """
        org_id = org.get('id')
        org_name = org.get('name')
        
        # Vérifier si l'organisation existe déjà
        check_url = f"{self.target_url}/api/action/organization_show"
        check_params = {'id': org_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        if existing and existing.get('result'):
            # Organisation existe déjà
            existing_org_id = existing['result']['id']
            self.org_mapping[org_id] = existing_org_id
            self.stats['organizations']['skipped'] += 1
            
            # Note: Les membres seront ajoutés après l'import des utilisateurs
            # On stocke les données de l'organisation pour plus tard
            if not hasattr(self, '_pending_org_members'):
                self._pending_org_members = []
            self._pending_org_members.append({
                'org_id': existing_org_id,
                'source_org': org
            })
            
            return existing_org_id
        
        # Préparer les données pour l'import
        org_data = {
            'name': org_name,
            'title': org.get('title', org_name),
            'description': org.get('description', ''),
            'image_url': org.get('image_url', ''),
            'type': org.get('type', 'organization'),
            'approval_status': org.get('approval_status', 'approved'),
            'state': org.get('state', 'active'),
        }
        
        # Ajouter les extras
        if org.get('extras'):
            org_data['extras'] = org.get('extras')
        
        # Ajouter les tags si présents
        if org.get('tags'):
            org_data['tags'] = [{'name': tag.get('name', '')} for tag in org.get('tags', [])]
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait organisation: {org_name}")
            self.org_mapping[org_id] = f"dry-run-{org_name}"
            self.stats['organizations']['imported'] += 1
            return self.org_mapping[org_id]
        
        # Créer l'organisation
        create_url = f"{self.target_url}/api/action/organization_create"
        result = self._call_api(create_url, method='POST', data=org_data, is_source=False)
        
        if result and result.get('result'):
            created_org = result['result']
            created_org_id = created_org['id']
            # Ne pas logger les succès (seulement les erreurs)
            self.org_mapping[org_id] = created_org_id
            self.stats['organizations']['imported'] += 1
            
            # Note: Les membres seront ajoutés après l'import des utilisateurs
            # On stocke les données de l'organisation pour plus tard
            if not hasattr(self, '_pending_org_members'):
                self._pending_org_members = []
            self._pending_org_members.append({
                'org_id': created_org_id,
                'source_org': org
            })
            
            return created_org_id
        else:
            logger.error(f"Erreur lors de la création de l'organisation: {org_name}")
            self.stats['organizations']['errors'] += 1
            return None
    
    def import_organization_members(self, target_org_id: str, source_org: Dict[str, Any]) -> int:
        """
        Importe les membres d'une organisation (utilisateurs avec leurs rôles)
        
        Args:
            target_org_id: ID de l'organisation cible
            source_org: Données de l'organisation source (contient les users)
            
        Returns:
            Nombre de membres ajoutés
        """
        members_added = 0
        
        # Récupérer les utilisateurs de l'organisation source
        org_users = source_org.get('users', [])
        if not org_users:
            return 0
        
        for org_user in org_users:
            user_name = org_user.get('name') or org_user.get('id')
            capacity = org_user.get('capacity', 'member')  # member, editor, admin
            
            if not user_name:
                continue
            
            # Chercher l'utilisateur cible par nom (plus simple et direct)
            # D'abord essayer directement par nom
            check_user_url = f"{self.target_url}/api/action/user_show"
            check_result = self._call_api(check_user_url, params={'id': user_name}, is_source=False)
            target_user_id = None
            
            if check_result and check_result.get('result'):
                target_user_id = check_result['result'].get('id')
            else:
                # Si pas trouvé par nom, chercher dans user_mapping
                # user_mapping contient source_id -> target_id
                # On doit trouver le source_id correspondant au user_name
                # Pour cela, on peut chercher dans les IDs source qui correspondent au nom
                # Mais c'est complexe, donc on essaie juste de trouver par nom dans le mapping
                # En fait, le plus simple est d'utiliser directement le nom comme identifiant
                # car CKAN utilise souvent le nom comme identifiant principal
                pass
            
            if not target_user_id:
                logger.warning(f" Utilisateur '{user_name}' non trouvé pour l'organisation '{source_org.get('name')}', membre non ajouté")
                continue
            
            # Ajouter le membre à l'organisation
            if self.dry_run:
                logger.debug(f"  [DRY RUN] Ajouterait membre '{user_name}' ({capacity}) à l'organisation")
                members_added += 1
                continue
            
            member_url = f"{self.target_url}/api/action/organization_member_create"
            member_data = {
                'id': target_org_id,
                'username': user_name,
                'role': capacity
            }
            
            result = self._call_api(member_url, method='POST', data=member_data, is_source=False)
            if result and result.get('success', True):
                # Ne pas logger les succès (seulement les erreurs)
                members_added += 1
            else:
                logger.error(f"Erreur lors de l'ajout du membre '{user_name}' à l'organisation '{source_org.get('name')}'")
        
        return members_added
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """Récupère tous les utilisateurs du CKAN source"""
        logger.info("Récupération des utilisateurs...")
        users = []
        offset = 0
        limit = 1000
        
        # Set pour détecter les doublons
        seen_user_ids = set()
        previous_batch_size = 0
        
        while True:
            # Essayer d'abord /api/action/, puis /api/2/action/ si nécessaire
            url = f"{self.source_url}/api/action/user_list"
            params = {'offset': offset, 'limit': limit}
            
            result = self._call_api(url, params=params, is_source=True)
            
            # Si échec, essayer avec /api/2/action/
            if not result and offset == 0:
                logger.info(" Tentative avec /api/2/action/...")
                url = f"{self.source_url}/api/2/action/user_list"
                result = self._call_api(url, params=params, is_source=True)
            
            if not result:
                break
            
            user_list = result.get('result', [])
            if not user_list:
                break
            
            # Vérifier que user_list est bien une liste
            if not isinstance(user_list, list):
                logger.error(f"user_list n'est pas une liste mais {type(user_list)}: {user_list}")
                break
            
            # Ne pas logger les détails (seulement les erreurs)
            
            # Vérifier si on a déjà vu ces utilisateurs (détection de boucle infinie)
            batch_user_ids = set()
            new_users_in_batch = 0
            
            # Récupérer les détails complets de chaque utilisateur
            for idx, user_item in enumerate(user_list, 1):
                user_id = None
                
                # user_list peut retourner soit des strings (noms), soit des dicts (objets complets)
                if isinstance(user_item, dict):
                    # C'est déjà un objet utilisateur complet - l'utiliser directement
                    if all(key in user_item for key in ['id', 'name']):
                        user_id = user_item.get('id')
                        # Vérifier si on a déjà vu cet utilisateur
                        if user_id and user_id in seen_user_ids:
                            continue  # Déjà vu, ignorer
                        
                        # L'objet contient déjà toutes les infos nécessaires
                        users.append(user_item)
                        seen_user_ids.add(user_id)
                        new_users_in_batch += 1
                        # Ne pas logger la progression (seulement les erreurs)
                    else:
                        logger.warning(f" Élément utilisateur incomplet dans user_list (manque id ou name): {user_item.get('name', 'unknown')}")
                elif isinstance(user_item, str):
                    # C'est juste un nom d'utilisateur (string) - récupérer les détails complets
                    user_name = user_item.strip()
                    if not user_name:
                        continue
                    
                    user_url = f"{self.source_url}/api/action/user_show"
                    user_params = {'id': user_name, 'include_num_followers': True}
                    user_result = self._call_api(user_url, params=user_params, is_source=True)
                    if user_result and user_result.get('result'):
                        user_id = user_result['result'].get('id')
                        if user_id and user_id in seen_user_ids:
                            continue  # Déjà vu, ignorer
                        
                        users.append(user_result['result'])
                        seen_user_ids.add(user_id)
                        new_users_in_batch += 1
                        # Ne pas logger la progression (seulement les erreurs)
                    else:
                        logger.warning(f" Impossible de récupérer les détails de l'utilisateur: {user_name}")
                else:
                    logger.warning(f" Type inattendu dans user_list: {type(user_item)} - {user_item}")
                    continue
            
            # Ne pas logger la progression (seulement les erreurs)
            
            # Arrêter si :
            # 1. On n'a pas reçu de nouveaux utilisateurs dans ce batch (déjà tous vus)
            # 2. La liste est vide
            # 3. On a reçu moins d'utilisateurs que demandé (fin de la liste)
            if new_users_in_batch == 0:
                logger.info("   Aucun nouvel utilisateur dans ce batch, arrêt de la récupération")
                break
            
            if len(user_list) < limit:
                break
            
            # Protection contre les boucles infinies : si on reçoit toujours le même nombre
            if len(user_list) == previous_batch_size and previous_batch_size > 0:
                logger.warning(f" Même nombre d'utilisateurs qu'au batch précédent ({previous_batch_size}), possible boucle infinie. Arrêt.")
                break
            
            previous_batch_size = len(user_list)
            offset += limit
        
        # Ne logger que le total final, pas les détails
        if len(users) > 0:
            logger.info(f"{len(users)} utilisateurs récupérés")
        
        # En mode test, limiter à 5 utilisateurs
        if self.test_mode and len(users) > 0:
            users = users[:min(5, len(users))]
            logger.info(f"Mode TEST: limitation à {len(users)} utilisateurs")
        
        return users
    
    def import_user(self, user: Dict[str, Any]) -> Optional[str]:
        """
        Importe un utilisateur dans le CKAN cible
        
        Args:
            user: Données de l'utilisateur source
            
        Returns:
            ID de l'utilisateur créé ou None
        """
        user_id = user.get('id')
        user_name = user.get('name')
        
        if not user_name:
            logger.warning(f" Utilisateur sans nom (ID: {user_id}), ignoré")
            return None
        
        # Vérifier si l'utilisateur existe déjà
        check_url = f"{self.target_url}/api/action/user_show"
        check_params = {'id': user_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        if existing and existing.get('result'):
            # Utilisateur trouvé par nom
            self.user_mapping[user_id] = existing['result']['id']
            self.stats['users']['skipped'] += 1
            return existing['result']['id']
        
        # Si l'utilisateur n'existe pas par nom, vérifier par email si disponible
        # (pour éviter les erreurs 409 si l'email existe déjà avec un autre nom)
        user_email = user.get('email', '')
        if user_email:
            # Chercher l'utilisateur par email via user_list
            search_url = f"{self.target_url}/api/action/user_list"
            search_result = self._call_api(search_url, params={}, is_source=False)
            
            if search_result and search_result.get('result'):
                user_list = search_result.get('result', [])
                # Chercher l'utilisateur avec le même email
                for existing_user_item in user_list:
                    existing_user = None
                    if isinstance(existing_user_item, dict):
                        existing_user = existing_user_item
                    elif isinstance(existing_user_item, str):
                        # Récupérer les détails
                        user_show_url = f"{self.target_url}/api/action/user_show"
                        user_details = self._call_api(user_show_url, params={'id': existing_user_item}, is_source=False)
                        if user_details and user_details.get('result'):
                            existing_user = user_details['result']
                    
                    if existing_user and existing_user.get('email', '').lower() == user_email.lower():
                        # Trouvé ! Utiliser cet utilisateur existant
                        existing_user_id = existing_user.get('id')
                        if existing_user_id:
                            logger.warning(f" Utilisateur '{user_name}' non créé (email '{user_email}' existe déjà avec le nom '{existing_user.get('name', 'unknown')}'), utilisation de l'utilisateur existant")
                            self.user_mapping[user_id] = existing_user_id
                            self.stats['users']['skipped'] += 1
                            return existing_user_id
        
        # Générer un mot de passe aléatoire sécurisé (requis par CKAN pour créer un utilisateur)
        # L'utilisateur devra le réinitialiser via "mot de passe oublié"
        password_length = 16
        password_chars = string.ascii_letters + string.digits + string.punctuation
        generated_password = ''.join(secrets.choice(password_chars) for _ in range(password_length))
        
        # Préparer les données pour l'import
        user_data = {
            'name': user_name,
            'email': user_email,
            'fullname': user.get('fullname', user_name),
            'about': user.get('about', ''),
            'sysadmin': user.get('sysadmin', False),
            'state': user.get('state', 'active'),
            'password': generated_password,  # Mot de passe temporaire généré (requis par CKAN)
        }
        
        # Note: Un mot de passe aléatoire est généré. L'utilisateur devra le réinitialiser via "mot de passe oublié"
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait utilisateur: {user_name}")
            self.user_mapping[user_id] = f"dry-run-{user_name}"
            self.stats['users']['imported'] += 1
            return self.user_mapping[user_id]
        
        # Créer l'utilisateur
        create_url = f"{self.target_url}/api/action/user_create"
        result = self._call_api(create_url, method='POST', data=user_data, is_source=False)
        
        if result and result.get('result'):
            created_user = result['result']
            # Ne pas logger les succès, seulement les erreurs
            self.user_mapping[user_id] = created_user['id']
            self.stats['users']['imported'] += 1
            return created_user['id']
        else:
            # Gérer les erreurs 409 (CONFLICT) - email déjà utilisé
            # Extraire l'email de l'erreur si disponible
            error_info = None
            if hasattr(self, '_last_api_error'):
                try:
                    import json
                    error_info = json.loads(self._last_api_error) if isinstance(self._last_api_error, str) else self._last_api_error
                except Exception:
                    pass
            
            # Si l'erreur indique que l'email existe déjà, chercher l'utilisateur existant
            user_email = user.get('email', '')
            if user_email and error_info:
                error_detail = error_info.get('error', {})
                email_error = error_detail.get('email', [])
                if email_error and 'belongs to a registered user' in str(email_error):
                    # L'email existe déjà, chercher l'utilisateur par email
                    # Note: CKAN ne permet pas de chercher directement par email via l'API
                    # On va utiliser user_list et filtrer par email
                    search_url = f"{self.target_url}/api/action/user_list"
                    search_result = self._call_api(search_url, params={}, is_source=False)
                    
                    if search_result and search_result.get('result'):
                        user_list = search_result.get('result', [])
                        # Chercher l'utilisateur avec le même email
                        for existing_user_item in user_list:
                            existing_user = None
                            if isinstance(existing_user_item, dict):
                                existing_user = existing_user_item
                            elif isinstance(existing_user_item, str):
                                # Récupérer les détails
                                user_show_url = f"{self.target_url}/api/action/user_show"
                                user_details = self._call_api(user_show_url, params={'id': existing_user_item}, is_source=False)
                                if user_details and user_details.get('result'):
                                    existing_user = user_details['result']
                            
                            if existing_user and existing_user.get('email', '').lower() == user_email.lower():
                                # Trouvé ! Utiliser cet utilisateur existant
                                existing_user_id = existing_user.get('id')
                                if existing_user_id:
                                    logger.warning(f" Utilisateur '{user_name}' non créé (email '{user_email}' existe déjà avec le nom '{existing_user.get('name', 'unknown')}'), utilisation de l'utilisateur existant")
                                    self.user_mapping[user_id] = existing_user_id
                                    self.stats['users']['skipped'] += 1
                                    return existing_user_id
            
            # Si on n'a pas trouvé l'utilisateur existant, logger l'erreur
            logger.error(f"Erreur lors de la création de l'utilisateur: {user_name} (email: {user_email})")
            self.stats['users']['errors'] += 1
            return None
    
    def get_all_groups(self) -> List[Dict[str, Any]]:
        """Récupère tous les groupes du CKAN source"""
        logger.info(" Récupération des groupes...")
        groups = []
        
        # Utiliser group_list avec all_fields pour obtenir tous les détails
        url = f"{self.source_url}/api/action/group_list"
        params = {'all_fields': True, 'include_dataset_count': True}
        
        result = self._call_api(url, params=params, is_source=True)
        if not result:
            return []
        
        group_list = result.get('result', [])
        
        # Si group_list retourne des objets complets, les utiliser directement
        # Sinon, récupérer les détails avec group_show
        for group_item in group_list:
            if isinstance(group_item, dict) and 'id' in group_item:
                # C'est déjà un objet complet
                group_id = group_item.get('id') or group_item.get('name')
                # Récupérer les détails complets quand même pour être sûr
                group_url = f"{self.source_url}/api/action/group_show"
                group_params = {'id': group_id}
                group_result = self._call_api(group_url, params=group_params, is_source=True)
                if group_result and group_result.get('result'):
                    groups.append(group_result['result'])
            else:
                # C'est juste un nom (string)
                group_name = group_item if isinstance(group_item, str) else group_item.get('name', '')
                group_url = f"{self.source_url}/api/action/group_show"
                group_params = {'id': group_name}
                group_result = self._call_api(group_url, params=group_params, is_source=True)
                if group_result and group_result.get('result'):
                    groups.append(group_result['result'])
        
        logger.info(f"{len(groups)} groupes trouvés")
        
        # En mode test, limiter à quelques groupes
        if self.test_mode and len(groups) > 0:
            groups = groups[:min(3, len(groups))]
            logger.info(f"Mode TEST: limitation à {len(groups)} groupes")
        
        return groups
    
    def import_group(self, group: Dict[str, Any]) -> Optional[str]:
        """
        Importe un groupe dans le CKAN cible
        
        Args:
            group: Données du groupe source
            
        Returns:
            ID du groupe créé ou None
        """
        group_id = group.get('id')
        group_name = group.get('name')
        
        if not group_name:
            logger.warning(f" Groupe sans nom (ID: {group_id}), ignoré")
            return None
        
        # Vérifier si le groupe existe déjà
        check_url = f"{self.target_url}/api/action/group_show"
        check_params = {'id': group_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        if existing and existing.get('result'):
            # Groupe existe déjà
            existing_group_id = existing['result']['id']
            self.group_mapping[group_id] = existing_group_id
            self.stats['groups']['skipped'] += 1
            return existing_group_id
        
        # Préparer les données pour l'import
        group_data = {
            'name': group_name,
            'title': group.get('title', group_name),
            'description': group.get('description', ''),
            'image_url': group.get('image_url', ''),
            'type': group.get('type', 'group'),
            'approval_status': group.get('approval_status', 'approved'),
            'state': group.get('state', 'active'),
        }
        
        # Ajouter les extras
        if group.get('extras'):
            group_data['extras'] = group.get('extras')
        
        # Ajouter les tags si présents
        if group.get('tags'):
            group_data['tags'] = [{'name': tag.get('name', '')} for tag in group.get('tags', [])]
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait groupe: {group_name}")
            self.group_mapping[group_id] = f"dry-run-{group_name}"
            self.stats['groups']['imported'] += 1
            return self.group_mapping[group_id]
        
        # Créer le groupe
        create_url = f"{self.target_url}/api/action/group_create"
        result = self._call_api(create_url, method='POST', data=group_data, is_source=False)
        
        if result and result.get('result'):
            created_group = result['result']
            created_group_id = created_group['id']
            self.group_mapping[group_id] = created_group_id
            self.stats['groups']['imported'] += 1
            return created_group_id
        else:
            logger.error(f"Erreur lors de la création du groupe: {group_name}")
            self.stats['groups']['errors'] += 1
            return None
    
    def get_all_showcases(self) -> List[Dict[str, Any]]:
        """Récupère tous les showcases (réutilisations) du CKAN source"""
        logger.info("Récupération des showcases (réutilisations)...")
        showcases = []
        
        # Utiliser l'API showcase_list de ckanext-showcase
        # Note: Cette extension doit être installée sur le CKAN source
        url = f"{self.source_url}/api/action/ckanext_showcase_list"
        params = {}
        
        result = self._call_api(url, params=params, is_source=True)
        if not result:
            logger.warning(" Extension showcase non disponible ou aucune action ckanext_showcase_list trouvée")
            return []
        
        showcase_list = result.get('result', [])
        if not showcase_list:
            logger.info("Aucun showcase trouvé")
            return []
        
        # Si la liste retourne des IDs ou noms, récupérer les détails complets
        for item in showcase_list:
            showcase_id = None
            if isinstance(item, dict):
                showcase_id = item.get('id') or item.get('name')
            elif isinstance(item, str):
                showcase_id = item
            
            if showcase_id:
                # Récupérer les détails complets - essayer avec différents paramètres pour obtenir les packages
                show_url = f"{self.source_url}/api/action/ckanext_showcase_show"
                showcase_data = None
                
                # Essayer d'abord sans paramètre spécial avec retry
                show_params = {'id': showcase_id}
                max_showcase_retries = 3
                for attempt in range(max_showcase_retries):
                    try:
                        show_result = self._call_api(show_url, params=show_params, is_source=True)
                        if show_result and show_result.get('result'):
                            showcase_data = show_result['result']
                            break
                        elif attempt < max_showcase_retries - 1:
                            logger.warning(f"   Échec récupération showcase {showcase_id} (tentative {attempt + 1}/{max_showcase_retries}), retry...")
                            time.sleep(2)
                    except Exception as e:
                        if attempt < max_showcase_retries - 1:
                            logger.warning(f"   Erreur récupération showcase {showcase_id} (tentative {attempt + 1}/{max_showcase_retries}): {e}, retry...")
                            time.sleep(2)
                        else:
                            logger.error(f"  Erreur récupération showcase {showcase_id} après {max_showcase_retries} tentatives: {e}")
                            continue  # Passer au showcase suivant
                
                if not showcase_data:
                    logger.error(f"  Impossible de récupérer le showcase {showcase_id}, passage au suivant")
                    continue
                
                # Si num_datasets > 0 mais pas de liste de packages, essayer différents paramètres
                num_datasets = showcase_data.get('num_datasets', 0)
                has_packages_list = bool(showcase_data.get('packages') or showcase_data.get('package_ids') or 
                                        showcase_data.get('datasets') or showcase_data.get('dataset_ids'))
                
                if num_datasets > 0 and not has_packages_list:
                    logger.info(f"  Showcase {showcase_id} a {num_datasets} dataset(s) mais la liste n'est pas dans la réponse, essai de paramètres supplémentaires...")
                    
                    # Essayer différents paramètres pour obtenir la liste
                    params_to_try = [
                        {'id': showcase_id, 'include_packages': True},
                        {'id': showcase_id, 'include_datasets': True},
                        {'id': showcase_id, 'include_packages': 'true'},
                        {'id': showcase_id, 'include_datasets': 'true'},
                        {'id': showcase_id, 'packages': True},
                        {'id': showcase_id, 'datasets': True},
                        {'id': showcase_id, 'with_packages': True},
                        {'id': showcase_id, 'with_datasets': True},
                    ]
                    
                    for params in params_to_try:
                        try:
                            test_result = self._call_api(show_url, params=params, is_source=True)
                            if test_result and test_result.get('result'):
                                test_data = test_result['result']
                                # Vérifier si on a maintenant la liste
                                if (test_data.get('packages') or test_data.get('package_ids') or 
                                    test_data.get('datasets') or test_data.get('dataset_ids')):
                                    showcase_data = test_data
                                    logger.info(f"  Liste des datasets obtenue avec les paramètres: {params}")
                                    break
                        except Exception as e:
                            logger.debug(f"   Paramètres {params} non supportés: {e}")
                            continue
                
                # Utiliser showcase_data qui a été défini ci-dessus (peut avoir été modifié par les tentatives avec paramètres)
                if showcase_data:
                    # Log les champs disponibles pour debug (première fois seulement)
                    is_first = (isinstance(showcase_list, list) and len(showcase_list) > 0 and 
                               showcase_id == (showcase_list[0].get('id') if isinstance(showcase_list[0], dict) else showcase_list[0]))
                    if is_first:
                        available_keys = list(showcase_data.keys())
                        logger.info(f"  Champs disponibles dans ckanext_showcase_show pour le premier showcase: {', '.join(available_keys)}")
                        logger.info(f"\n  EXEMPLE COMPLET DU PREMIER SHOWCASE:")
                        logger.info(f"  {'='*60}")
                        # Afficher tous les champs avec leurs valeurs (tronquées si trop longues)
                        for key in sorted(available_keys):
                            value = showcase_data.get(key)
                            if value is None:
                                logger.info(f"  {key}: None")
                            elif isinstance(value, (str, int, float, bool)):
                                str_value = str(value)
                                if len(str_value) > 200:
                                    logger.info(f"  {key}: {str_value[:200]}... (tronqué, {len(str_value)} caractères)")
                                else:
                                    logger.info(f"  {key}: {str_value}")
                            elif isinstance(value, list):
                                logger.info(f"  {key}: [liste de {len(value)} éléments]")
                                if len(value) > 0:
                                    # Afficher les 3 premiers éléments
                                    for i, item in enumerate(value[:3]):
                                        if isinstance(item, dict):
                                            item_keys = list(item.keys())[:10]
                                            logger.info(f"    [{i}] dict avec clés: {', '.join(item_keys)}{'...' if len(item.keys()) > 10 else ''}")
                                            # Afficher quelques valeurs importantes
                                            for subkey in ['id', 'name', 'title', 'package_id', 'showcase_id']:
                                                if subkey in item:
                                                    logger.info(f"      {subkey}: {item[subkey]}")
                                        else:
                                            logger.info(f"    [{i}]: {str(item)[:100]}{'...' if len(str(item)) > 100 else ''}")
                                    if len(value) > 3:
                                        logger.info(f"    ... et {len(value) - 3} autres éléments")
                            elif isinstance(value, dict):
                                dict_keys = list(value.keys())[:10]
                                logger.info(f"  {key}: {{dict avec clés: {', '.join(dict_keys)}{'...' if len(value.keys()) > 10 else ''}}}")
                                # Afficher quelques valeurs importantes
                                for subkey in ['id', 'name', 'title', 'count', 'total']:
                                    if subkey in value:
                                        logger.info(f"    {subkey}: {value[subkey]}")
                            else:
                                logger.info(f"  {key}: {type(value).__name__} = {str(value)[:200]}")
                        logger.info(f"  {'='*60}\n")
                    
                    # Vérifier si les packages sont déjà dans la réponse de ckanext_showcase_show
                    packages_found = False
                    if 'packages' in showcase_data and showcase_data.get('packages'):
                        packages_list = showcase_data.get('packages', [])
                        if packages_list:
                            packages_found = True
                            logger.info(f"  {len(packages_list)} package(s) trouvé(s) directement dans ckanext_showcase_show pour le showcase {showcase_id}")
                    elif 'package_ids' in showcase_data and showcase_data.get('package_ids'):
                        # Certaines versions retournent package_ids au lieu de packages
                        package_ids_list = showcase_data.get('package_ids', [])
                        if package_ids_list:
                            showcase_data['packages'] = [{'id': pid} for pid in package_ids_list if pid]
                            packages_found = True
                            logger.info(f"  {len(showcase_data['packages'])} package(s) trouvé(s) dans package_ids pour le showcase {showcase_id}")
                    elif 'datasets' in showcase_data and showcase_data.get('datasets'):
                        # Certaines versions utilisent 'datasets' au lieu de 'packages'
                        datasets_list = showcase_data.get('datasets', [])
                        if datasets_list:
                            # Convertir en format packages
                            showcase_data['packages'] = []
                            for ds in datasets_list:
                                if isinstance(ds, dict):
                                    pkg_id = ds.get('id') or ds.get('name')
                                elif isinstance(ds, str):
                                    pkg_id = ds
                                else:
                                    continue
                                if pkg_id:
                                    showcase_data['packages'].append({'id': pkg_id})
                            if showcase_data['packages']:
                                packages_found = True
                                logger.info(f"  {len(showcase_data['packages'])} package(s) trouvé(s) dans datasets pour le showcase {showcase_id}")
                    elif 'dataset_ids' in showcase_data and showcase_data.get('dataset_ids'):
                        # Certaines versions utilisent 'dataset_ids'
                        dataset_ids_list = showcase_data.get('dataset_ids', [])
                        if dataset_ids_list:
                            showcase_data['packages'] = [{'id': pid} for pid in dataset_ids_list if pid]
                            packages_found = True
                            logger.info(f"  {len(showcase_data['packages'])} package(s) trouvé(s) dans dataset_ids pour le showcase {showcase_id}")
                    
                    # Récupérer les packages associés si non présents dans la réponse
                    if not packages_found:
                        # Si num_datasets > 0, on sait qu'il y a des datasets mais ils ne sont pas dans la réponse
                        num_datasets = showcase_data.get('num_datasets', 0)
                        if num_datasets > 0:
                            logger.info(f"   Showcase {showcase_id} a {num_datasets} dataset(s) mais la liste n'est pas disponible dans l'API, tentative via ckanext_showcase_package_association_list...")
                        
                        # Essayer plusieurs méthodes pour récupérer les packages associés
                        import requests
                        headers = self.source_headers
                        showcase_name = showcase_data.get('name')
                        max_assoc_retries = 3
                        package_ids = []
                        
                        # Méthode 1: Essayer ckanext_showcase_package_list (action spécifique pour lister les packages)
                        assoc_urls_to_try = [
                            f"{self.source_url}/api/action/ckanext_showcase_package_list",
                            f"{self.source_url}/api/3/action/ckanext_showcase_package_list",
                            f"{self.source_url}/api/action/ckanext_showcase_package_association_list",
                            f"{self.source_url}/api/3/action/ckanext_showcase_package_association_list",
                        ]
                        
                        # Essayer aussi avec l'API v3 pour showcase_show
                        show_urls_to_try = [
                            f"{self.source_url}/api/3/action/ckanext_showcase_show",
                        ]
                        
                        # D'abord essayer les actions spécifiques pour lister les packages
                        for assoc_url in assoc_urls_to_try:
                            if package_ids:  # Si on a déjà trouvé, arrêter
                                break
                            
                            for attempt in range(max_assoc_retries):
                                try:
                                    # Essayer avec l'ID
                                    assoc_params = {'showcase_id': showcase_id}
                                    response = requests.get(assoc_url, params=assoc_params, headers=headers, timeout=30)
                                    
                                    # Si ça ne marche pas avec l'ID, essayer avec le nom
                                    if response.status_code != 200 and showcase_name:
                                        assoc_params = {'showcase_id': showcase_name}
                                        response = requests.get(assoc_url, params=assoc_params, headers=headers, timeout=30)
                                    
                                    if response.status_code == 200:
                                        result = response.json()
                                        if result and result.get('result'):
                                            # Extraire les IDs des packages
                                            packages_result = result.get('result', [])
                                            for pkg in packages_result:
                                                if isinstance(pkg, dict):
                                                    pkg_id = pkg.get('id') or pkg.get('package_id') or pkg.get('name')
                                                elif isinstance(pkg, str):
                                                    pkg_id = pkg
                                                else:
                                                    continue
                                                if pkg_id:
                                                    package_ids.append({'id': pkg_id})
                                            
                                            if package_ids:
                                                logger.info(f"  {len(package_ids)} package(s) trouvé(s) via {assoc_url} pour le showcase {showcase_id}")
                                                break  # Succès, sortir de la boucle de retry
                                    elif response.status_code == 400:
                                        # API non disponible, essayer la suivante
                                        try:
                                            error_data = response.json()
                                            error_msg = str(error_data.get('error', {}))
                                            if 'inconnue' in error_msg.lower() or 'unknown' in error_msg.lower():
                                                logger.debug(f"  API {assoc_url} non disponible")
                                                break  # Ne pas retry si l'API n'existe pas
                                        except Exception:
                                            break
                                    elif response.status_code in [502, 503, 504]:
                                        # Erreur temporaire, retry
                                        if attempt < max_assoc_retries - 1:
                                            logger.debug(f"   Erreur temporaire {response.status_code} avec {assoc_url} (tentative {attempt + 1}/{max_assoc_retries}), retry...")
                                            time.sleep(2)
                                            continue
                                    else:
                                        # Autre erreur, essayer l'URL suivante
                                        break
                                        
                                except requests.exceptions.RequestException as e:
                                    if attempt < max_assoc_retries - 1:
                                        logger.debug(f"   Erreur de connexion avec {assoc_url} (tentative {attempt + 1}/{max_assoc_retries}): {e}, retry...")
                                        time.sleep(2)
                                        continue
                                    else:
                                        break  # Passer à l'URL suivante
                                except Exception as e:
                                    logger.debug(f"   Erreur avec {assoc_url}: {e}")
                                    break  # Passer à l'URL suivante
                        
                        # Si toujours pas de packages, essayer avec l'API v3 showcase_show avec différents paramètres
                        if not package_ids:
                            for show_url_v3 in show_urls_to_try:
                                if package_ids:  # Si on a déjà trouvé, arrêter
                                    break
                                
                                params_to_try_v3 = [
                                    {'id': showcase_id, 'include_packages': True},
                                    {'id': showcase_id, 'include_datasets': True},
                                    {'id': showcase_id, 'packages': True},
                                    {'id': showcase_id, 'datasets': True},
                                ]
                                
                                for params_v3 in params_to_try_v3:
                                    try:
                                        response = requests.get(show_url_v3, params=params_v3, headers=headers, timeout=30)
                                        if response.status_code == 200:
                                            result = response.json()
                                            if result and result.get('result'):
                                                test_data = result['result']
                                                # Vérifier si on a maintenant la liste
                                                if test_data.get('packages'):
                                                    for pkg in test_data.get('packages', []):
                                                        if isinstance(pkg, dict):
                                                            pkg_id = pkg.get('id') or pkg.get('name')
                                                        elif isinstance(pkg, str):
                                                            pkg_id = pkg
                                                        else:
                                                            continue
                                                        if pkg_id:
                                                            package_ids.append({'id': pkg_id})
                                                
                                                if package_ids:
                                                    logger.info(f"  {len(package_ids)} package(s) trouvé(s) via {show_url_v3} avec paramètres {params_v3} pour le showcase {showcase_id}")
                                                    break
                                    except Exception as e:
                                        logger.debug(f"   Erreur avec {show_url_v3} et paramètres {params_v3}: {e}")
                                        continue
                                
                                if package_ids:
                                    break
                        
                        # Si on a trouvé des packages, les ajouter au showcase_data
                        if package_ids:
                            showcase_data['packages'] = package_ids
                            packages_found = True
                        else:
                            # Dernière tentative avec l'ancienne méthode (ckanext_showcase_package_association_list)
                            assoc_url = f"{self.source_url}/api/action/ckanext_showcase_package_association_list"
                        
                        for attempt in range(max_assoc_retries):
                            try:
                                # Essayer d'abord avec l'ID
                                assoc_params = {'showcase_id': showcase_id}
                                response = requests.get(assoc_url, params=assoc_params, headers=headers, timeout=30)
                                
                                # Si ça ne marche pas avec l'ID, essayer avec le nom
                                if response.status_code != 200 and showcase_name:
                                    assoc_params = {'showcase_id': showcase_name}
                                    response = requests.get(assoc_url, params=assoc_params, headers=headers, timeout=30)
                                
                                # Si l'API n'existe pas (400 avec "action inconnue"), c'est normal pour les anciennes versions
                                if response.status_code == 400:
                                    try:
                                        error_data = response.json()
                                        error_msg = str(error_data.get('error', {}))
                                        if 'inconnue' in error_msg.lower() or 'unknown' in error_msg.lower():
                                            logger.debug(f"  API ckanext_showcase_package_association_list non disponible (version CKAN source)")
                                            break  # Ne pas retry si l'API n'existe pas
                                        else:
                                            # Autre erreur 400, logger et retry
                                            if attempt < max_assoc_retries - 1:
                                                logger.warning(f"   Erreur 400 lors de la récupération des packages associés (tentative {attempt + 1}/{max_assoc_retries}): {error_msg}, retry...")
                                                time.sleep(2)
                                                continue
                                            else:
                                                logger.debug(f"   Erreur 400 après {max_assoc_retries} tentatives: {error_msg}")
                                    except Exception:
                                        if attempt < max_assoc_retries - 1:
                                            time.sleep(2)
                                            continue
                                        logger.debug(f"  API ckanext_showcase_package_association_list non disponible (version CKAN source)")
                                elif response.status_code == 200:
                                    result = response.json()
                                    if result and result.get('result'):
                                        # Extraire les IDs des packages depuis les associations
                                        associations = result.get('result', [])
                                        for assoc in associations:
                                            if isinstance(assoc, dict):
                                                pkg_id = assoc.get('package_id') or assoc.get('id')
                                            elif isinstance(assoc, str):
                                                pkg_id = assoc
                                            else:
                                                continue
                                            if pkg_id:
                                                package_ids.append({'id': pkg_id})
                                        
                                        if package_ids:
                                            showcase_data['packages'] = package_ids
                                            packages_found = True
                                            logger.info(f"  {len(package_ids)} package(s) associé(s) trouvé(s) via ckanext_showcase_package_association_list pour le showcase {showcase_id}")
                                            break  # Succès, sortir de la boucle de retry
                                elif response.status_code in [502, 503, 504]:
                                    # Erreur temporaire, retry
                                    if attempt < max_assoc_retries - 1:
                                        logger.warning(f"   Erreur temporaire {response.status_code} lors de la récupération des packages associés (tentative {attempt + 1}/{max_assoc_retries}), retry...")
                                        time.sleep(2)
                                        continue
                                    else:
                                        logger.error(f"  Erreur {response.status_code} après {max_assoc_retries} tentatives pour le showcase {showcase_id}")
                                else:
                                    # Autre erreur
                                    if attempt < max_assoc_retries - 1:
                                        logger.warning(f"   Erreur {response.status_code} lors de la récupération des packages associés (tentative {attempt + 1}/{max_assoc_retries}), retry...")
                                        time.sleep(2)
                                        continue
                                    else:
                                        logger.error(f"  Erreur {response.status_code} après {max_assoc_retries} tentatives pour le showcase {showcase_id}")
                                
                            except requests.exceptions.RequestException as e:
                                if attempt < max_assoc_retries - 1:
                                    logger.warning(f"   Erreur de connexion lors de la récupération des packages associés (tentative {attempt + 1}/{max_assoc_retries}): {e}, retry...")
                                    time.sleep(2)
                                    continue
                                else:
                                    logger.error(f"  Erreur de connexion après {max_assoc_retries} tentatives pour le showcase {showcase_id}: {e}")
                            except Exception as e:
                                if attempt < max_assoc_retries - 1:
                                    logger.warning(f"   Erreur inattendue lors de la récupération des packages associés (tentative {attempt + 1}/{max_assoc_retries}): {e}, retry...")
                                    time.sleep(2)
                                    continue
                                else:
                                    logger.error(f"  Erreur inattendue après {max_assoc_retries} tentatives pour le showcase {showcase_id}: {e}")
                        
                        if not packages_found and num_datasets > 0:
                            logger.warning(f"   Impossible de récupérer la liste des {num_datasets} dataset(s) associé(s) au showcase {showcase_id} - les associations ne seront pas importées")
                    
                    # Si toujours pas de packages, vérifier d'autres champs possibles
                    if 'packages' not in showcase_data or not showcase_data.get('packages'):
                        # Certaines versions peuvent retourner les packages dans d'autres champs
                        if 'package_ids' in showcase_data:
                            package_ids_list = showcase_data.get('package_ids', [])
                            if package_ids_list:
                                showcase_data['packages'] = [{'id': pid} for pid in package_ids_list if pid]
                                logger.debug(f"  {len(showcase_data['packages'])} package(s) trouvé(s) dans package_ids pour le showcase {showcase_id}")
                    
                    showcases.append(showcase_data)
        
        logger.info(f"{len(showcases)} showcases trouvés")
        
        # En mode test, limiter à quelques showcases
        if self.test_mode and len(showcases) > 0:
            showcases = showcases[:min(3, len(showcases))]
            logger.info(f"Mode TEST: limitation à {len(showcases)} showcases")
        
        return showcases
    
    def import_showcase(self, showcase: Dict[str, Any]) -> Optional[str]:
        """
        Importe un showcase (réutilisation) dans le CKAN cible
        
        Args:
            showcase: Données du showcase source
            
        Returns:
            ID du showcase créé ou None
        """
        showcase_id = showcase.get('id')
        showcase_name = showcase.get('name')
        
        if not showcase_name:
            logger.warning(f" Showcase sans nom (ID: {showcase_id}), ignoré")
            return None
        
        # Vérifier si le showcase existe déjà
        check_url = f"{self.target_url}/api/action/ckanext_showcase_show"
        check_params = {'id': showcase_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        existing_showcase = existing.get('result') if existing else None
        if existing_showcase:
            logger.info(f"Showcase '{showcase_name}' existe déjà, vérification des mises à jour...")
            existing_showcase_id = existing_showcase['id']
            
            # Stocker le mapping showcase_id_source -> showcase_id_target
            source_showcase_id = showcase.get('id')
            if source_showcase_id:
                self.showcase_mapping[source_showcase_id] = existing_showcase_id
            
            # Vérifier si une mise à jour est nécessaire
            needs_update = False
            update_data = {}
            
            # Comparer les champs importants
            important_fields = ['title', 'notes', 'image_url', 'featured', 'author']
            for field in important_fields:
                source_value = showcase.get(field)
                current_value = existing_showcase.get(field)
                if source_value != current_value:
                    logger.info(f"  Champ '{field}' a changé: '{current_value}' → '{source_value}'")
                    update_data[field] = source_value
                    needs_update = True
            
            # Mettre à jour si nécessaire
            if needs_update:
                logger.info(f"  Mise à jour du showcase '{showcase_name}'...")
                update_url = f"{self.target_url}/api/action/ckanext_showcase_update"
                update_data['id'] = existing_showcase_id
                # Préserver le nom
                if 'name' in existing_showcase:
                    update_data['name'] = existing_showcase['name']
                
                result = self._call_api(update_url, method='POST', data=update_data, is_source=False)
                if result and result.get('result'):
                    logger.info(f"  Showcase '{showcase_name}' mis à jour")
                    self.stats['showcases']['updated'] += 1
                else:
                    logger.error(f"  Erreur lors de la mise à jour du showcase '{showcase_name}'")
                    self.stats['showcases']['errors'] += 1
            else:
                logger.info(f"  Showcase '{showcase_name}' est à jour")
                self.stats['showcases']['skipped'] += 1
            
            # Mettre à jour les datasets associés (packages)
            source_packages = showcase.get('packages', [])
            if source_packages:
                self._update_showcase_packages(existing_showcase_id, source_packages)
            
            return existing_showcase_id
        
        # Préparer les données pour l'import
        showcase_data = {
            'name': showcase_name,
            'title': showcase.get('title', showcase_name),
            'notes': showcase.get('notes', ''),
            'image_url': showcase.get('image_url', ''),
            'featured': showcase.get('featured', False),
            'author': showcase.get('author', ''),
            'url': showcase.get('url', ''),
        }
        
        # Ajouter les extras si présents
        if showcase.get('extras'):
            showcase_data['extras'] = showcase.get('extras')
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait showcase: {showcase_name}")
            self.stats['showcases']['imported'] += 1
            return f"dry-run-{showcase_name}"
        
        # Créer le showcase
        create_url = f"{self.target_url}/api/action/ckanext_showcase_create"
        result = self._call_api(create_url, method='POST', data=showcase_data, is_source=False)
        
        if result and result.get('result'):
            created_showcase = result['result']
            created_showcase_id = created_showcase['id']
            logger.info(f"Showcase créé: {showcase_name} (ID: {created_showcase_id})")
            self.stats['showcases']['imported'] += 1
            
            # Stocker le mapping showcase_id_source -> showcase_id_target
            source_showcase_id = showcase.get('id')
            if source_showcase_id:
                self.showcase_mapping[source_showcase_id] = created_showcase_id
            
            # Associer les datasets (packages) au showcase
            source_packages = showcase.get('packages', [])
            if source_packages:
                logger.info(f"  Association de {len(source_packages)} dataset(s) au showcase...")
                self._update_showcase_packages(created_showcase_id, source_packages)
            else:
                logger.warning(f"   Aucun package associé trouvé pour le showcase {showcase_name} - le showcase sera créé sans associations (limitation de l'ancienne version CKAN source)")
            
            return created_showcase_id
        else:
            logger.error(f"Erreur lors de la création du showcase: {showcase_name}")
            self.stats['showcases']['errors'] += 1
            return None
    
    def _update_showcase_packages(self, showcase_id: str, source_packages: List[Dict[str, Any]]) -> None:
        """
        Met à jour les datasets associés à un showcase
        
        Args:
            showcase_id: ID du showcase cible
            source_packages: Liste des packages source associés au showcase
        """
        # Mapper les IDs de packages source vers les IDs cibles
        package_ids = []
        for source_package in source_packages:
            source_package_id = source_package.get('id') if isinstance(source_package, dict) else source_package
            if source_package_id and source_package_id in self.dataset_mapping:
                target_package_id = self.dataset_mapping[source_package_id]
                package_ids.append(target_package_id)
        
        if not package_ids:
            return
        
        # Associer les packages au showcase
        try:
            update_url = f"{self.target_url}/api/action/ckanext_showcase_package_association_create"
            for package_id in package_ids:
                association_data = {
                    'showcase_id': showcase_id,
                    'package_id': package_id
                }
                result = self._call_api(update_url, method='POST', data=association_data, is_source=False)
                if not result or not result.get('success', True):
                    logger.warning(f"   Impossible d'associer le package {package_id} au showcase {showcase_id}")
        except Exception as e:
            logger.warning(f"   Erreur lors de l'association des packages au showcase: {e}")
    
    def _recover_showcase_associations_from_datasets(self) -> None:
        """
        Récupère les associations showcase-dataset depuis les datasets (approche inverse)
        Utile quand l'API showcase ne retourne pas les packages associés
        """
        logger.info("Tentative de récupération des associations depuis les datasets...")
        
        # Approche 1: Essayer de récupérer les associations via package_search avec un filtre showcase
        # (certaines versions de CKAN permettent de filtrer par showcase)
        logger.info("  Tentative via package_search avec filtre showcase...")
        try:
            search_url = f"{self.source_url}/api/action/package_search"
            # Essayer différents formats de filtre
            for filter_type in ['showcase_id', 'showcase', 'fq']:
                try:
                    if filter_type == 'fq':
                        # Essayer avec fq (filter query)
                        search_params = {'fq': 'showcase_id:*', 'rows': 1000}
                    else:
                        search_params = {filter_type: '*', 'rows': 1000}
                    
                    search_result = self._call_api(search_url, params=search_params, is_source=True)
                    if search_result and search_result.get('result'):
                        results = search_result['result'].get('results', [])
                        if results:
                            logger.info(f"  {len(results)} dataset(s) trouvé(s) avec filtre {filter_type}")
                            # Extraire les associations depuis les résultats
                            for pkg in results:
                                pkg_id = pkg.get('id')
                                # Chercher showcase_id dans les métadonnées
                                showcase_ids = []
                                if 'showcase_id' in pkg:
                                    showcase_ids.append(pkg['showcase_id'])
                                if 'showcases' in pkg:
                                    showcases = pkg['showcases']
                                    if isinstance(showcases, list):
                                        for sc in showcases:
                                            sc_id = sc.get('id') if isinstance(sc, dict) else sc
                                            if sc_id:
                                                showcase_ids.append(sc_id)
                                
                                for showcase_id in showcase_ids:
                                    if showcase_id not in self._showcase_packages_mapping:
                                        self._showcase_packages_mapping[showcase_id] = []
                                    if pkg_id and pkg_id not in self._showcase_packages_mapping[showcase_id]:
                                        self._showcase_packages_mapping[showcase_id].append(pkg_id)
                            break
                except Exception as e:
                    logger.debug(f"   Filtre {filter_type} non supporté: {e}")
        except Exception as e:
            logger.debug(f"   Erreur lors de la recherche par filtre showcase: {e}")
        
        # Approche 2: Parcourir tous les datasets et chercher dans leurs métadonnées
        logger.info("  Parcours des datasets pour chercher les associations dans les métadonnées...")
        datasets = self.get_all_datasets()
        associations_found = 0
        
        # Échantillonner quelques datasets pour voir leur structure
        sample_size = min(10, len(datasets))
        logger.info(f"  Analyse de {sample_size} datasets en échantillon pour comprendre la structure...")
        
        for i, dataset in enumerate(datasets[:sample_size]):
            dataset_id = dataset.get('id')
            dataset_name = dataset.get('name')
            
            # Log la structure du premier dataset pour debug
            if i == 0:
                dataset_keys = list(dataset.keys())
                logger.info(f"  Champs disponibles dans dataset (échantillon): {', '.join(dataset_keys[:30])}{'...' if len(dataset_keys) > 30 else ''}")
                # Vérifier les extras
                extras = dataset.get('extras', [])
                if extras:
                    logger.info(f"  Extras du dataset (échantillon): {extras[:5] if len(extras) > 5 else extras}")
            
            # Vérifier si le dataset a des showcases associés dans ses extras ou métadonnées
            extras = dataset.get('extras', [])
            if isinstance(extras, list):
                for extra in extras:
                    if isinstance(extra, dict):
                        key = extra.get('key', '')
                        value = extra.get('value', '')
                        # Chercher des champs liés aux showcases
                        if 'showcase' in key.lower() and value:
                            try:
                                # Peut être un JSON string ou une liste
                                if isinstance(value, str):
                                    import json
                                    showcase_ids = json.loads(value) if value.startswith('[') or value.startswith('{') else [value]
                                else:
                                    showcase_ids = value if isinstance(value, list) else [value]
                                
                                for showcase_id in showcase_ids:
                                    if showcase_id not in self._showcase_packages_mapping:
                                        self._showcase_packages_mapping[showcase_id] = []
                                    if dataset_id not in self._showcase_packages_mapping[showcase_id]:
                                        self._showcase_packages_mapping[showcase_id].append(dataset_id)
                                        associations_found += 1
                                        logger.info(f"  Association trouvée (extras): showcase {showcase_id} <-> dataset {dataset_name}")
                            except Exception as e:
                                logger.debug(f"   Erreur parsing showcase extra pour dataset {dataset_name}: {e}")
            
            # Essayer aussi de récupérer depuis package_show avec un paramètre spécial
            # (certaines versions peuvent retourner les showcases associés)
            if i < 3:  # Seulement pour les 3 premiers pour ne pas surcharger
                try:
                    show_url = f"{self.source_url}/api/action/package_show"
                    show_params = {'id': dataset_id}
                    show_result = self._call_api(show_url, params=show_params, is_source=True)
                    if show_result and show_result.get('result'):
                        package_data = show_result['result']
                        # Log la structure pour le premier
                        if i == 0:
                            pkg_keys = list(package_data.keys())
                            logger.info(f"  Champs disponibles dans package_show (échantillon): {', '.join(pkg_keys[:30])}{'...' if len(pkg_keys) > 30 else ''}")
                        # Chercher les showcases dans la réponse
                        if 'showcases' in package_data:
                            showcase_list = package_data.get('showcases', [])
                            for showcase_item in showcase_list:
                                showcase_id = showcase_item.get('id') if isinstance(showcase_item, dict) else showcase_item
                                if showcase_id:
                                    if showcase_id not in self._showcase_packages_mapping:
                                        self._showcase_packages_mapping[showcase_id] = []
                                    if dataset_id not in self._showcase_packages_mapping[showcase_id]:
                                        self._showcase_packages_mapping[showcase_id].append(dataset_id)
                                        associations_found += 1
                                        logger.info(f"  Association trouvée (package_show): showcase {showcase_id} <-> dataset {dataset_name}")
                except Exception as e:
                    logger.debug(f"   Erreur lors de la récupération package_show pour dataset {dataset_name}: {e}")
        
        # Si on a trouvé des associations dans l'échantillon, parcourir tous les datasets
        if associations_found > 0:
            logger.info(f"  {associations_found} association(s) trouvée(s) dans l'échantillon, parcours de tous les datasets...")
            for dataset in datasets[sample_size:]:
                dataset_id = dataset.get('id')
                dataset_name = dataset.get('name')
                
                # Vérifier les extras
                extras = dataset.get('extras', [])
                if isinstance(extras, list):
                    for extra in extras:
                        if isinstance(extra, dict):
                            key = extra.get('key', '')
                            value = extra.get('value', '')
                            if 'showcase' in key.lower() and value:
                                try:
                                    if isinstance(value, str):
                                        import json
                                        showcase_ids = json.loads(value) if value.startswith('[') or value.startswith('{') else [value]
                                    else:
                                        showcase_ids = value if isinstance(value, list) else [value]
                                    
                                    for showcase_id in showcase_ids:
                                        if showcase_id not in self._showcase_packages_mapping:
                                            self._showcase_packages_mapping[showcase_id] = []
                                        if dataset_id not in self._showcase_packages_mapping[showcase_id]:
                                            self._showcase_packages_mapping[showcase_id].append(dataset_id)
                                            associations_found += 1
                                except Exception:
                                    pass
        
        if associations_found > 0:
            logger.info(f"{associations_found} association(s) trouvée(s) depuis les datasets")
            # Maintenant, créer ces associations sur le CKAN cible
            self._create_showcase_associations_from_mapping()
        else:
            logger.warning(" Aucune association trouvée depuis les datasets - les associations peuvent être stockées uniquement dans la base de données (non accessible via API)")
    
    def _create_showcase_associations_from_mapping(self) -> None:
        """
        Crée les associations showcase-dataset sur le CKAN cible depuis le mapping récupéré
        """
        logger.info("Création des associations showcase-dataset depuis le mapping...")
        created_count = 0
        skipped_count = 0
        
        for showcase_id_source, package_ids_source in self._showcase_packages_mapping.items():
            # Trouver le showcase cible correspondant via le mapping
            showcase_target_id = self.showcase_mapping.get(showcase_id_source)
            
            if not showcase_target_id:
                # Essayer de trouver le showcase cible par son ID source dans le CKAN cible
                # (si les IDs sont préservés lors de l'import)
                try:
                    check_url = f"{self.target_url}/api/action/ckanext_showcase_show"
                    check_params = {'id': showcase_id_source}
                    existing = self._call_api(check_url, params=check_params, is_source=False)
                    
                    if existing and existing.get('result'):
                        showcase_target_id = existing['result'].get('id')
                        # Stocker dans le mapping pour les prochaines fois
                        self.showcase_mapping[showcase_id_source] = showcase_target_id
                except Exception as e:
                    logger.debug(f"   Showcase {showcase_id_source} non trouvé dans le CKAN cible: {e}")
                    skipped_count += 1
                    continue
            
            if showcase_target_id:
                # Mapper les package IDs source vers target
                target_package_ids = []
                for package_id_source in package_ids_source:
                    if package_id_source in self.dataset_mapping:
                        target_package_ids.append(self.dataset_mapping[package_id_source])
                    else:
                        logger.debug(f"   Package {package_id_source} non trouvé dans le mapping (peut-être pas importé)")
                
                if target_package_ids:
                    # Créer les associations
                    for package_id_target in target_package_ids:
                        try:
                            assoc_url = f"{self.target_url}/api/action/ckanext_showcase_package_association_create"
                            assoc_data = {
                                'showcase_id': showcase_target_id,
                                'package_id': package_id_target
                            }
                            result = self._call_api(assoc_url, method='POST', data=assoc_data, is_source=False)
                            if result and result.get('success', True):
                                created_count += 1
                                logger.info(f"  Association créée: showcase {showcase_target_id} <-> dataset {package_id_target}")
                            else:
                                # L'association existe peut-être déjà, ce n'est pas grave
                                logger.debug(f"   Association déjà existante ou erreur: showcase {showcase_target_id} <-> dataset {package_id_target}")
                        except Exception as e:
                            logger.debug(f"   Erreur création association: {e}")
                else:
                    logger.debug(f"   Aucun package target trouvé pour le showcase {showcase_id_source}")
            else:
                skipped_count += 1
        
        if created_count > 0:
            logger.info(f"{created_count} association(s) showcase-dataset créée(s) depuis le mapping")
        if skipped_count > 0:
            logger.warning(f" {skipped_count} showcase(s) non trouvé(s) dans le mapping (associations non créées)")
        if created_count == 0 and skipped_count == 0:
            logger.info("Aucune association créée (toutes les associations étaient déjà existantes ou aucun mapping trouvé)")
    
    def get_all_harvest_sources(self) -> List[Dict[str, Any]]:
        """Récupère toutes les sources de moissonnage du CKAN source"""
        logger.info("Récupération des sources de moissonnage...")
        harvest_sources = []
        
        # Utiliser l'API harvest_source_list de ckanext-harvest
        # Note: Cette extension doit être installée sur le CKAN source
        url = f"{self.source_url}/api/action/harvest_source_list"
        params = {}
        
        result = self._call_api(url, params=params, is_source=True)
        if not result:
            # L'extension harvest n'est peut-être pas installée sur le CKAN source, c'est normal
            logger.info("Extension harvest non disponible sur le CKAN source (c'est normal si l'extension n'est pas installée)")
            return []
        
        source_list = result.get('result', [])
        if not source_list:
            logger.info("Aucune source de moissonnage trouvée")
            return []
        
        # Récupérer les détails complets de chaque source
        for item in source_list:
            source_id = None
            if isinstance(item, dict):
                source_id = item.get('id') or item.get('name')
            elif isinstance(item, str):
                source_id = item
            
            if source_id:
                # Récupérer les détails complets
                show_url = f"{self.source_url}/api/action/harvest_source_show"
                show_params = {'id': source_id}
                show_result = self._call_api(show_url, params=show_params, is_source=True)
                if show_result and show_result.get('result'):
                    harvest_sources.append(show_result['result'])
        
        logger.info(f"{len(harvest_sources)} sources de moissonnage trouvées")
        
        # En mode test, limiter à quelques sources
        if self.test_mode and len(harvest_sources) > 0:
            harvest_sources = harvest_sources[:min(3, len(harvest_sources))]
            logger.info(f"Mode TEST: limitation à {len(harvest_sources)} sources de moissonnage")
        
        return harvest_sources
    
    def import_harvest_source(self, source: Dict[str, Any]) -> Optional[str]:
        """
        Importe une source de moissonnage dans le CKAN cible
        
        Args:
            source: Données de la source source
            
        Returns:
            ID de la source créée ou None
        """
        source_id = source.get('id')
        source_name = source.get('name')
        source_url = source.get('url', '')
        
        if not source_name:
            logger.warning(f" Source de moissonnage sans nom (ID: {source_id}), ignorée")
            return None
        
        # Vérifier si la source existe déjà
        check_url = f"{self.target_url}/api/action/harvest_source_show"
        check_params = {'id': source_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        existing_source = existing.get('result') if existing else None
        if existing_source:
            logger.info(f"Source de moissonnage '{source_name}' existe déjà, vérification des mises à jour...")
            existing_source_id = existing_source['id']
            
            # Vérifier si une mise à jour est nécessaire
            needs_update = False
            update_data = {}
            
            # Comparer les champs importants
            important_fields = ['url', 'title', 'notes', 'source_type', 'active', 'config', 'frequency']
            for field in important_fields:
                source_value = source.get(field)
                current_value = existing_source.get(field)
                if source_value != current_value:
                    logger.debug(f"  Champ '{field}' a changé")
                    update_data[field] = source_value
                    needs_update = True
            
            # Mettre à jour si nécessaire
            if needs_update:
                logger.info(f"  Mise à jour de la source de moissonnage '{source_name}'...")
                update_url = f"{self.target_url}/api/action/harvest_source_update"
                update_data['id'] = existing_source_id
                # Préserver le nom
                if 'name' in existing_source:
                    update_data['name'] = existing_source['name']
                
                result = self._call_api(update_url, method='POST', data=update_data, is_source=False)
                if result and result.get('result'):
                    logger.info(f"  Source de moissonnage '{source_name}' mise à jour")
                    self.stats['harvest_sources']['updated'] += 1
                    return existing_source_id
                else:
                    logger.error(f"  Erreur lors de la mise à jour de la source de moissonnage '{source_name}'")
                    self.stats['harvest_sources']['errors'] += 1
                    return None
            else:
                logger.info(f"  Source de moissonnage '{source_name}' est à jour")
                self.stats['harvest_sources']['skipped'] += 1
                return existing_source_id
        
        # Préparer les données pour l'import
        source_data = {
            'name': source_name,
            'title': source.get('title', source_name),
            'url': source_url,
            'source_type': source.get('source_type', 'ckan'),
            'active': source.get('active', True),
            'config': source.get('config', ''),
            'frequency': source.get('frequency', 'MANUAL'),
        }
        
        # Ajouter les notes si présentes
        if source.get('notes'):
            source_data['notes'] = source.get('notes')
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait source de moissonnage: {source_name}")
            self.stats['harvest_sources']['imported'] += 1
            return f"dry-run-{source_name}"
        
        # Créer la source de moissonnage
        create_url = f"{self.target_url}/api/action/harvest_source_create"
        result = self._call_api(create_url, method='POST', data=source_data, is_source=False)
        
        if result and result.get('result'):
            created_source = result['result']
            created_source_id = created_source['id']
            logger.info(f"Source de moissonnage créée: {source_name} (ID: {created_source_id})")
            self.stats['harvest_sources']['imported'] += 1
            return created_source_id
        else:
            logger.error(f"Erreur lors de la création de la source de moissonnage: {source_name}")
            self.stats['harvest_sources']['errors'] += 1
            return None
    
    def import_dataset_relationships(self, dataset: Dict[str, Any], target_dataset_id: str) -> int:
        """
        Importe les relations entre datasets
        
        Args:
            dataset: Données du dataset source (contient relationships_as_subject et relationships_as_object)
            target_dataset_id: ID du dataset cible
            
        Returns:
            Nombre de relations créées
        """
        created_count = 0
        
        # Traiter les relations où ce dataset est le sujet (subject)
        relationships_as_subject = dataset.get('relationships_as_subject', [])
        for rel in relationships_as_subject:
            subject_id = target_dataset_id  # Ce dataset
            object_id_source = rel.get('object', '') if isinstance(rel, dict) else rel
            relationship_type = rel.get('type', 'depends_on') if isinstance(rel, dict) else 'depends_on'
            
            # Mapper l'ID du dataset objet
            if object_id_source and object_id_source in self.dataset_mapping:
                object_id_target = self.dataset_mapping[object_id_source]
                
                # Créer la relation
                try:
                    create_url = f"{self.target_url}/api/action/package_relationship_create"
                    rel_data = {
                        'subject': subject_id,
                        'object': object_id_target,
                        'type': relationship_type
                    }
                    result = self._call_api(create_url, method='POST', data=rel_data, is_source=False)
                    if result and result.get('success', True):
                        created_count += 1
                        logger.debug(f"  Relation créée: {subject_id} {relationship_type} {object_id_target}")
                    else:
                        # La relation existe peut-être déjà, ce n'est pas grave
                        logger.debug(f"   Relation déjà existante ou erreur: {subject_id} {relationship_type} {object_id_target}")
                except Exception as e:
                    logger.debug(f"   Erreur lors de la création de la relation (probablement existe déjà): {e}")
        
        # Traiter les relations où ce dataset est l'objet (object)
        relationships_as_object = dataset.get('relationships_as_object', [])
        for rel in relationships_as_object:
            object_id = target_dataset_id  # Ce dataset
            subject_id_source = rel.get('subject', '') if isinstance(rel, dict) else rel
            relationship_type = rel.get('type', 'depends_on') if isinstance(rel, dict) else 'depends_on'
            
            # Mapper l'ID du dataset sujet
            if subject_id_source and subject_id_source in self.dataset_mapping:
                subject_id_target = self.dataset_mapping[subject_id_source]
                
                # Créer la relation
                try:
                    create_url = f"{self.target_url}/api/action/package_relationship_create"
                    rel_data = {
                        'subject': subject_id_target,
                        'object': object_id,
                        'type': relationship_type
                    }
                    result = self._call_api(create_url, method='POST', data=rel_data, is_source=False)
                    if result and result.get('success', True):
                        created_count += 1
                        logger.debug(f"  Relation créée: {subject_id_target} {relationship_type} {object_id}")
                    else:
                        # La relation existe peut-être déjà, ce n'est pas grave
                        logger.debug(f"   Relation déjà existante ou erreur: {subject_id_target} {relationship_type} {object_id}")
                except Exception as e:
                    logger.debug(f"   Erreur lors de la création de la relation (probablement existe déjà): {e}")
                    self.stats['dataset_relationships']['errors'] += 1
        
        if created_count > 0:
            logger.debug(f"  {created_count} relation(s) créée(s) pour ce dataset")
            self.stats['dataset_relationships']['created'] += created_count
        
        return created_count
    
    def get_all_datasets(self) -> List[Dict[str, Any]]:
        """Récupère tous les datasets du CKAN source avec TOUTES les métadonnées"""
        logger.info("Récupération des datasets...")
        datasets = []
        offset = 0
        limit = 1000
        
        # D'abord, récupérer la liste des noms/IDs via package_search
        dataset_names = []
        while True:
            url = f"{self.source_url}/api/action/package_search"
            params = {
                'rows': limit,
                'start': offset,
                'include_private': True,
                'include_drafts': True,
                'fq': '',  # Pas de filtre
            }
            
            result = self._call_api(url, params=params, is_source=True)
            if not result:
                break
            
            search_result = result.get('result', {})
            dataset_list = search_result.get('results', [])
            
            if not dataset_list:
                break
            
            # Extraire les noms/IDs des datasets
            for dataset in dataset_list:
                dataset_name = dataset.get('name') or dataset.get('id')
                if dataset_name:
                    dataset_names.append(dataset_name)
            
            count = search_result.get('count', 0)
            if offset + limit >= count:
                break
            
            offset += limit
            logger.info(f"   Récupéré {len(dataset_names)}/{count} datasets (noms)...")
        
        logger.info(f"{len(dataset_names)} datasets trouvés, récupération des métadonnées complètes...")
        
        # En mode test, limiter à 10 datasets
        if self.test_mode and len(dataset_names) > 0:
            dataset_names = dataset_names[:min(10, len(dataset_names))]
            logger.info(f"Mode TEST: limitation à {len(dataset_names)} datasets")
        
        # Maintenant, récupérer les détails complets via package_show pour garantir TOUTES les métadonnées
        total = len(dataset_names)
        for idx, dataset_name in enumerate(dataset_names, 1):
            try:
                url = f"{self.source_url}/api/action/package_show"
                params = {
                    'id': dataset_name,
                    'include_tracking': True,  # Inclure les statistiques de tracking
                    # S'assurer que tous les extras et métadonnées sont inclus
                    # (package_show inclut normalement tout par défaut, mais on le précise pour être sûr)
                }
                
                result = self._call_api(url, params=params, is_source=True)
                if result and result.get('result'):
                    dataset_data = result['result']
                    # Vérifier que les ressources sont présentes
                    resources = dataset_data.get('resources', [])
                    if not resources:
                        logger.warning(f"   Dataset '{dataset_name}' n'a pas de ressources dans la réponse package_show")
                    else:
                        logger.debug(f"  Dataset '{dataset_name}' a {len(resources)} ressource(s)")
                    
                    # Vérifier que les extras sont présents
                    extras = dataset_data.get('extras', [])
                    if extras:
                        logger.debug(f"  Dataset '{dataset_name}' a {len(extras)} extra(s)")
                    
                    datasets.append(dataset_data)
                else:
                    logger.warning(f"   Impossible de récupérer les détails complets pour dataset: {dataset_name}")
                
                # Logger la progression toutes les 50 datasets
                if idx % 50 == 0 or idx == total:
                    logger.info(f"   Récupéré {idx}/{total} datasets complets...")
            except Exception as e:
                logger.warning(f"   Erreur lors de la récupération du dataset {dataset_name}: {e}")
                continue
        
        logger.info(f"{len(datasets)} datasets avec métadonnées complètes récupérés")
        return datasets
    
    def import_dataset(self, dataset: Dict[str, Any]) -> Optional[str]:
        """
        Importe un dataset dans le CKAN cible
        
        Args:
            dataset: Données du dataset source
            
        Returns:
            ID du dataset créé ou None
        """
        dataset_id = dataset.get('id')
        dataset_name = dataset.get('name')
        
        # Filtrer si une liste de retry est fournie
        if self._retry_dataset_ids is not None and dataset_id not in self._retry_dataset_ids:
            return None  # Skip ce dataset si pas dans la liste de retry
        
        # Vérifier si le dataset existe déjà
        check_url = f"{self.target_url}/api/action/package_show"
        check_params = {'id': dataset_name}
        existing = self._call_api(check_url, params=check_params, is_source=False)
        
        existing_dataset = existing.get('result') if existing else None
        if existing_dataset:
            logger.info(f"Dataset '{dataset_name}' existe déjà, vérification des mises à jour...")
            self.dataset_mapping[dataset_id] = existing_dataset['id']
            existing_dataset_id = existing_dataset['id']
            
            # Vérifier et mettre à jour l'organisation si nécessaire
            needs_update = False
            update_data = {}
            
            # Mapper l'organisation attendue
            owner_org = dataset.get('owner_org')
            expected_org_id = None
            if owner_org:
                if owner_org in self.org_mapping:
                    expected_org_id = self.org_mapping[owner_org]
                else:
                    # Essayer de récupérer l'organisation depuis la source
                    org_url = f"{self.source_url}/api/action/organization_show"
                    org_result = self._call_api(org_url, params={'id': owner_org, 'include_users': True}, is_source=True)
                    if org_result and org_result.get('result'):
                        source_org = org_result['result']
                        imported_org_id = self.import_organization(source_org)
                        if imported_org_id:
                            expected_org_id = imported_org_id
            
            # Vérifier si l'organisation est correcte
            current_org_id = existing_dataset.get('owner_org')
            if expected_org_id and current_org_id != expected_org_id:
                logger.warning(f"   Organisation incorrecte: actuelle={current_org_id}, attendue={expected_org_id}")
                update_data['owner_org'] = expected_org_id
                needs_update = True
            
            # Vérifier TOUTES les métadonnées pour détecter les changements
            # Champs de base à vérifier
            important_fields = [
                'title', 'notes', 'license_id', 'state', 'private',
                'author', 'author_email', 'maintainer', 'maintainer_email',
                'url', 'version', 'metadata_created', 'metadata_modified'
            ]
            for field in important_fields:
                source_value = dataset.get(field)
                current_value = existing_dataset.get(field)
                if source_value != current_value:
                    logger.info(f"  Champ '{field}' a changé: '{current_value}' → '{source_value}'")
                    update_data[field] = source_value
                    needs_update = True
            
            # Vérifier les extras (métadonnées personnalisées)
            source_extras = dataset.get('extras', [])
            existing_extras = existing_dataset.get('extras', [])
            if source_extras != existing_extras:
                # Convertir les extras en dict pour comparaison plus facile
                source_extras_dict = {e.get('key'): e.get('value') for e in source_extras if isinstance(e, dict)}
                existing_extras_dict = {e.get('key'): e.get('value') for e in existing_extras if isinstance(e, dict)}
                if source_extras_dict != existing_extras_dict:
                    logger.info(f"  Extras ont changé (source: {len(source_extras)} extras, cible: {len(existing_extras)} extras)")
                    update_data['extras'] = source_extras
                    needs_update = True
            
            # Vérifier les tags
            source_tags = sorted([t.get('name', '') if isinstance(t, dict) else str(t) for t in dataset.get('tags', [])])
            existing_tags = sorted([t.get('name', '') if isinstance(t, dict) else str(t) for t in existing_dataset.get('tags', [])])
            if source_tags != existing_tags:
                logger.info(f"  Tags ont changé (source: {len(source_tags)} tags, cible: {len(existing_tags)} tags)")
                # Nettoyer les tags comme lors de la création
                cleaned_tags = []
                for tag in dataset.get('tags', []):
                    tag_name = tag.get('name', '') if isinstance(tag, dict) else str(tag)
                    import re
                    cleaned_name = re.sub(r'[^a-zA-Z0-9\s\-_\.]', '', tag_name)
                    cleaned_name = cleaned_name.strip()
                    if cleaned_name:
                        cleaned_tags.append({'name': cleaned_name})
                update_data['tags'] = cleaned_tags
                needs_update = True
            
            # Vérifier les groupes
            source_groups = sorted([g.get('name', '') if isinstance(g, dict) else str(g) for g in dataset.get('groups', [])])
            existing_groups = sorted([g.get('name', '') if isinstance(g, dict) else str(g) for g in existing_dataset.get('groups', [])])
            if source_groups != existing_groups:
                logger.info(f"  Groupes ont changé (source: {len(source_groups)} groupes, cible: {len(existing_groups)} groupes)")
                groups_to_add = []
                for group in dataset.get('groups', []):
                    group_name = group.get('name', '') if isinstance(group, dict) else str(group)
                    if group_name:
                        groups_to_add.append({'name': group_name})
                if groups_to_add:
                    update_data['groups'] = groups_to_add
                needs_update = True
            
            # Fonction helper pour normaliser les valeurs pour comparaison
            def normalize_for_comparison(value):
                """Normalise les valeurs pour comparaison (None, '', etc. sont équivalents)"""
                if value is None:
                    return ''
                if isinstance(value, str):
                    return value.strip()
                return value
            
            # Vérifier les autres champs (métadonnées d'extensions)
            # Comparer tous les champs simples qui existent dans la source mais pas dans la cible
            # IMPORTANT: Inclure TOUS les champs personnalisés, même ceux qui ne sont pas dans known_fields
            known_fields = set(important_fields) | {
                'id', 'name', 'resources', 'relationships_as_subject', 'relationships_as_object',
                'tags', 'groups', 'extras', 'owner_org', 'isopen', 'ratings_average',
                'ratings_count', 'ratings_sum', 'tracking_summary', 'num_resources',
                'num_tags', 'organization', 'revision_id', 'revision_timestamp'
            }
            for key, source_value in dataset.items():
                if key not in known_fields and not key.startswith('_'):
                    # Copier les champs simples (str, int, float, bool, None)
                    if isinstance(source_value, (str, int, float, bool, type(None))):
                        existing_value = existing_dataset.get(key)
                        source_norm = normalize_for_comparison(source_value)
                        existing_norm = normalize_for_comparison(existing_value)
                        # Ignorer les différences entre None et '' pour les champs d'extensions
                        if source_norm != existing_norm and (source_norm or existing_norm):
                            logger.info(f"  Champ '{key}' (extension) a changé: '{existing_norm}' → '{source_norm}'")
                            update_data[key] = source_value
                            needs_update = True
                    # Copier aussi les listes et dicts simples (mais pas les objets complexes)
                    elif isinstance(source_value, (list, dict)):
                        existing_value = existing_dataset.get(key)
                        # Comparer les listes/dicts seulement s'ils sont différents
                        if source_value != existing_value:
                            logger.info(f"  Champ '{key}' (extension, liste/dict) a changé")
                            update_data[key] = source_value
                            needs_update = True
            
            # Mettre à jour le dataset si nécessaire
            if needs_update:
                logger.info(f"  Mise à jour du dataset '{dataset_name}'...")
                update_url = f"{self.target_url}/api/action/package_update"
                update_data['id'] = existing_dataset_id
                # Préserver les autres champs
                for field in ['name', 'type']:
                    if field in existing_dataset:
                        update_data[field] = existing_dataset[field]
                
                # IMPORTANT: Ne PAS inclure le champ 'resources' dans update_data
                # car cela écraserait les ressources existantes. Les ressources sont gérées séparément
                # via import_resources() qui est appelé après.
                if 'resources' in update_data:
                    logger.warning(f"   Le champ 'resources' a été trouvé dans update_data, suppression pour éviter l'écrasement")
                    del update_data['resources']
                
                result = self._call_api(update_url, method='POST', data=update_data, is_source=False)
                if result and result.get('result'):
                    logger.info(f"  Dataset '{dataset_name}' mis à jour")
                    self.stats['datasets']['updated'] += 1
                else:
                    logger.error(f"  Erreur lors de la mise à jour du dataset '{dataset_name}'")
                    self.stats['datasets']['errors'] += 1
            else:
                logger.info(f"  Dataset '{dataset_name}' est à jour")
                self.stats['datasets']['skipped'] += 1
            
            # Toujours mettre à jour les ressources (elles peuvent avoir changé)
            # IMPORTANT: Les ressources sont gérées séparément pour éviter l'écrasement
            # Seules les ressources de la source sont importées/mises à jour
            # Les ressources existantes qui ne sont pas dans la source sont préservées
            resources = dataset.get('resources', [])
            if not resources:
                logger.warning(f"   Dataset '{dataset_name}' n'a pas de ressources dans la source")
            else:
                logger.info(f"  Import de {len(resources)} ressource(s) pour le dataset '{dataset_name}'")
            self.import_resources(existing_dataset_id, resources, existing_dataset.get('resources', []), dataset_name)
            
            # Stocker le dataset pour le rapport final
            self._imported_datasets.append({
                'id': existing_dataset_id,
                'name': dataset_name,
                'title': dataset.get('title', dataset_name),
                'owner_org': existing_dataset.get('owner_org'),
                'relationships': dataset.get('relationships_as_subject', []) + dataset.get('relationships_as_object', [])
            })
            
            # Importer les relations entre datasets
            self.import_dataset_relationships(dataset, existing_dataset_id)
            
            return existing_dataset_id
        
        # Préparer les données pour l'import (TOUTES les métadonnées)
        # Copier explicitement les champs principaux
        dataset_data = {
            'name': dataset_name,
            'title': dataset.get('title', dataset_name),
            'notes': dataset.get('notes', ''),
            'author': dataset.get('author', ''),
            'author_email': dataset.get('author_email', ''),
            'maintainer': dataset.get('maintainer', ''),
            'maintainer_email': dataset.get('maintainer_email', ''),
            'license_id': dataset.get('license_id', ''),
            'url': dataset.get('url', ''),
            'version': dataset.get('version', ''),
            'state': dataset.get('state', 'active'),
            'private': dataset.get('private', False),
            'type': dataset.get('type', 'dataset'),
            # Métadonnées additionnelles
            'metadata_created': dataset.get('metadata_created'),
            'metadata_modified': dataset.get('metadata_modified'),
        }
        
        # Copier tous les autres champs qui pourraient exister dans le dataset
        # (pour ne pas perdre de métadonnées ajoutées par des extensions)
        # IMPORTANT: Copier TOUS les champs personnalisés, y compris les listes et dicts simples
        known_fields = set(dataset_data.keys()) | {
            'id', 'name', 'resources', 'relationships_as_subject', 'relationships_as_object',
            'tags', 'groups', 'extras', 'owner_org', 'isopen', 'ratings_average',
            'ratings_count', 'ratings_sum', 'tracking_summary', 'num_resources',
            'num_tags', 'organization', 'revision_id', 'revision_timestamp'
        }
        for key, value in dataset.items():
            if key not in known_fields and not key.startswith('_'):
                # Copier les champs inconnus (peuvent être des métadonnées d'extensions)
                # Copier les types simples (str, int, float, bool, None)
                if isinstance(value, (str, int, float, bool, type(None))):
                    dataset_data[key] = value
                # Copier aussi les listes et dicts simples (pour les métadonnées d'extensions)
                elif isinstance(value, (list, dict)):
                    # Vérifier que ce sont des structures simples (pas trop profondes)
                    try:
                        # Tenter de sérialiser pour vérifier que c'est JSON-serializable
                        import json
                        json.dumps(value)
                        dataset_data[key] = value
                        logger.debug(f"  Champ personnalisé '{key}' (liste/dict) copié depuis la source")
                    except (TypeError, ValueError):
                        # Structure trop complexe, ne pas copier
                        logger.debug(f"   Champ '{key}' ignoré (structure trop complexe pour être copiée)")
        
        # Ajouter les relations si présentes
        if dataset.get('relationships_as_subject'):
            dataset_data['relationships_as_subject'] = dataset.get('relationships_as_subject')
        if dataset.get('relationships_as_object'):
            dataset_data['relationships_as_object'] = dataset.get('relationships_as_object')
        
        # Mapper l'organisation (IMPORTANT: relier le dataset à l'organisation)
        # CKAN exige qu'un dataset ait une organisation (owner_org obligatoire)
        owner_org = dataset.get('owner_org')
        if owner_org:
            if owner_org in self.org_mapping:
                dataset_data['owner_org'] = self.org_mapping[owner_org]
                logger.debug(f"  Dataset '{dataset_name}' relié à l'organisation '{owner_org}' (mappé vers '{self.org_mapping[owner_org]}')")
            else:
                # Organisation non trouvée dans le mapping - essayer de la récupérer depuis la source
                logger.warning(f" Organisation '{owner_org}' non trouvée dans le mapping pour le dataset '{dataset_name}'")
                
                # Essayer de récupérer l'organisation depuis la source et l'importer
                org_url = f"{self.source_url}/api/action/organization_show"
                org_result = self._call_api(org_url, params={'id': owner_org, 'include_users': True}, is_source=True)
                
                if org_result and org_result.get('result'):
                    source_org = org_result['result']
                    logger.info(f"  Récupération de l'organisation manquante: {source_org.get('name')}")
                    imported_org_id = self.import_organization(source_org)
                    if imported_org_id:
                        dataset_data['owner_org'] = imported_org_id
                        logger.info(f"  Organisation importée et liée au dataset: {source_org.get('name')}")
                    else:
                        logger.error(f"  Impossible d'importer l'organisation '{owner_org}' - le dataset ne pourra pas être créé")
                        self.stats['datasets']['errors'] += 1
                        self._error_datasets.append({
                            'id': dataset_id,
                            'name': dataset_name,
                            'title': dataset.get('title', dataset_name),
                            'error': f"Organisation '{owner_org}' introuvable"
                        })
                        return None
                else:
                    # Organisation introuvable même dans la source - ignorer le dataset
                    logger.error(f"  Organisation '{owner_org}' introuvable dans la source - le dataset ne pourra pas être créé")
                    self.stats['datasets']['errors'] += 1
                    self._error_datasets.append({
                        'id': dataset_id,
                        'name': dataset_name,
                        'title': dataset.get('title', dataset_name),
                        'error': f"Organisation '{owner_org}' introuvable dans la source"
                    })
                    return None
        else:
            # Pas d'organisation - CKAN exige une organisation, donc on ne peut pas créer le dataset
            logger.error(f"  Dataset '{dataset_name}' n'a pas d'organisation propriétaire - CKAN exige une organisation")
            self.stats['datasets']['errors'] += 1
            self._error_datasets.append({
                'id': dataset_id,
                'name': dataset_name,
                'title': dataset.get('title', dataset_name),
                'error': "Pas d'organisation propriétaire"
            })
            return None
        
        # Ajouter les tags (nettoyer les caractères invalides)
        if dataset.get('tags'):
            cleaned_tags = []
            for tag in dataset.get('tags', []):
                tag_name = tag.get('name', '') if isinstance(tag, dict) else str(tag)
                # Nettoyer les caractères invalides (CKAN n'accepte que alphanumérique, espaces, tirets, underscores, points)
                import re
                cleaned_name = re.sub(r'[^a-zA-Z0-9\s\-_\.]', '', tag_name)
                cleaned_name = cleaned_name.strip()
                if cleaned_name:  # Ne garder que les tags non vides après nettoyage
                    cleaned_tags.append({'name': cleaned_name})
                    if cleaned_name != tag_name:
                        logger.debug(f"   Tag nettoyé: '{tag_name}' → '{cleaned_name}'")
            dataset_data['tags'] = cleaned_tags
        
        # Ajouter les extras (incluant les métadonnées de harvest)
        if dataset.get('extras'):
            dataset_data['extras'] = dataset.get('extras')
            # Vérifier si c'est un dataset moissonné
            harvest_extras = [e for e in dataset.get('extras', []) 
                            if isinstance(e, dict) and e.get('key') in ['harvest_source_id', 'harvest_source_title', 'harvest_source_url']]
            if harvest_extras:
                logger.info(f"Dataset '{dataset_name}' est moissonné (harvest_source_id préservé)")
                self.stats['datasets']['harvested'] += 1
        
        # Ajouter les groupes
        if dataset.get('groups'):
            groups_to_add = []
            for group in dataset.get('groups', []):
                group_name = group.get('name', '') if isinstance(group, dict) else str(group)
                if group_name:
                    # Vérifier que le groupe existe dans le mapping (ou sera créé)
                    # Note: Les groupes sont importés avant les datasets, donc ils devraient exister
                    groups_to_add.append({'name': group_name})
                else:
                    logger.warning(f"   Groupe sans nom ignoré pour le dataset '{dataset_name}'")
            if groups_to_add:
                dataset_data['groups'] = groups_to_add
        
        # Préparer les ressources (seront importées séparément)
        resources = dataset.get('resources', [])
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Créerait dataset: {dataset_name} ({len(resources)} ressources)")
            self.dataset_mapping[dataset_id] = f"dry-run-{dataset_name}"
            self.stats['datasets']['imported'] += 1
            return self.dataset_mapping[dataset_id]
        
        # Créer le dataset (sans ressources pour l'instant)
        # IMPORTANT: Ne pas inclure les ressources dans dataset_data lors de la création
        # car elles seront importées séparément après la création du dataset
        dataset_data['resources'] = []  # Les ressources seront ajoutées après
        
        create_url = f"{self.target_url}/api/action/package_create"
        result = self._call_api(create_url, method='POST', data=dataset_data, is_source=False)
        
        if result and result.get('result'):
            created_dataset = result['result']
            logger.info(f"Dataset créé: {dataset_name} (ID: {created_dataset['id']})")
            self.dataset_mapping[dataset_id] = created_dataset['id']
            self.stats['datasets']['imported'] += 1
            
            # Stocker le dataset pour le rapport final
            self._imported_datasets.append({
                'id': created_dataset['id'],
                'name': dataset_name,
                'title': dataset.get('title', dataset_name),
                'owner_org': created_dataset.get('owner_org'),
                'relationships': dataset.get('relationships_as_subject', []) + dataset.get('relationships_as_object', [])
            })
            
            # Délai pour ne pas surcharger le serveur
            if self.request_delay > 0:
                time.sleep(self.request_delay)
            
            # Importer les ressources
            if not resources:
                logger.warning(f"   Dataset '{dataset_name}' n'a pas de ressources dans la source - le dataset sera créé sans ressources")
            else:
                logger.info(f"  Import de {len(resources)} ressource(s) pour le dataset '{dataset_name}'")
                imported_count = self.import_resources(created_dataset['id'], resources, dataset_name=dataset_name)
                if imported_count == 0:
                    logger.warning(f"   Aucune ressource n'a été importée pour le dataset '{dataset_name}' (vérifiez les logs pour les erreurs)")
            
            # Importer les relations entre datasets
            self.import_dataset_relationships(dataset, created_dataset['id'])
            
            # Importer l'historique d'activité si demandé
            if self.import_activities:
                self.import_dataset_activities(dataset_id, created_dataset['id'])
            
            return created_dataset['id']
        else:
            logger.error(f"Erreur lors de la création du dataset: {dataset_name}")
            self.stats['datasets']['errors'] += 1
            self._error_datasets.append({
                'id': dataset_id,
                'name': dataset_name,
                'title': dataset.get('title', dataset_name),
                'error': "Erreur lors de la création"
            })
            return None
    
    def _download_file(self, url: str, filepath: Path) -> bool:
        """
        Télécharge un fichier depuis une URL
        
        Args:
            url: URL du fichier à télécharger
            filepath: Chemin où sauvegarder le fichier
            
        Returns:
            True si succès, False sinon
        """
        try:
            headers = self.source_headers.copy()
            logger.debug(f"  Tentative de téléchargement: {url}")
            response = requests.get(url, headers=headers, stream=True, timeout=300)
            
            # Logger le code de statut
            logger.debug(f"  Code HTTP: {response.status_code}")
            
            response.raise_for_status()
            
            # Vérifier le Content-Length si disponible
            content_length = response.headers.get('Content-Length')
            if content_length:
                logger.debug(f"  Taille attendue: {content_length} bytes")
            
            filepath.parent.mkdir(parents=True, exist_ok=True)
            total_size = 0
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total_size += len(chunk)
            
            # Vérifier que le fichier a bien été écrit
            if filepath.exists():
                actual_size = filepath.stat().st_size
                logger.debug(f"  Fichier téléchargé: {actual_size} bytes")
                if content_length and int(content_length) != actual_size:
                    logger.warning(f"   Taille inattendue: attendu {content_length} bytes, obtenu {actual_size} bytes")
                if actual_size == 0:
                    logger.error(f"  Fichier téléchargé vide (0 bytes)")
                    return False
                return True
            else:
                logger.error(f"  Fichier non créé après téléchargement")
                return False
                
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'N/A'
            error_detail = ''
            try:
                if e.response:
                    error_detail = e.response.text[:200]
            except Exception:
                pass
            logger.error(f"  Erreur HTTP {status_code} lors du téléchargement de {url[:80]}...")
            logger.error(f"  Détail: {str(e)}")
            if error_detail:
                logger.error(f"  Réponse serveur: {error_detail}")
            return False
        except requests.exceptions.Timeout as e:
            logger.error(f"  Timeout lors du téléchargement de {url[:80]}... (délai > 300s)")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error(f"  Erreur de connexion lors du téléchargement de {url[:80]}...")
            logger.error(f"  Détail: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"  Erreur inattendue lors du téléchargement de {url[:80]}...")
            logger.error(f"  Type: {type(e).__name__}, Message: {str(e)}")
            import traceback
            logger.debug(f"  Traceback: {traceback.format_exc()}")
            return False
    
    def import_resources(self, dataset_id: str, resources: List[Dict[str, Any]], 
                        existing_resources: Optional[List[Dict[str, Any]]] = None,
                        dataset_name: Optional[str] = None) -> int:
        """
        Importe les ressources d'un dataset (avec téléchargement et upload des fichiers)
        Compare avec les ressources existantes et met à jour si nécessaire
        
        IMPORTANT: Cette fonction ne supprime PAS les ressources existantes qui ne sont pas dans la source.
        Seules les ressources de la source sont importées/mises à jour. Les ressources existantes qui
        ne sont pas dans la source sont préservées.
        
        Args:
            dataset_id: ID du dataset cible
            resources: Liste des ressources à importer depuis la source
            existing_resources: Liste des ressources existantes (optionnel, pour comparaison)
            dataset_name: Nom du dataset (optionnel, pour les rapports d'erreur)
            
        Returns:
            Nombre de ressources importées/mises à jour
        """
        imported = 0
        updated = 0
        dataset_name = dataset_name or 'unknown'
        
        if not resources:
            logger.warning(f"   Aucune ressource à importer pour le dataset '{dataset_name}'")
            return 0
        
        existing_count = len(existing_resources) if existing_resources else 0
        logger.info(f"  Import de {len(resources)} ressource(s) depuis la source (dataset '{dataset_name}' a {existing_count} ressource(s) existante(s))")
        
        # Créer un index des ressources existantes par nom (plus fiable que par ID)
        existing_by_name = {}
        if existing_resources:
            for res in existing_resources:
                res_name = res.get('name', '')
                if res_name:
                    existing_by_name[res_name] = res
        
        # En mode test, limiter le nombre de ressources par dataset
        if self.test_mode and len(resources) > 3:
            resources = resources[:3]
            logger.debug(f"  Mode TEST: limitation à {len(resources)} ressources pour ce dataset")
        
        for resource in resources:
            resource_id = resource.get('id')
            resource_name = resource.get('name', f"resource-{resource_id}")
            
            # Filtrer si une liste de retry est fournie
            if self._retry_resource_ids is not None and resource_id not in self._retry_resource_ids:
                continue  # Skip cette ressource si pas dans la liste de retry
            resource_url = resource.get('url', '')
            url_type = resource.get('url_type', '')
            
            # Transformer les URLs internes en URLs publiques
            transformed_url = self._transform_internal_url(resource_url)
            
            # Préparer les données de la ressource (TOUTES les métadonnées)
            # Copier explicitement les champs principaux
            resource_data = {
                'package_id': dataset_id,
                'name': resource.get('name', ''),
                'description': resource.get('description', ''),
                'format': resource.get('format', ''),
                'mimetype': resource.get('mimetype', ''),
                'size': resource.get('size'),
                'hash': resource.get('hash', ''),
                'created': resource.get('created', ''),
                'last_modified': resource.get('last_modified', ''),
                'position': resource.get('position', 0),
                'state': resource.get('state', 'active'),
                # Métadonnées additionnelles
                'resource_type': resource.get('resource_type', ''),
                'cache_url': self._transform_internal_url(resource.get('cache_url', '')) if resource.get('cache_url') else '',
                'cache_last_updated': resource.get('cache_last_updated', ''),
                'datastore_active': resource.get('datastore_active', False),
                'on_same_domain': resource.get('on_same_domain', False),
                'revision_id': resource.get('revision_id', ''),
            }
            
            # Copier tous les autres champs qui pourraient exister dans la ressource
            # (pour ne pas perdre de métadonnées ajoutées par des extensions)
            known_fields = set(resource_data.keys()) | {'id', 'package_id', 'url', 'url_type'}
            for key, value in resource.items():
                if key not in known_fields and not key.startswith('_'):
                    # Copier les champs inconnus (peuvent être des métadonnées d'extensions)
                    # mais seulement si ce sont des types simples (pas de listes/dicts complexes)
                    if isinstance(value, (str, int, float, bool, type(None))):
                        resource_data[key] = value
            
            # Logger si transformation effectuée
            if transformed_url != resource_url:
                logger.info(f"  URL ressource transformée: {resource_url[:60]}... → {transformed_url[:60]}...")
            
            # Ajouter les extras
            if resource.get('extras'):
                resource_data['extras'] = resource.get('extras')
            
            # Déterminer si on doit télécharger et uploader le fichier
            should_upload_file = False
            file_to_upload = None
            
            # Détecter si l'URL pointe vers un fichier dans le storage CKAN
            # Patterns typiques: /dataset/.../resource/.../download/..., /storage/f/..., /uploads/...
            is_ckan_storage_url = False
            if transformed_url:
                # Vérifier si l'URL pointe vers le CKAN source (fichier stocké dans CKAN)
                ckan_storage_patterns = [
                    r'/dataset/[^/]+/resource/[^/]+/download/',
                    r'/storage/f/',
                    r'/uploads/',
                    r'/resource/[^/]+/download/',
                ]
                
                # Vérifier si l'URL contient le domaine du CKAN source
                source_domain = self.source_url.replace('https://', '').replace('http://', '').split('/')[0]
                is_same_domain = source_domain in transformed_url or transformed_url.startswith('/')
                
                # Si url_type est 'upload', c'est définitivement un fichier stocké dans CKAN
                if url_type == 'upload':
                    is_ckan_storage_url = True
                # Sinon, vérifier les patterns et le domaine
                elif is_same_domain and any(re.search(pattern, transformed_url) for pattern in ckan_storage_patterns):
                    is_ckan_storage_url = True
                    logger.info(f"  Détection: URL pointe vers un fichier dans le storage CKAN (url_type={url_type})")
            
            # Si c'est un fichier stocké dans CKAN (upload ou détecté), télécharger et uploader
            if is_ckan_storage_url and transformed_url:
                # Construire l'URL de téléchargement depuis la source
                download_url = transformed_url
                if not download_url.startswith('http'):
                    # URL relative, construire l'URL complète
                    download_url = urljoin(self.source_url, download_url)
                
                # Télécharger le fichier temporairement
                # Utiliser le format du fichier, le mimetype ou l'extension de l'URL pour le nom de fichier
                file_extension = ''
                
                # 1. Essayer depuis le format
                if resource.get('format'):
                    format_str = resource.get('format', '').lower().strip()
                    # Nettoyer le format (enlever les espaces, points, etc.)
                    format_str = format_str.replace('.', '').replace(' ', '')
                    if format_str:
                        file_extension = f".{format_str}"
                
                # 2. Si pas de format, essayer depuis le mimetype
                if not file_extension and resource.get('mimetype'):
                    mimetype = resource.get('mimetype', '').lower()
                    mimetype_to_ext = {
                        'application/pdf': '.pdf',
                        'text/csv': '.csv',
                        'application/vnd.ms-excel': '.xls',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
                        'application/json': '.json',
                        'application/xml': '.xml',
                        'text/xml': '.xml',
                        'image/png': '.png',
                        'image/jpeg': '.jpg',
                        'image/gif': '.gif',
                    }
                    file_extension = mimetype_to_ext.get(mimetype, '')
                    # Fallback: chercher des mots-clés dans le mimetype
                    if not file_extension:
                        if 'pdf' in mimetype:
                            file_extension = '.pdf'
                        elif 'csv' in mimetype:
                            file_extension = '.csv'
                        elif 'excel' in mimetype or 'spreadsheet' in mimetype:
                            file_extension = '.xlsx'
                        elif 'json' in mimetype:
                            file_extension = '.json'
                        elif 'xml' in mimetype:
                            file_extension = '.xml'
                
                # 3. Si toujours pas d'extension, essayer de l'extraire de l'URL
                if not file_extension and download_url:
                    url_match = re.search(r'\.([a-zA-Z0-9]+)(?:\?|$)', download_url)
                    if url_match:
                        file_extension = f".{url_match.group(1).lower()}"
                
                temp_file = Path(self.temp_dir) / f"{resource_id}_{resource.get('name', 'file')}{file_extension}"
                logger.info(f"  Téléchargement du fichier depuis: {download_url[:80]}...")
                
                download_success = self._download_file(download_url, temp_file)
                if download_success:
                    if temp_file.exists() and temp_file.stat().st_size > 0:
                        file_size = temp_file.stat().st_size
                        file_to_upload = open(temp_file, 'rb')
                        should_upload_file = True
                        resource_data['url_type'] = 'upload'
                        logger.info(f"  Fichier téléchargé: {file_size} bytes")
                    else:
                        if temp_file.exists():
                            actual_size = temp_file.stat().st_size
                            logger.warning(f"   Fichier téléchargé vide ({actual_size} bytes) - création avec URL externe")
                        else:
                            logger.warning(f"   Fichier téléchargé inexistant - création avec URL externe")
                        should_upload_file = False
                        resource_data['url'] = transformed_url
                        resource_data['url_type'] = 'link'
                else:
                    logger.warning(f"   Échec du téléchargement depuis {download_url[:80]}... - création avec URL externe")
                    logger.warning(f"   La ressource sera créée avec l'URL: {transformed_url[:80]}...")
                    resource_data['url'] = transformed_url
                    resource_data['url_type'] = 'link'
            
            # Si ce n'est pas un upload ou si le téléchargement a échoué, utiliser l'URL
            if not should_upload_file:
                resource_data['url'] = transformed_url
                resource_data['url_type'] = url_type or 'link'
            
            # Vérifier si la ressource existe déjà
            existing_resource = existing_by_name.get(resource_name) if existing_by_name else None
            resource_needs_update = False
            
            if existing_resource:
                # Comparer TOUS les champs pour détecter les changements
                existing_resource_id = existing_resource.get('id')
                existing_url_type = existing_resource.get('url_type', 'upload')
                source_url_type = resource_data.get('url_type', 'upload')
                
                # Pour les ressources externes (link), être plus conservateur dans les mises à jour
                is_external = (existing_url_type == 'link' or source_url_type == 'link') and transformed_url
                
                # Fonction helper pour normaliser les valeurs pour comparaison
                def normalize_for_comparison(value):
                    """Normalise les valeurs pour comparaison (None, '', etc. sont équivalents)"""
                    if value is None:
                        return ''
                    if isinstance(value, str):
                        return value.strip()
                    return value
                
                important_fields = [
                    'url', 'format', 'mimetype', 'size', 'hash', 'description',
                    'name', 'resource_type', 'cache_url', 'cache_last_updated',
                    'datastore_active', 'on_same_domain', 'created', 'last_modified',
                    'position', 'state'
                ]
                
                for field in important_fields:
                    source_value = resource_data.get(field)
                    existing_value = existing_resource.get(field)
                    # Normaliser les valeurs pour comparaison
                    source_norm = normalize_for_comparison(source_value)
                    existing_norm = normalize_for_comparison(existing_value)
                    
                    # Pour les ressources externes, ignorer certains champs non critiques
                    if is_external and field in ['cache_url', 'cache_last_updated', 'on_same_domain', 'created', 'last_modified']:
                        continue
                    
                    if source_norm != existing_norm:
                        logger.debug(f"  Ressource '{resource_name}': champ '{field}' a changé: '{existing_norm}' → '{source_norm}'")
                        resource_needs_update = True
                        break
                
                # Vérifier aussi si l'URL a changé (même après transformation)
                existing_url = existing_resource.get('url', '')
                if transformed_url and existing_url != transformed_url:
                    logger.debug(f"  Ressource '{resource_name}': URL a changé: '{existing_url[:50]}...' → '{transformed_url[:50]}...'")
                    resource_needs_update = True
                
                # Vérifier les extras (métadonnées personnalisées) - seulement pour ressources non-externes
                if not is_external:
                    source_extras = resource.get('extras', [])
                    existing_extras = existing_resource.get('extras', [])
                    if source_extras != existing_extras:
                        source_extras_dict = {e.get('key'): normalize_for_comparison(e.get('value')) for e in source_extras if isinstance(e, dict)}
                        existing_extras_dict = {e.get('key'): normalize_for_comparison(e.get('value')) for e in existing_extras if isinstance(e, dict)}
                        if source_extras_dict != existing_extras_dict:
                            logger.debug(f"  Ressource '{resource_name}': extras ont changé")
                            resource_needs_update = True
                
                # Vérifier les autres champs (métadonnées d'extensions) - seulement pour ressources non-externes
                if not is_external:
                    known_fields = set(important_fields) | {'id', 'package_id', 'url_type', 'extras'}
                    for key, source_value in resource.items():
                        if key not in known_fields and not key.startswith('_'):
                            if isinstance(source_value, (str, int, float, bool, type(None))):
                                existing_value = existing_resource.get(key)
                                source_norm = normalize_for_comparison(source_value)
                                existing_norm = normalize_for_comparison(existing_value)
                                if source_norm != existing_norm:
                                    logger.debug(f"  Ressource '{resource_name}': champ '{key}' (extension) a changé: '{existing_norm}' → '{source_norm}'")
                                    resource_needs_update = True
                                    break
            
            # Maintenant qu'on sait si on doit traiter la ressource, appliquer le délai minimum
            # (seulement pour les ressources qui seront réellement importées/mises à jour)
            will_process = True
            if self.dry_run:
                will_process = False  # En dry_run, on ne traite pas vraiment
            elif existing_resource and not resource_needs_update:
                will_process = False  # Ressource déjà à jour, on skip
            
            if will_process:
                # Garantir le délai minimum avant de commencer le traitement réel
                if self._last_resource_import_time is not None:
                    elapsed = time.time() - self._last_resource_import_time
                    if elapsed < self._min_resource_interval:
                        wait_time = self._min_resource_interval - elapsed
                        logger.info(f"   Attente de {wait_time:.1f}s pour respecter le délai minimum de {self._min_resource_interval}s entre les imports de ressources...")
                        time.sleep(wait_time)
                
                # Marquer le début de l'import de cette ressource (avant le téléchargement/upload)
                self._last_resource_import_time = time.time()
            
            if self.dry_run:
                if existing_resource:
                    if resource_needs_update:
                        logger.info(f"  [DRY RUN] Mettrait à jour ressource: {resource_name}")
                    else:
                        logger.info(f"  [DRY RUN] Ressource '{resource_name}' est à jour")
                else:
                    logger.info(f"  [DRY RUN] Créerait ressource: {resource_name}")
                if should_upload_file:
                    logger.info(f"    [DRY RUN] Uploaderait fichier: {temp_file.name}")
                if existing_resource and resource_needs_update:
                    self.stats['resources']['updated'] += 1
                elif not existing_resource:
                    self.stats['resources']['imported'] += 1
                else:
                    self.stats['resources']['skipped'] += 1
                imported += 1
                if file_to_upload:
                    file_to_upload.close()
                continue
            
            # Si la ressource existe et n'a pas besoin de mise à jour, la skip
            if existing_resource and not resource_needs_update:
                logger.debug(f"   Ressource '{resource_name}' existe déjà et est à jour")
                self.stats['resources']['skipped'] += 1
                if file_to_upload:
                    file_to_upload.close()
                continue
            
            # Garantir un délai minimum de 60 secondes entre le début de chaque import de ressource
            # (seulement pour les ressources qui seront réellement importées/mises à jour)
            if self._last_resource_import_time is not None:
                elapsed = time.time() - self._last_resource_import_time
                if elapsed < self._min_resource_interval:
                    wait_time = self._min_resource_interval - elapsed
                    logger.info(f"   Attente de {wait_time:.1f}s pour respecter le délai minimum de {self._min_resource_interval}s entre les imports de ressources...")
                    time.sleep(wait_time)
            
            # Marquer le début de l'import de cette ressource (avant le téléchargement/upload)
            self._last_resource_import_time = time.time()
            
            # Créer ou mettre à jour la ressource
            if existing_resource and resource_needs_update:
                # Mise à jour de la ressource existante
                update_url = f"{self.target_url}/api/action/resource_update"
                resource_data['id'] = existing_resource.get('id')
                logger.info(f"  Mise à jour de la ressource: {resource_name}")
            else:
                # Création d'une nouvelle ressource
                create_url = f"{self.target_url}/api/action/resource_create"
                update_url = create_url
                logger.info(f"  Création de la ressource: {resource_name}")
            
            if should_upload_file and file_to_upload:
                # Upload avec fichier (multipart/form-data)
                file_size = temp_file.stat().st_size if temp_file.exists() else 0
                file_name = resource.get('name', 'file')
                mimetype = resource.get('mimetype', 'application/octet-stream')
                logger.info(f"  Tentative d'upload du fichier: {file_name} ({file_size} bytes, {mimetype})")
                
                files = {
                    'upload': (file_name, file_to_upload, mimetype)
                }
                result = self._call_api(update_url, method='POST', data=resource_data, files=files, is_source=False)
                file_to_upload.close()
                
                if result and result.get('result'):
                    if existing_resource and resource_needs_update:
                        logger.info(f"  Ressource mise à jour avec upload: {resource_name}")
                        logger.info(f"    Fichier uploadé avec succès ({file_size} bytes)")
                        self.stats['resources']['updated'] += 1
                        updated += 1
                    else:
                        logger.info(f"  Ressource créée avec upload: {resource_name}")
                        logger.info(f"    Fichier uploadé avec succès ({file_size} bytes)")
                        self.stats['resources']['imported'] += 1
                        imported += 1
                    
                    # Le délai minimum entre ressources est géré au début de la boucle
                    # Pas besoin de délai supplémentaire ici
                else:
                    # Log détaillé de l'erreur d'upload
                    action = "mise à jour" if existing_resource else "création"
                    logger.error(f"  Échec de l'upload pour la ressource: {resource_name} ({action})")
                    logger.error(f"    Fichier: {file_name} ({file_size} bytes)")
                    logger.error(f"    URL d'upload: {update_url}")
                    
                    # Récupérer les détails de l'erreur depuis _last_api_error
                    if hasattr(self, '_last_api_error') and self._last_api_error:
                        # _last_api_error peut être un dict ou une string selon le type d'erreur
                        if isinstance(self._last_api_error, dict):
                            error_info = self._last_api_error.get('error', {})
                            if isinstance(error_info, dict):
                                error_type = error_info.get('__type', 'Unknown')
                                error_message = error_info.get('message', str(error_info))
                                logger.error(f"    Type d'erreur: {error_type}")
                                logger.error(f"    Message: {error_message}")
                            else:
                                logger.error(f"    Erreur API: {error_info}")
                        else:
                            # _last_api_error est une string (erreur HTML ou texte)
                            logger.error(f"    Erreur serveur: {str(self._last_api_error)[:500]}")
                    else:
                        logger.error(f"    Aucun détail d'erreur disponible (réponse API vide ou None)")
                    
                    # Le fichier a été téléchargé mais l'upload a échoué
                    # L'utilisateur veut garder le téléchargement même si l'upload échoue
                    logger.warning(f"     Le fichier a été téléchargé mais l'upload a échoué. Le fichier téléchargé est disponible localement.")
                    self.stats['resources']['errors'] += 1
                    self._error_resources.append({
                        'id': resource_id,
                        'name': resource_name,
                        'dataset_id': dataset_id,
                        'dataset_name': dataset_name,
                        'error': f"Échec de l'upload ({action}) - fichier téléchargé mais non uploadé"
                    })
            else:
                # Création/mise à jour avec URL seulement
                action = "mise à jour" if existing_resource else "création"
                logger.debug(f"  {action.capitalize()} ressource avec URL (pas d'upload): {transformed_url[:80] if transformed_url else 'N/A'}...")
                result = self._call_api(update_url, method='POST', data=resource_data, is_source=False)
                
                if result and result.get('result'):
                    if existing_resource and resource_needs_update:
                        logger.info(f"  Ressource mise à jour avec URL: {resource_name}")
                        self.stats['resources']['updated'] += 1
                        updated += 1
                    else:
                        logger.info(f"  Ressource créée avec URL: {resource_name}")
                        self.stats['resources']['imported'] += 1
                        imported += 1
                    
                    # Le délai minimum entre ressources est géré au début de la boucle
                    # Pas besoin de délai supplémentaire ici
                else:
                    logger.error(f"  Erreur lors de la création de la ressource: {resource_name}")
                    logger.error(f"    URL: {transformed_url[:80] if transformed_url else 'N/A'}...")
                    
                    # Récupérer les détails de l'erreur
                    if hasattr(self, '_last_api_error') and self._last_api_error:
                        # _last_api_error peut être un dict ou une string selon le type d'erreur
                        if isinstance(self._last_api_error, dict):
                            error_info = self._last_api_error.get('error', {})
                            if isinstance(error_info, dict):
                                error_type = error_info.get('__type', 'Unknown')
                                error_message = error_info.get('message', str(error_info))
                                logger.error(f"    Type d'erreur: {error_type}")
                                logger.error(f"    Message: {error_message}")
                            else:
                                logger.error(f"    Erreur API: {error_info}")
                        else:
                            # _last_api_error est une string (erreur HTML ou texte)
                            logger.error(f"    Erreur serveur: {str(self._last_api_error)[:500]}")
                    else:
                        logger.error(f"    Aucun détail d'erreur disponible")
                    
                    self.stats['resources']['errors'] += 1
                    self._error_resources.append({
                        'id': resource_id,
                        'name': resource_name,
                        'dataset_id': dataset_id,
                        'dataset_name': dataset_name,
                        'error': f"Erreur lors de la {action}"
                    })
        
        logger.info(f"  Ressources traitées: {imported} créées, {updated} mises à jour")
        total_processed = imported + updated
        if total_processed > 0:
            logger.info(f"  {total_processed} ressource(s) traitée(s) pour le dataset '{dataset_name}' ({imported} créée(s), {updated} mise(s) à jour)")
        elif len(resources) > 0:
            logger.warning(f"   Aucune ressource n'a été importée/mise à jour pour le dataset '{dataset_name}' (vérifiez les logs pour les erreurs)")
        return total_processed
    
    def get_dataset_activities(self, dataset_id: str) -> List[Dict[str, Any]]:
        """
        Récupère l'historique d'activité d'un dataset
        
        Args:
            dataset_id: ID du dataset source
            
        Returns:
            Liste des activités
        """
        # Vérifier si l'API activity_list est disponible (certaines installations CKAN ne l'ont pas)
        try:
            url = f"{self.source_url}/api/action/activity_list"
            params = {
                'id': dataset_id,
                'object_type': 'package',
                'limit': 1000
            }
            
            result = self._call_api(url, params=params, is_source=True)
            if result and result.get('result'):
                activities = result.get('result', [])
                logger.debug(f"{len(activities)} activités trouvées pour le dataset {dataset_id}")
                return activities
            return []
        except Exception as e:
            # Ne pas logger d'erreur si l'API n'est pas disponible (c'est normal pour certaines installations)
            # Juste retourner une liste vide
            return []
    
    def import_dataset_activities(self, source_dataset_id: str, target_dataset_id: str) -> int:
        """
        Importe l'historique d'activité d'un dataset et le sauvegarde dans un fichier JSON
        
        Note: CKAN ne permet pas directement d'importer des activités via l'API.
        Cette fonction récupère l'historique et le sauvegarde pour archive.
        
        Args:
            source_dataset_id: ID du dataset source
            target_dataset_id: ID du dataset cible
            
        Returns:
            Nombre d'activités récupérées
        """
        activities = self.get_dataset_activities(source_dataset_id)
        
        if not activities:
            return 0
        
        logger.info(f"Historique récupéré pour le dataset: {len(activities)} activités")
        
        # Sauvegarder les activités dans un fichier JSON
        if self.import_activities and self.activities_dir.exists():
            activity_file = self.activities_dir / f"dataset-{source_dataset_id}.json"
            activity_data = {
                'source_dataset_id': source_dataset_id,
                'target_dataset_id': target_dataset_id,
                'export_date': datetime.now().isoformat(),
                'activities_count': len(activities),
                'activities': activities
            }
            
            try:
                with open(activity_file, 'w', encoding='utf-8') as f:
                    json.dump(activity_data, f, indent=2, ensure_ascii=False, default=str)
                logger.info(f"  Activités sauvegardées: {activity_file}")
            except Exception as e:
                logger.error(f"  Erreur sauvegarde activités: {e}")
        
        # Log les activités pour référence
        for activity in activities[:5]:  # Logger seulement les 5 premières
            activity_type = activity.get('activity_type', 'unknown')
            timestamp = activity.get('timestamp', '')
            user_id = activity.get('user_id', '')
            logger.debug(f"  {activity_type} - {timestamp} - User: {user_id}")
        
        if len(activities) > 5:
            logger.debug(f"  ... et {len(activities) - 5} autres activités")
        
        # Note: Les activités ne peuvent pas être importées directement via l'API CKAN
        # Elles sont créées automatiquement lors des actions (create, update, etc.)
        # On les sauvegarde pour archive
        
        self.stats['activities']['imported'] += len(activities)
        return len(activities)
    
    def import_all(self):
        """Importe toutes les données dans l'ordre"""
        logger.info("Début de l'import complet")
        logger.info("=" * 60)
        
        # 1. Importer les organisations
        if not self.skip_organizations:
            logger.info("\nÉtape 1: Import des organisations")
            logger.info("-" * 60)
            organizations = self.get_all_organizations()
            total_orgs = len(organizations)
            logger.info(f"{total_orgs} organisations à importer")
            
            for org in organizations:
                self.import_organization(org)
                # Ne pas logger la progression (seulement les erreurs)
        else:
            logger.info(" Import des organisations ignoré")
        
        # 2. Importer les utilisateurs
        if not self.skip_users:
            logger.info("\nÉtape 2: Import des utilisateurs")
            logger.info("-" * 60)
            users = self.get_all_users()
            total_users = len(users)
            logger.info(f"{total_users} utilisateurs à importer")
            
            for idx, user in enumerate(users, 1):
                self.import_user(user)
                # Ne pas logger la progression (seulement les erreurs)
        else:
            logger.info(" Import des utilisateurs ignoré")
        
        # 2.5. Ajouter les membres aux organisations (après l'import des utilisateurs)
        if not self.skip_organizations and not self.skip_users:
            if hasattr(self, '_pending_org_members') and self._pending_org_members:
                logger.info("\nÉtape 2.5: Liaison des utilisateurs aux organisations")
                logger.info("-" * 60)
                logger.info(f"{len(self._pending_org_members)} organisations à traiter")
                
                for pending in self._pending_org_members:
                    self.import_organization_members(pending['org_id'], pending['source_org'])
                
                # Nettoyer la liste
                self._pending_org_members = []
        
        # 2.6. Importer les groupes (avant les datasets pour que les associations fonctionnent)
        if not self.skip_datasets:
            logger.info("\n Étape 2.6: Import des groupes")
            logger.info("-" * 60)
            groups = self.get_all_groups()
            total_groups = len(groups)
            logger.info(f"{total_groups} groupes à importer")
            
            for group in groups:
                self.import_group(group)
        
        # 3. Importer les datasets (et leurs ressources)
        if not self.skip_datasets:
            logger.info("\nÉtape 3: Import des datasets")
            logger.info("-" * 60)
            datasets = self.get_all_datasets()
            total_datasets = len(datasets)
            logger.info(f"{total_datasets} datasets à importer")
            
            # Suivi du temps pour calculer la vitesse
            datasets_start_time = time.time()
            datasets_imported_count = 0
            
            for idx, dataset in enumerate(datasets, 1):
                self.import_dataset(dataset)
                datasets_imported_count += 1
                
                # Calculer la vitesse toutes les 5 datasets ou à chaque dataset si moins de 20 au total
                if datasets_imported_count % 5 == 0 or total_datasets <= 20 or idx == total_datasets:
                    elapsed_time = time.time() - datasets_start_time
                    if elapsed_time > 0:
                        speed = (datasets_imported_count / elapsed_time) * 60  # datasets par minute
                        remaining_datasets = total_datasets - datasets_imported_count
                        percentage = (datasets_imported_count * 100) // total_datasets if total_datasets > 0 else 0
                        
                        if speed > 0 and remaining_datasets > 0:
                            estimated_remaining_seconds = (remaining_datasets / (speed / 60)) if speed > 0 else 0
                            estimated_minutes = int(estimated_remaining_seconds // 60)
                            estimated_seconds = int(estimated_remaining_seconds % 60)
                            
                            if estimated_minutes > 0:
                                time_str = f"{estimated_minutes} min {estimated_seconds} sec"
                            else:
                                time_str = f"{estimated_seconds} sec"
                            
                            logger.info(f"   [{datasets_imported_count}/{total_datasets}] {percentage}% | "
                                      f"Vitesse: {speed:.1f} datasets/min | "
                                      f"Temps restant: ~{time_str}")
                        else:
                            logger.info(f"   [{datasets_imported_count}/{total_datasets}] {percentage}% | "
                                      f"Vitesse: {speed:.1f} datasets/min")
                    else:
                        percentage = (datasets_imported_count * 100) // total_datasets if total_datasets > 0 else 0
                        logger.info(f"   [{datasets_imported_count}/{total_datasets}] {percentage}%")
        else:
            logger.info(" Import des datasets ignoré")
        
        # 4. Importer les showcases (réutilisations)
        if not self.skip_showcases:
            logger.info("\nÉtape 4: Import des showcases (réutilisations)")
            logger.info("-" * 60)
            showcases = self.get_all_showcases()
            total_showcases = len(showcases)
            logger.info(f"{total_showcases} showcases à importer")
            
            for showcase in showcases:
                self.import_showcase(showcase)
            
            # 4.5. Récupérer les associations depuis les datasets (approche inverse)
            # Si les showcases n'ont pas de packages, essayer de les récupérer depuis les datasets
            if not self.skip_datasets:
                logger.info("\nÉtape 4.5: Récupération des associations showcase-dataset (approche inverse)")
                logger.info("-" * 60)
                self._recover_showcase_associations_from_datasets()
        else:
            logger.info(" Import des showcases ignoré")
        
        # 5. Importer les sources de moissonnage
        logger.info("\nÉtape 5: Import des sources de moissonnage")
        logger.info("-" * 60)
        harvest_sources = self.get_all_harvest_sources()
        total_sources = len(harvest_sources)
        logger.info(f"{total_sources} sources de moissonnage à importer")
        
        for source in harvest_sources:
            self.import_harvest_source(source)
        
        # Nettoyer le dossier temporaire
        try:
            import shutil
            shutil.rmtree(self.temp_dir)
            logger.info(f"Dossier temporaire nettoyé: {self.temp_dir}")
        except Exception as e:
            logger.warning(f" Erreur nettoyage dossier temporaire: {e}")
        
        # Afficher les statistiques
        self.print_stats()
        
        # Afficher le rapport des datasets sans organisation
        self.print_datasets_without_organization_report()
        
        # Afficher le rapport des erreurs
        self.print_errors_report()
        
        # Afficher le récapitulatif des fichiers d'activités
        if self.import_activities and self.activities_dir.exists():
            activity_files = list(self.activities_dir.glob('*.json'))
            if activity_files:
                logger.info(f"\nFichiers d'activités sauvegardés: {len(activity_files)} fichiers dans {self.activities_dir}")
                logger.info(f"   Exemple: {activity_files[0].name}")
    
    def print_stats(self):
        """Affiche les statistiques d'import"""
        logger.info("\n" + "=" * 60)
        logger.info("Statistiques d'import")
        logger.info("=" * 60)
        
        for category, stats in self.stats.items():
            # Vérifier si cette catégorie a des statistiques à afficher
            has_data = False
            
            # Structure standard (imported, skipped, errors, updated, harvested)
            if 'imported' in stats:
                has_data = (stats.get('imported', 0) > 0 or 
                           stats.get('skipped', 0) > 0 or 
                           stats.get('errors', 0) > 0 or 
                           stats.get('updated', 0) > 0 or 
                           stats.get('harvested', 0) > 0)
            
            # Structure pour dataset_relationships (created, errors)
            elif 'created' in stats:
                has_data = (stats.get('created', 0) > 0 or 
                           stats.get('errors', 0) > 0)
            
            if has_data:
                logger.info(f"\n{category.capitalize()}:")
                
                # Afficher les statistiques selon la structure
                if 'imported' in stats:
                    logger.info(f"  Importés: {stats.get('imported', 0)}")
                    if stats.get('updated', 0) > 0:
                        logger.info(f"  Mis à jour: {stats.get('updated', 0)}")
                    logger.info(f"   Ignorés: {stats.get('skipped', 0)}")
                    logger.info(f"  Erreurs: {stats.get('errors', 0)}")
                    if stats.get('harvested', 0) > 0:
                        logger.info(f"  Moissonnés: {stats.get('harvested', 0)}")
                    if category == 'activities' and stats.get('imported', 0) > 0:
                        logger.info(f"  Activités récupérées: {stats.get('imported', 0)}")
                
                elif 'created' in stats:
                    logger.info(f"  Créées: {stats.get('created', 0)}")
                    logger.info(f"  Erreurs: {stats.get('errors', 0)}")
        
        logger.info("\n" + "=" * 60)
    
    def print_datasets_without_organization_report(self):
        """Affiche un rapport des datasets sans organisation"""
        logger.info("\n" + "=" * 60)
        logger.info("Rapport: Datasets sans organisation")
        logger.info("=" * 60)
        
        datasets_without_org = []
        for dataset in self._imported_datasets:
            owner_org = dataset.get('owner_org')
            if not owner_org:
                datasets_without_org.append(dataset)
        
        if datasets_without_org:
            logger.warning(f"\n {len(datasets_without_org)} dataset(s) sans organisation:")
            for dataset in datasets_without_org:
                logger.warning(f"  - {dataset.get('name', 'N/A')} (ID: {dataset.get('id', 'N/A')}) - {dataset.get('title', 'N/A')}")
            logger.warning("\n NOTE: CKAN exige qu'un dataset ait une organisation. Ces datasets pourraient être dans un état invalide.")
        else:
            logger.info("\nTous les datasets ont une organisation associée.")
        
        logger.info("\n" + "=" * 60)
    
    def print_errors_report(self):
        """Affiche un rapport des datasets et ressources en erreur"""
        logger.info("\n" + "=" * 60)
        logger.info("Rapport: Datasets et ressources en erreur")
        logger.info("=" * 60)
        
        if self._error_datasets:
            logger.warning(f"\n {len(self._error_datasets)} dataset(s) en erreur:")
            for dataset_error in self._error_datasets:
                logger.warning(f"  - {dataset_error.get('name', 'N/A')} (ID: {dataset_error.get('id', 'N/A')}) - {dataset_error.get('title', 'N/A')}")
                logger.warning(f"    Erreur: {dataset_error.get('error', 'Unknown')}")
            
            # Générer la liste des IDs pour réessayer
            error_dataset_ids = [d['id'] for d in self._error_datasets if d.get('id')]
            if error_dataset_ids:
                logger.warning(f"\nPour réessayer uniquement ces datasets, utilisez:")
                logger.warning(f"   --retry-dataset-ids {' '.join(error_dataset_ids)}")
                logger.warning(f"   Ou en JSON: --retry-dataset-ids-json '{json.dumps(error_dataset_ids)}'")
        else:
            logger.info("\nAucun dataset en erreur.")
        
        if self._error_resources:
            logger.warning(f"\n {len(self._error_resources)} ressource(s) en erreur:")
            for resource_error in self._error_resources:
                logger.warning(f"  - {resource_error.get('name', 'N/A')} (ID: {resource_error.get('id', 'N/A')}) - Dataset: {resource_error.get('dataset_name', 'N/A')}")
                logger.warning(f"    Erreur: {resource_error.get('error', 'Unknown')}")
            
            # Générer la liste des IDs pour réessayer
            error_resource_ids = [r['id'] for r in self._error_resources if r.get('id')]
            if error_resource_ids:
                logger.warning(f"\nPour réessayer uniquement ces ressources, utilisez:")
                logger.warning(f"   --retry-resource-ids {' '.join(error_resource_ids)}")
                logger.warning(f"   Ou en JSON: --retry-resource-ids-json '{json.dumps(error_resource_ids)}'")
        else:
            logger.info("\nAucune ressource en erreur.")
        
        if not self._error_datasets and not self._error_resources:
            logger.info("\nAucune erreur à signaler.")
        
        logger.info("\n" + "=" * 60)


def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(
        description='Importe toutes les données depuis un CKAN source vers un CKAN cible',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  # Avec arguments
  python import-from-ckan.py \\
    --source-url https://source.ckan.fr \\
    --source-token YOUR_SOURCE_TOKEN \\
    --target-url https://target.ckan.fr \\
    --target-token YOUR_TARGET_TOKEN

  # Avec variables d'environnement
  SOURCE_CKAN_URL=https://source.ckan.fr \\
  SOURCE_CKAN_TOKEN=TOKEN \\
  TARGET_CKAN_URL=https://target.ckan.fr \\
  TARGET_CKAN_TOKEN=TOKEN \\
  python import-from-ckan.py

  # Mode dry-run (simulation)
  python import-from-ckan.py --dry-run --source-url ... --target-url ...

  # Mode test (import limité: 3 orgs, 5 users, 10 datasets, max 3 ressources/dataset)
  python import-from-ckan.py --test --source-url ... --target-url ...

  # Importer seulement les datasets (sans users/orgs)
  python import-from-ckan.py --skip-users --skip-organizations ...
        """
    )
    
    parser.add_argument('--source-url', 
                       default=os.getenv('SOURCE_CKAN_URL', ''),
                       help='URL du CKAN source (ou SOURCE_CKAN_URL)')
    parser.add_argument('--source-token',
                       default=os.getenv('SOURCE_CKAN_TOKEN', ''),
                       help='Token admin du CKAN source (ou SOURCE_CKAN_TOKEN)')
    parser.add_argument('--target-url',
                       default=os.getenv('TARGET_CKAN_URL', ''),
                       help='URL du CKAN cible (ou TARGET_CKAN_URL)')
    parser.add_argument('--target-token',
                       default=os.getenv('TARGET_CKAN_TOKEN', ''),
                       help='Token admin du CKAN cible (ou TARGET_CKAN_TOKEN)')
    parser.add_argument('--dry-run',
                       action='store_true',
                       help='Mode simulation (ne fait rien)')
    parser.add_argument('--skip-users',
                       action='store_true',
                       help='Ignorer l\'import des utilisateurs')
    parser.add_argument('--skip-organizations',
                       action='store_true',
                       help='Ignorer l\'import des organisations')
    parser.add_argument('--skip-user-orgs',
                       action='store_true',
                       help='Ignorer l\'import des utilisateurs ET des organisations (équivalent à --skip-users --skip-organizations)')
    parser.add_argument('--skip-datasets',
                       action='store_true',
                       help='Ignorer l\'import des datasets')
    parser.add_argument('--skip-showcases',
                       action='store_true',
                       help='Ignorer l\'import des showcases (réutilisations)')
    parser.add_argument('--retry-dataset-ids',
                       nargs='+',
                       default=[],
                       help='Liste d\'IDs de datasets à réessayer (pour réessayer uniquement ceux en erreur)')
    parser.add_argument('--retry-dataset-ids-json',
                       default='',
                       help='Liste d\'IDs de datasets à réessayer au format JSON (ex: \'["id1", "id2"]\')')
    parser.add_argument('--retry-resource-ids',
                       nargs='+',
                       default=[],
                       help='Liste d\'IDs de ressources à réessayer (pour réessayer uniquement celles en erreur)')
    parser.add_argument('--retry-resource-ids-json',
                       default='',
                       help='Liste d\'IDs de ressources à réessayer au format JSON (ex: \'["id1", "id2"]\')')
    parser.add_argument('--source-public-url',
                       default=os.getenv('SOURCE_CKAN_PUBLIC_URL', ''),
                       help='URL publique du CKAN source (pour transformer les URLs internes, ou SOURCE_CKAN_PUBLIC_URL)')
    parser.add_argument('--namespace-url-mapping',
                       default=os.getenv('NAMESPACE_URL_MAPPING', ''),
                       help='Mapping JSON des namespaces vers URLs publiques (ex: \'{"dijon-bfc":"https://data.metropole-dijon.fr"}\' ou NAMESPACE_URL_MAPPING)')
    parser.add_argument('--namespace-url-mapping-file',
                       default=os.getenv('NAMESPACE_URL_MAPPING_FILE', ''),
                       help='Fichier JSON contenant le mapping des namespaces (ex: namespace-mapping.json ou NAMESPACE_URL_MAPPING_FILE)')
    # Import activities activé par défaut (peut être désactivé avec --no-import-activities ou IMPORT_ACTIVITIES=false)
    import_activities_default = os.getenv('IMPORT_ACTIVITIES', '').lower() != 'false'
    parser.add_argument('--no-import-activities',
                       action='store_false',
                       dest='import_activities',
                       default=import_activities_default,
                       help='Désactiver l\'import de l\'historique d\'activité (activé par défaut)')
    parser.add_argument('--test',
                       action='store_true',
                       help='Mode test: import limité à quelques éléments (3 orgs, 5 users, 10 datasets, max 3 ressources par dataset)')
    parser.add_argument('--request-delay',
                       type=float,
                       default=float(os.getenv('REQUEST_DELAY', '0.5')),
                       help='Délai en secondes entre les requêtes de création de datasets pour ne pas surcharger les serveurs CKAN (défaut: 0.5s, ou REQUEST_DELAY). Note: Les ressources ont un délai minimum garanti de 10s entre chaque import.')
    
    args = parser.parse_args()
    
    # Si --skip-user-orgs est activé, activer aussi --skip-users et --skip-organizations
    if args.skip_user_orgs:
        args.skip_users = True
        args.skip_organizations = True
    
    # Parser le mapping des namespaces si fourni
    namespace_mapping = None
    
    # Priorité 1: Fichier JSON
    if args.namespace_url_mapping_file:
        mapping_file = args.namespace_url_mapping_file
        if not os.path.exists(mapping_file):
            logger.error(f"Fichier de mapping introuvable: {mapping_file}")
            sys.exit(1)
        try:
            with open(mapping_file, 'r', encoding='utf-8') as f:
                namespace_mapping = json.load(f)
            logger.info(f"Mapping chargé depuis le fichier: {mapping_file}")
        except json.JSONDecodeError as e:
            logger.error(f"Format JSON invalide dans le fichier {mapping_file}: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Erreur lors de la lecture du fichier {mapping_file}: {e}")
            sys.exit(1)
    
    # Priorité 2: JSON en ligne de commande ou variable d'environnement
    elif args.namespace_url_mapping:
        try:
            namespace_mapping = json.loads(args.namespace_url_mapping)
            logger.info(f"Mapping chargé depuis la ligne de commande")
        except json.JSONDecodeError as e:
            logger.error(f"Format JSON invalide pour --namespace-url-mapping: {e}")
            sys.exit(1)
    
    # Vérifier les paramètres requis
    if not args.source_url:
        logger.error("--source-url ou SOURCE_CKAN_URL requis")
        sys.exit(1)
    
    if not args.source_token:
        logger.error("--source-token ou SOURCE_CKAN_TOKEN requis")
        sys.exit(1)
    
    if not args.target_url:
        logger.error("--target-url ou TARGET_CKAN_URL requis")
        sys.exit(1)
    
    if not args.target_token:
        logger.error("--target-token ou TARGET_CKAN_TOKEN requis")
        sys.exit(1)
    
    # Parser les IDs de retry pour datasets
    retry_dataset_ids = []
    if args.retry_dataset_ids:
        retry_dataset_ids.extend(args.retry_dataset_ids)
    if args.retry_dataset_ids_json:
        try:
            json_ids = json.loads(args.retry_dataset_ids_json)
            if isinstance(json_ids, list):
                retry_dataset_ids.extend(json_ids)
            else:
                logger.warning(" --retry-dataset-ids-json doit être une liste JSON")
        except json.JSONDecodeError as e:
            logger.error(f"Format JSON invalide pour --retry-dataset-ids-json: {e}")
            sys.exit(1)
    
    # Parser les IDs de retry pour ressources
    retry_resource_ids = []
    if args.retry_resource_ids:
        retry_resource_ids.extend(args.retry_resource_ids)
    if args.retry_resource_ids_json:
        try:
            json_ids = json.loads(args.retry_resource_ids_json)
            if isinstance(json_ids, list):
                retry_resource_ids.extend(json_ids)
            else:
                logger.warning(" --retry-resource-ids-json doit être une liste JSON")
        except json.JSONDecodeError as e:
            logger.error(f"Format JSON invalide pour --retry-resource-ids-json: {e}")
            sys.exit(1)
    
    # Créer l'importeur et lancer l'import
    importer = CKANImporter(
        source_url=args.source_url,
        source_token=args.source_token,
        target_url=args.target_url,
        target_token=args.target_token,
        dry_run=args.dry_run,
        skip_users=args.skip_users,
        skip_organizations=args.skip_organizations,
        skip_datasets=args.skip_datasets,
        skip_showcases=args.skip_showcases,
        source_public_url=args.source_public_url if args.source_public_url else None,
        namespace_url_mapping=namespace_mapping,
        import_activities=args.import_activities,
        test_mode=args.test,
        request_delay=args.request_delay,
        retry_dataset_ids=retry_dataset_ids if retry_dataset_ids else None,
        retry_resource_ids=retry_resource_ids if retry_resource_ids else None
    )
    
    try:
        importer.import_all()
        logger.info("\nImport terminé avec succès!")
    except KeyboardInterrupt:
        logger.warning("\n Import interrompu par l'utilisateur")
        importer.print_stats()
        sys.exit(1)
    except Exception as e:
        logger.error(f"\nErreur fatale: {e}", exc_info=True)
        importer.print_stats()
        sys.exit(1)


if __name__ == '__main__':
    main()

