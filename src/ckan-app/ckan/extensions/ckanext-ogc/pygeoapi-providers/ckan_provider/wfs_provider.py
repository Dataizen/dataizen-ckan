"""
WFS Provider for pygeoapi

This provider serves CKAN geospatial datasets as WFS (Web Feature Service)
returning data in XML/GML format.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
import requests
import json
from pygeoapi.provider.base import BaseProvider

LOGGER = logging.getLogger(__name__)


class WFSProvider(BaseProvider):
    """WFS Provider for pygeoapi"""

    def __init__(self, provider_def: Dict[str, Any]):
        """
        Initialize WFS Provider
        
        Args:
            provider_def: Provider definition from configuration
        """
        super().__init__(provider_def)
        
        # Provider type
        self.type = 'feature'
        
        # CKAN configuration from options
        options = provider_def.get('options', {})
        self.ckan_url = options.get('ckan_url', 'http://localhost:5000')
        self.api_key = options.get('api_key', '')
        self.dataset_id = options.get('dataset_id', '')
        
        # Validate configuration
        if not self.ckan_url:
            raise ValueError("ckan_url is required for WFS provider")
        if not self.dataset_id:
            raise ValueError("dataset_id is required for WFS provider")
            
        LOGGER.info(f"WFS Provider initialized for dataset: {self.dataset_id}")
        LOGGER.info(f"CKAN URL: {self.ckan_url}")
        
        # Default CRS
        self.crs = 'EPSG:4326'
        self.namespace = 'http://dataizen.eu/wfs'

    def get_capabilities(self) -> str:
        """
        Generate WFS GetCapabilities response
        
        Returns:
            XML string with WFS capabilities
        """
        try:
            # Create XML root
            root = ET.Element('WFS_Capabilities')
            root.set('version', '2.0.0')
            root.set('xmlns', 'http://www.opengis.net/wfs/2.0')
            root.set('xmlns:gml', 'http://www.opengis.net/gml/3.2')
            root.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
            
            # Service identification
            service_id = ET.SubElement(root, 'ServiceIdentification')
            ET.SubElement(service_id, 'Title').text = f'WFS Service for {self.dataset_id}'
            ET.SubElement(service_id, 'Abstract').text = f'Web Feature Service for CKAN dataset {self.dataset_id}'
            ET.SubElement(service_id, 'ServiceType').text = 'WFS'
            ET.SubElement(service_id, 'ServiceTypeVersion').text = '2.0.0'
            
            # Operations metadata
            ops_metadata = ET.SubElement(root, 'OperationsMetadata')
            
            # GetCapabilities operation
            get_caps_op = ET.SubElement(ops_metadata, 'Operation')
            get_caps_op.set('name', 'GetCapabilities')
            
            # GetFeature operation
            get_feature_op = ET.SubElement(ops_metadata, 'Operation')
            get_feature_op.set('name', 'GetFeature')
            
            # Feature type list
            feature_types = ET.SubElement(root, 'FeatureTypeList')
            feature_type = ET.SubElement(feature_types, 'FeatureType')
            ET.SubElement(feature_type, 'Name').text = self.dataset_id
            ET.SubElement(feature_type, 'Title').text = f'Features from {self.dataset_id}'
            ET.SubElement(feature_type, 'DefaultCRS').text = self.crs
            
            # Convert to string
            return ET.tostring(root, encoding='unicode', xml_declaration=True)
            
        except Exception as e:
            LOGGER.error(f"Error generating WFS capabilities: {e}")
            return self._create_error_xml(str(e))

    def get_features(self, bbox: List[float] = None, limit: int = 100, 
                    offset: int = 0, **kwargs) -> str:
        """
        Get features as GML XML
        
        Args:
            bbox: Bounding box filter
            limit: Maximum number of features
            offset: Offset for pagination
            
        Returns:
            XML string with features in GML format
        """
        try:
            LOGGER.info(f"Getting WFS features for {self.dataset_id}")
            
            # Get features from CKAN
            features = self._get_ckan_features(bbox, limit, offset)
            
            # Convert to GML XML
            return self._features_to_gml(features)
            
        except Exception as e:
            LOGGER.error(f"Error getting WFS features: {e}")
            return self._create_error_xml(str(e))

    def _get_ckan_features(self, bbox: List[float] = None, 
                          limit: int = 100, offset: int = 0) -> List[Dict]:
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
                for record in records:
                    feature = self._record_to_feature(record, bbox)
                    if feature:
                        features.append(feature)
                
                return features
            
        except Exception as e:
            LOGGER.error(f"Error getting resource features: {e}")
        
        return []

    def _record_to_feature(self, record: Dict, bbox: List[float] = None) -> Optional[Dict]:
        """
        Convert a datastore record to a GeoJSON-like feature
        
        Args:
            record: Datastore record
            bbox: Bounding box for filtering
            
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
            if bbox:
                minx, miny, maxx, maxy = bbox
                if not (minx <= lon <= maxx and miny <= lat <= maxy):
                    return None
            
            # Create feature
            feature = {
                'type': 'Feature',
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

    def _features_to_gml(self, features: List[Dict]) -> str:
        """
        Convert features to GML XML format
        
        Args:
            features: List of GeoJSON features
            
        Returns:
            XML string in GML format
        """
        try:
            # Create XML root
            root = ET.Element('wfs:FeatureCollection')
            root.set('xmlns:wfs', 'http://www.opengis.net/wfs/2.0')
            root.set('xmlns:gml', 'http://www.opengis.net/gml/3.2')
            root.set('xmlns:dataizen', self.namespace)
            root.set('numberMatched', str(len(features)))
            root.set('numberReturned', str(len(features)))
            
            # Add features
            for i, feature in enumerate(features):
                self._add_feature_to_gml(root, feature, i)
            
            # Convert to string
            return ET.tostring(root, encoding='unicode', xml_declaration=True)
            
        except Exception as e:
            LOGGER.error(f"Error converting features to GML: {e}")
            return self._create_error_xml(str(e))

    def _add_feature_to_gml(self, parent: ET.Element, feature: Dict, feature_id: int):
        """
        Add a single feature to GML XML
        
        Args:
            parent: Parent XML element
            feature: GeoJSON feature
            feature_id: Unique feature ID
        """
        try:
            # Create feature member
            member = ET.SubElement(parent, 'wfs:member')
            
            # Create feature element
            feature_elem = ET.SubElement(member, f'dataizen:{self.dataset_id}')
            feature_elem.set('gml:id', f'{self.dataset_id}.{feature_id}')
            
            # Add geometry
            geom = feature['geometry']
            if geom['type'] == 'Point':
                geom_elem = ET.SubElement(feature_elem, 'dataizen:geometry')
                point_elem = ET.SubElement(geom_elem, 'gml:Point')
                point_elem.set('srsName', self.crs)
                
                coords = geom['coordinates']
                pos_elem = ET.SubElement(point_elem, 'gml:pos')
                pos_elem.text = f'{coords[0]} {coords[1]}'
            
            # Add properties
            properties = feature.get('properties', {})
            for key, value in properties.items():
                if value is not None:
                    prop_elem = ET.SubElement(feature_elem, f'dataizen:{key}')
                    prop_elem.text = str(value)
                    
        except Exception as e:
            LOGGER.error(f"Error adding feature to GML: {e}")

    def _create_error_xml(self, error: str) -> str:
        """
        Create an error XML response
        
        Args:
            error: Error message
            
        Returns:
            XML error response
        """
        try:
            root = ET.Element('ExceptionReport')
            root.set('xmlns', 'http://www.opengis.net/ows/1.1')
            root.set('version', '1.0.0')
            
            exception = ET.SubElement(root, 'Exception')
            exception.set('exceptionCode', 'NoApplicableCode')
            
            exception_text = ET.SubElement(exception, 'ExceptionText')
            exception_text.text = error
            
            return ET.tostring(root, encoding='unicode', xml_declaration=True)
            
        except Exception as e:
            LOGGER.error(f"Error creating error XML: {e}")
            return f'<?xml version="1.0"?><error>{error}</error>'

    def describe_feature_type(self) -> str:
        """
        Generate DescribeFeatureType response
        
        Returns:
            XML schema for the feature type
        """
        try:
            # Create XML schema
            root = ET.Element('schema')
            root.set('xmlns', 'http://www.w3.org/2001/XMLSchema')
            root.set('xmlns:gml', 'http://www.opengis.net/gml/3.2')
            root.set('xmlns:dataizen', self.namespace)
            root.set('targetNamespace', self.namespace)
            
            # Import GML
            import_elem = ET.SubElement(root, 'import')
            import_elem.set('namespace', 'http://www.opengis.net/gml/3.2')
            import_elem.set('schemaLocation', 'http://schemas.opengis.net/gml/3.2.1/gml.xsd')
            
            # Define feature type
            complex_type = ET.SubElement(root, 'complexType')
            complex_type.set('name', f'{self.dataset_id}Type')
            
            complex_content = ET.SubElement(complex_type, 'complexContent')
            extension = ET.SubElement(complex_content, 'extension')
            extension.set('base', 'gml:AbstractFeatureType')
            
            sequence = ET.SubElement(extension, 'sequence')
            
            # Add geometry element
            geom_elem = ET.SubElement(sequence, 'element')
            geom_elem.set('name', 'geometry')
            geom_elem.set('type', 'gml:PointPropertyType')
            geom_elem.set('minOccurs', '0')
            
            # Add property elements (generic)
            prop_elem = ET.SubElement(sequence, 'element')
            prop_elem.set('name', 'properties')
            prop_elem.set('type', 'string')
            prop_elem.set('minOccurs', '0')
            prop_elem.set('maxOccurs', 'unbounded')
            
            # Define element
            element = ET.SubElement(root, 'element')
            element.set('name', self.dataset_id)
            element.set('type', f'dataizen:{self.dataset_id}Type')
            element.set('substitutionGroup', 'gml:AbstractFeature')
            
            return ET.tostring(root, encoding='unicode', xml_declaration=True)
            
        except Exception as e:
            LOGGER.error(f"Error generating feature type schema: {e}")
            return self._create_error_xml(str(e))


