"""
CKAN provider for pygeoapi.

Connects pygeoapi to CKAN and exposes its datasets as OGC collections
(Features / WMS / WFS / simple Map).
"""
from .provider import CKANProvider
from .wms_provider import WMSProvider
from .wfs_provider import WFSProvider
from .simple_map_provider import SimpleMapProvider

__all__ = ['CKANProvider', 'WMSProvider', 'WFSProvider', 'SimpleMapProvider']


