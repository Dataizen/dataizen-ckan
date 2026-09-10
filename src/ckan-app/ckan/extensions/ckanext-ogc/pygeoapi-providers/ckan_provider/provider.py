"""
CKAN Provider for pygeoapi

This provider connects to CKAN and exposes datasets as OGC collections.
It automatically detects geospatial datasets and creates corresponding OGC endpoints.
"""

import logging
import sys
from typing import Any, Dict, List, Optional, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import zipfile
import tempfile
import os
from pathlib import Path
from pygeoapi.provider.base import BaseProvider, ProviderTypeError
from pygeoapi.util import get_provider_by_type
try:
    from pyproj import Transformer
    HAS_PYPROJ = True
except ImportError:
    HAS_PYPROJ = False
try:
    import fiona
    from fiona.io import ZipMemoryFile
    HAS_FIONA = True
except ImportError:
    HAS_FIONA = False
try:
    from shapely.geometry import mapping
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

# Configure logging to output to stdout/stderr for Kubernetes visibility
LOGGER = logging.getLogger(__name__)
# Ensure logs are visible in Kubernetes (stdout/stderr)
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


class CKANProvider(BaseProvider):
    """CKAN Provider for pygeoapi"""

    def __init__(self, provider_def: Dict[str, Any]):
        """
        Initialize CKAN Provider
        
        Args:
            provider_def: Provider definition from configuration
        """
        super().__init__(provider_def)
        
        # Provider type
        self.type = 'feature'
        
        # CKAN configuration from options
        options = provider_def.get('options', {})
        # CKAN configuration from options
        options = provider_def.get('options', {})
# Use CKAN_URL env var first (set in docker-compose for pygeoapi service)
        # Then fall back to CKAN_SITE_URL or options
        ckan_url_env = os.getenv('CKAN_URL') or os.getenv('CKAN_SITE_URL') or options.get('ckan_url', 'http://localhost:5000')
        
        # If CKAN_URL is explicitly set (e.g., in docker-compose for pygeoapi service), use it
        # Otherwise, if CKAN_SITE_URL is a public URL (localhost:8080), use internal URL
        if os.getenv('CKAN_URL'):
            self.ckan_url = os.getenv('CKAN_URL')
            LOGGER.info(f"Using CKAN_URL from environment: {self.ckan_url}")
        elif ckan_url_env and 'localhost:8080' in ckan_url_env:
            # pygeoapi is in separate container, use Docker service name
            self.ckan_url = 'http://ckan:5000'  # Internal CKAN URL via Docker network
            LOGGER.info(f"Using Docker internal CKAN URL: {self.ckan_url}")
        else:
            self.ckan_url = ckan_url_env
        self.api_key = os.getenv('CKAN_API_KEY') or options.get('api_key', '')
        self.dataset_id = options.get('dataset_id', '')
        
        # Validate configuration
        if not self.ckan_url:
            raise ValueError("ckan_url is required for CKAN provider")
        if not self.dataset_id:
            raise ValueError("dataset_id is required for CKAN provider")
            
        LOGGER.info(f"CKAN Provider initialized for dataset: {self.dataset_id}")
        LOGGER.info(f"CKAN URL: {self.ckan_url}")
        
        # Create a reusable HTTP session with keep-alive and retries
        self.session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,  # Total number of retries
            backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
            status_forcelist=[500, 502, 503, 504],  # Retry on these status codes
            allowed_methods=["GET", "POST"],  # Only retry safe methods
            raise_on_status=False  # Don't raise exception on status codes
        )
        
        # Mount adapter with retry strategy
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,  # Number of connection pools to cache
            pool_maxsize=20,  # Maximum number of connections to save in the pool
            pool_block=False  # Don't block if pool is full
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set default headers for keep-alive
        self.session.headers.update({
            'Connection': 'keep-alive',
            'User-Agent': 'pygeoapi-ckan-provider/1.0'
        })
        
        # Default CRS
        self.crs = 'http://www.opengis.net/def/crs/OGC/1.3/CRS84'
        
        # Required attributes for pygeoapi templates
        # Use limits from options if provided, otherwise use defaults (très élevées)
        self.limits = options.get('limits', {
            'default': 10000000,  # Très élevé pour permettre toutes les données
            'max': 10000000  # Très élevé pour permettre toutes les données
        })
        
        # Ensure limits is accessible as a property for templates
        if not hasattr(self, 'limits') or self.limits is None:
            self.limits = {'default': 10000000, 'max': 10000000}
        
        # Queryables - will be populated dynamically
        self.queryables = {}
        
        # Schema - will be populated dynamically  
        self.schema = {}
        
        # Fields for pygeoapi queryables (maps to queryables)
        self._fields = {}
        
        # Initialize queryables and schema from CKAN
        self._initialize_metadata()

    def query(self, startindex: int = 0, limit: int = None, 
              resulttype: str = 'results', bbox: List[float] = None,
              datetime_: str = None, properties: List[str] = None,
              sortby: List[Dict[str, str]] = None, select_properties: List[str] = None,
              skip_geometry: bool = False, q: str = None, filterq: str = None,
              **kwargs: Any) -> Dict[str, Any]:
        """
        Query CKAN dataset
        
        Args:
            startindex: Starting index for pagination
            limit: Maximum number of results (None = use default from limits, or all if very high)
            resulttype: Type of results ('results', 'hits', 'hits_and_results')
            bbox: Bounding box filter
            datetime_: Temporal filter
            properties: Property filters
            sortby: Sorting criteria
            select_properties: Properties to select
            skip_geometry: Whether to skip geometry
            q: Text search query
            filterq: Filter query
            
        Returns:
            Dictionary with query results
        """
        try:
            LOGGER.info(f"Querying CKAN dataset: {self.dataset_id}")
            LOGGER.info(f"Query parameters: startindex={startindex}, limit={limit}, resulttype={resulttype}")
            
            # Use default limit from config if not provided or if very small (pygeoapi default is 10)
            if limit is None or limit <= 10:
                limit = self.limits.get('default', 10000000)
            
            # If limit is very high, fetch all data
            max_limit = self.limits.get('max', 10000000)
            if limit >= max_limit:
                limit = None  # Fetch all data
            
            # Get dataset from CKAN
            LOGGER.info(f"Fetching dataset from CKAN: {self.ckan_url}/api/action/package_show?id={self.dataset_id}")
            dataset_data = self._get_ckan_dataset()
            
            if not dataset_data:
                LOGGER.warning(f"Dataset {self.dataset_id} not found in CKAN")
                return {
                    'type': 'FeatureCollection',
                    'features': [],
                    'numberMatched': 0,
                    'numberReturned': 0
                }
            
            LOGGER.info(f"Dataset found: {dataset_data.get('name', 'unnamed')} (ID: {dataset_data.get('id', 'unknown')})")
            
            # Get resources (files) from dataset
            resources = dataset_data.get('resources', [])
            LOGGER.info(f"Found {len(resources)} resource(s) in dataset")
            
            # Filter geospatial resources
            geo_resources = self._filter_geospatial_resources(resources)
            LOGGER.info(f"Found {len(geo_resources)} geospatial resource(s)")
            
            if not geo_resources:
                LOGGER.warning(f"No geospatial resources found in dataset {self.dataset_id}")
                return {
                    'type': 'FeatureCollection',
                    'features': [],
                    'numberMatched': 0,
                    'numberReturned': 0
                }
            
            # Get data from first geospatial resource
            primary_resource = geo_resources[0]
            LOGGER.info(f"Fetching features from resource: {primary_resource.get('name', 'unnamed')} (ID: {primary_resource.get('id', 'unknown')})")
            LOGGER.info(f"Resource format: {primary_resource.get('format', 'unknown')}, datastore_active: {primary_resource.get('datastore_active', False)}")
            
            features = self._get_resource_features(primary_resource, startindex, limit)
            LOGGER.info(f"Fetched {len(features)} feature(s) from resource")
            
            # Apply filters
            if bbox:
                LOGGER.info(f"Applying bbox filter: {bbox}")
                features = self._filter_by_bbox(features, bbox)
                LOGGER.info(f"After bbox filter: {len(features)} feature(s)")
            if datetime_:
                LOGGER.info(f"Applying datetime filter: {datetime_}")
                features = self._filter_by_datetime(features, datetime_)
                LOGGER.info(f"After datetime filter: {len(features)} feature(s)")
            if q:
                LOGGER.info(f"Applying text filter: {q}")
                features = self._filter_by_text(features, q)
                LOGGER.info(f"After text filter: {len(features)} feature(s)")
            
            # Pagination only if limit is specified
            total_count = len(features)
            if limit:
                features = features[startindex:startindex + limit]
            else:
                features = features[startindex:]
            
            LOGGER.info(f"Returning {len(features)} feature(s) (total matched: {total_count})")
            
            return {
                'type': 'FeatureCollection',
                'features': features,
                'numberMatched': total_count,
                'numberReturned': len(features)
            }
            
        except Exception as e:
            LOGGER.error(f"Error querying CKAN dataset: {e}")
            raise ProviderTypeError(f"Failed to query CKAN: {e}")

    def get(self, identifier: str, **kwargs) -> Dict[str, Any]:
        """
        Get a specific feature by ID
        
        Args:
            identifier: Feature identifier
            
        Returns:
            Feature dictionary
        """
        try:
            # Get dataset from CKAN
            dataset_data = self._get_ckan_dataset()
            
            if not dataset_data:
                raise ProviderTypeError("Dataset not found")
            
            # Get resources
            resources = dataset_data.get('resources', [])
            geo_resources = self._filter_geospatial_resources(resources)
            
            if not geo_resources:
                raise ProviderTypeError("No geospatial resources found")
            
            # Get data from primary resource
            primary_resource = geo_resources[0]
            features = self._get_resource_features(primary_resource)
            
            # Find feature by ID
            for feature in features:
                if feature.get('id') == identifier:
                    return feature
            
            raise ProviderTypeError(f"Feature {identifier} not found")
            
        except Exception as e:
            LOGGER.error(f"Error getting feature {identifier}: {e}")
            raise ProviderTypeError(f"Failed to get feature: {e}")

    def _get_ckan_dataset(self) -> Optional[Dict[str, Any]]:
        """
        Get dataset from CKAN API
        
        Returns:
            Dataset data or None if not found
        """
        try:
            url = f"{self.ckan_url}/api/action/package_show"
            params = {'id': self.dataset_id}
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = self.api_key
            
            # Use session with keep-alive and retries
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if result.get('success'):
                return result.get('result')
            else:
                LOGGER.error(f"CKAN API error: {result.get('error')}")
                return None
                
        except requests.exceptions.ConnectionError as e:
            LOGGER.error(f"Connection error fetching dataset from CKAN: {e}")
            LOGGER.error(f"CKAN URL: {self.ckan_url}, Dataset ID: {self.dataset_id}")
            return None
        except requests.exceptions.Timeout as e:
            LOGGER.error(f"Timeout fetching dataset from CKAN: {e}")
            return None
        except Exception as e:
            LOGGER.error(f"Error fetching dataset from CKAN: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            return None

    def _filter_geospatial_resources(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter resources to find geospatial ones, prioritizing datastore-active resources
        
        Args:
            resources: List of resources from CKAN
            
        Returns:
            List of geospatial resources, sorted by datastore_active priority
        """
        geo_resources = []
        
        for resource in resources:
            # Check if resource has geometry data
            if self._is_geospatial_resource(resource):
                geo_resources.append(resource)
        
        # Sort by datastore_active priority (datastore-active resources first)
        geo_resources.sort(key=lambda r: r.get('datastore_active', False), reverse=True)
        
        return geo_resources

    def _is_geospatial_resource(self, resource: Dict[str, Any]) -> bool:
        """
        Check if a resource contains geospatial data
        
        Args:
            resource: Resource dictionary
            
        Returns:
            True if geospatial, False otherwise
        """
        # Check format - normalize IANA media types to simple format names
        format_raw = resource.get('format') or ''
        format_ = format_raw.upper()
        
        # Normalize IANA media types (e.g., "application/zip" -> "ZIP")
        format_lower = format_raw.lower()
        if 'application/zip' in format_lower or 'zip' in format_lower:
            format_ = 'ZIP'
        elif 'application/json' in format_lower and 'geo' in format_lower:
            format_ = 'GEOJSON'
        elif 'application/vnd.google-earth' in format_lower:
            format_ = 'KML'
        elif 'text/csv' in format_lower or 'csv' in format_lower:
            format_ = 'CSV'
        elif '/' in format_raw:
            # Remove IANA prefix if present (e.g., "application/zip" -> "zip")
            format_ = format_raw.split('/')[-1].upper()
        
        geo_formats = ['GEOJSON', 'SHAPEFILE', 'KML', 'KMZ', 'GPX', 'CSV', 'ZIP']
        
        # Also check URL extension for ZIP files
        url = (resource.get('url') or '').lower()
        if url.endswith('.zip') and format_ not in geo_formats:
            format_ = 'ZIP'
        
        if format_ in geo_formats:
            return True
        
        # Check if datastore is active (CSV with geometry)
        if format_ == 'CSV' and resource.get('datastore_active'):
            return True
        
        # Check description/keywords for spatial indicators
        description = (resource.get('description') or '').lower()
        keywords = resource.get('tags', [])
        
        spatial_indicators = ['geo', 'spatial', 'geometry', 'coordinate', 'latitude', 'longitude']
        
        if any(indicator in description.lower() for indicator in spatial_indicators):
            return True
        
        if any(any(indicator in tag.get('name', '').lower() for indicator in spatial_indicators) 
               for tag in keywords):
            return True
        
        return False

    def _get_resource_features(self, resource: Dict[str, Any], 
                              startindex: int = 0, limit: int = None) -> List[Dict[str, Any]]:
        """
        Get features from a resource
        
        Args:
            resource: Resource dictionary
            startindex: Starting index
            limit: Maximum number of features
            
        Returns:
            List of features
        """
        try:
            # Try to get data from datastore first
            LOGGER.info(f"Resource '{resource.get('name', 'unnamed')}' datastore_active: {resource.get('datastore_active')}")
            
            # Only use datastore if it's actually active
            if resource.get('datastore_active') is True:
                LOGGER.info(f"Using datastore for resource '{resource.get('name', 'unnamed')}'")
                return self._get_datastore_features(resource, startindex, limit)
            
            # Fallback to direct URL for non-datastore resources (ZIP, files, etc.)
            LOGGER.info(f"Using URL fallback for resource '{resource.get('name', 'unnamed')}' (datastore_active={resource.get('datastore_active')})")
            return self._get_url_features(resource, startindex, limit)
            
        except Exception as e:
            LOGGER.error(f"Error getting features from resource: {e}")
            return []

    def _get_datastore_features(self, resource: Dict[str, Any], 
                              startindex: int = 0, limit: int = None) -> List[Dict[str, Any]]:
        """
        Get features from CKAN datastore
        
        Args:
            resource: Resource dictionary
            startindex: Starting index
            limit: Maximum number of features (None = fetch all)
            
        Returns:
            List of features
        """
        try:
            all_records = []
            offset = startindex
            batch_size = 32000  # CKAN's maximum per request
            
            while True:
                url = f"{self.ckan_url}/api/action/datastore_search"
                params = {
                    'resource_id': resource['id'],
                    'offset': offset,
                    'limit': batch_size
                }
                
                headers = {}
                if self.api_key:
                    headers['Authorization'] = self.api_key
                
                response = self.session.get(url, params=params, headers=headers, timeout=60)
                response.raise_for_status()
                
                result = response.json()
                if result.get('success'):
                    records = result.get('result', {}).get('records', [])
                    total = result.get('result', {}).get('total', len(records))
                    
                    if not records:
                        break
                    
                    all_records.extend(records)
                    
                    # If we got fewer records than requested, we've reached the end
                    if len(records) < batch_size:
                        break
                    
                    # If limit is specified and we've reached it, stop
                    if limit and len(all_records) >= limit:
                        all_records = all_records[:limit]
                        break
                    
                    # If we've fetched all available records
                    if len(all_records) >= total:
                        break
                    
                    offset += batch_size
                else:
                    LOGGER.error(f"Datastore API error: {result.get('error')}")
                    break
            
            # Convert records to features
            return self._convert_records_to_features(all_records, resource)
                
        except Exception as e:
            LOGGER.error(f"Error fetching from datastore: {e}")
            return []

    def _get_url_features(self, resource: Dict[str, Any], 
                          startindex: int = 0, limit: int = None) -> List[Dict[str, Any]]:
        """
        Get features from resource URL
        
        Args:
            resource: Resource dictionary
            startindex: Starting index
            limit: Maximum number of features
            
        Returns:
            List of features
        """
        try:
            # Use local CKAN URL instead of external URL
            url = self._get_local_resource_url(resource)
            if not url:
                LOGGER.warning(f"No URL found for resource '{resource.get('name', 'unnamed')}' (id: {resource.get('id')})")
                return []
            
            LOGGER.info(f"Fetching resource from URL: {url}")
            response = self.session.get(url, timeout=60)  # Increased timeout for large ZIP files
            response.raise_for_status()
            LOGGER.info(f"Successfully fetched resource, size: {len(response.content)} bytes, Content-Type: {response.headers.get('Content-Type', 'unknown')}")
            
            # Parse based on format - normalize IANA media types
            format_raw = resource.get('format') or ''
            format_ = format_raw.upper()
            format_lower = format_raw.lower()
            
            # Normalize IANA media types (e.g., "application/zip" -> "ZIP")
            if 'application/zip' in format_lower or 'zip' in format_lower:
                format_ = 'ZIP'
            elif 'application/json' in format_lower and 'geo' in format_lower:
                format_ = 'GEOJSON'
            elif 'application/vnd.google-earth' in format_lower:
                format_ = 'KML'
            elif 'text/csv' in format_lower or 'csv' in format_lower:
                format_ = 'CSV'
            elif '/' in format_raw:
                # Remove IANA prefix if present
                format_ = format_raw.split('/')[-1].upper()
            
            content_type = response.headers.get('Content-Type', '').lower()
            
            # Check if response is ZIP (either by format or Content-Type)
            is_zip = format_ in ['SHP', 'SHAPEFILE', 'ZIP'] or 'zip' in content_type or url.lower().endswith('.zip')
            
            if format_ == 'GEOJSON':
                data = response.json()
                if data.get('type') == 'FeatureCollection':
                    features = data.get('features', [])
                else:
                    features = [data]
            elif format_ == 'CSV':
                features = self._parse_csv_features(response.text)
            elif is_zip:
                # Handle Shapefile ZIP files
                if HAS_FIONA:
                    LOGGER.info(f"Detected ZIP file (format: {format_}, Content-Type: {content_type}), parsing as Shapefile")
                    LOGGER.info(f"ZIP file size: {len(response.content)} bytes")
                    features = self._parse_shapefile_zip(response.content, format_)
                    LOGGER.info(f"Parsed {len(features)} features from ZIP file")
                    if len(features) == 0:
                        LOGGER.warning(f"No features extracted from ZIP file. Check if fiona can read the Shapefile inside the ZIP.")
                else:
                    LOGGER.warning("fiona not available, cannot parse Shapefile. Install fiona to enable ZIP/Shapefile support.")
                    LOGGER.warning(f"Resource URL: {url}, Format: {format_}")
                    return []
            else:
                # Default: try to parse as JSON
                try:
                    data = response.json()
                    if isinstance(data, list):
                        features = data
                    else:
                        features = [data]
                except Exception:
                    LOGGER.warning(f"Unsupported format: {format_}")
                    return []
            
            # Apply pagination only if limit is specified
            if limit:
                features = features[startindex:startindex + limit]
            else:
                features = features[startindex:]
            
            return features
            
        except Exception as e:
            LOGGER.error(f"Error fetching from URL: {e}")
            return []

    def _parse_shapefile_zip(self, zip_content: bytes, format_: str) -> List[Dict[str, Any]]:
        """
        Parse Shapefile from ZIP archive
        
        Args:
            zip_content: ZIP file content as bytes
            format_: File format (SHP, SHAPEFILE, ZIP)
            
        Returns:
            List of GeoJSON features
        """
        if not HAS_FIONA:
            LOGGER.error("fiona is required to parse Shapefiles")
            return []
        
        features = []
        temp_dir = None
        
        try:
            # Create temporary directory for extraction
            temp_dir = tempfile.mkdtemp()
            LOGGER.info(f"Extracting Shapefile to temporary directory: {temp_dir}")
            
            # Write ZIP content to temporary file
            zip_path = os.path.join(temp_dir, 'shapefile.zip')
            with open(zip_path, 'wb') as f:
                f.write(zip_content)
            
            # Open ZIP file and find Shapefile components
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # List all files in ZIP
                file_list = zip_ref.namelist()
                LOGGER.debug(f"Files in ZIP: {file_list}")
                
                # Find .shp file
                shp_files = [f for f in file_list if f.lower().endswith('.shp')]
                if not shp_files:
                    LOGGER.warning("No .shp file found in ZIP archive")
                    return []
                
                # Extract all files to temp directory
                zip_ref.extractall(temp_dir)
                
                # Use fiona to read Shapefile
                # Find the base name (without .shp extension)
                shp_file = shp_files[0]
                base_name = os.path.splitext(shp_file)[0]
                shp_path = os.path.join(temp_dir, shp_file)
                
                LOGGER.info(f"Reading Shapefile: {shp_path}")
                
                # Open Shapefile with fiona
                with fiona.open(shp_path, 'r') as shp:
                    LOGGER.info(f"Shapefile CRS: {shp.crs}")
                    LOGGER.info(f"Shapefile schema: {shp.schema}")
                    LOGGER.info(f"Number of features: {len(shp)}")
                    
                    # Determine source CRS from Shapefile metadata
                    source_crs = None
                    if shp.crs:
                        # fiona returns CRS as dict or string
                        if isinstance(shp.crs, dict):
                            # Try to extract EPSG code
                            init = shp.crs.get('init', '')
                            if 'epsg:2154' in init.lower():
                                source_crs = 'EPSG:2154'
                            elif 'epsg:4326' in init.lower() or 'epsg:3857' in init.lower():
                                source_crs = 'EPSG:4326'  # Already in WGS84
                        elif isinstance(shp.crs, str):
                            if '2154' in shp.crs or 'lambert' in shp.crs.lower():
                                source_crs = 'EPSG:2154'
                            elif '4326' in shp.crs or 'wgs84' in shp.crs.lower():
                                source_crs = 'EPSG:4326'
                    
                    for idx, feature in enumerate(shp):
                        # Convert fiona feature to GeoJSON feature
                        # Preserve UTF-8 encoding for properties
                        properties = {}
                        for key, value in feature.get('properties', {}).items():
                            if isinstance(value, str):
                                # Ensure UTF-8 encoding is preserved
                                properties[key] = value
                            else:
                                properties[key] = value
                        
                        # Convert fiona geometry to GeoJSON dict (fiona.model.Geometry is not JSON serializable)
                        geometry = feature.get('geometry')
                        if geometry is not None:
                            # If it's a fiona.model.Geometry object, convert to dict
                            # Try multiple conversion methods to ensure we get a plain dict
                            if isinstance(geometry, dict):
                                # Already a dict, ensure it's a plain dict (not OrderedDict or similar)
                                geometry = dict(geometry)
                            elif hasattr(geometry, '__geo_interface__'):
                                # Use __geo_interface__ protocol
                                try:
                                    geo_dict = geometry.__geo_interface__
                                    # Ensure it's a plain dict, not another fiona object
                                    if isinstance(geo_dict, dict):
                                        geometry = dict(geo_dict)
                                    else:
                                        # If __geo_interface__ returns something else, try shapely
                                        raise ValueError("__geo_interface__ did not return a dict")
                                except Exception:
                                    # Fall through to shapely conversion
                                    if HAS_SHAPELY:
                                        try:
                                            from shapely.geometry import shape
                                            geom_obj = shape(geometry)
                                            geometry = dict(mapping(geom_obj))
                                        except Exception as e:
                                            LOGGER.warning(f"Could not convert geometry via __geo_interface__ or shapely: {e}")
                                            geometry = None
                                    else:
                                        LOGGER.warning("Geometry is not a dict and shapely is not available, skipping geometry")
                                        geometry = None
                            elif HAS_SHAPELY:
                                # Try to convert using shapely mapping
                                try:
                                    from shapely.geometry import shape
                                    geom_obj = shape(geometry)
                                    geometry = dict(mapping(geom_obj))
                                except Exception as e:
                                    LOGGER.warning(f"Could not convert geometry using shapely: {e}")
                                    geometry = None
                            else:
                                LOGGER.warning("Geometry is not a dict and shapely is not available, skipping geometry")
                                geometry = None
                            
                            # Final check: ensure geometry is a dict with required keys
                            if geometry and not isinstance(geometry, dict):
                                LOGGER.warning(f"Geometry conversion failed, got type {type(geometry)} instead of dict")
                                geometry = None
                            elif geometry and ('type' not in geometry or 'coordinates' not in geometry):
                                LOGGER.warning("Geometry dict missing 'type' or 'coordinates' keys")
                                geometry = None
                        
                        geojson_feature = {
                            'type': 'Feature',
                            'id': str(feature.get('id', idx)),
                            'properties': properties,
                            'geometry': geometry
                        }
                        
                        # Transform geometry if needed (from Lambert-93 to WGS84)
                        # Ensure geometry is a dict before transformation
                        if geojson_feature['geometry']:
                            geom = geojson_feature['geometry']
                            # If geometry is still a fiona.model.Geometry object, convert it
                            if not isinstance(geom, dict):
                                if hasattr(geom, '__geo_interface__'):
                                    geom = geom.__geo_interface__
                                elif HAS_SHAPELY:
                                    try:
                                        from shapely.geometry import shape
                                        geom_obj = shape(geom)
                                        geom = mapping(geom_obj)
                                    except Exception as e:
                                        LOGGER.warning(f"Could not convert geometry for transformation: {e}")
                                        geom = None
                                else:
                                    LOGGER.warning("Geometry is not a dict and shapely is not available, skipping transformation")
                                    geom = None
                            
                            # Update geojson_feature with converted geometry
                            if geom:
                                geojson_feature['geometry'] = geom
                            
                            if geom and HAS_PYPROJ:
                                geom_type = geom.get('type')
                                coords = geom.get('coordinates')
                                
                                if coords:
                                    # Use CRS from Shapefile if available, otherwise detect from coordinates
                                    needs_transform = False
                                    
                                    if source_crs == 'EPSG:2154':
                                        needs_transform = True
                                        LOGGER.debug("CRS detected as EPSG:2154 (Lambert-93), transforming to WGS84")
                                    elif source_crs == 'EPSG:4326':
                                        needs_transform = False
                                        LOGGER.debug("CRS detected as EPSG:4326 (WGS84), no transformation needed")
                                    else:
                                        # Fallback: detect from coordinate values
                                        def is_lambert93(x, y):
                                            return (600000 <= x <= 1200000) and (6000000 <= y <= 7200000)
                                        
                                        # Check first coordinate to determine if transformation is needed
                                        first_coord = coords
                                        if geom_type in ['LineString', 'MultiPoint']:
                                            first_coord = coords[0] if coords else None
                                        elif geom_type in ['Polygon', 'MultiLineString']:
                                            first_coord = coords[0][0] if coords and coords[0] else None
                                        elif geom_type == 'MultiPolygon':
                                            first_coord = coords[0][0][0] if coords and coords[0] and coords[0][0] else None
                                        
                                        if first_coord and len(first_coord) >= 2:
                                            x, y = first_coord[0], first_coord[1]
                                            needs_transform = is_lambert93(x, y)
                                            if needs_transform:
                                                LOGGER.debug(f"Coordinates detected as Lambert-93 ({x}, {y}), transforming to WGS84")
                                    
                                    if needs_transform:
                                        # Create transformer once for efficiency
                                        transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
                                        
                                        def is_lambert93(x, y):
                                            return (600000 <= x <= 1200000) and (6000000 <= y <= 7200000)
                                        
                                        def transform_coords(coords_array, geom_type):
                                            """Recursively transform coordinates"""
                                            if geom_type == 'Point':
                                                if len(coords_array) >= 2:
                                                    x, y = coords_array[0], coords_array[1]
                                                    if is_lambert93(x, y):
                                                        lon, lat = transformer.transform(x, y)
                                                        LOGGER.debug(f"Transformed Point: ({x:.2f}, {y:.2f}) → ({lon:.6f}, {lat:.6f})")
                                                        return [lon, lat] + (coords_array[2:] if len(coords_array) > 2 else [])
                                                return coords_array
                                            elif geom_type in ['LineString', 'MultiPoint']:
                                                return [transform_coords(coord, 'Point') for coord in coords_array]
                                            elif geom_type in ['Polygon', 'MultiLineString']:
                                                return [transform_coords(ring, 'LineString') for ring in coords_array]
                                            elif geom_type == 'MultiPolygon':
                                                return [transform_coords(poly, 'Polygon') for poly in coords_array]
                                            return coords_array
                                        
                                        transformed_coords = transform_coords(coords, geom_type)
                                        geojson_feature['geometry']['coordinates'] = transformed_coords
                                        LOGGER.debug(f"Transformed {geom_type} geometry from Lambert-93 to WGS84")
                        
                        features.append(geojson_feature)
                        
                        # Limit features for memory management (can be adjusted)
                        if len(features) >= 100000:
                            LOGGER.warning(f"Limiting features to 100000 for memory management")
                            break
                
                LOGGER.info(f"Successfully parsed {len(features)} features from Shapefile")
                
        except zipfile.BadZipFile:
            LOGGER.error("Invalid ZIP file")
            return []
        except Exception as e:
            LOGGER.error(f"Error parsing Shapefile: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            return []
        finally:
            # Clean up temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                    LOGGER.debug(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    LOGGER.warning(f"Could not clean up temporary directory: {e}")
        
        return features

    def _convert_records_to_features(self, records: List[Dict[str, Any]], 
                                   resource: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convert datastore records to GeoJSON features
        
        Args:
            records: List of records from datastore
            resource: Resource dictionary
            
        Returns:
            List of GeoJSON features
        """
        features = []
        
        for record in records:
            feature = {
                'type': 'Feature',
                'id': str(record.get('_id', record.get('id', ''))),
                'properties': {},
                'geometry': None
            }
            
            # Extract properties (exclude internal fields)
            # Preserve UTF-8 encoding for all string values
            for key, value in record.items():
                if not key.startswith('_') and key not in ['id', 'geom', 'geometry', 'st_asgeojson', 'wkt', 'geojson', 'the_geom', 'geometry_coordinates', 'geometry_type', 'x', 'y', 'X', 'Y', 'coord_x', 'coord_y', 'x_l93', 'y_l93', 'lambert_x', 'lambert_y', 'wgs84_x', 'wgs84_y', 'wgs84_lon', 'wgs84_lat', 'longitude', 'latitude', 'lon', 'lat', 'lng']:
                    # Ensure UTF-8 encoding is preserved for strings
                    if isinstance(value, bytes):
                        try:
                            feature['properties'][key] = value.decode('utf-8')
                        except UnicodeDecodeError:
                            feature['properties'][key] = value.decode('utf-8', errors='replace')
                    elif isinstance(value, str):
                        feature['properties'][key] = value
                    else:
                        feature['properties'][key] = value
            
            # Extract geometry if present (try multiple common field names)
            geometry_fields = ['geom', 'geometry', 'st_asgeojson', 'wkt', 'geojson', 'the_geom']
            geometry_found = False
            
            for field in geometry_fields:
                if field in record and record[field]:
                    feature['geometry'] = self._parse_geometry(record[field])
                    if feature['geometry']:
                        geometry_found = True
                        LOGGER.debug(f"Extracted geometry from field '{field}'")
                        break
            
            # If no geometry found, try coordinate pairs (French data often has separate X/Y fields)
            if not geometry_found:
                geometry = self._extract_geometry_from_coordinates(record)
                if geometry:
                    feature['geometry'] = geometry
                    geometry_found = True
                    LOGGER.debug("Extracted geometry from coordinate fields")
            
            # Fallback to legacy format
            if not geometry_found and 'geometry_coordinates' in record and 'geometry_type' in record:
                # Handle CKAN geometry format
                geom_type = record.get('geometry_type', 'Point')
                geom_coords = record.get('geometry_coordinates')
                
                if geom_coords:
                    try:
                        # Parse coordinates string
                        if isinstance(geom_coords, str):
                            # Try to parse as JSON
                            if geom_coords.startswith('['):
                                coords = json.loads(geom_coords)
                            else:
                                # Try to parse as WKT
                                coords = self._parse_wkt_coordinates(geom_coords)
                        else:
                            coords = geom_coords
                        
                        if coords:
                            LOGGER.info(f"Found coordinates for {geom_type}: {coords[:2]}... (showing first 2)")
                            # Transform coordinates if needed (Lambert-93 to WGS84)
                            transformed_coords = self._transform_coordinates(coords, geom_type)
                            LOGGER.info(f"Transformed coordinates: {transformed_coords[:2]}... (showing first 2)")
                            feature['geometry'] = {
                                'type': geom_type,
                                'coordinates': transformed_coords
                            }
                    except Exception as e:
                        LOGGER.warning(f"Could not parse geometry coordinates: {e}")
            
            features.append(feature)
        
        return features

    def _parse_geometry(self, geom_str: str) -> Optional[Dict[str, Any]]:
        """
        Parse geometry string to GeoJSON geometry with coordinate transformation
        
        Args:
            geom_str: Geometry string (WKT, GeoJSON, etc.)
            
        Returns:
            GeoJSON geometry or None
        """
        try:
            # Try to parse as JSON first
            if geom_str.startswith('{'):
                geom = json.loads(geom_str)
                # Transform coordinates if they appear to be in Lambert-93
                if geom and 'coordinates' in geom:
                    geom['coordinates'] = self._transform_coordinates_if_needed(geom['coordinates'], geom.get('type', 'Point'))
                return geom
            
            # Try to parse as WKT (simplified)
            if geom_str.startswith('POINT'):
                coords = self._parse_wkt_point(geom_str)
                if coords:
                    # Transform coordinates if needed
                    transformed_coords = self._transform_coordinates_if_needed(coords, 'Point')
                    return {
                        'type': 'Point',
                        'coordinates': transformed_coords
                    }
            
            # Add more WKT parsing as needed
            
            return None
            
        except Exception as e:
            LOGGER.warning(f"Could not parse geometry: {e}")
            return None

    def _transform_coordinates_if_needed(self, coords, geom_type: str):
        """
        Transform coordinates from Lambert-93 to WGS84 if needed
        
        Args:
            coords: Coordinate array
            geom_type: Geometry type
            
        Returns:
            Transformed coordinates
        """
        if not HAS_PYPROJ:
            LOGGER.warning("pyproj not available, coordinate transformation disabled")
            return coords
            
        try:
            # Detect if coordinates are in Lambert-93 (EPSG:2154)
            # Lambert-93 coordinates are typically: X ~= 600000-1200000, Y ~= 6000000-7200000
            def is_lambert93(x, y):
                return (600000 <= x <= 1200000) and (6000000 <= y <= 7200000)
            
            def transform_coord_pair(coord_pair):
                if len(coord_pair) >= 2:
                    x, y = coord_pair[0], coord_pair[1]
                    if is_lambert93(x, y):
                        # Transform from Lambert-93 (EPSG:2154) to WGS84 (EPSG:4326)
                        transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
                        lon, lat = transformer.transform(x, y)
                        LOGGER.info(f"Transformed Lambert-93 ({x}, {y}) → WGS84 ({lon:.6f}, {lat:.6f})")
                        return [lon, lat] + coord_pair[2:]  # Keep any Z coordinate
                return coord_pair
            
            if geom_type == 'Point':
                return transform_coord_pair(coords)
            elif geom_type == 'MultiPoint':
                return [transform_coord_pair(point) for point in coords]
            elif geom_type in ['LineString', 'MultiLineString']:
                if geom_type == 'LineString':
                    return [transform_coord_pair(coord) for coord in coords]
                else:
                    return [[transform_coord_pair(coord) for coord in line] for line in coords]
            elif geom_type in ['Polygon', 'MultiPolygon']:
                if geom_type == 'Polygon':
                    return [[transform_coord_pair(coord) for coord in ring] for ring in coords]
                else:
                    return [[[transform_coord_pair(coord) for coord in ring] for ring in polygon] for polygon in coords]
            
            return coords
            
        except Exception as e:
            LOGGER.warning(f"Error transforming coordinates: {e}")
            return coords

    def _extract_geometry_from_coordinates(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract geometry from coordinate fields commonly used in French datasets
        
        Args:
            record: Data record
            
        Returns:
            GeoJSON geometry or None
        """
        try:
            # Common coordinate field patterns in French datasets
            coord_patterns = [
                # X/Y patterns
                ('x', 'y'),
                ('X', 'Y'),
                ('coord_x', 'coord_y'),
                ('longitude', 'latitude'),
                ('lon', 'lat'),
                ('lng', 'lat'),
                # Lambert-93 patterns
                ('x_l93', 'y_l93'),
                ('lambert_x', 'lambert_y'),
                # WGS84 patterns
                ('wgs84_x', 'wgs84_y'),
                ('wgs84_lon', 'wgs84_lat')
            ]
            
            for x_field, y_field in coord_patterns:
                if x_field in record and y_field in record:
                    try:
                        x = float(record[x_field])
                        y = float(record[y_field])
                        
                        # Transform coordinates if needed
                        coords = self._transform_coordinates_if_needed([x, y], 'Point')
                        
                        LOGGER.debug(f"Found coordinates in fields {x_field}/{y_field}: {x}, {y} → {coords}")
                        
                        return {
                            'type': 'Point',
                            'coordinates': coords
                        }
                    except (ValueError, TypeError):
                        continue
            
            # Try geo_point_2d format (lat, lon string)
            if 'geo_point_2d' in record:
                try:
                    geo_str = record['geo_point_2d']
                    if isinstance(geo_str, str) and ',' in geo_str:
                        parts = geo_str.split(',')
                        if len(parts) >= 2:
                            lat, lon = float(parts[0].strip()), float(parts[1].strip())
                            # Note: geo_point_2d is usually lat,lon (WGS84)
                            LOGGER.debug(f"Found geo_point_2d: {lat}, {lon}")
                            return {
                                'type': 'Point',
                                'coordinates': [lon, lat]  # GeoJSON is lon,lat
                            }
                except (ValueError, TypeError):
                    pass
            
            return None
            
        except Exception as e:
            LOGGER.warning(f"Error extracting geometry from coordinates: {e}")
            return None

    def _parse_wkt_point(self, wkt: str) -> Optional[List[float]]:
        """
        Parse WKT POINT to coordinates
        
        Args:
            wkt: WKT string
            
        Returns:
            List of coordinates or None
        """
        try:
            # Simple WKT POINT parsing
            if wkt.startswith('POINT(') and wkt.endswith(')'):
                coords_str = wkt[6:-1]  # Remove 'POINT(' and ')'
                coords = [float(x.strip()) for x in coords_str.split()]
                return coords
            return None
        except Exception:
            return None

    def _parse_wkt_coordinates(self, wkt: str) -> Optional[List[float]]:
        """
        Parse WKT coordinates to list
        
        Args:
            wkt: WKT string
            
        Returns:
            List of coordinates or None
        """
        try:
            # Handle different WKT formats
            if wkt.startswith('POINT('):
                return self._parse_wkt_point(wkt)
            elif wkt.startswith('LINESTRING('):
                # Parse LINESTRING coordinates
                coords_str = wkt[11:-1]  # Remove 'LINESTRING(' and ')'
                coords = []
                for point_str in coords_str.split(','):
                    point_coords = [float(x.strip()) for x in point_str.split()]
                    coords.append(point_coords)
                return coords
            elif wkt.startswith('POLYGON('):
                # Parse POLYGON coordinates
                coords_str = wkt[8:-1]  # Remove 'POLYGON(' and ')'
                coords = []
                for ring_str in coords_str.split('),('):
                    ring_str = ring_str.strip('()')
                    ring_coords = []
                    for point_str in ring_str.split(','):
                        point_coords = [float(x.strip()) for x in point_str.split()]
                        ring_coords.append(point_coords)
                    coords.append(ring_coords)
                return coords
            else:
                # Try to parse as simple coordinate list
                coords = [float(x.strip()) for x in wkt.split(',')]
                return coords
        except Exception as e:
            LOGGER.warning(f"Could not parse WKT coordinates: {e}")
            return None

    def _transform_coordinates(self, coords: List[Any], geom_type: str) -> List[Any]:
        """
        Transform coordinates from Lambert-93 to WGS84 if needed
        
        Args:
            coords: Coordinates to transform
            geom_type: Geometry type
            
        Returns:
            Transformed coordinates
        """
        try:
            # Try to import pyproj for coordinate transformation
            try:
                from pyproj import Transformer
                transformer = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
                LOGGER.info(f"pyproj transformer created successfully")
            except ImportError:
                LOGGER.warning("pyproj not available, using coordinates as-is")
                return coords
            
            def transform_point(x, y):
                """Transform a single point from Lambert-93 to WGS84"""
                try:
                    # Check if coordinates are in Lambert-93 range
                    if 1000000 < x < 2000000 and 6000000 < y < 7000000:
                        LOGGER.info(f"Transforming point: ({x}, {y}) from Lambert-93 to WGS84")
                        lon, lat = transformer.transform(x, y)
                        LOGGER.info(f"Transformed to: ({lon}, {lat})")
                        return [lon, lat]
                    else:
                        LOGGER.info(f"Point ({x}, {y}) not in Lambert-93 range, keeping as-is")
                        return [x, y]
                except Exception as e:
                    LOGGER.warning(f"Error transforming point ({x}, {y}): {e}")
                    return [x, y]
            
            if geom_type == 'Point' and len(coords) == 2:
                return transform_point(coords[0], coords[1])
            elif geom_type == 'Polygon':
                # Transform each ring
                transformed_rings = []
                for ring in coords:
                    transformed_ring = []
                    for point in ring:
                        if len(point) == 2:
                            transformed_point = transform_point(point[0], point[1])
                            transformed_ring.append(transformed_point)
                        else:
                            transformed_ring.append(point)
                    transformed_rings.append(transformed_ring)
                return transformed_rings
            elif geom_type == 'LineString':
                # Transform each point in the line
                transformed_line = []
                for point in coords:
                    if len(point) == 2:
                        transformed_point = transform_point(point[0], point[1])
                        transformed_line.append(transformed_point)
                    else:
                        transformed_line.append(point)
                return transformed_line
            
            return coords
            
        except Exception as e:
            LOGGER.warning(f"Error transforming coordinates: {e}")
            return coords

    def _parse_csv_features(self, csv_content: str) -> List[Dict[str, Any]]:
        """
        Parse CSV content to features
        
        Args:
            csv_content: CSV content as string
            
        Returns:
            List of features
        """
        try:
            lines = csv_content.strip().split('\n')
            if len(lines) < 2:
                return []
            
            # Parse header
            headers = [h.strip() for h in lines[0].split(',')]
            
            features = []
            for line in lines[1:]:
                values = [v.strip() for v in line.split(',')]
                if len(values) == len(headers):
                    feature = {
                        'type': 'Feature',
                        'properties': {},
                        'geometry': None
                    }
                    
                    # Extract properties
                    for i, header in enumerate(headers):
                        if i < len(values):
                            feature['properties'][header] = values[i]
                    
                    # Try to find geometry columns
                    geom_cols = ['geom', 'geometry', 'wkt', 'coordinates']
                    for col in geom_cols:
                        if col in feature['properties']:
                            feature['geometry'] = self._parse_geometry(feature['properties'][col])
                            break
                    
                    features.append(feature)
            
            return features
            
        except Exception as e:
            LOGGER.error(f"Error parsing CSV: {e}")
            return []

    def queryables(self, properties: List[str] = None) -> Dict[str, Any]:
        """
        Get queryables for this provider
        
        Args:
            properties: List of properties to include
            
        Returns:
            Queryables dictionary
        """
        try:
            # Get dataset from CKAN
            dataset_data = self._get_ckan_dataset()
            if not dataset_data:
                return {}
            
            # Get resources
            resources = dataset_data.get('resources', [])
            geo_resources = self._filter_geospatial_resources(resources)
            
            if not geo_resources:
                return {}
            
            # Get datastore schema from first geospatial resource
            primary_resource = geo_resources[0]
            if not primary_resource.get('datastore_active'):
                return {}
            
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': primary_resource['id'],
                'limit': 0  # Just get schema
            }
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = self.api_key
            
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                return {}
            
            fields = result.get('result', {}).get('fields', [])
            
            # Build queryables
            queryables = {
                'type': 'object',
                'properties': {},
                'required': []
            }
            
            for field in fields:
                field_id = field.get('id')
                field_type = field.get('type', 'string')
                
                # Skip internal fields
                if field_id.startswith('_'):
                    continue
                
                # Skip only internal geometry fields (they're handled separately)
                if field_id in ['geom', 'geometry']:
                    continue
                
                # Map CKAN types to JSON Schema types
                if field_type == 'int':
                    json_type = 'integer'
                elif field_type == 'float':
                    json_type = 'number'
                elif field_type == 'bool':
                    json_type = 'boolean'
                else:
                    json_type = 'string'
                
                queryables['properties'][field_id] = {
                    'type': json_type,
                    'title': field_id
                }
            
            # Add geometry field to queryables (required by OGC API Features)
            queryables['properties']['geometry'] = {
                'type': 'object',
                'title': 'Geometry',
                'format': 'geometry-any',
                'x-ogc-role': 'primary-geometry'
            }
            
            return queryables
            
        except Exception as e:
            LOGGER.error(f"Error getting queryables: {e}")
            return {}

    def get_schema(self) -> Dict[str, Any]:
        """
        Get schema for this provider
        
        Returns:
            Schema dictionary
        """
        try:
            # Get dataset from CKAN
            dataset_data = self._get_ckan_dataset()
            if not dataset_data:
                return {}
            
            # Get resources
            resources = dataset_data.get('resources', [])
            geo_resources = self._filter_geospatial_resources(resources)
            
            if not geo_resources:
                return {}
            
            # Get datastore schema from first geospatial resource
            primary_resource = geo_resources[0]
            if not primary_resource.get('datastore_active'):
                return {}
            
            url = f"{self.ckan_url}/api/action/datastore_search"
            params = {
                'resource_id': primary_resource['id'],
                'limit': 0  # Just get schema
            }
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = self.api_key
            
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            if not result.get('success'):
                return {}
            
            fields = result.get('result', {}).get('fields', [])
            
            # Build schema
            schema = {
                'type': 'object',
                'properties': {},
                'required': []
            }
            
            for field in fields:
                field_id = field.get('id')
                field_type = field.get('type', 'string')
                
                # Skip internal fields
                if field_id.startswith('_'):
                    continue
                
                # Map CKAN types to JSON Schema types
                if field_type == 'int':
                    json_type = 'integer'
                elif field_type == 'float':
                    json_type = 'number'
                elif field_type == 'bool':
                    json_type = 'boolean'
                else:
                    json_type = 'string'
                
                schema['properties'][field_id] = {
                    'type': json_type,
                    'title': field_id
                }
            
            return schema
            
        except Exception as e:
            LOGGER.error(f"Error getting schema: {e}")
            return {}

    def _filter_by_bbox(self, features: List[Dict[str, Any]], 
                        bbox: List[float]) -> List[Dict[str, Any]]:
        """
        Filter features by bounding box
        
        Args:
            features: List of features
            bbox: Bounding box [minx, miny, maxx, maxy]
            
        Returns:
            Filtered features
        """
        if not bbox or len(bbox) != 4:
            return features
        
        filtered = []
        for feature in features:
            if self._feature_in_bbox(feature, bbox):
                filtered.append(feature)
        
        return filtered

    def _feature_in_bbox(self, feature: Dict[str, Any], bbox: List[float]) -> bool:
        """
        Check if feature is within bounding box
        
        Args:
            feature: Feature dictionary
            bbox: Bounding box [minx, miny, maxx, maxy]
            
        Returns:
            True if feature is in bbox
        """
        geometry = feature.get('geometry')
        if not geometry:
            return False
        
        # Simple bbox check for Point geometries
        if geometry.get('type') == 'Point':
            coords = geometry.get('coordinates', [])
            if len(coords) >= 2:
                x, y = coords[0], coords[1]
                return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]
        
        # For other geometry types, assume they're in bbox for now
        # In production, implement proper spatial intersection
        return True

    def _filter_by_datetime(self, features: List[Dict[str, Any]], 
                           datetime_: str) -> List[Dict[str, Any]]:
        """
        Filter features by datetime
        
        Args:
            features: List of features
            datetime_: Datetime filter string
            
        Returns:
            Filtered features
        """
        # TODO: Implement datetime filtering
        # For now, return all features
        return features

    def _filter_by_text(self, features: List[Dict[str, Any]], 
                       q: str) -> List[Dict[str, Any]]:
        """
        Filter features by text query
        
        Args:
            features: List of features
            q: Text query
            
        Returns:
            Filtered features
        """
        if not q:
            return features
        
        q_lower = q.lower()
        filtered = []
        
        for feature in features:
            properties = feature.get('properties', {})
            
            # Search in properties
            for key, value in properties.items():
                if isinstance(value, str) and q_lower in value.lower():
                    filtered.append(feature)
                    break
        
        return filtered

    def __repr__(self):
        return f'<CKANProvider> {self.dataset_id}'
    
    def get_coverage_domainset(self, *args, **kwargs):
        """
        Get coverage domainset
        
        Returns:
            Coverage domainset
        """
        raise ProviderTypeError('Coverage not supported')
    
    def get_coverage_rangetype(self, *args, **kwargs):
        """
        Get coverage rangetype
        
        Returns:
            Coverage rangetype
        """
        raise ProviderTypeError('Coverage not supported')
    
    def get_crs(self) -> str:
        """
        Get CRS for this provider
        
        Returns:
            CRS URI
        """
        return self.crs
    
    def _get_local_resource_url(self, resource: Dict[str, Any]) -> Optional[str]:
        """
        Get local CKAN URL for a resource
        
        Args:
            resource: Resource dictionary
            
        Returns:
            Local URL or None
        """
        try:
            # Try to get direct resource URL first (most reliable)
            resource_url = resource.get('url')
            if resource_url and resource_url.startswith('http'):
                # If it's already a full URL, use it if it's internal
                if self.ckan_url.replace('http://', '').replace('https://', '') in resource_url:
                    LOGGER.info(f"Using direct resource URL: {resource_url}")
                    return resource_url
            
            # Otherwise, build download URL
            resource_id = resource.get('id')
            if not resource_id:
                return None
            
            # Get dataset name from CKAN API
            dataset_data = self._get_ckan_dataset()
            dataset_name = dataset_data.get('name', self.dataset_id) if dataset_data else self.dataset_id
            
            # Use simple download URL without filename/format (CKAN handles it)
            local_url = f"{self.ckan_url}/dataset/{dataset_name}/resource/{resource_id}/download"
            
            LOGGER.info(f"Local resource URL: {local_url}")
            return local_url
            
        except Exception as e:
            LOGGER.error(f"Error building local URL: {e}")
            return None
    
    def _parse_csv_features(self, csv_text: str) -> List[Dict[str, Any]]:
        """
        Parse CSV to GeoJSON features
        
        Args:
            csv_text: CSV content
            
        Returns:
            List of GeoJSON features
        """
        try:
            import csv
            from io import StringIO
            
            features = []
            # Ensure CSV is read as UTF-8
            if isinstance(csv_text, bytes):
                csv_text = csv_text.decode('utf-8')
            
            reader = csv.DictReader(StringIO(csv_text))
            
            for row in reader:
                feature = {
                    'type': 'Feature',
                    'id': str(row.get('id', len(features))),
                    'properties': {},
                    'geometry': None
                }
                
                # Extract coordinates if present
                lat = row.get('latitude') or row.get('lat') or row.get('y')
                lon = row.get('longitude') or row.get('lon') or row.get('x')
                
                if lat and lon:
                    try:
                        feature['geometry'] = {
                            'type': 'Point',
                            'coordinates': [float(lon), float(lat)]  # GeoJSON: [lon, lat]
                        }
                    except ValueError:
                        pass
                
                # Add all other fields as properties (preserve UTF-8)
                for key, value in row.items():
                    if key.lower() not in ['latitude', 'longitude', 'lat', 'lon', 'x', 'y']:
                        # Ensure UTF-8 encoding is preserved
                        if isinstance(value, bytes):
                            try:
                                feature['properties'][key] = value.decode('utf-8')
                            except UnicodeDecodeError:
                                feature['properties'][key] = value.decode('utf-8', errors='replace')
                        else:
                            feature['properties'][key] = value
                
                features.append(feature)
            
            return features
            
        except Exception as e:
            LOGGER.error(f"Error parsing CSV: {e}")
            return []



    def _initialize_metadata(self):
        """
        Initialize queryables and schema from CKAN dataset
        """
        try:
            # Get dataset metadata
            dataset = self._get_ckan_dataset()
            if not dataset:
                return
            
            # Get first resource to analyze schema
            resources = dataset.get("resources", [])
            if not resources:
                return
            
            # Find a geospatial resource with datastore active (prioritize datastore resources)
            datastore_resource = None
            fallback_resource = None
            
            for resource in resources:
                format_lower = resource.get("format", "").lower()
                if format_lower in ["geojson", "json", "csv"]:
                    # Prioritize resources with active datastore
                    if resource.get("datastore_active", False):
                        datastore_resource = resource
                        break
                    elif fallback_resource is None:
                        fallback_resource = resource
            
            # Use datastore resource if available, otherwise fallback
            target_resource = datastore_resource or fallback_resource
            if target_resource:
                LOGGER.info(f"Using resource: {target_resource.get('name')} (datastore_active: {target_resource.get('datastore_active', False)})")
                self._populate_schema_from_resource(target_resource)
                    
        except Exception as e:
            LOGGER.error(f"Error initializing metadata: {e}")

    def _populate_schema_from_resource(self, resource):
        """
        Populate schema and queryables from a CKAN resource
        """
        try:
            # Check if resource has datastore active
            if not resource.get("datastore_active", False):
                LOGGER.warning(f"Resource {resource.get('name')} does not have active datastore, skipping schema population")
                return
            
            # Get datastore schema
            url = f"{self.ckan_url}/api/action/datastore_search"
            headers = {}
            if self.api_key:
                headers['Authorization'] = self.api_key
            
            response = self.session.get(url, 
                                  params={
                                      "resource_id": resource["id"],
                                      "limit": 0  # Just get schema
                                  },
                                  headers=headers,
                                  timeout=10)
            
            if response.status_code == 200:
                result = response.json()["result"]
                fields = result.get("fields", [])
                
                # Build schema and queryables
                properties = {}
                queryables = {}
                
                for field in fields:
                    field_name = field.get("id", "")
                    field_type = field.get("type", "text")
                    
                    # Convert PostgreSQL types to JSON Schema types
                    json_type = self._pg_type_to_json_type(field_type)
                    
                    properties[field_name] = {
                        "type": json_type,
                        "title": field_name.replace("_", " ").title()
                    }
                    
                    # Add to queryables (searchable fields)
                    queryables[field_name] = {
                        "type": json_type,
                        "title": field_name.replace("_", " ").title()
                    }
                
                # Update schema
                self.schema = {
                    "type": "object",
                    "properties": properties
                }
                
                # Add geometry field to queryables (required by OGC API Features)
                # Geometry is always queryable in OGC API Features
                queryables['geometry'] = {
                    'type': 'object',
                    'title': 'Geometry'
                }
                
                # Update queryables and fields (pygeoapi uses fields for queryables)
                self.queryables = queryables
                self._fields = queryables  # pygeoapi expects _fields attribute
                
                # Also set fields as a property for pygeoapi compatibility
                # pygeoapi accesses p.fields directly, so we need both _fields and fields
                
                LOGGER.info(f"Schema populated with {len(properties)} fields")
                LOGGER.info(f"Queryables populated with {len(queryables)} fields (including geometry)")
                
        except Exception as e:
            LOGGER.error(f"Error populating schema: {e}")

    def _pg_type_to_json_type(self, pg_type):
        """
        Convert PostgreSQL type to JSON Schema type
        """
        type_mapping = {
            "text": "string",
            "varchar": "string", 
            "char": "string",
            "integer": "integer",
            "int": "integer",
            "int4": "integer",
            "int8": "integer",
            "bigint": "integer",
            "smallint": "integer",
            "numeric": "number",
            "decimal": "number",
            "float": "number",
            "float4": "number",
            "float8": "number",
            "double": "number",
            "boolean": "boolean",
            "bool": "boolean",
            "timestamp": "string",
            "timestamptz": "string",
            "date": "string",
            "time": "string",
            "json": "object",
            "jsonb": "object"
        }
        
        return type_mapping.get(pg_type.lower(), "string")


    def get_schema(self):
        """
        Get the schema for this collection
        """
        return self.schema

    def get_queryables(self):
        """
        Get the queryables for this collection
        """
        return self.queryables

    @property
    def fields(self):
        """
        Get the fields for this collection (used by pygeoapi for queryables)
        Returns a dictionary where keys are field names and values are field definitions
        """
        # Return _fields if populated, otherwise return empty dict
        if hasattr(self, '_fields') and self._fields:
            return self._fields
        # Fallback: try to get from queryables
        if hasattr(self, 'queryables') and self.queryables:
            return self.queryables
        return {}
    
    def get_fields(self):
        """
        Get the fields for this collection (legacy method)
        """
        return self.fields
    
    def get_limits(self):
        """
        Get the limits for this provider
        """
        return self.limits
