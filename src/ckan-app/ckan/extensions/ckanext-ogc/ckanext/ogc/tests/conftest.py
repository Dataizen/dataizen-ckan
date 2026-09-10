"""
Pytest fixtures and conftest for ckanext-ogc tests.

Provides lightweight stubs for ``ckan.plugins`` so the pure-Python helpers
can be imported and tested in isolation, without a running CKAN instance.
"""
import sys
from unittest.mock import MagicMock


def _install_ckan_stubs() -> None:
    """Install minimal stubs for ckan.plugins / ckan.common / ckan.model.

    Called at import time so test modules that import
    ``ckanext.ogc.helpers*`` succeed even when CKAN itself is not installed.
    Real CKAN is used inside the Docker image; here we only test pure logic.
    """
    if 'ckan' in sys.modules:
        return
    ckan = MagicMock()
    ckan.plugins = MagicMock()
    ckan.plugins.toolkit = MagicMock()
    ckan.common = MagicMock()
    ckan.common.config = MagicMock()
    ckan.model = MagicMock()
    sys.modules['ckan'] = ckan
    sys.modules['ckan.plugins'] = ckan.plugins
    sys.modules['ckan.plugins.toolkit'] = ckan.plugins.toolkit
    sys.modules['ckan.common'] = ckan.common
    sys.modules['ckan.model'] = ckan.model


_install_ckan_stubs()
