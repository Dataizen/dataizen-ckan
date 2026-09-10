"""
WMS Provider for pygeoapi with Pillow/matplotlib rendering

This provider generates PNG map images from CKAN geospatial datasets
using Pillow and matplotlib for rendering.
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

# Try to import pyproj for coordinate transformation
try:
    import pyproj
    HAS_PYPROJ = True
except ImportError:
    HAS_PYPROJ = False

LOGGER = logging.getLogger(__name__)


class WMSProvider(BaseProvider):
    """WMS Provider for pygeoapi with Pillow/matplotlib rendering"""

    def __init__(self, provider_def: Dict[str, Any]):
        """
        Initialize WMS Provider
        
        Args:
            provider_def: Provider definition from configuration
        """
        super().__init__(provider_def)
        
        # Provider type
        self.type = 'map'
        
        # CKAN configuration from options
        options = provider_def.get('options', {})
        self.ckan_url = options.get('ckan_url', 'http://localhost:5000')
        self.api_key = options.get('api_key', '')
        self.dataset_id = options.get('dataset_id', '')
        
        # Map rendering options
        self.default_width = options.get('width', 512)
        self.default_height = options.get('height', 512)
        self.default_format = options.get('format', 'image/png')
        
        # Validate configuration
        if not self.ckan_url:
            raise ValueError("ckan_url is required for WMS provider")
        if not self.dataset_id:
            raise ValueError("dataset_id is required for WMS provider")
            
        LOGGER.info(f"WMS Provider initialized for dataset: {self.dataset_id}")
        LOGGER.info(f"CKAN URL: {self.ckan_url}")
        
        # Default CRS and extent
        self.crs = 'EPSG:4326'
        self.default_bbox = [-180, -90, 180, 90]  # World extent
        
        # Required attributes for pygeoapi
        self.tile_type = 'map'  # Type of tiles (map, coverage, etc.)
        self.format_type = 'image/png'  # Default format

    def query(self, bbox: List[float] = None, width: int = 512, height: int = 512,
              crs: str = None, **kwargs) -> bytes:
        """
        Query method called by pygeoapi for /map endpoint
        This method is called by pygeoapi's maps.py:get_collection_map()
        
        Args:
            bbox: Bounding box [minx, miny, maxx, maxy]
            width: Image width in pixels
            height: Image height in pixels
            crs: Coordinate reference system (ignored, we use CRS84)
            **kwargs: Additional parameters (layers, format, etc.)
            
        Returns:
            bytes: Generated map image
        """
        # Log received parameters for debugging
        LOGGER.info(f"WMSProvider.query() called with bbox={bbox}, width={width}, height={height}, kwargs={kwargs}")
        
        # Extract format from kwargs if present
        format_type = kwargs.get('f') or kwargs.get('format') or 'image/png'
        layers = kwargs.get('layers') or kwargs.get('layer')
        
        # Check if bbox is in kwargs (pygeoapi might pass it there instead)
        if bbox is None:
            # Try to get bbox from kwargs (pygeoapi might pass it as a string)
            bbox_str = kwargs.get('bbox')
            if bbox_str:
                try:
                    if isinstance(bbox_str, str):
                        bbox_parts = bbox_str.split(',')
                        if len(bbox_parts) >= 4:
                            bbox = [float(bbox_parts[0]), float(bbox_parts[1]), 
                                   float(bbox_parts[2]), float(bbox_parts[3])]
                            LOGGER.info(f"Parsed bbox from kwargs string: {bbox}")
                    elif isinstance(bbox_str, list):
                        bbox = bbox_str
                        LOGGER.info(f"Using bbox from kwargs list: {bbox}")
                except (ValueError, TypeError) as e:
                    LOGGER.warning(f"Could not parse bbox from kwargs: {e}")
        
        # Use provided bbox or default
        final_bbox = bbox if bbox is not None else self.default_bbox
        if bbox is None:
            LOGGER.warning(f"No bbox provided, using default: {final_bbox}")
        
        # Call get_map with the extracted parameters
        return self.get_map(final_bbox, width, height, layers, format_type, **kwargs)

    def get_map(self, bbox: List[float], width: int, height: int, 
                layers: str = None, format_type: str = 'image/png',
                **kwargs) -> bytes:
        """
        Generate a map image for the specified bounding box
        
        Args:
            bbox: Bounding box [minx, miny, maxx, maxy] in WGS84
            width: Image width in pixels
            height: Image height in pixels
            layers: Layer name (dataset_id)
            format_type: Output format (image/png, image/jpeg)
            
        Returns:
            bytes: Generated map image
        """
        try:
            LOGGER.info(f"Generating WMS map for {self.dataset_id}")
            LOGGER.info(f"BBox received: {bbox}, Size: {width}x{height}")
            
            # Validate width and height
            if width is None or height is None:
                LOGGER.error(f"Invalid width or height: width={width}, height={height}")
                return self._create_error_map(width or 512, height or 512, "Invalid width or height")
            
            try:
                width = int(width)
                height = int(height)
            except (ValueError, TypeError) as e:
                LOGGER.error(f"Could not convert width/height to int: width={width}, height={height}, error={e}")
                return self._create_error_map(512, 512, f"Invalid width or height: {width}x{height}")
            
            if width <= 0 or height <= 0:
                LOGGER.error(f"Invalid width or height (must be > 0): width={width}, height={height}")
                return self._create_error_map(512, 512, f"Invalid width or height: {width}x{height}")
            
            if bbox is None or len(bbox) < 4:
                LOGGER.warning("Invalid or missing bbox, using default world extent")
                bbox = self.default_bbox

            # IMPORTANT : on considère que le BBOX est déjà en WGS84 [minx, miny, maxx, maxy]
            # Validate and convert bbox values, filtering out None
            try:
                # Check for None values before conversion
                if any(v is None for v in bbox[:4]):
                    LOGGER.error(f"Bbox contains None values: {bbox}, using default bbox")
                    bbox = self.default_bbox
                else:
                    # Convert to float - all values should be valid at this point
                    minx = float(bbox[0])
                    miny = float(bbox[1])
                    maxx = float(bbox[2])
                    maxy = float(bbox[3])
                    bbox = [minx, miny, maxx, maxy]
            except (ValueError, TypeError) as e:
                LOGGER.error(f"Could not parse bbox {bbox}: {e}, using default bbox")
                bbox = self.default_bbox

            # (ne plus faire d'heuristique sur l'ordre ici !)
            features = self._get_ckan_features(bbox)

            if not features:
                LOGGER.warning(f"No features found for dataset {self.dataset_id}")
                return self._create_empty_map(width, height, bbox)

            return self._render_map(features, bbox, width, height, format_type)

        except Exception as e:
            LOGGER.error(f"Error generating WMS map: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            # Ensure width and height are valid for error map
            try:
                error_width = int(width) if width is not None else 512
                error_height = int(height) if height is not None else 512
            except Exception:
                error_width, error_height = 512, 512
            return self._create_error_map(error_width, error_height, str(e))

    def _get_ckan_features(self, bbox: List[float]) -> List[Dict]:
        """
        Retrieve geospatial features from CKAN datastore
        Uses the same logic as CKANProvider to find geospatial resources
        
        Args:
            bbox: Bounding box to filter features
            
        Returns:
            List of GeoJSON-like features
        """
        try:
            # Get dataset resources
            url = f"{self.ckan_url}/api/action/package_show"
            headers = {'Authorization': self.api_key} if self.api_key else {}
            
            response = requests.get(url, 
                                  params={'id': self.dataset_id},
                                  headers=headers,
                                  timeout=60)  # Increase timeout for large datasets
            
            if response.status_code != 200:
                LOGGER.error(f"Failed to get dataset: {response.status_code}")
                return []
            
            dataset = response.json()['result']
            resources = dataset.get('resources', [])
            
            # Find geospatial resources (same logic as CKANProvider)
            # Priority: datastore_active resources with geometry columns
            geospatial_features = []
            for resource in resources:
                # Check if resource has geospatial data
                if self._is_geospatial_resource(resource):
                    LOGGER.info(f"Found geospatial resource: {resource.get('name', resource.get('id'))}")
                    features = self._get_resource_features(resource['id'], bbox)
                    geospatial_features.extend(features)
                    LOGGER.info(f"Extracted {len(features)} features from resource {resource.get('id')}")
            
            return geospatial_features
            
        except Exception as e:
            LOGGER.error(f"Error getting CKAN features: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            return []
    
    def _is_geospatial_resource(self, resource: Dict[str, Any]) -> bool:
        """
        Check if a resource contains geospatial data
        Uses the same logic as CKANProvider
        
        Args:
            resource: Resource dictionary
            
        Returns:
            True if geospatial, False otherwise
        """
        try:
            # Check format
            format_ = resource.get('format', '').upper()
            if format_ in ['GEOJSON', 'SHAPEFILE', 'KML', 'KMZ', 'GPX']:
                return True
            
            # Check if datastore_active and has geometry columns
            if resource.get('datastore_active'):
                try:
                    url = f"{self.ckan_url}/api/action/datastore_search"
                    params = {'resource_id': resource['id'], 'limit': 0}
                    headers = {'Authorization': self.api_key} if self.api_key else {}
                    response = requests.get(url, params=params, headers=headers, timeout=30)
                    if response.status_code == 200:
                        result = response.json()
                        if result.get('success'):
                            fields = result.get('result', {}).get('fields', [])
                            # Check for geometry columns
                            geom_fields = ['geom', 'geometry', 'the_geom', 'latitude', 'longitude', 'lon', 'lat', 'x', 'y']
                            field_names = [f.get('id', '').lower() for f in fields]
                            has_geom = any(field_name in [g.lower() for g in geom_fields] for field_name in field_names)
                            if has_geom:
                                return True
                except Exception as e:
                    LOGGER.warning(f"Error checking geometry columns: {e}")
            
            return False
            
        except Exception as e:
            LOGGER.warning(f"Error checking if resource is geospatial: {e}")
            return False

    def _get_resource_features(self, resource_id: str, bbox: List[float]) -> List[Dict]:
        """
        Get features from a specific resource
        For WMS, we need ALL features (no limit) so QGIS can display everything
        Uses pagination to fetch all records in batches
        
        Args:
            resource_id: CKAN resource ID
            bbox: Bounding box (used for filtering in _render_map, but we fetch all data)
            
        Returns:
            List of features with coordinates
        """
        try:
            # Try datastore first
            url = f"{self.ckan_url}/api/action/datastore_search"
            headers = {'Authorization': self.api_key} if self.api_key else {}
            
            # For WMS, we need ALL features - no limit
            # QGIS and other WMS clients expect to see all data
            # Use pagination to fetch all records in batches (like CKANProvider)
            all_records = []
            offset = 0
            batch_size = 32000  # CKAN's maximum per request
            
            LOGGER.info(f"Fetching all features from datastore for resource {resource_id} (using pagination)")
            
            while True:
                params = {
                    'resource_id': resource_id,
                    'offset': offset,
                    'limit': batch_size
                }
                
                response = requests.get(url,
                                      params=params,
                                      headers=headers,
                                      timeout=120)  # Increased timeout for large datasets
                
                if response.status_code != 200:
                    LOGGER.error(f"Failed to get datastore data: {response.status_code}")
                    break
                
                data = response.json().get('result', {})
                records = data.get('records', [])
                
                if not records:
                    break
                
                all_records.extend(records)
                LOGGER.info(f"Fetched batch: {len(records)} records (total so far: {len(all_records)})")
                
                # Check if there are more records
                total = data.get('total', len(records))
                if len(all_records) >= total or len(records) < batch_size:
                    break
                
                offset += batch_size
            
            LOGGER.info(f"Retrieved {len(all_records)} total records from datastore for resource {resource_id}")
            
            # Log field names for debugging (from first batch)
            if all_records:
                sample_response = requests.get(url,
                                              params={'resource_id': resource_id, 'limit': 1},
                                              headers=headers,
                                              timeout=10)
                if sample_response.status_code == 200:
                    sample_data = sample_response.json().get('result', {})
                    fields = sample_data.get('fields', [])
                    field_names = [f.get('id') for f in fields]
                    LOGGER.debug(f"Available fields in datastore: {field_names[:20]}")
                
                # Convert records to features
                features = []
                for i, record in enumerate(all_records):
                    feature = self._record_to_feature(record, bbox)
                    if feature:
                        features.append(feature)
                    elif i < 3:  # Log first 3 failures for debugging
                        LOGGER.debug(f"Failed to convert record {i} to feature")
                
                LOGGER.info(f"Converted {len(features)} features from {len(all_records)} records")
                return features
            
        except Exception as e:
            LOGGER.error(f"Error getting resource features: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
        
        return []

    def _record_to_feature(self, record: Dict, bbox: List[float]) -> Optional[Dict]:
        """
        Convert a datastore record to a GeoJSON-like feature
        Uses the same logic as CKANProvider to extract geometries
        
        Args:
            record: Datastore record
            bbox: Bounding box for filtering
            
        Returns:
            Feature dict or None
        """
        try:
            feature = {
                'type': 'Feature',
                'id': str(record.get('_id', record.get('id', ''))),
                'properties': {},
                'geometry': None
            }
            
            # Extract properties (exclude internal fields and geometry fields)
            geometry_fields = ['geom', 'geometry', 'st_asgeojson', 'wkt', 'geojson', 'the_geom', 'geometry_coordinates', 'geometry_type']
            coord_fields = ['x', 'y', 'X', 'Y', 'coord_x', 'coord_y', 'x_l93', 'y_l93', 'lambert_x', 'lambert_y', 'wgs84_x', 'wgs84_y', 'wgs84_lon', 'wgs84_lat', 'longitude', 'latitude', 'lon', 'lat', 'lng']
            
            for key, value in record.items():
                # Skip internal fields and geometry/coordinate fields (they're handled separately)
                if not key.startswith('_') and key not in ['id'] + geometry_fields + coord_fields:
                    feature['properties'][key] = value
            
            # Extract geometry if present (try multiple common field names)
            geometry_found = False
            
            for field in geometry_fields:
                if field in record and record[field]:
                    feature['geometry'] = self._parse_geometry(record[field])
                    if feature['geometry']:
                        geometry_found = True
                        LOGGER.debug(f"Extracted geometry from field '{field}'")
                        break
            
            # If no geometry found, try coordinate pairs
            if not geometry_found:
                geometry = self._extract_geometry_from_coordinates(record)
                if geometry:
                    feature['geometry'] = geometry
                    geometry_found = True
                    coords = geometry.get('coordinates', [])
                    LOGGER.info(f"Extracted geometry from coordinate fields: {coords}")
                else:
                    LOGGER.debug(f"_extract_geometry_from_coordinates returned None for record {record.get('_id', 'unknown')}")
            
            # Fallback to legacy format
            if not geometry_found and 'geometry_coordinates' in record and 'geometry_type' in record:
                geom_type = record.get('geometry_type', 'Point')
                geom_coords = record.get('geometry_coordinates')
                
                if geom_coords:
                    try:
                        if isinstance(geom_coords, str):
                            if geom_coords.startswith('['):
                                coords = json.loads(geom_coords)
                            else:
                                coords = self._parse_wkt_coordinates(geom_coords)
                        else:
                            coords = geom_coords
                        
                        if coords:
                            feature['geometry'] = {
                                'type': geom_type,
                                'coordinates': coords
                            }
                            geometry_found = True
                    except Exception as e:
                        LOGGER.warning(f"Could not parse geometry coordinates: {e}")
            
            # If no geometry found, skip this record
            if not geometry_found:
                LOGGER.debug(f"No geometry found in record {record.get('_id', 'unknown')}. Available fields: {list(record.keys())}")
                # Log first few field values to help debug
                sample_fields = list(record.keys())[:10]
                LOGGER.debug(f"  Sample field values: {[(k, str(record.get(k, ''))[:50]) for k in sample_fields]}")
                return None
            
            # Check if geometry is within bbox (simplified check for Point)
            # NOTE: We should NOT filter by bbox here - let all features be extracted first
            # The bbox filtering should be done at render time, not at extraction time
            # This allows the map to show all available data and let the user zoom/pan
            # if feature['geometry'] and feature['geometry'].get('type') == 'Point':
            #     coords = feature['geometry'].get('coordinates', [])
            #     if len(coords) >= 2:
            #         lon, lat = coords[0], coords[1]
            #         minx, miny, maxx, maxy = bbox
            #         if not (minx <= lon <= maxx and miny <= lat <= maxy):
            #             LOGGER.debug(f"Point {coords} is outside bbox {bbox}")
            #             return None
            
            return feature
            
        except Exception as e:
            LOGGER.error(f"Error converting record to feature: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            return None

    def _render_map(self, features: List[Dict], bbox: List[float], 
                   width: int, height: int, format_type: str) -> bytes:
        """
        Render map using matplotlib and Pillow
        
        Args:
            features: List of GeoJSON features
            bbox: Bounding box [minx, miny, maxx, maxy]
            width: Image width
            height: Image height
            format_type: Output format
            
        Returns:
            bytes: Rendered map image
        """
        try:
            if not features:
                LOGGER.warning("No features to render")
                return self._create_empty_map(width, height, bbox)
            
            # Filter features by bbox and collect valid points
            # BBOX format: [minx, miny, maxx, maxy] in WGS84 (lon, lat)
            # Use BBOX as-is, no inversion detection
            # Validate bbox values are not None
            if not bbox or len(bbox) < 4:
                LOGGER.error(f"Invalid bbox: {bbox}")
                return self._create_error_map(width, height, f"Invalid bbox: {bbox}")
            
            # Ensure all values are numeric (not None)
            try:
                minx, miny, maxx, maxy = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                # Check for None or NaN
                if any(v is None or (isinstance(v, float) and (v != v)) for v in [minx, miny, maxx, maxy]):
                    LOGGER.error(f"Bbox contains None or NaN values: {bbox}")
                    return self._create_error_map(width, height, f"Bbox contains invalid values: {bbox}")
            except (ValueError, TypeError) as e:
                LOGGER.error(f"Could not convert bbox to float: {bbox}, error: {e}")
                return self._create_error_map(width, height, f"Invalid bbox values: {bbox}")
            
            LOGGER.info(f"BBOX received: [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
            
            # Log for debugging
            LOGGER.info(f"Filtering features: bbox=[{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}], total features={len(features)}")
            
            valid_features = []
            all_coords = []
            points_checked = 0
            points_in_bbox = 0
            
            for feature in features:
                geom = feature.get('geometry')
                if not geom or geom.get('type') != 'Point':
                    continue
                
                coords = geom.get('coordinates', [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    all_coords.append((lon, lat))
                    points_checked += 1
                    
                    # Check if point is within bbox
                    # Note: In WGS84, coordinates are [longitude, latitude]
                    # BBOX is [minx (min lon), miny (min lat), maxx (max lon), maxy (max lat)]
                    in_bbox = minx <= lon <= maxx and miny <= lat <= maxy
                    
                    # Special logging for Brest point (ID 71)
                    if abs(lon - (-4.484855)) < 0.001 and abs(lat - 48.403698) < 0.001:
                        LOGGER.info(f"Checking Brest point (71): lon={lon:.6f}, lat={lat:.6f}")
                        LOGGER.info(f"BBOX: minx={minx:.6f}, miny={miny:.6f}, maxx={maxx:.6f}, maxy={maxy:.6f}")
                        LOGGER.info(f"Check: {minx:.6f} <= {lon:.6f} <= {maxx:.6f} = {minx <= lon <= maxx}")
                        LOGGER.info(f"Check: {miny:.6f} <= {lat:.6f} <= {maxy:.6f} = {miny <= lat <= maxy}")
                        LOGGER.info(f"Result: in_bbox={in_bbox}")
                    
                    if in_bbox:
                        valid_features.append(feature)
                        points_in_bbox += 1
                    elif points_checked <= 5:  # Log first 5 points for debugging
                        LOGGER.debug(f"  Point [{lon:.6f}, {lat:.6f}] outside bbox [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
            
            LOGGER.info(f"Filtered {points_in_bbox} points in bbox out of {points_checked} checked")
            
            # Log first few points for debugging coordinate order
            if all_coords and points_checked > 0:
                sample_points = all_coords[:5]
                LOGGER.info(f"Sample coordinates (first 5): {sample_points}")
                LOGGER.info(f"BBOX used for filtering: [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
                # Check if coordinates are in correct order (lon, lat)
                for i, (lon, lat) in enumerate(sample_points):
                    if not (-180 <= lon <= 180):
                        LOGGER.warning(f"Point {i}: longitude {lon:.6f} is out of range [-180, 180] - might be latitude!")
                    if not (-90 <= lat <= 90):
                        LOGGER.warning(f"Point {i}: latitude {lat:.6f} is out of range [-90, 90] - might be longitude!")
            
            # CRITICAL: For WMS, we MUST use the requested BBOX, not recalculate it
            # If no features are in the requested BBOX, we should still render with the exact BBOX
            # This ensures that zoom/pan works correctly - the map extent must match the requested BBOX
            if not valid_features and all_coords:
                LOGGER.warning(f"No features in requested bbox {bbox}, but using requested bbox anyway (WMS requirement)")
                LOGGER.warning(f"This ensures zoom/pan works correctly - map extent must match requested BBOX")
                # DO NOT recalculate bbox - use the requested one
                # This is critical for WMS: the map must show exactly what was requested
                # valid_features will be empty, but we'll render an empty map with the correct extent
                # Actually, let's check if the point is just slightly outside due to rounding
                # and include nearby points for better user experience
                # But the BBOX limits must remain exact
                LOGGER.info(f"Using requested bbox (exact): [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
            elif not valid_features:
                LOGGER.warning("No valid features to render")
                return self._create_empty_map(width, height, bbox)
            
            LOGGER.info(f"Rendering {len(valid_features)} features in bbox [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
            
            # Log a few sample points that will be rendered
            if valid_features:
                sample_rendered = []
                for i, feature in enumerate(valid_features[:3]):
                    geom = feature.get('geometry')
                    if geom and geom.get('type') == 'Point':
                        coords = geom.get('coordinates', [])
                        if len(coords) >= 2:
                            sample_rendered.append((coords[0], coords[1]))
                LOGGER.info(f"Sample points to render (first 3): {sample_rendered}")
            
            # UTILISER PILLOW POUR UN RENDU PIXEL-PERFECT WMS
            # Matplotlib ne sait pas faire un WMS pixel-perfect avec ax.plot()
            # car il réinterprète les limites. La solution robuste est :
            # 1. Convertir latitude/longitude en pixel_x / pixel_y manuellement
            # 2. Dessiner avec Pillow (ImageDraw)
            # 3. Ne pas laisser Matplotlib gérer le mapping coord→pixel
            
            LOGGER.info(f"Rendering with Pillow (pixel-perfect WMS): {len(valid_features)} points")
            LOGGER.info(f"BBOX: [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}], Image: {width}x{height}")
            
            # Create transparent image with Pillow
            img = Image.new('RGBA', (width, height), (0, 0, 0, 0))  # Transparent background
            draw = ImageDraw.Draw(img)
            
            # Calculate BBOX dimensions for coordinate transformation
            bbox_width = maxx - minx
            bbox_height = maxy - miny
            
            # Transform coordinates to pixels and draw points
            points_drawn = 0
            for feature in valid_features:
                geom = feature.get('geometry')
                if not geom or geom.get('type') != 'Point':
                    continue
                
                coords = geom.get('coordinates', [])
                if len(coords) >= 2:
                    lon, lat = coords[0], coords[1]
                    
                    # Transform geographic coordinates to pixel coordinates
                    # pixel_x = ((lon - minx) / (maxx - minx)) * width
                    # pixel_y = height - ((lat - miny) / (maxy - miny)) * height
                    # (inverted Y because image origin is top-left, but lat increases upward)
                    
                    if bbox_width > 0 and bbox_height > 0:
                        pixel_x = ((lon - minx) / bbox_width) * width
                        pixel_y = height - ((lat - miny) / bbox_height) * height
                        
                        # Clamp to image bounds (safety check)
                        pixel_x = max(0, min(width - 1, pixel_x))
                        pixel_y = max(0, min(height - 1, pixel_y))
                        
                        # Log specific point for debugging (Brest: -4.484855, 48.403698)
                        if abs(lon - (-4.484855)) < 0.001 and abs(lat - 48.403698) < 0.001:
                            LOGGER.info(f"Rendering Brest point: lon={lon:.6f}, lat={lat:.6f}")
                            LOGGER.info(f"BBOX: [{minx:.6f}, {miny:.6f}, {maxx:.6f}, {maxy:.6f}]")
                            LOGGER.info(f"Pixel position: x={pixel_x:.1f}, y={pixel_y:.1f} (image size: {width}x{height})")
                        
                        # Draw red point (6px radius circle)
                        # Use ellipse for circle: (x0, y0, x1, y1) where center is (pixel_x, pixel_y)
                        radius = 3
                        draw.ellipse(
                            [pixel_x - radius, pixel_y - radius, pixel_x + radius, pixel_y + radius],
                            fill='red',
                            outline='darkred',
                            width=1
                        )
                        points_drawn += 1
            
            LOGGER.info(f"Drew {points_drawn} points with Pillow")
            
            # Convert to bytes
            buf = io.BytesIO()
            if 'png' in format_type:
                img.save(buf, format='PNG')
            else:
                # Convert RGBA to RGB for JPEG (no transparency)
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                rgb_img.paste(img, mask=img.split()[3])  # Use alpha channel as mask
                rgb_img.save(buf, format='JPEG')
            
            buf.seek(0)
            return buf.getvalue()
            
        except Exception as e:
            LOGGER.error(f"Error rendering map: {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            # Ensure width and height are valid for error map
            try:
                error_width = int(width) if width is not None else 512
                error_height = int(height) if height is not None else 512
            except Exception:
                error_width, error_height = 512, 512
            return self._create_error_map(error_width, error_height, str(e))

    def _create_empty_map(self, width: int, height: int, bbox: List[float]) -> bytes:
        """Create an empty map image"""
        try:
            # Create empty image with Pillow
            img = Image.new('RGB', (width, height), color='lightblue')
            draw = ImageDraw.Draw(img)
            
            # Add text
            text = f"No data in bbox: {bbox}"
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            
            # Calculate text position (center)
            if font:
                bbox_text = draw.textbbox((0, 0), text, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            else:
                text_width = len(text) * 6
                text_height = 11
            
            x = (width - text_width) // 2
            y = (height - text_height) // 2
            
            draw.text((x, y), text, fill='black', font=font)
            
            # Save to bytes
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            
            return buf.getvalue()
            
        except Exception as e:
            LOGGER.error(f"Error creating empty map: {e}")
            return b''

    def _create_error_map(self, width: int, height: int, error: str) -> bytes:
        """Create an error map image"""
        try:
            # Create error image with Pillow
            img = Image.new('RGB', (width, height), color='lightcoral')
            draw = ImageDraw.Draw(img)
            
            # Add error text
            text = f"Error: {error}"
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            
            # Calculate text position (center)
            if font:
                bbox_text = draw.textbbox((0, 0), text, font=font)
                text_width = bbox_text[2] - bbox_text[0]
                text_height = bbox_text[3] - bbox_text[1]
            else:
                text_width = len(text) * 6
                text_height = 11
            
            x = (width - text_width) // 2
            y = (height - text_height) // 2
            
            draw.text((x, y), text, fill='white', font=font)
            
            # Save to bytes
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            
            return buf.getvalue()
            
        except Exception as e:
            LOGGER.error(f"Error creating error map: {e}")
            return b''

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
            if isinstance(geom_str, str) and geom_str.startswith('{'):
                geom = json.loads(geom_str)
                # Transform coordinates if they appear to be in Lambert-93
                if geom and 'coordinates' in geom:
                    geom['coordinates'] = self._transform_coordinates_if_needed(geom['coordinates'], geom.get('type', 'Point'))
                return geom
            
            # Try to parse as WKT (simplified)
            if isinstance(geom_str, str) and geom_str.startswith('POINT'):
                coords = self._parse_wkt_point(geom_str)
                if coords:
                    # Transform coordinates if needed
                    transformed_coords = self._transform_coordinates_if_needed(coords, 'Point')
                    return {
                        'type': 'Point',
                        'coordinates': transformed_coords
                    }
            
            return None
            
        except Exception as e:
            LOGGER.warning(f"Could not parse geometry: {e}")
            return None

    def _extract_geometry_from_coordinates(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract geometry from coordinate fields commonly used in French datasets
        
        Args:
            record: Data record
            
        Returns:
            GeoJSON geometry or None
        """
        try:
            # Special case: latitude/longitude (check FIRST as it's the most common)
            if 'latitude' in record and 'longitude' in record:
                try:
                    lat = float(record['latitude'])
                    lon = float(record['longitude'])
                    
                    # Validate coordinates are reasonable (not None, not 0,0 unless valid)
                    if lat is not None and lon is not None and not (lat == 0 and lon == 0):
                        # Validate coordinate ranges
                        # Longitude should be between -180 and 180
                        # Latitude should be between -90 and 90
                        if not (-180 <= lon <= 180):
                            LOGGER.warning(f"Longitude {lon} is out of range [-180, 180] - might be latitude!")
                            # Try swapping if longitude looks like latitude
                            if -90 <= lon <= 90 and -180 <= lat <= 180:
                                LOGGER.warning(f"Swapping coordinates: lon={lon}, lat={lat} -> lon={lat}, lat={lon}")
                                lon, lat = lat, lon
                        
                        if not (-90 <= lat <= 90):
                            LOGGER.warning(f"Latitude {lat} is out of range [-90, 90] - might be longitude!")
                        
                        # Transform coordinates if needed (assume WGS84 if reasonable, else Lambert-93)
                        # But only if coordinates are NOT already in WGS84 range
                        if -180 <= lon <= 180 and -90 <= lat <= 90:
                            # Already in WGS84, no transformation needed
                            coords = [lon, lat]
                        else:
                            # Might be in Lambert-93, try transformation
                            coords = self._transform_coordinates_if_needed([lon, lat], 'Point')
                        
                        LOGGER.info(f"Found coordinates in latitude/longitude: lat={lat}, lon={lon} → coords={coords}")
                        
                        return {
                            'type': 'Point',
                            'coordinates': coords  # GeoJSON is lon,lat
                        }
                except (ValueError, TypeError) as e:
                    LOGGER.debug(f"Could not parse latitude/longitude: {e}")
            
            # Common coordinate field patterns in French datasets
            coord_patterns = [
                # X/Y patterns
                ('x', 'y'),
                ('X', 'Y'),
                ('coord_x', 'coord_y'),
                ('longitude', 'latitude'),  # Already checked above, but keep for completeness
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
                        transformer = pyproj.Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
                        lon, lat = transformer.transform(x, y)
                        return [lon, lat]
                return coord_pair
            
            # Handle different geometry types
            if geom_type == 'Point':
                if len(coords) >= 2:
                    return transform_coord_pair(coords)
            elif geom_type == 'LineString':
                return [transform_coord_pair(coord) for coord in coords]
            elif geom_type == 'Polygon':
                return [[transform_coord_pair(coord) for coord in ring] for ring in coords]
            elif geom_type == 'MultiPoint':
                return [transform_coord_pair(coord) for coord in coords]
            elif geom_type == 'MultiLineString':
                return [[transform_coord_pair(coord) for coord in line] for line in coords]
            elif geom_type == 'MultiPolygon':
                return [[[transform_coord_pair(coord) for coord in ring] for ring in polygon] for polygon in coords]
            
            return coords
            
        except Exception as e:
            LOGGER.warning(f"Error transforming coordinates: {e}")
            return coords

    def get_tiles(self, layer: str, tileset: str, z: int, y: int, x: int, 
                  format_: str = None) -> bytes:
        """
        Get a tile (for WMTS compatibility)
        
        Args:
            layer: Layer name
            tileset: Tileset name
            z: Zoom level
            y: Tile Y coordinate  
            x: Tile X coordinate
            format_: Output format
            
        Returns:
            bytes: Tile image
        """
        # Calculate bbox for the tile
        # This is a simplified implementation - real tile servers use proper tile math
        bbox = [-180, -90, 180, 90]  # Simplified world bbox
        return self.get_map(bbox, 256, 256, layer, format_ or 'image/png')

    def get_capabilities(self) -> Dict[str, Any]:
        """
        Get WMS capabilities
        
        Returns:
            Dict with WMS capabilities
        """
        return {
            'service': 'WMS',
            'version': '1.3.0',
            'layers': [self.dataset_id],
            'formats': ['image/png', 'image/jpeg'],
            'crs': [self.crs],
            'extent': self.default_bbox
        }
