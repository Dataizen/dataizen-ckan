"""
Template helpers and shared logic for ckanext-ogc.

This module is the historical entry point. Bulky functions have been moved
to thematic submodules:

- helpers_sld     : SLD style parsing, conversion and persistence
- helpers_mapfile : .map file manipulation (LAYER, CLASS, SYMBOL)
- helpers_home    : statistics, popular tags and categories for the home page

The imports at the bottom of this file re-export them to preserve
`import ckanext.ogc.helpers as ogc_helpers` and the compatibility with the
templates that use them through the plugin.
"""
import os
import logging
from typing import Any, Optional
from urllib.parse import urlparse
from ckan.plugins import toolkit

log = logging.getLogger(__name__)


def get_organization_id_from_package(package_id: str) -> Optional[str]:
    """Return the organization ID for a given package."""
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        orgs = package.get('organization', {})
        if orgs:
            return orgs.get('id')
        return None
    except Exception as e:
        log.error(f"Error getting organization ID from package {package_id}: {e}")
        return None


def get_organization_name_from_package(package_id: str) -> Optional[str]:
    """Return the organization name for a given package."""
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        orgs = package.get('organization', {})
        if orgs:
            return orgs.get('name')
        return None
    except Exception as e:
        log.error(f"Error getting organization name from package {package_id}: {e}")
        return None


def get_collection_name_from_dataset(package_id: str) -> Optional[str]:
    """Return the pygeoapi collection name for a CKAN dataset (its CKAN name)."""
    try:
        context = {'ignore_auth': True}
        package = toolkit.get_action('package_show')(context, {'id': package_id})
        return package.get('name')  # Le nom de la collection est le nom du dataset
    except Exception as e:
        log.error(f"Error getting collection name from dataset {package_id}: {e}")
        return None


def get_ogc_base_url() -> str:
    """Return the base URL used to build OGC service links.

    The OGC links point to the CKAN Blueprint (/maps/{org_name}), so we use
    the CKAN URL, not the pygeoapi one. HTTPS is forced when the current
    request or the upstream proxy indicates that CKAN is served over HTTPS.
    """
    site_url = (os.getenv('CKAN_PUBLIC_URL') or
                os.getenv('CKAN_SITE_URL') or
                os.getenv('CKAN_URL') or
                os.getenv('PUBLIC_URL') or
                os.getenv('SERVER_NAME') or
                'http://localhost:8080')

    if 'localhost' in site_url:
        public_url = os.getenv('CKAN_PUBLIC_URL') or os.getenv('PUBLIC_URL')
        if public_url:
            site_url = public_url

    # Détecter si on doit forcer HTTPS (proxy X-Forwarded-Proto, scheme actuel, env vars)
    force_https = False
    try:
        from flask import request
        if hasattr(request, 'is_secure') and request.is_secure:
            force_https = True
        elif hasattr(request, 'scheme') and request.scheme == 'https':
            force_https = True
        elif hasattr(request, 'headers'):
            forwarded_proto = request.headers.get('X-Forwarded-Proto', '').lower()
            forwarded_ssl = request.headers.get('X-Forwarded-Ssl', '').lower()
            if forwarded_proto == 'https' or forwarded_ssl == 'on':
                force_https = True
    except (RuntimeError, AttributeError):
        if os.getenv('HTTPS', '').lower() in ('on', 'true', '1'):
            force_https = True
        elif os.getenv('X_FORWARDED_PROTO', '').lower() == 'https':
            force_https = True

    if force_https and site_url.startswith('http://'):
        site_url = site_url.replace('http://', 'https://', 1)
        log.debug(f"Forçage HTTPS pour URL OGC: {site_url}")

    if site_url.endswith('/'):
        site_url = site_url[:-1]
    return site_url


def get_site_title() -> str:
    """Return the CKAN site title."""
    try:
        from ckan.common import config
        return config.get('ckan.site_title', 'CKAN')
    except Exception:
        return 'CKAN'


def format_resource_size(size: Optional[int]) -> str:
    """Format a byte size into a human-readable string (Ko, Mo, Go).

    Args:
        size: Size in bytes (int or None).

    Returns:
        Formatted size, or an empty string if unavailable.
    """
    if size is None:
        return ''
    try:
        size = int(size)
    except (TypeError, ValueError):
        return ''
    if size < 0:
        return ''
    if size < 1024:
        return f'{size} o'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} Ko'
    if size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} Mo'
    return f'{size / (1024 * 1024 * 1024):.1f} Go'


def is_geospatial_dataset(package: Any) -> bool:
    """Check whether a dataset is geospatial.

    Args:
        package: CKAN package object (dict or object with attributes).

    Returns:
        True if the dataset is geospatial, False otherwise.
    """
    if not package:
        return False

    # Formats géospatiaux reconnus (y compris ZIP contenant des shapefiles)
    geospatial_formats = {
        'shp', 'shapefile', 'geojson', 'kml', 'kmz',
        'gpx', 'gpkg', 'geopackage', 'geotiff', 'tif', 'tiff', 'zip'
    }

    # Vérifier les métadonnées spatiales (extras)
    if hasattr(package, 'extras'):
        extras = package.extras
    elif isinstance(package, dict):
        extras = package.get('extras', [])
    else:
        extras = []

    for extra in extras:
        if isinstance(extra, dict):
            key = extra.get('key', '')
            value = extra.get('value', '')
            if key in ['spatial', 'spatial_text', 'spatial_uri'] and value:
                return True

    if isinstance(package, dict):
        if package.get('spatial') or package.get('spatial_text') or package.get('spatial_uri'):
            return True

    # Vérifier les formats des ressources
    resources = []
    if hasattr(package, 'resources'):
        resources = package.resources
    elif isinstance(package, dict):
        resources = package.get('resources', [])

    for resource in resources:
        format_value = ''
        if hasattr(resource, 'format'):
            format_value = resource.format
        elif isinstance(resource, dict):
            format_value = resource.get('format', '')

        if format_value:
            format_lower = format_value.lower().strip()
            if format_lower in geospatial_formats:
                return True

        # Vérifier datastore_active : si le datastore contient des colonnes géométriques
        datastore_active = False
        if hasattr(resource, 'datastore_active'):
            datastore_active = resource.datastore_active
        elif isinstance(resource, dict):
            datastore_active = resource.get('datastore_active', False)

        if datastore_active:
            try:
                resource_id = None
                if hasattr(resource, 'id'):
                    resource_id = resource.id
                elif isinstance(resource, dict):
                    resource_id = resource.get('id')

                if resource_id:
                    import requests
                    ckan_url = os.getenv('CKAN_SITE_URL', os.getenv('CKAN_URL', 'http://localhost:5000'))
                    if 'localhost:8080' in ckan_url or 'localhost:8083' in ckan_url:
                        ckan_url = ckan_url.replace('localhost:8080', 'localhost:5000').replace('localhost:8083', 'localhost:5000')

                    api_key = os.getenv('CKAN_API_KEY', '')
                    headers = {}
                    if api_key:
                        headers['Authorization'] = api_key

                    # Requête limit=0 pour obtenir uniquement les métadonnées
                    response = requests.get(
                        f"{ckan_url}/api/action/datastore_search",
                        params={'resource_id': resource_id, 'limit': 0},
                        headers=headers,
                        timeout=5
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if result.get('success'):
                            fields = result.get('result', {}).get('fields', [])
                            geom_fields = ['geom', 'geometry', 'the_geom', 'latitude', 'longitude',
                                           'lon', 'lat', 'x', 'y', 'geo_point', 'geo_point_2d',
                                           'coordinates', 'coord']
                            field_names = [f.get('id', '').lower() for f in fields]
                            has_geom = any(
                                field_name in [g.lower() for g in geom_fields]
                                or 'geo' in field_name or 'geom' in field_name
                                for field_name in field_names
                            )
                            if has_geom:
                                return True
            except Exception:
                pass

    return False


def get_mapserver_url() -> str:
    """Return the public MapServer URL."""
    mapserver_url = os.getenv('MAPSERVER_PUBLIC_URL', 'http://localhost:8081')
    if mapserver_url.endswith('/'):
        mapserver_url = mapserver_url[:-1]
    return mapserver_url


def get_pygeoapi_url() -> str:
    """Return the public pygeoapi URL.

    Forces HTTPS when CKAN is served over HTTPS to avoid mixed content.
    """
    pygeoapi_url = (os.getenv('PYGEOAPI_PUBLIC_URL') or
                    os.getenv('PYGEOAPI_URL') or
                    'http://localhost:5001')

    if 'localhost' in pygeoapi_url:
        public_url = os.getenv('PYGEOAPI_PUBLIC_URL')
        if public_url:
            pygeoapi_url = public_url
        else:
            # Construire l'URL à partir de CKAN_SITE_URL si disponible
            ckan_url = os.getenv('CKAN_PUBLIC_URL') or os.getenv('CKAN_SITE_URL') or os.getenv('CKAN_URL')
            if ckan_url and 'localhost' not in ckan_url:
                parsed = urlparse(ckan_url)
                domain = parsed.netloc
                pygeoapi_url = f"{parsed.scheme}://ogc.{domain.split('.', 1)[-1] if '.' in domain else domain}"

    if pygeoapi_url.endswith('/'):
        pygeoapi_url = pygeoapi_url[:-1]

    # Remplacer ogc. par ogc.ckan2. dans l'URL (pour les environnements ckan2)
    if 'ogc.' in pygeoapi_url and 'ogc.ckan2.' not in pygeoapi_url:
        pygeoapi_url = pygeoapi_url.replace('ogc.', 'ogc.ckan2.', 1)

    # Forcer HTTPS si CKAN est en HTTPS (pour éviter le contenu mixte)
    ckan_base_url = get_ogc_base_url()
    if ckan_base_url.startswith('https://') and pygeoapi_url.startswith('http://'):
        pygeoapi_url = pygeoapi_url.replace('http://', 'https://', 1)
        log.debug(f"Forçage HTTPS pour URL pygeoapi: {pygeoapi_url}")

    return pygeoapi_url


def safe_entity_url(entity: Any) -> Optional[str]:
    """Build a safe URL for a deleted entity (admin trash view).

    Handles the special case where ``entity.type == 'file'`` because there is
    no ``file.read`` endpoint.

    Args:
        entity: Entity object with at least ``type`` and ``name`` attributes.

    Returns:
        URL of the entity, or None if it cannot be built.
    """
    if not entity or not hasattr(entity, 'type'):
        return None

    entity_type = getattr(entity, 'type', None)
    entity_name = getattr(entity, 'name', None)

    if not entity_type or not entity_name:
        return None

    # Les fichiers supprimés n'ont pas d'endpoint 'file.read'
    if entity_type == 'file':
        return None

    try:
        from ckan.plugins.toolkit import url_for
        # Pour les ressources, il faut aussi le package_id
        if entity_type == 'resource':
            package_id = getattr(entity, 'package_id', None)
            if package_id:
                return url_for('resource.read', id=package_id, resource_id=entity_name)
            return None

        endpoint = f"{entity_type}.read"
        return url_for(endpoint, id=entity_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Re-exports depuis les sous-modules pour préserver `import ckanext.ogc.helpers as ogc_helpers`
# (utilisé par plugin.py, views.py et les templates via get_helpers()).
# ---------------------------------------------------------------------------

from ckanext.ogc.helpers_sld import (  # noqa: E402,F401
    get_sld_style_name,
    sld_contains_graphic_fill,
    layer_name_for_resource,
    get_sld_style_for_resource,
    save_sld_style_for_resource,
    has_sld_style_for_resource,
    keep_only_userstyle,
    sld_to_mapserver_class,
    delete_sld_style_for_resource,
    build_combined_sld_for_layers,
)

from ckanext.ogc.helpers_mapfile import (  # noqa: E402,F401
    ensure_mapfile_has_hatch_symbols,
    patch_mapfile_layer_classes,
    get_layer_geometry_type,
    remove_layer_from_mapfile,
    has_mapfile,
)

from ckanext.ogc.helpers_home import (  # noqa: E402,F401
    get_home_statistics,
    get_popular_tags,
    get_categories,
)
