"""
End-to-end tests for CKAN-side display of ingested data.

Once a resource has been ingested (CSV, GeoJSON, shapefile), CKAN must
expose it properly through:

- HTML pages (``/dataset/<name>``, ``/dataset/<name>/resource/<id>``)
- the JSON API (``package_show`` with and without ``include_ogc=true``)
- the datastore (``datastore_search`` returns rows for tabular data)

These tests verify those user-facing contracts.
"""
from pathlib import Path


def _resource(session, ckan_url, resource_id):
    r = session.get(
        f'{ckan_url}/api/3/action/resource_show',
        params={'id': resource_id}, timeout=10,
    )
    r.raise_for_status()
    return r.json()['result']


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

def test_dataset_html_page_renders(make_dataset, upload_resource, wait_for_datastore,
                                   session, ckan_url, data_dir):
    """The /dataset/<name> page must render HTML containing the dataset title."""
    pkg = make_dataset('display-html')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')
    wait_for_datastore(res['id'])

    r = session.get(f'{ckan_url}/dataset/{pkg["name"]}', timeout=15)
    assert r.status_code == 200
    body = r.text
    assert pkg['name'] in body
    # Une page CKAN normale doit contenir le marqueur <html ou <!DOCTYPE
    assert '<html' in body.lower() or '<!doctype' in body.lower()


def test_resource_html_page_renders(make_dataset, upload_resource, wait_for_datastore,
                                    session, ckan_url, data_dir):
    """The /dataset/<name>/resource/<id> page must render HTML."""
    pkg = make_dataset('display-res')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')
    wait_for_datastore(res['id'])

    url = f'{ckan_url}/dataset/{pkg["name"]}/resource/{res["id"]}'
    r = session.get(url, timeout=15)
    assert r.status_code == 200, f'unexpected status {r.status_code} on {url}'
    body_lower = r.text.lower()
    assert '<html' in body_lower or '<!doctype' in body_lower
    # Mentions classiques d'une page resource CKAN
    assert ('download' in body_lower) or ('resource' in body_lower)


# ---------------------------------------------------------------------------
# JSON API: package_show
# ---------------------------------------------------------------------------

def test_package_show_returns_resource_with_format(make_dataset, upload_resource,
                                                   wait_for_datastore,
                                                   session, ckan_url, data_dir):
    """package_show must list the uploaded resource with its format / url_type."""
    pkg = make_dataset('display-api')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')
    wait_for_datastore(res['id'])

    r = session.get(
        f'{ckan_url}/api/3/action/package_show',
        params={'id': pkg['name']}, timeout=10,
    )
    r.raise_for_status()
    result = r.json()['result']
    assert result['name'] == pkg['name']
    assert len(result['resources']) >= 1
    target = next((x for x in result['resources'] if x['id'] == res['id']), None)
    assert target is not None
    assert target['format'].lower() == 'csv'
    assert target['url_type'] == 'upload'
    assert target['datastore_active'] is True


def test_package_show_include_ogc_exposes_metadata(make_dataset, upload_resource,
                                                   session, ckan_url, data_dir,
                                                   wait_for_condition):
    """``package_show?include_ogc=true`` exposes OGC extras on geospatial resources."""
    pkg = make_dataset('display-ogc')
    res = upload_resource(pkg['id'], data_dir / 'points.geojson', fmt='GeoJSON')

    def has_ogc_info() -> bool:
        r = session.get(
            f'{ckan_url}/api/3/action/package_show',
            params={'id': pkg['name'], 'include_ogc': 'true'},
            timeout=10,
        )
        if r.status_code != 200:
            return False
        result = r.json()['result']
        # Soit ogc_resources est rempli, soit la ressource elle-meme porte
        # is_geospatial=True (selon le moment du polling)
        has_ogc_list = bool(result.get('ogc_resources'))
        flagged_res = any(
            (r2.get('is_geospatial') or r2.get('datagis_imported'))
            for r2 in result.get('resources', [])
        )
        return has_ogc_list or flagged_res

    wait_for_condition(has_ogc_info,
                       'OGC metadata exposed on package_show', timeout=180)


# ---------------------------------------------------------------------------
# Datastore search returns rows
# ---------------------------------------------------------------------------

def test_datastore_search_returns_rows(make_dataset, upload_resource,
                                       wait_for_datastore,
                                       session, ckan_url, data_dir):
    """After CSV ingest, datastore_search must return the actual rows."""
    pkg = make_dataset('display-rows')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')
    wait_for_datastore(res['id'])

    r = session.get(
        f'{ckan_url}/api/3/action/datastore_search',
        params={'resource_id': res['id'], 'limit': 100},
        timeout=15,
    )
    r.raise_for_status()
    result = r.json()['result']
    assert result['total'] == 5
    assert {row['name'] for row in result['records']} == {
        'Dijon', 'Besancon', 'Belfort', 'Nevers', 'Macon',
    }


def test_datastore_search_with_filter(make_dataset, upload_resource,
                                      wait_for_datastore,
                                      session, ckan_url, data_dir):
    """datastore_search supports filters server-side."""
    pkg = make_dataset('display-filter')
    res = upload_resource(pkg['id'], data_dir / 'simple.csv', fmt='CSV')
    wait_for_datastore(res['id'])

    r = session.get(
        f'{ckan_url}/api/3/action/datastore_search',
        params={
            'resource_id': res['id'],
            'filters': '{"name": "Dijon"}',
        },
        timeout=15,
    )
    r.raise_for_status()
    result = r.json()['result']
    assert result['total'] == 1
    assert result['records'][0]['name'] == 'Dijon'
