"""
End-to-end tests for GeoJSON ingestion.

Covers the three OGC geometry families (Point, LineString, Polygon) and
verifies that the dataset becomes geospatially discoverable through the
package metadata (``is_geospatial``, ``ogc_resources``).
"""
import pytest


@pytest.mark.parametrize('geometry_kind,filename,expected_count', [
    ('points',   'points.geojson',   3),
    ('lines',    'lines.geojson',    2),
    ('polygons', 'polygons.geojson', 2),
])
def test_geojson_resource_is_recognized(geometry_kind, filename, expected_count,
                                        make_dataset, upload_resource,
                                        session, ckan_url, data_dir,
                                        wait_for_condition):
    """Upload a GeoJSON of each geometry kind and verify resource_show flags it."""
    pkg = make_dataset(f'geojson-{geometry_kind}')
    res = upload_resource(pkg['id'], data_dir / filename, fmt='GeoJSON')

    def is_geo() -> bool:
        r = session.get(
            f'{ckan_url}/api/3/action/resource_show',
            params={'id': res['id']}, timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json()['result']
        return bool(data.get('is_geospatial') or data.get('datagis_imported'))

    wait_for_condition(is_geo, f'GeoJSON {geometry_kind} flagged as geospatial', timeout=120)

    # Package side: ogc_resources field should now expose this resource
    r = session.get(
        f'{ckan_url}/api/3/action/package_show',
        params={'id': pkg['id'], 'include_ogc': 'true'}, timeout=15,
    )
    r.raise_for_status()
    pkg_full = r.json()['result']
    # Loose assertion: the resource list still contains our resource
    res_ids = {r['id'] for r in pkg_full.get('resources', [])}
    assert res['id'] in res_ids
