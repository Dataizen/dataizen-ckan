#!/usr/bin/env python3
"""
CKAN to pygeoapi Synchronization Tool

This tool synchronizes CKAN datasets to pygeoapi collections.
It automatically detects geospatial datasets and creates corresponding OGC endpoints.
"""

import logging
import sys
import os
import yaml
import requests
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ckan_sync import CKANSync
# restore_ogc_links functionality is now integrated directly in ckan_sync.py

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOGGER = logging.getLogger(__name__)


class CKANSyncTool:
    """Main synchronization tool for CKAN to pygeoapi"""
    
    def __init__(self, ckan_url: str, api_key: str, config_path: str):
        """
        Initialize the sync tool
        
        Args:
            ckan_url: CKAN URL
            api_key: CKAN API key
            config_path: Path to pygeoapi config file
        """
        import os
        self.ckan_url = ckan_url
        self.api_key = api_key
        self.config_path = config_path
        pygeoapi_url = os.getenv('PYGEOAPI_URL', 'http://localhost:5001')
        self.sync = CKANSync(ckan_url, api_key, config_path, pygeoapi_url)
        
        LOGGER.info(f"CKAN Sync Tool initialized")
        LOGGER.info(f"CKAN URL: {ckan_url}")
        LOGGER.info(f"Config path: {config_path}")
    
    def check_ckan_connectivity(self) -> bool:
        """
        Check if CKAN is accessible
        
        Returns:
            True if accessible, False otherwise
        """
        try:
            LOGGER.info("Checking CKAN connectivity...")
            
            # Test basic connectivity using dataset page
            response = requests.get(f"{self.ckan_url}/dataset/", timeout=10)
            response.raise_for_status()
            
            # If we get a 200 response, CKAN is accessible
            LOGGER.info("CKAN is accessible")
            return True
                
        except Exception as e:
            LOGGER.error(f"Cannot connect to CKAN: {e}")
            return False
    
    def get_geospatial_datasets(self) -> List[Dict[str, Any]]:
        """
        Get all geospatial datasets from CKAN
        
        Returns:
            List of geospatial datasets
        """
        try:
            LOGGER.info("Fetching geospatial datasets from CKAN...")
            
            url = f"{self.ckan_url}/api/action/package_list"
            params = {'limit': 1000}
            
            if self.api_key:
                headers = {'Authorization': self.api_key}
            else:
                headers = {}
            
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                LOGGER.error(f"CKAN API error: {result.get('error')}")
                return []
            
            dataset_ids = result.get('result', [])
            geospatial_datasets = []
            
            LOGGER.info(f"Found {len(dataset_ids)} datasets, checking for geospatial ones...")
            
            for dataset_id in dataset_ids:
                try:
                    # Get dataset details
                    dataset_url = f"{self.ckan_url}/api/action/package_show"
                    dataset_params = {'id': dataset_id}
                    
                    dataset_response = requests.get(
                        dataset_url, 
                        params=dataset_params, 
                        headers=headers, 
                        timeout=10
                    )
                    dataset_response.raise_for_status()
                    
                    dataset_result = dataset_response.json()
                    if dataset_result.get('success'):
                        dataset = dataset_result.get('result', {})
                        
                        # Check if dataset has geospatial resources
                        if self._is_geospatial_dataset(dataset):
                            geospatial_datasets.append(dataset)
                            LOGGER.info(f"Found geospatial dataset: {dataset.get('name', dataset_id)}")
                
                except Exception as e:
                    LOGGER.warning(f"Error fetching dataset {dataset_id}: {e}")
                    continue
            
            LOGGER.info(f"Found {len(geospatial_datasets)} geospatial datasets")
            return geospatial_datasets
            
        except Exception as e:
            LOGGER.error(f"Error fetching datasets: {e}")
            return []
    
    def _is_geospatial_dataset(self, dataset: Dict[str, Any]) -> bool:
        """
        Check if a dataset contains geospatial data
        
        Args:
            dataset: Dataset dictionary
            
        Returns:
            True if geospatial, False otherwise
        """
        resources = dataset.get('resources', [])
        
        for resource in resources:
            # Check format
            format_ = resource.get('format', '').upper()
            geo_formats = ['GEOJSON', 'SHAPEFILE', 'KML', 'KMZ', 'GPX', 'CSV']
            
            if format_ in geo_formats:
                return True
            
            # Check if datastore is active (CSV with geometry)
            if format_ == 'CSV' and resource.get('datastore_active'):
                return True
            
            # Check description/keywords for spatial indicators
            description = resource.get('description', '').lower()
            keywords = resource.get('tags', [])
            
            spatial_indicators = ['geo', 'spatial', 'geometry', 'coordinate', 'latitude', 'longitude']
            
            if any(indicator in description.lower() for indicator in spatial_indicators):
                return True
            
            if any(any(indicator in tag.get('name', '').lower() for indicator in spatial_indicators) 
                   for tag in keywords):
                return True
        
        return False
    
    def sync_all_datasets(self, limit: int = None) -> Dict[str, Any]:
        """
        Synchronize all geospatial datasets
        
        Args:
            limit: Maximum number of datasets to process (for testing)
            
        Returns:
            Dictionary with sync results
        """
        try:
            LOGGER.info("Starting full synchronization...")
            
            # Check connectivity
            if not self.check_ckan_connectivity():
                return {
                    'success': False,
                    'error': 'CKAN not accessible',
                    'datasets_synced': 0,
                    'datasets_failed': 0
                }
            
            # Use the optimized sync method from CKANSync
            success = self.sync.sync(limit=limit)
            
            if success:
                # OGC links are now integrated directly in ckan_sync.py
                LOGGER.info("OGC links integrated in synchronization")
                
                # Copy config to host file for volume persistence
                LOGGER.info("Copying configuration to host file...")
                try:
                    import subprocess
                    subprocess.run([
                        'cp', self.config_path, 
                        '/srv/app/pygeoapi/local.config.yml.host'
                    ], check=True)
                    LOGGER.info("Configuration copied to host")
                except Exception as e:
                    LOGGER.error(f"Error copying config: {e}")
                
                # Restart pygeoapi to reload configuration
                LOGGER.info("Restarting pygeoapi to reload configuration...")
                try:
                    self._restart_pygeoapi()
                    LOGGER.info("Pygeoapi restarted")
                except Exception as e:
                    LOGGER.error(f"Error restarting pygeoapi: {e}")
                
                return {
                    'success': True,
                    'datasets_synced': 'all' if limit is None else limit,
                    'datasets_failed': 0,
                    'message': f'Synchronization completed successfully'
                }
            else:
                return {
                    'success': False,
                    'error': 'Synchronization failed',
                    'datasets_synced': 0,
                    'datasets_failed': 0
                }
            
        except Exception as e:
            LOGGER.error(f"Error during synchronization: {e}")
            return {
                'success': False,
                'error': str(e),
                'datasets_synced': 0,
                'datasets_failed': 0
            }
    
    def sync_single_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """
        Synchronize a single dataset
        
        Args:
            dataset_id: Dataset ID to sync
            
        Returns:
            Dictionary with sync results
        """
        try:
            LOGGER.info(f"Syncing single dataset: {dataset_id}")
            
            # Check connectivity
            if not self.check_ckan_connectivity():
                return {
                    'success': False,
                    'error': 'CKAN not accessible'
                }
            
            # Sync the dataset
            result = self.sync.sync_dataset(dataset_id)
            
            if result.get('success'):
                # Restore OGC links
                LOGGER.info("Restoring OGC links...")
                try:
                    restore_ogc_links(self.config_path)
                    LOGGER.info("OGC links restored")
                except Exception as e:
                    LOGGER.error(f"Error restoring OGC links: {e}")
            
            return result
            
        except Exception as e:
            LOGGER.error(f"Error syncing dataset {dataset_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _restart_pygeoapi(self):
        """
        Restart pygeoapi to reload configuration after sync
        """
        import subprocess
        import signal
        import time
        
        try:
            # Find pygeoapi process
            result = subprocess.run(['pgrep', '-f', 'pygeoapi'], capture_output=True, text=True)
            if result.returncode == 0:
                pids = result.stdout.strip().split('\n')
                LOGGER.info(f"Found pygeoapi processes: {pids}")
                
                # Kill existing pygeoapi processes
                for pid in pids:
                    if pid.strip():
                        try:
                            subprocess.run(['kill', '-TERM', pid.strip()], check=True)
                            LOGGER.info(f"Stopped pygeoapi process {pid}")
                        except subprocess.CalledProcessError:
                            LOGGER.warning(f"Could not stop process {pid}")
                
                # Wait a bit for processes to stop
                time.sleep(2)
            
            # Start pygeoapi again
            LOGGER.info("Starting pygeoapi...")
            subprocess.Popen([
                'bash', '-c', 
                'cd /srv/app && /srv/app/start-pygeoapi.sh > /tmp/pygeoapi.log 2>&1 &'
            ])
            
            # Wait a bit for startup
            time.sleep(3)
            LOGGER.info("Pygeoapi restart initiated")
            
        except Exception as e:
            LOGGER.error(f"Error restarting pygeoapi: {e}")
            raise
    
    def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status
        
        Returns:
            Dictionary with status information
        """
        try:
            LOGGER.info("Getting sync status...")
            
            # Check CKAN connectivity
            ckan_accessible = self.check_ckan_connectivity()
            
            # Get geospatial datasets count
            datasets = self.get_geospatial_datasets() if ckan_accessible else []
            
            # Check pygeoapi config
            config_exists = os.path.exists(self.config_path)
            
            # Load config to check collections
            collections = []
            if config_exists:
                try:
                    with open(self.config_path, 'r') as f:
                        config = yaml.safe_load(f)
                        collections = list(config.get('resources', {}).keys())
                except Exception as e:
                    LOGGER.warning(f"Error reading config: {e}")
            
            return {
                'ckan_accessible': ckan_accessible,
                'geospatial_datasets_count': len(datasets),
                'pygeoapi_collections_count': len(collections),
                'collections': collections,
                'config_exists': config_exists,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            LOGGER.error(f"Error getting status: {e}")
            return {
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }


def main():
    """Main function for command line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description='CKAN to pygeoapi Synchronization Tool')
    parser.add_argument('--ckan-url', default='http://localhost:5000', 
                       help='CKAN URL (default: http://localhost:5000)')
    parser.add_argument('--api-key', default='ckan-local-dev-apikey',
                       help='CKAN API key')
    parser.add_argument('--config', default='/srv/app/pygeoapi/local.config.yml',
                       help='pygeoapi config file path')
    parser.add_argument('--action', choices=['sync-all', 'sync-dataset', 'status'],
                       default='sync-all', help='Action to perform')
    parser.add_argument('--dataset-id', help='Dataset ID (for sync-dataset action)')
    parser.add_argument('--limit', type=int, help='Limit number of datasets to process (for testing)')
    
    args = parser.parse_args()
    
    # Initialize sync tool
    sync_tool = CKANSyncTool(args.ckan_url, args.api_key, args.config)
    
    # Perform action
    if args.action == 'sync-all':
        LOGGER.info("Starting full synchronization...")
        result = sync_tool.sync_all_datasets(limit=args.limit)
        
        if result.get('success'):
            LOGGER.info(f"Synchronization completed!")
            LOGGER.info(f"Datasets synced: {result.get('datasets_synced')}")
            LOGGER.info(f"Datasets failed: {result.get('datasets_failed')}")
            
            if result.get('failed_datasets'):
                LOGGER.warning(f"Failed datasets: {', '.join(result['failed_datasets'])}")
        else:
            LOGGER.error(f"Synchronization failed: {result.get('error')}")
            sys.exit(1)
    
    elif args.action == 'sync-dataset':
        if not args.dataset_id:
            LOGGER.error("Dataset ID is required for sync-dataset action")
            sys.exit(1)
        
        LOGGER.info(f"Syncing dataset: {args.dataset_id}")
        result = sync_tool.sync_single_dataset(args.dataset_id)
        
        if result.get('success'):
            LOGGER.info(f"Dataset {args.dataset_id} synced successfully")
        else:
            LOGGER.error(f"Failed to sync dataset {args.dataset_id}: {result.get('error')}")
            sys.exit(1)
    
    elif args.action == 'status':
        LOGGER.info("Getting sync status...")
        status = sync_tool.get_sync_status()
        
        print("\n" + "="*50)
        print("CKAN to pygeoapi Sync Status")
        print("="*50)
        print(f"CKAN Accessible: {'Yes' if status.get('ckan_accessible') else 'No'}")
        print(f"Geospatial Datasets: {status.get('geospatial_datasets_count', 0)}")
        print(f"pygeoapi Collections: {status.get('pygeoapi_collections_count', 0)}")
        print(f"Config File: {'Exists' if status.get('config_exists') else 'Missing'}")
        
        if status.get('collections'):
            print(f"\nCollections:")
            for collection in status['collections']:
                print(f"  - {collection}")
        
        print(f"\nTimestamp: {status.get('timestamp')}")
        print("="*50)


if __name__ == '__main__':
    main()
