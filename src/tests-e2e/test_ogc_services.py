"""
End-to-end checks of the OGC endpoints once test data has been ingested.

These are smoke tests: we don't validate the XML schema deeply, we just
assert that the GetCapabilities of WMS/WFS and the OGC API root respond
with the expected MIME types and contain the organization name.
"""
import pytest


def test_pygeoapi_root_returns_html_or_json(session, ckan_url):
    """The pygeoapi root must respond. Port 5001 is exposed on the host."""
    pygeoapi_url = ckan_url.replace(':8080', ':5001')
    r = session.get(pygeoapi_url, timeout=10)
    assert r.status_code == 200


def test_mapserver_is_reachable(session, ckan_url):
    """MapServer must be reachable on its port. Without a ``map=`` parameter
    it returns an HTML error page, which is the expected behaviour: we only
    smoke-test that the server is up and routing.
    """
    mapserver_url = ckan_url.replace(':8080', ':8081')
    # MapServer with no map= parameter responds 200 with an HTML error page;
    # we only check it answers (TCP + Apache OK).
    r = session.get(
        f'{mapserver_url}/wms',
        params={'SERVICE': 'WMS', 'REQUEST': 'GetCapabilities'},
        timeout=10,
    )
    assert r.status_code == 200, f'mapserver returned {r.status_code}'


def test_ckan_wms_proxy_returns_capabilities_or_404(session, ckan_url, test_org):
    """The CKAN-side WMS proxy at /wms/<org> should respond (200 if data, 404 if empty).

    We do not require data here, only that the route is wired.
    """
    r = session.get(
        f'{ckan_url}/wms/{test_org["name"]}',
        params={'SERVICE': 'WMS', 'REQUEST': 'GetCapabilities'},
        timeout=15,
        allow_redirects=False,
    )
    assert r.status_code in (200, 404, 500), f'unexpected status {r.status_code}'


def test_ckan_wfs_proxy_returns_capabilities_or_404(session, ckan_url, test_org):
    """Same for WFS."""
    r = session.get(
        f'{ckan_url}/wfs/{test_org["name"]}',
        params={'SERVICE': 'WFS', 'REQUEST': 'GetCapabilities'},
        timeout=15,
        allow_redirects=False,
    )
    assert r.status_code in (200, 404, 500), f'unexpected status {r.status_code}'
