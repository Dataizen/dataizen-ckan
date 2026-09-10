"""
Simple Map Provider for pygeoapi with Pillow/matplotlib rendering

This provider generates PNG map images from CKAN geospatial datasets
using a simplified approach compatible with pygeoapi.
"""

import logging
import io
import json
from typing import Any, Dict, List, Optional, Tuple
import requests
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pygeoapi.provider.base import BaseProvider

LOGGER = logging.getLogger(__name__)


class SimpleMapProvider(BaseProvider):
    """Simple Map Provider for pygeoapi with Pillow/matplotlib rendering"""

    def __init__(self, provider_def: Dict[str, Any]):
        """
        Initialize Simple Map Provider
        
        Args:
            provider_def: Provider definition from configuration
        """
        super().__init__(provider_def)
        
        # Provider type - MUST be 'feature' for pygeoapi compatibility
        self.type = 'feature'
        
        # CKAN configuration from options
        options = provider_def.get('options', {})
        self.ckan_url = options.get('ckan_url', 'http://localhost:5000')
        self.api_key = options.get('api_key', '')
        self.dataset_id = options.get('dataset_id', '')
        
        # Validate configuration
        if not self.ckan_url:
            raise ValueError("ckan_url is required for Simple Map provider")
        if not self.dataset_id:
            raise ValueError("dataset_id is required for Simple Map provider")
            
        LOGGER.info(f"Simple Map Provider initialized for dataset: {self.dataset_id}")
        LOGGER.info(f"CKAN URL: {self.ckan_url}")
        
        # Default CRS
        self.crs = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'

    def query(self, startindex: int = 0, limit: int = 10, resulttype: str = 'results',
              bbox: List[float] = [], datetime_: str = None, properties: List = [],
              sortby: List = [], select_properties: List = [], skip_geometry: bool = False,
              q: str = None, **kwargs) -> Dict[str, Any]:
        """
        Query the provider for features
        
        Args:
            startindex: Starting record index
            limit: Maximum number of records to return
            resulttype: Type of result ('results' or 'hits')
            bbox: Bounding box filter
            datetime_: Temporal filter
            properties: Property filters
            sortby: Sort criteria
            select_properties: Properties to include
            skip_geometry: Whether to skip geometry
            q: Text query
            
        Returns:
            Dict with features and metadata
        """
        try:
            LOGGER.info(f"Querying Simple Map Provider for {self.dataset_id}")
            
            # Get features from CKAN
            features = self._get_ckan_features(bbox, limit, startindex)
            
            # Return GeoJSON FeatureCollection
            return {
                'type': 'FeatureCollection',
                'features': features,
                'numberMatched': len(features),
                'numberReturned': len(features)
            }
            
        except Exception as e:
            LOGGER.error(f"Error querying Simple Map Provider: {e}")
            return {
                'type': 'FeatureCollection',
                'features': [],
                'numberMatched': 0,
                'numberReturned': 0
            }

    def get(self, identifier: str, **kwargs) -> Dict[str, Any]:
        """
        Get a single feature by identifier
        
        Args:
            identifier: Feature identifier
            
        Returns:
            Single feature dict
        """
        try:
            # Get all features and find the one with matching ID
            features = self._get_ckan_features()
            
            for feature in features:
                if str(feature.get('id')) == str(identifier):
                    return feature
            
            raise ValueError(f"Feature {identifier} not found")
            
        except Exception as e:
            LOGGER.error(f"Error getting feature {identifier}: {e}")
            raise

    def _get_ckan_features(self, bbox: List[float] = None, limit: int = 100, 
                          offset: int = 0) -> List[Dict]:
        """
        Retrieve geospatial features from CKAN datastore
        
        Args:
            bbox: Bounding box to filter features
            limit: Maximum number of features
            offset: Offset for pagination
            
        Returns:
            List of GeoJSON-like features
        """
        try:
            # Get dataset resources
            url = f"{self.ckan_url}/api/action/package_show"
            headers = {'Authorization': self.api_key} if self.api_key else {}
            
            response = requests.get(url, 
                                  params={'id': self.dataset_id},
                                  headers=headers)
            
            if response.status_code != 200:
                LOGGER.error(f"Failed to get dataset: {response.status_code}")
                return []
            
            dataset = response.json()['result']
            resources = dataset.get('resources', [])
            
            # Find geospatial resources
            geospatial_features = []
            for resource in resources:
                if resource.get('format', '').lower() in ['geojson', 'json', 'csv']:
                    features = self._get_resource_features(resource['id'], bbox, limit, offset)
                    geospatial_features.extend(features)
                    
                    # Apply limit
                    if len(geospatial_features) >= limit:
                        break
            
            return geospatial_features[:limit]
            
        except Exception as e:
            LOGGER.error(f"Error getting CKAN features: {e}")
            return []

    def _get_resource_features(self, resource_id: str, bbox: List[float] = None,
                              limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        Get features from a specific resource
        
        Args:
            resource_id: CKAN resource ID
            bbox: Bounding box
            limit: Maximum number of features
            offset: Offset for pagination
            
        Returns:
            List of features with coordinates
        """
        try:
            # Try datastore first
            url = f"{self.ckan_url}/api/action/datastore_search"
            headers = {'Authorization': self.api_key} if self.api_key else {}
            
            response = requests.get(url,
                                  params={
                                      'resource_id': resource_id, 
                                      'limit': limit,
                                      'offset': offset
                                  },
                                  headers=headers)
            
            if response.status_code == 200:
                data = response.json()['result']
                records = data.get('records', [])
                
                # Convert records to features
                features = []
                for i, record in enumerate(records):
                    feature = self._record_to_feature(record, bbox, i + offset)
                    if feature:
                        features.append(feature)
                
                return features
            
        except Exception as e:
            LOGGER.error(f"Error getting resource features: {e}")
        
        return []

    def _record_to_feature(self, record: Dict, bbox: List[float] = None, 
                          feature_id: int = None) -> Optional[Dict]:
        """
        Convert a datastore record to a GeoJSON-like feature
        
        Args:
            record: Datastore record
            bbox: Bounding box for filtering
            feature_id: Unique feature ID
            
        Returns:
            Feature dict or None
        """
        try:
            # Look for coordinate fields
            lon = lat = None
            
            # Common coordinate field names
            coord_fields = {
                'lon': ['longitude', 'lon', 'lng', 'x'],
                'lat': ['latitude', 'lat', 'y']
            }
            
            for key, value in record.items():
                key_lower = key.lower()
                
                # Check for longitude
                if any(field in key_lower for field in coord_fields['lon']):
                    try:
                        lon = float(value)
                    except (ValueError, TypeError):
                        continue
                
                # Check for latitude  
                if any(field in key_lower for field in coord_fields['lat']):
                    try:
                        lat = float(value)
                    except (ValueError, TypeError):
                        continue
            
            # Check if we have valid coordinates
            if lon is None or lat is None:
                return None
            
            # Check if coordinates are within bbox
            if bbox and len(bbox) >= 4:
                minx, miny, maxx, maxy = bbox[:4]
                if not (minx <= lon <= maxx and miny <= lat <= maxy):
                    return None
            
            # Create feature with unique ID
            feature_id = feature_id or record.get('_id') or hash(str(record))
            
            feature = {
                'type': 'Feature',
                'id': str(feature_id),
                'geometry': {
                    'type': 'Point',
                    'coordinates': [lon, lat]
                },
                'properties': record
            }
            
            return feature
            
        except Exception as e:
            LOGGER.error(f"Error converting record to feature: {e}")
            return None


