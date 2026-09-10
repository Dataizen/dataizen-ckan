"""
End-to-end test for ESRI shapefile ingestion.

The shapefile (``.shp`` + ``.shx`` + ``.dbf`` + ``.prj`` zipped) is built
on the fly with ``pyshp`` to avoid committing a binary artefact. If
``pyshp`` is not installed, the test skips with a clear message.
"""
import io
import zipfile
from pathlib import Path

import pytest


try:
    import shapefile  # type: ignore  # pyshp
except ImportError:
    shapefile = None


def _build_points_shapefile_zip(tmp_path: Path) -> Path:
    """Create a minimal Point shapefile zip in tmp_path. Returns the zip path."""
    if shapefile is None:
        pytest.skip('pyshp not installed; run `pip install pyshp`')

    shp_dir = tmp_path / 'sample'
    shp_dir.mkdir()
    base = shp_dir / 'points'
    w = shapefile.Writer(str(base), shapeType=shapefile.POINT)
    w.field('NAME', 'C', size=40)
    w.field('POP', 'N', size=10)
    cities = [
        ('Dijon',    156920, 5.0415, 47.3220),
        ('Besancon', 116676, 6.0241, 47.2378),
        ('Belfort',   46455, 6.8629, 47.6380),
    ]
    for name, pop, lon, lat in cities:
        w.point(lon, lat)
        w.record(name, pop)
    w.close()

    # Minimal WGS84 .prj
    prj = (
        'GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
    )
    (shp_dir / 'points.prj').write_text(prj, encoding='ascii')

    zip_path = tmp_path / 'points-shapefile.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for ext in ('shp', 'shx', 'dbf', 'prj'):
            f = shp_dir / f'points.{ext}'
            assert f.exists(), f
            zf.write(f, arcname=f.name)
    return zip_path


def test_shapefile_zip_is_recognized(tmp_path, make_dataset, upload_resource,
                                     session, ckan_url, wait_for_condition):
    """Upload a Point shapefile (zipped) and verify it gets imported as geospatial."""
    zip_path = _build_points_shapefile_zip(tmp_path)

    pkg = make_dataset('shapefile-points')
    res = upload_resource(pkg['id'], zip_path, fmt='SHP', name='points-shapefile.zip')

    def is_geo() -> bool:
        r = session.get(
            f'{ckan_url}/api/3/action/resource_show',
            params={'id': res['id']}, timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json()['result']
        return bool(data.get('is_geospatial') or data.get('datagis_imported'))

    wait_for_condition(is_geo, 'Shapefile flagged as geospatial', timeout=180)
