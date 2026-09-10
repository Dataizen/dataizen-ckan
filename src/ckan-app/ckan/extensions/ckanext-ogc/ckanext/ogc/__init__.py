"""
ckanext-ogc package.

CKAN extension exposing OGC services (WMS, WFS, OGC API Features) for
geospatial datasets by combining pygeoapi, MapServer and CKAN. The main
plugin class lives in :mod:`ckanext.ogc.plugin`; this top-level module
only declares a handful of template helpers used by the plugin's
``get_helpers()`` registration.
"""
import logging

log = logging.getLogger(__name__)

def get_helpers():
    return {
        'ogc_collections_url': ogc_collections_url,
        'ogc_dataset_has_geo': ogc_dataset_has_geo,
    }

def ogc_collections_url():
    """Return the base URL for OGC collections"""
    from ckan.common import config
    return config.get('ckanext.ogc.pygeoapi_url', 'http://localhost:5000')

def ogc_dataset_has_geo(dataset):
    """Check if a dataset has geospatial data"""
    try:
        from .utils import is_geospatial_dataset
        return is_geospatial_dataset(dataset)
    except Exception as e:
        log.error(f"Error checking geospatial data: {e}")
        return False



