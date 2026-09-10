"""
Verify that MapServer actually serves the ingested geospatial data.

The earlier ingestion tests only check that resources are flagged as
``is_geospatial``. These tests go further: they upload a geospatial file
(GeoJSON / shapefile) then call MapServer ``GetMap`` / ``GetCapabilities``
through the dataset's mapfile and assert a real PNG / non-error response.
"""
import io
import time
import zipfile
from pathlib import Path

import pytest
import requests


try:
    import shapefile  # type: ignore  # pyshp
except ImportError:
    shapefile = None


def _mapserver_url(ckan_url: str) -> str:
    return ckan_url.replace(':8080', ':8081')


def _trigger_dataset_mapfile_job(session, ckan_url: str, dataset_name: str) -> None:
    """Enqueue a focused mapfile-generation job for a single dataset.

    The plugin OGC hook may have been killed by uwsgi harakiri before it
    could write the mapfile (bug #4). The ``dataset_generate_mapfile``
    action enqueues an RQ job that runs ``generate-mapfile.py --dataset
    <name>`` outside the request lifecycle, so it cannot harakiri.
    Idempotent: safe to call even if the mapfile already exists.
    """
    try:
        session.post(
            f'{ckan_url}/api/3/action/dataset_generate_mapfile',
            json={'dataset_id': dataset_name}, timeout=10,
        )
    except Exception:
        pass


def _wait_for_mapfile(session, ckan_url: str, dataset_name: str,
                      attempts: int = 120, delay: int = 3) -> Path:
    """Poll until the mapfile is written to disk on the host.

    Generation is asynchronous and can be slow on this stack (bug #4: the
    inline OGC hook is killed by harakiri before it can write, the RQ job
    then runs queued behind any in-flight xloader work). 360 s is a safe
    upper bound observed when a clean run already has a couple of geo
    uploads in flight ahead.

    Triggers ``dataset_generate_mapfile`` upfront and again after ~30s if
    nothing has appeared, then polls up to ``attempts * delay`` seconds.
    """
    mapfile = Path('mapserver_storage/mapfiles') / f'{dataset_name}.map'
    _trigger_dataset_mapfile_job(session, ckan_url, dataset_name)
    retried = False
    for i in range(attempts):
        if mapfile.exists() and mapfile.stat().st_size > 0:
            return mapfile
        if not retried and i >= 10:
            _trigger_dataset_mapfile_job(session, ckan_url, dataset_name)
            retried = True
        time.sleep(delay)
    pytest.fail(f'Mapfile {mapfile} not generated within '
                f'{attempts * delay}s')


def _wms_getmap(mapserver_url: str, dataset_name: str, layer_name: str,
                bbox: str = '4,46,7,48') -> requests.Response:
    return requests.get(
        f'{mapserver_url}/wms',
        params={
            'map': f'/mapserver/mapfiles/{dataset_name}.map',
            'SERVICE': 'WMS',
            'VERSION': '1.3.0',
            'REQUEST': 'GetMap',
            'LAYERS': layer_name,
            'BBOX': bbox,
            'WIDTH': 200, 'HEIGHT': 200,
            'CRS': 'EPSG:4326',
            'FORMAT': 'image/png',
            'STYLES': '',
        },
        timeout=15,
    )


def _wms_getcapabilities(mapserver_url: str, dataset_name: str) -> requests.Response:
    return requests.get(
        f'{mapserver_url}/wms',
        params={
            'map': f'/mapserver/mapfiles/{dataset_name}.map',
            'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetCapabilities',
        },
        timeout=15,
    )


def _first_layer_name_in_mapfile(mapfile: Path) -> str:
    """Extract the NAME of the first LAYER block in the mapfile."""
    text = mapfile.read_text(encoding='utf-8', errors='replace')
    # Match LAYER ... NAME "<value>" ; very permissive
    import re
    m = re.search(r'LAYER\s+[^L]*?NAME\s+"([^"]+)"', text, re.DOTALL)
    assert m, f'No LAYER NAME found in mapfile {mapfile}'
    return m.group(1)


# ---------------------------------------------------------------------------
# GeoJSON: MapServer must serve a real PNG
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason='Depend du bug #4 : le hook OGC after_resource_create est tue '
    'par uwsgi harakiri avant d ecrire le mapfile, et le job RQ de '
    'remplacement passe derriere les jobs xloader, ce qui depasse 360s '
    'en local. La generation finit par aboutir mais hors timeout du '
    'test. Voir KNOWN-ISSUES.md.',
    strict=False,
)
def test_geojson_is_served_by_mapserver_wms(make_dataset, upload_resource,
                                            session, ckan_url, data_dir):
    """After uploading a GeoJSON, MapServer must serve a non-empty PNG via WMS."""
    pkg = make_dataset('ms-geojson')
    upload_resource(pkg['id'], data_dir / 'points.geojson', fmt='GeoJSON')

    mapfile = _wait_for_mapfile(session, ckan_url, pkg['name'])
    layer = _first_layer_name_in_mapfile(mapfile)

    r = _wms_getmap(_mapserver_url(ckan_url), pkg['name'], layer)
    assert r.status_code == 200, f'WMS GetMap failed: {r.status_code} {r.text[:200]}'
    assert r.headers.get('Content-Type', '').startswith('image/'), \
        f'Expected an image response, got {r.headers.get("Content-Type")}: {r.text[:200]}'
    # PNG signature \x89PNG\r\n\x1a\n
    assert r.content[:4] == b'\x89PNG', \
        f'Response is not a PNG (first bytes: {r.content[:16]!r})'
    assert len(r.content) > 100, f'PNG suspiciously small: {len(r.content)} bytes'


@pytest.mark.xfail(
    reason='Idem test_geojson_is_served_by_mapserver_wms : depend du bug #4. '
    'La generation du mapfile peut prendre >360s en local sous charge.',
    strict=False,
)
def test_geojson_dataset_appears_in_wms_capabilities(make_dataset, upload_resource,
                                                     session, ckan_url, data_dir):
    """The dataset must be advertised in its mapfile's GetCapabilities."""
    pkg = make_dataset('ms-caps')
    upload_resource(pkg['id'], data_dir / 'points.geojson', fmt='GeoJSON')

    mapfile = _wait_for_mapfile(session, ckan_url, pkg['name'])
    layer = _first_layer_name_in_mapfile(mapfile)

    r = _wms_getcapabilities(_mapserver_url(ckan_url), pkg['name'])
    assert r.status_code == 200
    body = r.text
    # The layer NAME must show up in the WMS XML
    assert layer in body, f'Layer {layer} not advertised in GetCapabilities'


# ---------------------------------------------------------------------------
# Shapefile: same checks, ensures the full chain works for ESRI too
# ---------------------------------------------------------------------------

def _build_points_shapefile_zip(tmp_path: Path) -> Path:
    if shapefile is None:
        pytest.skip('pyshp not installed; run `pip install pyshp`')
    shp_dir = tmp_path / 'sample'
    shp_dir.mkdir()
    base = shp_dir / 'points'
    w = shapefile.Writer(str(base), shapeType=shapefile.POINT)
    w.field('NAME', 'C', size=40)
    w.field('POP', 'N', size=10)
    for name, pop, lon, lat in [
        ('Dijon',    156920, 5.0415, 47.3220),
        ('Besancon', 116676, 6.0241, 47.2378),
        ('Belfort',   46455, 6.8629, 47.6380),
    ]:
        w.point(lon, lat)
        w.record(name, pop)
    w.close()
    (shp_dir / 'points.prj').write_text(
        'GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]',
        encoding='ascii',
    )
    zip_path = tmp_path / 'points-shapefile.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ext in ('shp', 'shx', 'dbf', 'prj'):
            zf.write(shp_dir / f'points.{ext}', arcname=f'points.{ext}')
    return zip_path


@pytest.mark.xfail(
    reason='bug serveur : generate-mapfile.py ecrit "CONNECTION /var/lib/ckan/'
    'resources/<id>" (chemin direct du zip) au lieu de "/vsizip/<zip>/'
    '<file.shp>", donc MapServer ne sait pas ouvrir le shapefile. Le code '
    'pour le format /vsizip/ existe mais la branche n est jamais atteinte '
    '(probable detection is_zip fausse). Voir KNOWN-ISSUES.md.',
    strict=False,
)
def test_shapefile_is_served_by_mapserver_wms(tmp_path, make_dataset, upload_resource,
                                              session, ckan_url):
    """After uploading a shapefile zip, MapServer must serve a PNG via WMS."""
    zip_path = _build_points_shapefile_zip(tmp_path)
    pkg = make_dataset('ms-shp')
    upload_resource(pkg['id'], zip_path, fmt='SHP', name='points-shapefile.zip')

    mapfile = _wait_for_mapfile(session, ckan_url, pkg['name'])
    layer = _first_layer_name_in_mapfile(mapfile)

    r = _wms_getmap(_mapserver_url(ckan_url), pkg['name'], layer)
    assert r.status_code == 200, f'WMS GetMap failed: {r.status_code} {r.text[:200]}'
    assert r.headers.get('Content-Type', '').startswith('image/')
    assert r.content[:4] == b'\x89PNG'
    assert len(r.content) > 100
