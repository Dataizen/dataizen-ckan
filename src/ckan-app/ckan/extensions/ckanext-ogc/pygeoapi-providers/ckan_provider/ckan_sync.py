#!/usr/bin/env python3
"""
CKAN to pygeoapi Synchronization Script

This script automatically discovers geospatial datasets in CKAN and creates
corresponding OGC collections in pygeoapi.
"""

import json
import logging
import os
import shutil
import time
import requests
import yaml
from typing import Dict, List, Any, Optional
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CKANSync:
    """Synchronize CKAN datasets with pygeoapi"""
    
    def __init__(self, ckan_url: str, ckan_api_key: str, pygeoapi_config_path: str, 
                 pygeoapi_url: str = None, pygeoapi_restart_cmd: str = None):
        """
        Initialize CKAN sync
        
        Args:
            ckan_url: CKAN instance URL
            ckan_api_key: CKAN API key
            pygeoapi_config_path: Path to pygeoapi config file
            pygeoapi_url: pygeoapi instance URL (defaults to http://localhost:5001 or PYGEOAPI_URL env var)
            pygeoapi_restart_cmd: Command to restart pygeoapi (optional, defaults to PYGEOAPI_RESTART_CMD env var)
        """
        import os
        self.ckan_url = ckan_url.rstrip('/')
        self.ckan_api_key = ckan_api_key
        self.pygeoapi_config_path = Path(pygeoapi_config_path)
        self.pygeoapi_url = pygeoapi_url or os.getenv('PYGEOAPI_URL', 'http://localhost:5001')
        # Par défaut, utiliser le script qui détecte automatiquement l'environnement (Docker/Kubernetes)
        self.pygeoapi_restart_cmd = pygeoapi_restart_cmd or os.getenv('PYGEOAPI_RESTART_CMD', '/srv/app/restart-pygeoapi.sh')
        
        # Headers for CKAN API
        self.headers = {'Authorization': ckan_api_key} if ckan_api_key else {}
        
        # Cache pour optimiser les appels API répétés
        self._datastore_cache: dict = {}  # Cache pour datastore_search
        self._geospatial_cache: dict = {}  # Cache pour _has_geometry_columns
        
        logger.info(f"CKAN Sync initialized for: {ckan_url}")
        logger.info(f"pygeoapi config: {pygeoapi_config_path}")
        logger.info(f"pygeoapi URL: {self.pygeoapi_url}")
        if self.pygeoapi_restart_cmd:
            logger.info(f"pygeoapi restart command: {self.pygeoapi_restart_cmd}")

    def discover_geospatial_datasets(self, limit: int = None, progress_callback=None) -> List[Dict[str, Any]]:
        """
        Discover all geospatial datasets in CKAN
        Optimisé pour utiliser package_search au lieu de package_list + package_show
        
        Args:
            limit: Maximum number of datasets to process (for testing)
            progress_callback: optional callable(current, total, message) pour l'UI admin
            
        Returns:
            List of geospatial dataset dictionaries
        """
        try:
            # Optimisation: utiliser package_search avec pagination pour récupérer tous les packages
            url = f"{self.ckan_url}/api/action/package_search"
            page_size = 1000
            all_packages = []
            start = 0
            
            logger.info(f"Recherche des packages via package_search (pagination {page_size} par page)...")
            if progress_callback:
                progress_callback(0, 1, "Recherche des packages...")
            
            while True:
                params = {
                    "q": "*:*",
                    "rows": min(page_size, limit - len(all_packages)) if limit else page_size,
                    "start": start,
                    "include_private": True,
                }
                response = requests.get(url, params=params, headers=self.headers, timeout=120)
                response.raise_for_status()
                result = response.json()
                if not result.get('success'):
                    logger.error(f"Échec package_search: {result.get('error')}")
                    return []
                packages = result.get('result', {}).get('results', [])
                all_packages.extend(packages)
                if progress_callback and (start == 0 or len(packages) == page_size):
                    progress_callback(len(all_packages), max(len(all_packages), 1), f"{len(all_packages)} packages récupérés...")
                if len(packages) < page_size or (limit and len(all_packages) >= limit):
                    break
                if len(packages) == page_size:
                    logger.info(f"Page suivante: {len(all_packages)} packages récupérés...")
                start += len(packages)
                if limit and len(all_packages) >= limit:
                    all_packages = all_packages[:limit]
                    break
            
            packages = all_packages
            if packages:
                logger.info(f"Récupération de {len(packages)} packages via package_search (pagination)")
                
                # Filtrer les datasets géospatiaux
                geospatial_datasets = []
                total = len(packages)
                for i, dataset in enumerate(packages):
                    if (i + 1) % 100 == 0 or i == 0 or i == total - 1:
                        if progress_callback:
                            progress_callback(i + 1, total, f"{i + 1}/{total} packages analysés")
                    if self._is_geospatial_dataset(dataset):
                        geospatial_datasets.append(dataset)
                        logger.info(f"Dataset géospatial trouvé: {dataset.get('name', 'unnamed')}")
                
                if progress_callback:
                    progress_callback(total, total, f"{len(geospatial_datasets)} datasets géospatiaux trouvés")
                logger.info(f"Trouvé {len(geospatial_datasets)} datasets géospatiaux sur {len(packages)} packages")
                return geospatial_datasets
            else:
                logger.warning("Aucun package trouvé")
                return []
            
        except Exception as e:
            logger.warning(f"package_search échoué, utilisation du fallback: {e}")
            # Fallback: méthode originale (package_list + package_show pour chaque)
            try:
                url = f"{self.ckan_url}/api/action/package_list"
                response = requests.get(url, headers=self.headers, timeout=120)
                response.raise_for_status()
                
                result = response.json()
                if not result.get('success'):
                    logger.error(f"Failed to get package list: {result.get('error')}")
                    return []
                
                package_names = result.get('result', [])
                if limit:
                    package_names = package_names[:limit]
                    logger.info(f"Processing first {limit} packages (for testing)")
                else:
                    logger.info(f"Found {len(package_names)} total packages")
                
                # Get details for each package
                geospatial_datasets = []
                total = len(package_names)
                for i, package_name in enumerate(package_names):
                    if (i + 1) % 100 == 0 or i == 0 or i == total - 1:
                        if progress_callback:
                            progress_callback(i + 1, total, f"{i + 1}/{total} packages analysés")
                    logger.info(f"Checking package {i+1}/{len(package_names)}: {package_name}")
                    dataset = self._get_package_details(package_name)
                    if dataset and self._is_geospatial_dataset(dataset):
                        geospatial_datasets.append(dataset)
                        logger.info(f"Added geospatial dataset: {package_name}")
                
                logger.info(f"Found {len(geospatial_datasets)} geospatial datasets")
                return geospatial_datasets
                
            except Exception as e:
                logger.error(f"Error discovering datasets: {e}")
                return []

    def _get_package_details(self, package_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a package
        
        Args:
            package_name: Name of the package
            
        Returns:
            Package details or None
        """
        try:
            url = f"{self.ckan_url}/api/action/package_show"
            params = {'id': package_name}
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                return result.get('result')
            else:
                logger.warning(f"Failed to get package {package_name}: {result.get('error')}")
                return None
                
        except Exception as e:
            logger.warning(f"Error getting package {package_name}: {e}")
            return None

    def _is_geospatial_dataset(self, dataset: Dict[str, Any]) -> bool:
        """
        Check if a dataset contains geospatial data
        
        Args:
            dataset: Dataset dictionary
            
        Returns:
            True if geospatial, False otherwise
        """
        logger.info(f"Checking if dataset '{dataset.get('name') or 'unnamed'}' is geospatial...")
        resources = dataset.get('resources') or []
        logger.info(f"Dataset has {len(resources)} resources")
        
        for i, resource in enumerate(resources):
            logger.info(f"Resource {i+1}: {resource.get('name', 'unnamed')} (format: {resource.get('format', 'unknown')})")
            if self._is_geospatial_resource(resource):
                logger.info(f"Dataset '{dataset.get('name', 'unnamed')}' is geospatial (resource {i+1})")
                return True
        
        logger.info(f"Dataset '{dataset.get('name', 'unnamed')}' is not geospatial")
        return False

    def _is_geospatial_resource(self, resource: Dict[str, Any]) -> bool:
        """
        Check if a resource contains geospatial data
        Accepts st_asgeojson and geo_point_2d as valid geospatial indicators
        
        Args:
            resource: Resource dictionary
            
        Returns:
            True if geospatial, False otherwise
        """
        resource_name = resource.get('name') or 'unnamed'
        format_raw = resource.get('format') or ''
        format_ = format_raw.strip().upper() if format_raw else ''  # Strip whitespace and uppercase, handle None/empty
        
        datastore_active = resource.get('datastore_active', False)
        logger.info(f"  Checking resource '{resource_name}' (format: '{format_}' (raw: '{format_raw}'), datastore_active: {datastore_active})")
        
        # Check format (normalize format string - remove spaces, handle variations)
        format_normalized = format_.replace(' ', '').replace('-', '').replace('_', '') if format_ else ''
        geo_formats = ['GEOJSON', 'SHAPEFILE', 'SHP', 'KML', 'KMZ', 'GPX', 'CSV', 'ZIP']
        geo_formats_normalized = [f.replace(' ', '').replace('-', '').replace('_', '') for f in geo_formats]
        
        # Also check URL extension for ZIP files (like in provider.py)
        url = (resource.get('url') or '').lower()
        if url.endswith('.zip') and format_ not in geo_formats:
            format_ = 'ZIP'
            format_normalized = 'ZIP'
            logger.info(f"  Resource '{resource_name}' detected as ZIP from URL extension")
        
        if format_ and (format_normalized in geo_formats_normalized or format_ in geo_formats):
            logger.info(f"  Resource '{resource_name}' has geo format: {format_}")
            return True
        
        # Check if datastore is active (CSV with geometry)
        # IMPORTANT: Align with WMS provider logic - consider CSV with datastore_active as geospatial
        # The actual geometry extraction will happen during feature extraction
        # Also check normalized format to catch variations like "CSV", "csv", "Csv", etc.
        is_csv = format_ == 'CSV' or format_normalized == 'CSV'
        if is_csv and datastore_active:
            logger.info(f"  Resource '{resource_name}' is CSV with active datastore (considered geospatial)")
            # Log geometry columns if found, but don't require them for detection
            # This allows datasets with coordinate columns to be detected even if column names vary
            has_geom = self._has_geometry_columns(resource)
            if has_geom:
                logger.info(f"  Resource '{resource_name}' has geometry columns detected")
            else:
                logger.info(f"   Resource '{resource_name}' - no standard geometry columns detected, but will attempt extraction during sync")
            return True
        
        # Check description/keywords for spatial indicators
        description = (resource.get('description') or '').lower()
        tags = resource.get('tags', [])
        
        spatial_indicators = ['geo', 'spatial', 'geometry', 'coordinate', 'latitude', 'longitude', 'point', 'polygon', 'line']
        
        if any(indicator in description for indicator in spatial_indicators):
            logger.info(f"  Resource '{resource_name}' has spatial indicators in description")
            return True
        
        if any(any(indicator in (tag.get('name') or '').lower() for indicator in spatial_indicators) 
               for tag in (tags or [])):
            logger.info(f"  Resource '{resource_name}' has spatial indicators in tags")
            return True
        
        # Check if resource name contains spatial indicators
        resource_name_lower = (resource.get('name') or '').lower()
        if any(indicator in resource_name_lower for indicator in spatial_indicators):
            logger.info(f"  Resource '{resource_name}' has spatial indicators in name")
            return True
        
        logger.info(f"  Resource '{resource_name}' is not geospatial")
        return False

    def _has_geometry_columns(self, resource: Dict[str, Any]) -> bool:
        """
        Check if a resource has geometry columns in its datastore
        Utilise un cache pour éviter les appels API répétés
        
        Args:
            resource: Resource dictionary
            
        Returns:
            True if has geometry columns, False otherwise
        """
        rid = resource.get('id')
        if not rid:
            return False
        
        # Vérifier le cache d'abord
        if rid in self._geospatial_cache:
            return self._geospatial_cache[rid]
        
        try:
            # Vérifier le cache datastore_search
            if rid in self._datastore_cache:
                fields = self._datastore_cache[rid].get('fields', [])
            else:
                url = f"{self.ckan_url}/api/action/datastore_search"
                params = {
                    'resource_id': rid,
                    'limit': 0  # Just get schema, no data
                }
                
                response = requests.get(url, params=params, headers=self.headers, timeout=30)
                if response.status_code == 404:
                    self._geospatial_cache[rid] = False
                    return False  # Ressource absente du datastore, normal
                response.raise_for_status()
                
                result = response.json()
                if result.get('success'):
                    result_data = result.get('result', {})
                    fields = result_data.get('fields', [])
                    # Mettre en cache le résultat complet
                    self._datastore_cache[rid] = result_data
                else:
                    self._geospatial_cache[rid] = False
                    return False
            
            # Check for geometry columns (expanded list to match WMS provider)
            # Include st_asgeojson and geo_point_2d as valid indicators
            geometry_indicators = [
                'geometry', 'geom', 'the_geom', 'geojson', 'geo_shape',
                'st_asgeojson', 'geo_point_2d', 'geo_point',  # Added for CSV with GeoJSON text columns
                'latitude', 'longitude', 'lon', 'lng', 'lat', 'x', 'y',
                'coord', 'coordinate', 'coordinates', 'location', 'position',
                'point', 'polygon', 'linestring', 'multipoint', 'multipolygon'
            ]
            
            has_geo = False
            for field in (fields or []):
                field_name = (field.get('id') or '').lower()
                if any(indicator in field_name for indicator in geometry_indicators):
                    logger.info(f"    Found geometry column: {field.get('id')}")
                    has_geo = True
                    break
            
            if not has_geo:
                logger.info(f"    No geometry columns found in datastore")
            
            # Mettre en cache le résultat
            self._geospatial_cache[rid] = has_geo
            return has_geo
            
        except Exception as e:
            logger.warning(f"Error checking geometry columns: {e}")
            self._geospatial_cache[rid] = False
            return False

    def update_pygeoapi_config(self, datasets: List[Dict[str, Any]], backup_before_write: bool = True) -> bool:
        """
        Update pygeoapi configuration with CKAN datasets
        
        Args:
            datasets: List of geospatial datasets
            backup_before_write: If True, create one timestamped backup before writing (recommandé pour synchro complète; inutile à chaque ajout de ressource).
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Find base config file (in code source or same directory as target)
            base_config_path = None
            config_dir = self.pygeoapi_config_path.parent
            
            # Try to find local.config.base.yml in the same directory first (volume mount)
            # Then in the Docker image location
            base_config_candidates = [
                config_dir / 'local.config.base.yml',  # In pygeoapi_storage (volume)
                Path('/srv/app/pygeoapi/local.config.base.yml'),  # In Docker image
                Path('/srv/app/ckanext-ogc/ckanext/ogc/pygeoapi/local.config.base.yml'),
                Path('/srv/app/ckanext-ogc/pygeoapi/local.config.base.yml'),
            ]
            
            for candidate in base_config_candidates:
                if candidate.exists() and candidate.is_file():
                    base_config_path = candidate
                    logger.info(f"Using base config: {base_config_path}")
                    break
            
            if base_config_path and base_config_path.exists() and base_config_path.is_file():
                # Load base config
                with open(base_config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
            else:
                # Fallback: try to load existing config or create minimal structure
                if self.pygeoapi_config_path.exists():
                    logger.warning(f"Base config not found, using existing config as base")
                    with open(self.pygeoapi_config_path, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f) or {}
                else:
                    logger.warning(f"Base config not found and no existing config, creating minimal structure")
                    config = {
                        'server': {
                            'bind': {'host': '0.0.0.0', 'port': 5001},
                            'cors': True,
                            'limits': {'default': 10000000, 'max': 10000}
                        },
                        'logging': {'level': 'INFO'},
                        'metadata': {},
                        'resources': {}
                    }
            
            # Ensure resources section exists
            if 'resources' not in config:
                config['resources'] = {}
            
            # Update resources section - ADD/UPDATE collections from datasets
            for dataset in datasets:
                collection_name = dataset['name']
                collection_config = self._create_collection_config(dataset)
                config['resources'][collection_name] = collection_config
            
            # Ensure essential sections exist
            if 'server' not in config:
                config['server'] = {
                    'bind': {'host': '0.0.0.0', 'port': 5001},
                    'cors': True,
                    'limits': {'default': 10000000, 'max': 10000}
                }
            if 'logging' not in config:
                config['logging'] = {'level': 'INFO'}
            if 'metadata' not in config:
                config['metadata'] = {}
            
            # Update server.url with public URL from environment variable
            # Use same logic as helpers.get_pygeoapi_url() to ensure consistency
            pygeoapi_public_url = os.getenv('PYGEOAPI_PUBLIC_URL') or os.getenv('PYGEOAPI_URL') or 'http://localhost:5001'
            
            # If URL contains localhost, try to replace with public domain
            if 'localhost' in pygeoapi_public_url:
                # Try to get public URL from PYGEOAPI_PUBLIC_URL
                public_url = os.getenv('PYGEOAPI_PUBLIC_URL')
                if public_url:
                    pygeoapi_public_url = public_url
                else:
                    # Build URL from CKAN_SITE_URL if available
                    ckan_url = os.getenv('CKAN_SITE_URL') or os.getenv('CKAN_URL')
                    if ckan_url and 'localhost' not in ckan_url:
                        from urllib.parse import urlparse
                        parsed = urlparse(ckan_url)
                        domain = parsed.netloc
                        # Build pygeoapi URL with same domain
                        pygeoapi_public_url = f"{parsed.scheme}://ogc.{domain.split('.', 1)[-1] if '.' in domain else domain}"
            
            # Remove trailing slash if present
            if pygeoapi_public_url.endswith('/'):
                pygeoapi_public_url = pygeoapi_public_url[:-1]
            
            # Apply same transformation as in helpers.py (ogc. -> ogc.ckan2.)
            if 'ogc.' in pygeoapi_public_url and 'ogc.ckan2.' not in pygeoapi_public_url:
                pygeoapi_public_url = pygeoapi_public_url.replace('ogc.', 'ogc.ckan2.', 1)
            
            config['server']['url'] = pygeoapi_public_url
            logger.debug(f"Set pygeoapi server.url to: {pygeoapi_public_url}")
            
            # Un seul backup avant écriture en synchro complète (pas à chaque ajout de ressource)
            config_dir.mkdir(parents=True, exist_ok=True)
            if backup_before_write and self.pygeoapi_config_path.exists():
                ts = time.strftime("%Y%m%d-%H%M%S")
                backup_path = self.pygeoapi_config_path.with_suffix(f".{ts}.bak")
                shutil.copy2(self.pygeoapi_config_path, backup_path)
                logger.info(f"Backup config (avant synchro): {backup_path}")
            
            # Save updated config
            with open(self.pygeoapi_config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, default_flow_style=False, indent=2, allow_unicode=True)
            
            # Update openapi.yml servers URLs to use public URL
            self._update_openapi_servers(pygeoapi_public_url)
            
            # Regenerate openapi.yml from updated config to ensure it reflects the new server.url
            self._regenerate_openapi()
            
            logger.info(f"Updated pygeoapi config with {len(datasets)} collections")
            return True
            
        except Exception as e:
            logger.error(f"Error updating pygeoapi config: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _update_openapi_servers(self, public_url: str):
        """
        Update the servers URLs in openapi.yml to use the public URL instead of localhost
        
        Args:
            public_url: Public URL for pygeoapi (e.g., https://ogc.ckan2.qualif-data.example.org)
        """
        try:
            # Find openapi.yml in the same directory as the config file
            config_dir = self.pygeoapi_config_path.parent
            openapi_path = config_dir / 'openapi.yml'
            
            # Also check common locations
            openapi_candidates = [
                openapi_path,
                Path('/srv/app/pygeoapi/openapi.yml'),
                Path('/srv/app/ckanext-ogc/pygeoapi/openapi.yml'),
            ]
            
            openapi_file = None
            for candidate in openapi_candidates:
                if candidate.exists():
                    openapi_file = candidate
                    break
            
            if not openapi_file:
                logger.debug("openapi.yml not found, skipping update")
                return
            
            # Read the openapi.yml file
            with open(openapi_file, 'r', encoding='utf-8') as f:
                openapi_data = yaml.safe_load(f) or {}
            
            # Update servers section
            if 'servers' in openapi_data and isinstance(openapi_data['servers'], list):
                # Replace localhost URLs with public URL
                updated_servers = []
                for server in openapi_data['servers']:
                    if isinstance(server, dict) and 'url' in server:
                        server_url = server['url']
                        # Replace localhost URLs
                        if 'localhost' in server_url:
                            server['url'] = public_url
                            server['description'] = 'Production server'
                        # Also update if it's a public OGC server URL
                        elif 'ogc.' in server_url:
                            server['url'] = public_url
                            server['description'] = 'Production server'
                    updated_servers.append(server)
                
                # Ensure public URL is in the list (as first server)
                public_url_present = any(
                    isinstance(s, dict) and s.get('url') == public_url 
                    for s in updated_servers
                )
                if not public_url_present:
                    updated_servers.insert(0, {
                        'url': public_url,
                        'description': 'Production server'
                    })
                
                openapi_data['servers'] = updated_servers
                
                # Write updated openapi.yml
                with open(openapi_file, 'w', encoding='utf-8') as f:
                    yaml.dump(openapi_data, f, default_flow_style=False, indent=2, allow_unicode=True)
                
                logger.debug(f"Updated openapi.yml servers to use: {public_url}")
            else:
                # If servers section doesn't exist, add it
                if 'servers' not in openapi_data:
                    openapi_data['servers'] = []
                openapi_data['servers'].insert(0, {
                    'url': public_url,
                    'description': 'Production server'
                })
                
                with open(openapi_file, 'w', encoding='utf-8') as f:
                    yaml.dump(openapi_data, f, default_flow_style=False, indent=2, allow_unicode=True)
                
                logger.debug(f"Added servers section to openapi.yml with: {public_url}")
                
        except Exception as e:
            logger.warning(f"Error updating openapi.yml: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    def _regenerate_openapi(self):
        """
        Regenerate openapi.yml from local.config.yml to ensure it reflects the updated server.url
        """
        try:
            import subprocess
            config_dir = self.pygeoapi_config_path.parent
            openapi_path = config_dir / 'openapi.yml'
            
            logger.info(f"Regenerating openapi.yml from {self.pygeoapi_config_path}...")
            
            # Run pygeoapi openapi generate command (timeout 90s pour gros catalogues)
            result = subprocess.run(
                ['pygeoapi', 'openapi', 'generate', str(self.pygeoapi_config_path)],
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(config_dir)
            )
            
            if result.returncode == 0:
                logger.info(f"Successfully regenerated openapi.yml")
                # Update servers URLs again after regeneration
                pygeoapi_public_url = os.getenv('PYGEOAPI_PUBLIC_URL') or os.getenv('PYGEOAPI_URL') or 'http://localhost:5001'
                # Apply same transformation as in update_pygeoapi_config
                if 'localhost' in pygeoapi_public_url:
                    public_url = os.getenv('PYGEOAPI_PUBLIC_URL')
                    if public_url:
                        pygeoapi_public_url = public_url
                    else:
                        ckan_url = os.getenv('CKAN_SITE_URL') or os.getenv('CKAN_URL')
                        if ckan_url and 'localhost' not in ckan_url:
                            from urllib.parse import urlparse
                            parsed = urlparse(ckan_url)
                            domain = parsed.netloc
                            pygeoapi_public_url = f"{parsed.scheme}://ogc.{domain.split('.', 1)[-1] if '.' in domain else domain}"
                if 'ogc.' in pygeoapi_public_url and 'ogc.ckan2.' not in pygeoapi_public_url:
                    pygeoapi_public_url = pygeoapi_public_url.replace('ogc.', 'ogc.ckan2.', 1)
                if pygeoapi_public_url.endswith('/'):
                    pygeoapi_public_url = pygeoapi_public_url[:-1]
                self._update_openapi_servers(pygeoapi_public_url)
            else:
                logger.warning(f"Failed to regenerate openapi.yml: {result.stderr}")
        except subprocess.TimeoutExpired:
            logger.warning("pygeoapi openapi generate command timed out (90s) - openapi.yml peut être obsolète, la config principale est à jour")
        except FileNotFoundError:
            logger.debug("pygeoapi command not found, skipping openapi.yml regeneration")
        except Exception as e:
            logger.warning(f"Error regenerating openapi.yml: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    def _create_collection_config(self, dataset: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create pygeoapi collection configuration for a CKAN dataset
        
        Args:
            dataset: CKAN dataset dictionary
            
        Returns:
            pygeoapi collection configuration
        """
        # Get spatial extent from resources
        bbox = self._calculate_dataset_bbox(dataset)
        
        # Preserve UTF-8 encoding for keywords, title, and description
        keywords = []
        for tag in dataset.get('tags', []):
            # Keep UTF-8 characters (accents, etc.)
            tag_name = tag['name']
            if tag_name and tag_name.strip():
                keywords.append(tag_name)
        
        # Preserve UTF-8 encoding for title and description
        title = dataset.get('title', dataset['name'])
        if not title:
            title = dataset['name']
        
        description = dataset.get('notes', f"Dataset: {dataset['name']}")
        if not description:
            description = f"Dataset: {dataset['name']}"
        
        # Detect CRS from dataset resources
        detected_crs = self._detect_dataset_crs(dataset)
        
        # Check if French data for default CRS selection
        package_description = (dataset.get('notes', '') or '').lower()
        package_title = (dataset.get('title', '') or '').lower()
        package_text = f"{package_description} {package_title}"
        is_french_data = any(term in package_text for term in [
            'france', 'français', 'francais', 'bourgogne', 'franche-comté', 'franche-comte',
            'bfc', 'regionale', 'régionale', 'dijon', 'besançon', 'besancon'
        ])
        
        # Ensure we always have at least one CRS
        # For French data (Dataizen), default to Lambert-93 (EPSG:2154)
        # For other data, default to CRS84 (WGS84)
        if not detected_crs or len(detected_crs) == 0:
            if is_french_data:
                default_crs = 'http://www.opengis.net/def/crs/EPSG/0/2154'  # Lambert-93 for French data
                logger.info(f"No CRS detected, using Lambert-93 (EPSG:2154) as default for French data")
            else:
                default_crs = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'  # WGS84 for other data
            detected_crs = [default_crs]
        
        # Get the primary CRS (first in list, which is prioritized)
        # For French data, ensure Lambert-93 is primary if present
        if is_french_data and 'http://www.opengis.net/def/crs/EPSG/0/2154' in detected_crs:
            detected_crs.remove('http://www.opengis.net/def/crs/EPSG/0/2154')
            detected_crs.insert(0, 'http://www.opengis.net/def/crs/EPSG/0/2154')
        
        # Ensure detected_crs is not empty and is a list
        if not detected_crs or not isinstance(detected_crs, list) or len(detected_crs) == 0:
            if is_french_data:
                detected_crs = ['http://www.opengis.net/def/crs/EPSG/0/2154']
            else:
                detected_crs = ['http://www.opengis.net/def/crs/OGC/1.3/CRS84']
        
        primary_crs = detected_crs[0] if detected_crs and len(detected_crs) > 0 else (
            'http://www.opengis.net/def/crs/EPSG/0/2154' if is_french_data 
            else 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        )
        
        # CRITICAL: Ensure CRS84 is ALWAYS present - required for pygeoapi /map endpoint
        # Without CRS84, the /map endpoint will be rejected by pygeoapi
        crs84_uri = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        if crs84_uri not in detected_crs:
            detected_crs.append(crs84_uri)
            logger.info(f"Added CRS84 to detected CRS list (required for /map endpoint)")
        
        # Ensure primary_crs is never None
        if not primary_crs:
            primary_crs = crs84_uri
        
        # Add EPSG:4326 as an alias for CRS84 (WGS84) to support WMS requests
        # CRS84 and EPSG:4326 are equivalent (WGS84), but pygeoapi needs both formats
        if 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' in detected_crs and 'http://www.opengis.net/def/crs/EPSG/0/4326' not in detected_crs:
            detected_crs.append('http://www.opengis.net/def/crs/EPSG/0/4326')
        
        # Get organization name for OGC service links (use name, not UUID)
        org_id = dataset.get('organization', {}).get('name')
        if not org_id:
            # Try owner_org or organization.id, but convert to name
            org_uuid = dataset.get('owner_org') or dataset.get('organization', {}).get('id')
            if org_uuid:
                # Fetch organization to get its name
                try:
                    org_response = requests.get(
                        f"{self.ckan_url}/api/action/organization_show",
                        params={'id': org_uuid},
                        headers={'Authorization': self.ckan_api_key} if self.ckan_api_key else {},
                        timeout=5
                    )
                    if org_response.status_code == 200:
                        org_data = org_response.json().get('result', {})
                        org_id = org_data.get('name')  # Use name, not UUID
                except Exception as e:
                    logger.warning(f"Could not get organization name for dataset {dataset.get('name')}: {e}")
                    # Fallback: use UUID if name not available (will cause 404 but better than nothing)
                    org_id = org_uuid
        
        # Build OGC service links using /maps/{org_name} endpoint
        ogc_links = []
        if org_id:
            ogc_links = [
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WFS Service',
                    'href': f'/maps/{org_id}?SERVICE=WFS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMS Service (MapServer)',
                    'href': f'/wms?SERVICE=WMS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMTS Service',
                    'href': f'/maps/{org_id}?SERVICE=WMTS&REQUEST=GetCapabilities'
                }
            ]
        else:
            # Fallback: use collection-based links (may not work, but better than nothing)
            logger.warning(f"No organization ID found for dataset {dataset.get('name')}, using fallback links")
            ogc_links = [
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WFS Service',
                    'href': f'/maps/{dataset["name"]}?SERVICE=WFS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMS Service',
                    'href': f'/wms/{dataset["name"]}?SERVICE=WMS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMTS Service',
                    'href': f'/maps/{dataset["name"]}?SERVICE=WMTS&REQUEST=GetCapabilities'
                }
            ]
        
        # Get the first CRS, or use default if somehow empty
        primary_crs = detected_crs[0] if detected_crs and len(detected_crs) > 0 else 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        
        # Add EPSG:4326 as an alias for CRS84 (WGS84) to support WMS requests
        # CRS84 and EPSG:4326 are equivalent (WGS84), but pygeoapi needs both formats
        if 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' in detected_crs and 'http://www.opengis.net/def/crs/EPSG/0/4326' not in detected_crs:
            detected_crs.append('http://www.opengis.net/def/crs/EPSG/0/4326')
        
        # Get organization name and title for OGC service links and metadata (use name, not UUID)
        org_id = dataset.get('organization', {}).get('name')
        org_title = dataset.get('organization', {}).get('title')
        org_uuid = dataset.get('owner_org') or dataset.get('organization', {}).get('id')
        
        if not org_id:
            # Try owner_org or organization.id, but convert to name
            if org_uuid:
                # Fetch organization to get its name and title
                try:
                    org_response = requests.get(
                        f"{self.ckan_url}/api/action/organization_show",
                        params={'id': org_uuid},
                        headers={'Authorization': self.ckan_api_key} if self.ckan_api_key else {},
                        timeout=5
                    )
                    if org_response.status_code == 200:
                        org_data = org_response.json().get('result', {})
                        org_id = org_data.get('name')  # Use name, not UUID
                        org_title = org_data.get('title')  # Get title too
                except Exception as e:
                    logger.warning(f"Could not get organization name for dataset {dataset.get('name')}: {e}")
                    # Fallback: use UUID if name not available (will cause 404 but better than nothing)
                    org_id = org_uuid
        
        # Build OGC service links using /maps/{org_name} endpoint
        ogc_links = []
        if org_id:
            ogc_links = [
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WFS Service',
                    'href': f'/maps/{org_id}?SERVICE=WFS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMS Service (MapServer)',
                    'href': f'/wms?SERVICE=WMS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMTS Service',
                    'href': f'/maps/{org_id}?SERVICE=WMTS&REQUEST=GetCapabilities'
                }
            ]
        else:
            # Fallback: use collection-based links (may not work, but better than nothing)
            logger.warning(f"No organization ID found for dataset {dataset.get('name')}, using fallback links")
            ogc_links = [
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WFS Service',
                    'href': f'/maps/{dataset["name"]}?SERVICE=WFS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMS Service (MapServer)',
                    'href': f'/wms?SERVICE=WMS&REQUEST=GetCapabilities'
                },
                {
                    'type': 'application/xml',
                    'rel': 'service',
                    'title': 'WMTS Service',
                    'href': f'/maps/{dataset["name"]}?SERVICE=WMTS&REQUEST=GetCapabilities'
                }
            ]
        
        # Ensure CRS lists are normalized for config and provider definitions
        # Ensure detected_crs is a list and not empty
        if not detected_crs or not isinstance(detected_crs, list) or len(detected_crs) == 0:
            detected_crs = [primary_crs] if primary_crs else ['http://www.opengis.net/def/crs/OGC/1.3/CRS84']
        
        normalized_crs = detected_crs if isinstance(detected_crs, list) else [primary_crs if primary_crs else 'http://www.opengis.net/def/crs/OGC/1.3/CRS84']
        
        # Ensure primary_crs is never None
        if not primary_crs:
            primary_crs = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        
        # CRITICAL: Ensure CRS84 is ALWAYS present - required for pygeoapi /map endpoint
        # Without CRS84, the /map endpoint will be rejected by pygeoapi
        crs84_uri = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        if crs84_uri not in normalized_crs:
            normalized_crs.insert(0, crs84_uri)  # Insert at beginning for priority
            logger.info(f"Added CRS84 to collection CRS list (required for /map endpoint)")
        
        # Ensure normalized_crs is never empty
        if not normalized_crs or len(normalized_crs) == 0:
            normalized_crs = [crs84_uri]
            logger.warning(f"CRS list was empty, using CRS84 as default")

        # Ensure Web Mercator is present for common WMS clients
        if 'http://www.opengis.net/def/crs/EPSG/0/3857' not in normalized_crs:
            normalized_crs.append('http://www.opengis.net/def/crs/EPSG/0/3857')

        # Add canonical URN aliases (NO short-form codes - pygeoapi's get_crs_from_uri() cannot parse them)
        # pygeoapi requires full URI/URN formats in the config, but we normalize incoming requests in views.py
        crs_aliases = {
            'http://www.opengis.net/def/crs/OGC/1.3/CRS84': [
                'urn:ogc:def:crs:OGC::CRS84'
            ],
            'http://www.opengis.net/def/crs/EPSG/0/4326': [
                'urn:ogc:def:crs:EPSG::4326'
            ],
            'http://www.opengis.net/def/crs/EPSG/0/2154': [
                'urn:ogc:def:crs:EPSG::2154'
            ],
            'http://www.opengis.net/def/crs/EPSG/0/3857': [
                'urn:ogc:def:crs:EPSG::3857'
            ]
        }

        for base_crs, aliases in crs_aliases.items():
            if base_crs in normalized_crs:
                for alias in aliases:
                    if alias not in normalized_crs:
                        normalized_crs.append(alias)

        collection_config = {
            'type': 'collection',
            'title': title,
            'description': description,
            'keywords': keywords,
            'links': [
                {
                    'type': 'text/html',
                    'rel': 'canonical',
                    'title': dataset.get('title', dataset['name']),
                    'href': f"/dataset/{dataset['name']}"
                },
                # OGC Service Links - use /maps/{org_id} endpoint
                *ogc_links,
                # Download Links
                {
                    'type': 'application/zip',
                    'rel': 'download',
                    'title': 'Download as Shapefile',
                    'href': f'/collections/{dataset["name"]}/items?f=shp'
                },
                {
                    'type': 'application/zip',
                    'rel': 'download',
                    'title': 'Download as GeoPackage',
                    'href': f'/collections/{dataset["name"]}/items?f=gpkg'
                }
            ],
            'extents': {
                'spatial': {
                    'bbox': [bbox] if bbox else [[-5.0, 41.0, 10.0, 51.0]]  # France default
                },
                'temporal': {
                    'interval': [[None, None]]
                }
            },
            'crs': normalized_crs,
            # pygeoapi expects both camelCase and snake_case depending on the context
            'storageCrs': primary_crs,
            'storage_crs': primary_crs,
            # Add limits for HTML templates compatibility (très élevées pour permettre toutes les données)
            'limits': {
                'default': 10000000,  # Très élevé pour permettre toutes les données
                'max': 10000000  # Très élevé pour permettre toutes les données
            },
            # Add organization metadata for filtering/display in pygeoapi UI
            'organization': {
                'id': org_id if org_id else None,
                'name': org_id if org_id else None,
                'title': org_title if org_title else None,
                'uuid': org_uuid if org_uuid else None
            } if org_id else None,
            'providers': [
                # Feature Provider
                {
                    'name': 'ckan_provider.provider.CKANProvider',
                    'type': 'feature',
                    'data': f"ckan-{dataset['name']}",
                    'crs': normalized_crs,
                    'storage_crs': primary_crs,
                    'options': {
                        'ckan_url': self.ckan_url,
                        'api_key': self.ckan_api_key,
                        'dataset_id': dataset['id'],
                        'max_record_count': 10000000,  # Très élevé pour permettre toutes les données
                        'limits': {
                            'default': 10000000,  # Très élevé pour permettre toutes les données
                            'max': 10000000  # Très élevé pour permettre toutes les données
                        }
                    }
                },
                # Map Provider (for WMS/WMTS) - Using custom WMSProvider that generates PNG from CKAN features
                # WMSFacade nécessite un service WMS externe, pas l'endpoint /items de pygeoapi
                # On utilise donc notre WMSProvider personnalisé qui génère des images depuis CKAN
                {
                    'name': 'ckan_provider.wms_provider.WMSProvider',
                    'type': 'map',
                    'data': f"ckan-{dataset['name']}",
                    'format': {
                        'name': 'png',
                        'mimetype': 'image/png'
                    },
                    'options': {
                        'ckan_url': self.ckan_url,
                        'api_key': self.ckan_api_key,
                        'dataset_id': dataset['id'],
                        'layer': dataset['name'],
                        'style': 'default',
                        'width': 512,
                        'height': 512,
                        'format': 'image/png'
                    }
                }
            ]
        }
        
        return collection_config

    def _detect_dataset_crs(self, dataset: Dict[str, Any]) -> List[str]:
        """
        Detect CRS from dataset resources and metadata
        Prioritizes Lambert-93 (EPSG:2154) for French data
        
        Args:
            dataset: CKAN dataset dictionary
            
        Returns:
            List of CRS URIs (Lambert-93 first for French data, then WGS84/CRS84)
        """
        try:
            resources = dataset.get('resources', [])
            detected_crs = set()
            
            # 1. Check package extras for CRS information
            extras = dataset.get('extras', [])
            if isinstance(extras, list):
                extras_dict = {item.get('key', ''): item.get('value', '') for item in extras if isinstance(item, dict)}
            else:
                extras_dict = extras if isinstance(extras, dict) else {}
            
            # Check for CRS/SRID in package extras
            crs_extra = extras_dict.get('crs') or extras_dict.get('srid') or extras_dict.get('projection')
            if crs_extra:
                crs_str = str(crs_extra).lower()
                if '2154' in crs_str or 'lambert' in crs_str:
                    detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')
                elif '4326' in crs_str or 'wgs84' in crs_str:
                    detected_crs.add('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
            
            # 2. Check package description/notes for CRS hints
            package_description = (dataset.get('notes', '') or '').lower()
            package_title = (dataset.get('title', '') or '').lower()
            package_text = f"{package_description} {package_title}"
            
            # Detect if French data (Bourgogne-Franche-Comté, France, etc.)
            # For Dataizen, assume French data by default (can be overridden by explicit CRS detection)
            is_french_data = any(term in package_text for term in [
                'france', 'français', 'francais', 'bourgogne', 'franche-comté', 'franche-comte',
                'bfc', 'regionale', 'régionale', 'dijon', 'besançon', 'besancon'
            ])
            # If no explicit French indicators but also no explicit non-French indicators, 
            # default to French data for Dataizen (Bourgogne-Franche-Comté regional portal)
            if not is_french_data and not any(term in package_text for term in [
                'international', 'global', 'world', 'europe', 'usa', 'america'
            ]):
                # Default to French data for Dataizen portal
                is_french_data = True
                logger.debug(f"Assuming French data (Dataizen default) for dataset {dataset.get('name', 'unknown')}")
            
            # Check for CRS in package description
            if 'lambert' in package_text or 'epsg:2154' in package_text or '2154' in package_text:
                detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')
            elif 'wgs84' in package_text or 'epsg:4326' in package_text or '4326' in package_text:
                detected_crs.add('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
            
            # 3. Check resources for CRS information
            for resource in resources:
                # Check resource extras
                resource_extras = resource.get('extras', [])
                if isinstance(resource_extras, list):
                    resource_extras_dict = {item.get('key', ''): item.get('value', '') for item in resource_extras if isinstance(item, dict)}
                else:
                    resource_extras_dict = resource_extras if isinstance(resource_extras, dict) else {}
                
                # Check for CRS in resource extras
                resource_crs = resource_extras_dict.get('crs') or resource_extras_dict.get('srid') or resource_extras_dict.get('projection')
                if resource_crs:
                    crs_str = str(resource_crs).lower()
                    if '2154' in crs_str or 'lambert' in crs_str:
                        detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')
                    elif '4326' in crs_str or 'wgs84' in crs_str:
                        detected_crs.add('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
                
                # Check resource format
                format_ = resource.get('format', '').upper()
                
                # Check Shapefile ZIP - CRS will be detected from Shapefile metadata
                if format_ in ['SHAPEFILE', 'SHP', 'ZIP']:
                    # Shapefile CRS will be detected when parsing the file
                    # For now, assume Lambert-93 for French data
                    if is_french_data:
                        detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')
                
                # Check if datastore is active (CSV with geometry)
                if format_ == 'CSV' and resource.get('datastore_active'):
                    # Try to detect CRS from datastore data
                    datastore_crs = self._detect_datastore_crs(resource)
                    if datastore_crs:
                        detected_crs.add(datastore_crs)
                
                # Check resource description for CRS hints
                description = resource.get('description', '')
                if description:
                    description = description.lower()
                    if 'lambert' in description or 'epsg:2154' in description or '2154' in description:
                        detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')
                    elif 'wgs84' in description or 'epsg:4326' in description or '4326' in description:
                        detected_crs.add('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
                    elif 'utm' in description:
                        # Common UTM zones for France
                        detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/32630')  # UTM 30N
                        detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/32631')  # UTM 31N
            
            # 4. Default CRS based on data location
            if not detected_crs:
                if is_french_data:
                    # For French data, prioritize Lambert-93
                    detected_crs.add('http://www.opengis.net/def/crs/EPSG/0/2154')  # Lambert-93
                    logger.info(f"French data detected, using Lambert-93 (EPSG:2154) as default CRS")
                else:
                    # For other data, use WGS84
                    detected_crs.add('http://www.opengis.net/def/crs/OGC/1.3/CRS84')  # WGS84
            
            # 5. Build CRS list with priority
            crs_list = list(detected_crs)
            
            # For French data, prioritize Lambert-93, then add WGS84/CRS84 for compatibility
            if is_french_data and 'http://www.opengis.net/def/crs/EPSG/0/2154' in crs_list:
                # Move Lambert-93 to front for French data
                crs_list.remove('http://www.opengis.net/def/crs/EPSG/0/2154')
                crs_list.insert(0, 'http://www.opengis.net/def/crs/EPSG/0/2154')
                # Always add WGS84/CRS84 for OGC compatibility (even if not detected)
                if 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' not in crs_list:
                    crs_list.append('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
            else:
                # For non-French data, prioritize CRS84 for OGC API compliance
                if 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' in crs_list:
                    crs_list.remove('http://www.opengis.net/def/crs/OGC/1.3/CRS84')
                    crs_list.insert(0, 'http://www.opengis.net/def/crs/OGC/1.3/CRS84')
            
            # 6. Add EPSG:4326 as an alias for CRS84 (WGS84) to support WMS requests
            # CRS84 and EPSG:4326 are equivalent (WGS84), but pygeoapi needs both formats
            # IMPORTANT: Always add EPSG:4326 if CRS84 is present, regardless of data origin
            if 'http://www.opengis.net/def/crs/OGC/1.3/CRS84' in crs_list and 'http://www.opengis.net/def/crs/EPSG/0/4326' not in crs_list:
                crs_list.append('http://www.opengis.net/def/crs/EPSG/0/4326')
            
            logger.info(f"Detected CRS for dataset {dataset.get('name', 'unknown')}: {crs_list} (French data: {is_french_data})")
            
            return crs_list
            
        except Exception as e:
            logger.warning(f"Error detecting CRS for dataset {dataset.get('name', 'unknown')}: {e}")
            # Default to Lambert-93 for French data (Dataizen), CRS84 for other data
            package_text = f"{(dataset.get('notes', '') or '').lower()} {(dataset.get('title', '') or '').lower()}"
            is_french_data = any(term in package_text for term in [
                'france', 'français', 'francais', 'bourgogne', 'franche-comté', 'franche-comte',
                'bfc', 'regionale', 'régionale', 'dijon', 'besançon', 'besancon'
            ])
            # Default to French data for Dataizen portal if no explicit indicators
            if not is_french_data and not any(term in package_text for term in [
                'international', 'global', 'world', 'europe', 'usa', 'america'
            ]):
                is_french_data = True
            
            if is_french_data:
                logger.info(f"Error detecting CRS, using Lambert-93 (EPSG:2154) as default for French data")
                return ['http://www.opengis.net/def/crs/EPSG/0/2154']
            else:
                return ['http://www.opengis.net/def/crs/OGC/1.3/CRS84']

    def _detect_datastore_crs(self, resource: Dict[str, Any]) -> Optional[str]:
        """
        Detect CRS from datastore data
        
        Args:
            resource: Resource dictionary
            
        Returns:
            CRS URI or None
        """
        try:
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': resource['id'],
                'limit': 10  # Check first 10 records
            }
            
            if self.ckan_api_key:
                headers = {'Authorization': self.ckan_api_key}
            else:
                headers = {}
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 404:
                return None  # Ressource absente du datastore, normal
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                records = result.get('result', {}).get('records', [])
                
                for record in records:
                    # Check for geometry fields
                    if 'geo_shape' in record:
                        geo_shape = record['geo_shape']
                        if isinstance(geo_shape, dict) and 'coordinates' in geo_shape:
                            coords = geo_shape['coordinates']
                            if coords and len(coords) >= 2:
                                # Analyze coordinates to detect CRS
                                x, y = coords[0], coords[1]
                                if isinstance(x, (list, tuple)):
                                    x, y = x[0], x[1]
                                
                                # Detect Lambert-93 (France)
                                if 1000000 < x < 2000000 and 6000000 < y < 7000000:
                                    return 'http://www.opengis.net/def/crs/EPSG/0/2154'
                                
                                # Detect WGS84
                                if -180 <= x <= 180 and -90 <= y <= 90:
                                    return 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
            
            return None
            
        except Exception as e:
            logger.warning(f"Error detecting datastore CRS: {e}")
            return None

    def _calculate_dataset_bbox(self, dataset: Dict[str, Any]) -> Optional[List[float]]:
        """
        Calculate bounding box for a dataset based on its resources
        
        Args:
            dataset: Dataset dictionary
            
        Returns:
            Bounding box [minx, miny, maxx, maxy] or None
        """
        resources = dataset.get('resources', [])
        bboxes = []
        
        for resource in resources:
            if resource.get('datastore_active'):
                # Try to get bbox from datastore
                bbox = self._get_datastore_bbox(resource)
                if bbox:
                    bboxes.append(bbox)
        
        if not bboxes:
            return None
        
        # Calculate overall bbox
        minx = min(bbox[0] for bbox in bboxes)
        miny = min(bbox[1] for bbox in bboxes)
        maxx = max(bbox[2] for bbox in bboxes)
        maxy = max(bbox[3] for bbox in bboxes)
        
        return [minx, miny, maxx, maxy]

    def _get_datastore_bbox(self, resource: Dict[str, Any]) -> Optional[List[float]]:
        """
        Get bounding box from CKAN datastore
        
        Args:
            resource: Resource dictionary
            
        Returns:
            Bounding box [minx, miny, maxx, maxy] or None
        """
        try:
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': resource['id'],
                'limit': 1
            }
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            
            # 404 means resource doesn't exist in datastore - this is normal, don't log as warning
            if response.status_code == 404:
                return None
            
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                # Bbox réelle depuis les colonnes lat/lon (2000 premiers points).
                # (Remplace le stub historique qui renvoyait le monde entier.)
                fields = [f.get('id', '') for f in result['result'].get('fields', [])]
                lat_col = next((f for f in fields if f.lower() in ('latitude', 'lat', 'y_lat')), None)
                lon_col = next((f for f in fields if f.lower() in ('longitude', 'lon', 'lng', 'x_lon')), None)
                if not lat_col or not lon_col:
                    return None
                r2 = requests.get(url, params={'resource_id': resource['id'], 'limit': 2000,
                                               'fields': f'{lat_col},{lon_col}'},
                                  headers=self.headers, timeout=30)
                if r2.status_code != 200 or not r2.json().get('success'):
                    return None
                lats, lons = [], []
                for rec in r2.json()['result'].get('records', []):
                    try:
                        la, lo = float(rec.get(lat_col)), float(rec.get(lon_col))
                    except (TypeError, ValueError):
                        continue
                    if -90 <= la <= 90 and -180 <= lo <= 180:
                        lats.append(la)
                        lons.append(lo)
                if not lats:
                    return None
                m = 0.01
                return [min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m]
            
            return None
            
        except requests.exceptions.HTTPError as e:
            # Only log non-404 HTTP errors as warnings
            if e.response.status_code != 404:
                logger.warning(f"HTTP error getting datastore bbox for resource {resource.get('id', 'unknown')}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error getting datastore bbox for resource {resource.get('id', 'unknown')}: {e}")
            return None

    def sync_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """
        Synchronize a single dataset
        
        Args:
            dataset_id: Dataset ID to sync
            
        Returns:
            Dictionary with sync results
        """
        try:
            logger.info(f"Syncing dataset: {dataset_id}")
            
            # Get dataset details
            dataset = self._get_package_details(dataset_id)
            if not dataset:
                return {
                    'success': False,
                    'error': f'Dataset {dataset_id} not found'
                }
            
            # Check if it's geospatial
            if not self._is_geospatial_dataset(dataset):
                return {
                    'success': False,
                    'error': f'Dataset {dataset_id} is not geospatial'
                }
            
            # Update pygeoapi config with this dataset
            success = self.update_pygeoapi_config([dataset], backup_before_write=False)
            
            if success:
                logger.info(f"Successfully synced dataset: {dataset_id}")
                # Restart pygeoapi if configured
                if self.pygeoapi_restart_cmd:
                    self._restart_pygeoapi()
                return {
                    'success': True,
                    'dataset_id': dataset_id,
                    'dataset_name': dataset.get('name', dataset_id)
                }
            else:
                return {
                    'success': False,
                    'error': f'Failed to update pygeoapi config for {dataset_id}'
                }
                
        except Exception as e:
            logger.error(f"Error syncing dataset {dataset_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def sync(self, limit: int = None, progress_callback=None) -> bool:
        """
        Perform full synchronization
        
        Args:
            limit: Maximum number of datasets to process (for testing)
            progress_callback: optional callable(current, total, message) pour l'UI admin
            
        Returns:
            True if successful, False otherwise
        """
        logger.info("Starting CKAN to pygeoapi synchronization...")
        logger.info(f"CKAN URL: {self.ckan_url}")
        logger.info(f"API Key: {'***' if self.ckan_api_key else 'None'}")
        logger.info(f"Config file: {self.pygeoapi_config_path}")
        
        # Discover geospatial datasets
        logger.info("Discovering geospatial datasets...")
        if progress_callback:
            progress_callback(0, 1, "Découverte des datasets géospatiaux...")
        datasets = self.discover_geospatial_datasets(limit=limit, progress_callback=progress_callback)
        
        if not datasets:
            logger.warning(" No geospatial datasets found")
            if progress_callback:
                progress_callback(0, 1, "Aucun dataset géospatial trouvé")
            return False
        
        logger.info(f"Found {len(datasets)} geospatial datasets:")
        for dataset in datasets:
            logger.info(f"  - {dataset.get('name', 'unnamed')} (ID: {dataset.get('id', 'unknown')})")
        
        # Update pygeoapi configuration
        logger.info("Updating pygeoapi configuration...")
        if progress_callback:
            progress_callback(0, len(datasets), "Mise à jour de la configuration pygeoapi...")
        success = self.update_pygeoapi_config(datasets)
        
        if success:
            logger.info("Synchronization completed successfully")
            logger.info(f"Created {len(datasets)} OGC collections")
            if progress_callback:
                progress_callback(len(datasets), len(datasets), "Redémarrage de pygeoapi...")
            # Restart pygeoapi if configured
            if self.pygeoapi_restart_cmd:
                self._restart_pygeoapi()
        else:
            logger.error("Synchronization failed")
        
        return success
    
    def _restart_pygeoapi(self):
        """
        Restart pygeoapi: PYGEOAPI_RESTART_CMD puis fallback Kubernetes (API K8s prioritaire, puis kubectl).
        Par défaut utilise le script restart-pygeoapi.sh qui privilégie l'API Kubernetes.
        """
        import subprocess
        import time as _time

        cmd = self.pygeoapi_restart_cmd
        cmd_failed = False
        if cmd:
            try:
                logger.info(f"Restarting pygeoapi: {cmd}")
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    logger.info("pygeoapi restarted successfully")
                    return
                stderr = (result.stderr or "")[:300]
                if "not found" in stderr.lower() or "command not found" in stderr.lower():
                    logger.warning("Commande de redémarrage non disponible (%s), fallback Kubernetes", stderr.strip() or result.returncode)
                else:
                    logger.warning("Échec redémarrage pygeoapi (code %s): %s", result.returncode, stderr)
                cmd_failed = True
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                logger.warning("Erreur redémarrage pygeoapi (non bloquant): %s", e)
                cmd_failed = True

        # Fallback Kubernetes : API K8s (prioritaire) puis kubectl
        namespace = os.getenv("KUBERNETES_NAMESPACE", "ckan-bpm")
        deployment_name = os.getenv("PYGEOAPI_DEPLOYMENT_NAME", "pygeoapi")
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        api_host = os.getenv("KUBERNETES_SERVICE_HOST")
        api_port = os.getenv("KUBERNETES_SERVICE_PORT")

        if api_host and api_port and os.path.isfile(token_path) and os.path.isfile(ca_path):
            try:
                logger.info("Tentative redémarrage pygeoapi via API Kubernetes (deployment=%s, namespace=%s)...", deployment_name, namespace)
                with open(token_path, encoding="utf-8") as f:
                    token = f.read().strip()
                url = f"https://{api_host}:{api_port}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/strategic-merge-patch+json"}
                data = {"spec": {"template": {"metadata": {"annotations": {"ckan/restarted": f"restarted-at-{int(_time.time())}"}}}}}
                resp = requests.patch(url, json=data, headers=headers, verify=ca_path, timeout=15)
                if resp.status_code == 200:
                    logger.info("Redémarrage pygeoapi déclenché via API Kubernetes")
                    return
                logger.warning("API Kubernetes: PATCH deployment %s/%s → %s %s (vérifier Role/RoleBinding)", namespace, deployment_name, resp.status_code, (resp.text or "")[:200])
            except Exception as e:
                logger.warning("API Kubernetes non disponible: %s", e)
        else:
            missing = []
            if not api_host or not api_port:
                missing.append("KUBERNETES_SERVICE_HOST/PORT")
            if not os.path.isfile(token_path):
                missing.append("token SA")
            if not os.path.isfile(ca_path):
                missing.append("ca.crt")
            logger.info("Fallback K8s non possible (pod hors cluster ou %s manquant)", ", ".join(missing))

        try:
            kube_cmd = f"kubectl rollout restart deployment/{deployment_name} -n {namespace}"
            logger.info("Restarting pygeoapi via kubectl (rollout restart deployment/%s -n %s)...", deployment_name, namespace)
            result = subprocess.run(kube_cmd, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                logger.info("pygeoapi rollout restart déclenché via kubectl")
            elif not cmd_failed:
                logger.debug("kubectl non disponible, pygeoapi redémarrera au prochain déploiement")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug("Fallback kubectl non disponible: %s", e)
    
    def remove_dataset(self, dataset_id: str) -> bool:
        """
        Remove a dataset from pygeoapi configuration
        
        Args:
            dataset_id: Dataset ID to remove
            
        Returns:
            True if removed successfully, False otherwise
        """
        try:
            logger.info(f" Removing dataset from pygeoapi: {dataset_id}")
            
            # Load current config
            if not self.pygeoapi_config_path.exists():
                logger.warning("pygeoapi config file does not exist")
                return False
            
            with open(self.pygeoapi_config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            # Remove collection if it exists
            collections = config.get('resources', {})
            if dataset_id in collections:
                del collections[dataset_id]
                logger.info(f"Removed collection {dataset_id} from config")
                
                # Write updated config
                with open(self.pygeoapi_config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                
                # Restart pygeoapi if configured
                if self.pygeoapi_restart_cmd:
                    self._restart_pygeoapi()
                
                return True
            else:
                logger.info(f"Collection {dataset_id} not found in config, nothing to remove")
                return False
                
        except Exception as e:
            logger.error(f"Error removing dataset {dataset_id}: {e}")
            return False


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync CKAN datasets with pygeoapi')
    parser.add_argument('--ckan-url', required=True, help='CKAN instance URL')
    parser.add_argument('--ckan-api-key', help='CKAN API key')
    parser.add_argument('--pygeoapi-config', required=True, help='pygeoapi config file path')
    parser.add_argument('--pygeoapi-url', help='pygeoapi instance URL (defaults to PYGEOAPI_URL env var or http://localhost:5001)')
    
    args = parser.parse_args()
    
    # Create sync instance
    sync = CKANSync(args.ckan_url, args.ckan_api_key, args.pygeoapi_config, args.pygeoapi_url)
    
    # Perform synchronization
    success = sync.sync()
    
    exit(0 if success else 1)


if __name__ == '__main__':
    main()

    def _calculate_dataset_bbox(self, dataset: Dict[str, Any]) -> Optional[List[float]]:
        """
        Calculate bounding box for a dataset based on its resources
        
        Args:
            dataset: Dataset dictionary
            
        Returns:
            Bounding box [minx, miny, maxx, maxy] or None
        """
        resources = dataset.get('resources', [])
        bboxes = []
        
        for resource in resources:
            if resource.get('datastore_active'):
                # Try to get bbox from datastore
                bbox = self._get_datastore_bbox(resource)
                if bbox:
                    bboxes.append(bbox)
        
        if not bboxes:
            return None
        
        # Calculate overall bbox
        minx = min(bbox[0] for bbox in bboxes)
        miny = min(bbox[1] for bbox in bboxes)
        maxx = max(bbox[2] for bbox in bboxes)
        maxy = max(bbox[3] for bbox in bboxes)
        
        return [minx, miny, maxx, maxy]

    def _get_datastore_bbox(self, resource: Dict[str, Any]) -> Optional[List[float]]:
        """
        Get bounding box from CKAN datastore
        
        Args:
            resource: Resource dictionary
            
        Returns:
            Bounding box [minx, miny, maxx, maxy] or None
        """
        try:
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': resource['id'],
                'limit': 1
            }
            
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            
            # 404 means resource doesn't exist in datastore - this is normal, don't log as warning
            if response.status_code == 404:
                return None
            
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                # Bbox réelle depuis les colonnes lat/lon (2000 premiers points).
                # (Remplace le stub historique qui renvoyait le monde entier.)
                fields = [f.get('id', '') for f in result['result'].get('fields', [])]
                lat_col = next((f for f in fields if f.lower() in ('latitude', 'lat', 'y_lat')), None)
                lon_col = next((f for f in fields if f.lower() in ('longitude', 'lon', 'lng', 'x_lon')), None)
                if not lat_col or not lon_col:
                    return None
                r2 = requests.get(url, params={'resource_id': resource['id'], 'limit': 2000,
                                               'fields': f'{lat_col},{lon_col}'},
                                  headers=self.headers, timeout=30)
                if r2.status_code != 200 or not r2.json().get('success'):
                    return None
                lats, lons = [], []
                for rec in r2.json()['result'].get('records', []):
                    try:
                        la, lo = float(rec.get(lat_col)), float(rec.get(lon_col))
                    except (TypeError, ValueError):
                        continue
                    if -90 <= la <= 90 and -180 <= lo <= 180:
                        lats.append(la)
                        lons.append(lo)
                if not lats:
                    return None
                m = 0.01
                return [min(lons) - m, min(lats) - m, max(lons) + m, max(lats) + m]
            
            return None
            
        except requests.exceptions.HTTPError as e:
            # Only log non-404 HTTP errors as warnings
            if e.response.status_code != 404:
                logger.warning(f"HTTP error getting datastore bbox for resource {resource.get('id', 'unknown')}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error getting datastore bbox for resource {resource.get('id', 'unknown')}: {e}")
            return None

    def sync_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """
        Synchronize a single dataset
        
        Args:
            dataset_id: Dataset ID to sync
            
        Returns:
            Dictionary with sync results
        """
        try:
            logger.info(f"Syncing dataset: {dataset_id}")
            
            # Get dataset details
            dataset = self._get_package_details(dataset_id)
            if not dataset:
                return {
                    'success': False,
                    'error': f'Dataset {dataset_id} not found'
                }
            
            # Check if it's geospatial
            if not self._is_geospatial_dataset(dataset):
                return {
                    'success': False,
                    'error': f'Dataset {dataset_id} is not geospatial'
                }
            
            # Update pygeoapi config with this dataset
            success = self.update_pygeoapi_config([dataset], backup_before_write=False)
            
            if success:
                logger.info(f"Successfully synced dataset: {dataset_id}")
                # Restart pygeoapi if configured
                if self.pygeoapi_restart_cmd:
                    self._restart_pygeoapi()
                return {
                    'success': True,
                    'dataset_id': dataset_id,
                    'dataset_name': dataset.get('name', dataset_id)
                }
            else:
                return {
                    'success': False,
                    'error': f'Failed to update pygeoapi config for {dataset_id}'
                }
                
        except Exception as e:
            logger.error(f"Error syncing dataset {dataset_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def sync(self, limit: int = None, progress_callback=None) -> bool:
        """
        Perform full synchronization
        
        Args:
            limit: Maximum number of datasets to process (for testing)
            progress_callback: optional callable(current, total, message) pour l'UI admin
            
        Returns:
            True if successful, False otherwise
        """
        logger.info("Starting CKAN to pygeoapi synchronization...")
        logger.info(f"CKAN URL: {self.ckan_url}")
        logger.info(f"API Key: {'***' if self.ckan_api_key else 'None'}")
        logger.info(f"Config file: {self.pygeoapi_config_path}")
        
        # Discover geospatial datasets
        logger.info("Discovering geospatial datasets...")
        if progress_callback:
            progress_callback(0, 1, "Découverte des datasets géospatiaux...")
        datasets = self.discover_geospatial_datasets(limit=limit, progress_callback=progress_callback)
        
        if not datasets:
            logger.warning(" No geospatial datasets found")
            if progress_callback:
                progress_callback(0, 1, "Aucun dataset géospatial trouvé")
            return False
        
        logger.info(f"Found {len(datasets)} geospatial datasets:")
        for dataset in datasets:
            logger.info(f"  - {dataset.get('name', 'unnamed')} (ID: {dataset.get('id', 'unknown')})")
        
        # Update pygeoapi configuration
        logger.info("Updating pygeoapi configuration...")
        if progress_callback:
            progress_callback(0, len(datasets), "Mise à jour de la configuration pygeoapi...")
        success = self.update_pygeoapi_config(datasets)
        
        if success:
            logger.info("Synchronization completed successfully")
            logger.info(f"Created {len(datasets)} OGC collections")
            if progress_callback:
                progress_callback(len(datasets), len(datasets), "Redémarrage de pygeoapi...")
            # Restart pygeoapi if configured
            if self.pygeoapi_restart_cmd:
                self._restart_pygeoapi()
        else:
            logger.error("Synchronization failed")
        
        return success
    
    def _restart_pygeoapi(self):
        """
        Restart pygeoapi: PYGEOAPI_RESTART_CMD puis fallback Kubernetes (API K8s prioritaire, puis kubectl).
        Par défaut utilise le script restart-pygeoapi.sh qui privilégie l'API Kubernetes.
        """
        import subprocess
        import time as _time

        cmd = self.pygeoapi_restart_cmd
        cmd_failed = False
        if cmd:
            try:
                logger.info(f"Restarting pygeoapi: {cmd}")
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    logger.info("pygeoapi restarted successfully")
                    return
                stderr = (result.stderr or "")[:300]
                if "not found" in stderr.lower() or "command not found" in stderr.lower():
                    logger.warning("Commande de redémarrage non disponible (%s), fallback Kubernetes", stderr.strip() or result.returncode)
                else:
                    logger.warning("Échec redémarrage pygeoapi (code %s): %s", result.returncode, stderr)
                cmd_failed = True
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                logger.warning("Erreur redémarrage pygeoapi (non bloquant): %s", e)
                cmd_failed = True

        # Fallback Kubernetes : API K8s (prioritaire) puis kubectl
        namespace = os.getenv("KUBERNETES_NAMESPACE", "ckan-bpm")
        deployment_name = os.getenv("PYGEOAPI_DEPLOYMENT_NAME", "pygeoapi")
        token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        api_host = os.getenv("KUBERNETES_SERVICE_HOST")
        api_port = os.getenv("KUBERNETES_SERVICE_PORT")

        if api_host and api_port and os.path.isfile(token_path) and os.path.isfile(ca_path):
            try:
                logger.info("Tentative redémarrage pygeoapi via API Kubernetes (deployment=%s, namespace=%s)...", deployment_name, namespace)
                with open(token_path, encoding="utf-8") as f:
                    token = f.read().strip()
                url = f"https://{api_host}:{api_port}/apis/apps/v1/namespaces/{namespace}/deployments/{deployment_name}"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/strategic-merge-patch+json"}
                data = {"spec": {"template": {"metadata": {"annotations": {"ckan/restarted": f"restarted-at-{int(_time.time())}"}}}}}
                resp = requests.patch(url, json=data, headers=headers, verify=ca_path, timeout=15)
                if resp.status_code == 200:
                    logger.info("Redémarrage pygeoapi déclenché via API Kubernetes")
                    return
                logger.warning("API Kubernetes: PATCH deployment %s/%s → %s %s (vérifier Role/RoleBinding)", namespace, deployment_name, resp.status_code, (resp.text or "")[:200])
            except Exception as e:
                logger.warning("API Kubernetes non disponible: %s", e)
        else:
            missing = []
            if not api_host or not api_port:
                missing.append("KUBERNETES_SERVICE_HOST/PORT")
            if not os.path.isfile(token_path):
                missing.append("token SA")
            if not os.path.isfile(ca_path):
                missing.append("ca.crt")
            logger.info("Fallback K8s non possible (pod hors cluster ou %s manquant)", ", ".join(missing))

        try:
            kube_cmd = f"kubectl rollout restart deployment/{deployment_name} -n {namespace}"
            logger.info("Restarting pygeoapi via kubectl (rollout restart deployment/%s -n %s)...", deployment_name, namespace)
            result = subprocess.run(kube_cmd, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                logger.info("pygeoapi rollout restart déclenché via kubectl")
            elif not cmd_failed:
                logger.debug("kubectl non disponible, pygeoapi redémarrera au prochain déploiement")
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.debug("Fallback kubectl non disponible: %s", e)
    
    def remove_dataset(self, dataset_id: str) -> bool:
        """
        Remove a dataset from pygeoapi configuration
        
        Args:
            dataset_id: Dataset ID to remove
            
        Returns:
            True if removed successfully, False otherwise
        """
        try:
            logger.info(f" Removing dataset from pygeoapi: {dataset_id}")
            
            # Load current config
            if not self.pygeoapi_config_path.exists():
                logger.warning("pygeoapi config file does not exist")
                return False
            
            with open(self.pygeoapi_config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            # Remove collection if it exists
            collections = config.get('resources', {})
            if dataset_id in collections:
                del collections[dataset_id]
                logger.info(f"Removed collection {dataset_id} from config")
                
                # Write updated config
                with open(self.pygeoapi_config_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                
                # Restart pygeoapi if configured
                if self.pygeoapi_restart_cmd:
                    self._restart_pygeoapi()
                
                return True
            else:
                logger.info(f"Collection {dataset_id} not found in config, nothing to remove")
                return False
                
        except Exception as e:
            logger.error(f"Error removing dataset {dataset_id}: {e}")
            return False


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync CKAN datasets with pygeoapi')
    parser.add_argument('--ckan-url', required=True, help='CKAN instance URL')
    parser.add_argument('--ckan-api-key', help='CKAN API key')
    parser.add_argument('--pygeoapi-config', required=True, help='pygeoapi config file path')
    parser.add_argument('--pygeoapi-url', help='pygeoapi instance URL (defaults to PYGEOAPI_URL env var or http://localhost:5001)')
    
    args = parser.parse_args()
    
    # Create sync instance
    sync = CKANSync(args.ckan_url, args.ckan_api_key, args.pygeoapi_config, args.pygeoapi_url)
    
    # Perform synchronization
    success = sync.sync()
    
    exit(0 if success else 1)


if __name__ == '__main__':
    main() 
