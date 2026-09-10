"""
End-to-end tests for CSV ingestion (xloader -> datastore).

- ``simple.csv``        : plain tabular data, no geometry.
- ``points_latlon.csv`` : tabular with lat/lon columns ; the dataload_router
  should add a ``the_geom`` column when supported.
"""
import requests


def test_simple_csv_lands_in_datastore(make_dataset, upload_resource, wait_for_datastore,
                                       session, ckan_url, data_dir):
    """Upload a 5-row CSV and verify rows are queryable from datastore_search."""
    pkg = make_dataset('csv-simple')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')

    info = wait_for_datastore(res['id'])
    assert info['datastore_active'] is True

    r = session.get(
        f'{ckan_url}/api/3/action/datastore_search',
        params={'resource_id': res['id'], 'limit': 100},
        timeout=15,
    )
    r.raise_for_status()
    result = r.json()['result']
    assert result['total'] == 5, f'expected 5 rows, got {result["total"]}'
    names = {row['name'] for row in result['records']}
    assert {'Dijon', 'Besancon', 'Belfort'} <= names


def test_csv_with_latlon_gets_geom_column(make_dataset, upload_resource, wait_for_datastore,
                                          session, ckan_url, data_dir, wait_for_condition):
    """Upload a CSV with lat/lon. The dataload_router should detect those columns
    and add a ``the_geom`` geometry column to the datastore table.
    """
    pkg = make_dataset('csv-latlon')
    res = upload_resource(pkg['id'], data_dir / 'points_latlon.csv', fmt='CSV')

    wait_for_datastore(res['id'])

    def has_geom_field() -> bool:
        r = session.get(
            f'{ckan_url}/api/3/action/datastore_search',
            params={'resource_id': res['id'], 'limit': 0},
            timeout=10,
        )
        if r.status_code != 200:
            return False
        fields = {f['id'] for f in r.json()['result']['fields']}
        return 'the_geom' in fields or 'geom' in fields

    # the_geom may be added asynchronously by the router post xloader
    wait_for_condition(has_geom_field, 'the_geom column added to datastore', timeout=60)
