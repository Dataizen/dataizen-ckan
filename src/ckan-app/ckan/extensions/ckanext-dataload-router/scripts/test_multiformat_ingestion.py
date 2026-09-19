# -*- coding: utf-8 -*-
r"""Test E2E reproductible du socle d'ingestion multi-format (ckanext-dataload-router).

Dépose un jeu de données par format dans une org CKAN (données fictives : 5 villes), puis
vérifie que chaque dépôt aboutit à la représentation canonique : chargement datastore et,
pour les formats géo, géométrisation (colonne PostGIS `geometry`, servie WMS/WFS).

Formats couverts : CSV, XLSX, Parquet, GeoJSON, Shapefile (ZIP), GeoPackage, GeoParquet
(GeoParquet en Lambert-93 pour vérifier la reprojection 2154 -> 4326).

Passe par l'API INTERNE de CKAN (contexte site-user, ignore_auth) : aucun token ni secret
manipulé. À lancer DANS le conteneur CKAN web+workers (celui qui sert localhost:5000 ET
exécute les workers RQ), avec l'environnement applicatif chargé. Idempotent (recrée les
jeux `test-format-*`). Les jeux créés hors seed sont éphémères (nettoyés par le reset démo).

Lancement (exemple, conteneur web+workers = shared.container.5) :
    sudo docker cp scripts/test_multiformat_ingestion.py shared.container.5:/tmp/t.py
    sudo docker cp /data/svc/ckan.env shared.container.5:/tmp/ckan.env
    sudo docker exec -u 0 shared.container.5 bash -lc '
      set -a
      while IFS= read -r l; do case "$l" in ""|\#*) continue;; *=*) export "$l";; esac; done < /tmp/ckan.env
      export CKAN___SECRET_KEY=boot-only CKAN___WTF_CSRF_SECRET_KEY=boot-only \
             CKAN___API_TOKEN__JWT__ENCODE__SECRET=string:boot-only \
             CKAN___API_TOKEN__JWT__DECODE__SECRET=string:boot-only \
             CKAN___CKAN__DATAPUSHER__API_TOKEN=boot-only
      set +a
      DEMO_ORG=demo python3 /tmp/t.py
      rm -f /tmp/ckan.env'

Le SECRET_KEY/JWT factices ne servent QU'AU boot (aucune signature) ; l'ingestion réelle
utilise la base/Solr/Redis de l'env-file. Env : DEMO_ORG (org cible, défaut « demo »).
"""
import io
import os
import time
import csv
import zipfile
import tempfile

from ckan.cli import CKANConfigLoader
from ckan.config.middleware import make_app

_conf = CKANConfigLoader(os.environ.get("CKAN_INI", "/srv/app/ckan.ini")).get_config()
_app = make_app(_conf)
# make_app renvoie une pile de middlewares WSGI ; déballer jusqu'à l'app Flask.
_flask = _app
for _ in range(12):
    if hasattr(_flask, "test_request_context"):
        break
    _flask = getattr(_flask, "app", None) or getattr(_flask, "flask_app", None) \
        or getattr(_flask, "wsgi_app", None)

from ckan import model  # noqa: E402
from ckan.plugins import toolkit  # noqa: E402
from werkzeug.datastructures import FileStorage  # noqa: E402

ORG = os.environ.get("DEMO_ORG", "demo")
_su = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})["name"]


def action(name, data):
    ctx = {"model": model, "session": model.Session, "ignore_auth": True, "user": _su}
    return toolkit.get_action(name)(ctx, data)


VILLES = [("Paris", 2148000, 2.3522, 48.8566), ("Marseille", 870000, 5.3698, 43.2965),
          ("Lyon", 522000, 4.8357, 45.7640), ("Nantes", 320000, -1.5536, 47.2184),
          ("Strasbourg", 291000, 7.7521, 48.5734)]
COLS = ["nom", "population", "lon", "lat"]


def _rows():
    return [{"nom": n, "population": p, "lon": lon, "lat": lat} for (n, p, lon, lat) in VILLES]


def _gdf(crs="EPSG:4326"):
    import geopandas as gpd
    from shapely.geometry import Point
    g = gpd.GeoDataFrame({"nom": [v[0] for v in VILLES], "population": [v[1] for v in VILLES]},
                         geometry=[Point(v[2], v[3]) for v in VILLES], crs="EPSG:4326")
    return g.to_crs(crs) if crs != "EPSG:4326" else g


def make_csv(p):
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(_rows())


def make_xlsx(p):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "villes"
    ws.append(COLS)
    for v in VILLES:
        ws.append(list(v))
    wb.save(p)


def make_geojson(p):
    _gdf().to_file(p, driver="GeoJSON")


def make_shp_zip(p):
    d = tempfile.mkdtemp()
    _gdf().to_file(os.path.join(d, "villes.shp"), driver="ESRI Shapefile")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in os.listdir(d):
            z.write(os.path.join(d, fn), fn)


def make_gpkg(p):
    _gdf().to_file(p, driver="GPKG", layer="villes")


def make_parquet(p):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({c: [r[c] for r in _rows()] for c in COLS}), p)


def make_geoparquet(p):
    _gdf("EPSG:2154").to_parquet(p)  # Lambert-93 : teste la reprojection -> 4326


# (slug, titre, fabrique, nom fichier, format, géo attendu ?)
JEUX = [
    ("test-format-csv", "Test format : CSV", make_csv, "villes.csv", "CSV", True),
    ("test-format-xlsx", "Test format : Excel XLSX", make_xlsx, "villes.xlsx", "XLSX", True),
    ("test-format-parquet", "Test format : Parquet", make_parquet, "villes.parquet", "Parquet", True),
    ("test-format-geojson", "Test format : GeoJSON", make_geojson, "villes.geojson", "GeoJSON", True),
    ("test-format-shapefile", "Test format : Shapefile (zip)", make_shp_zip, "villes.zip", "SHP", True),
    ("test-format-geopackage", "Test format : GeoPackage", make_gpkg, "villes.gpkg", "GPKG", True),
    ("test-format-geoparquet", "Test format : GeoParquet Lambert-93", make_geoparquet, "villes.parquet", "Parquet", True),
]
NOTES = ("Jeu de test du socle multi-format Dataizen (données fictives). Déposé au format {fmt} "
         "pour vérifier l'ingestion : datastore et, le cas échéant, géométrisation.")


def ensure_dataset(slug, title, fmt):
    try:
        pkg = action("package_show", {"id": slug})
        for r in pkg.get("resources", []):
            try:
                action("resource_delete", {"id": r["id"]})
            except Exception:
                pass
        action("package_patch", {"id": slug, "title": title, "notes": NOTES.format(fmt=fmt),
                                 "owner_org": ORG, "private": False})
        return pkg["id"]
    except toolkit.ObjectNotFound:
        return action("package_create", {"name": slug, "title": title, "owner_org": ORG,
                                         "private": False, "license_id": "cc-by",
                                         "notes": NOTES.format(fmt=fmt)})["id"]


def upload(pkg_id, fname, fmt, path):
    with open(path, "rb") as fh:
        fs = FileStorage(stream=io.BytesIO(fh.read()), filename=fname, content_type="application/octet-stream")
        action("resource_create", {"package_id": pkg_id, "name": fname, "format": fmt, "upload": fs})


def poll(slug, tries=40, delay=6):
    """OK dès qu'une ressource du jeu est datastore_active (géo si l'une est is_geospatial)."""
    ds = geo = False
    for _ in range(tries):
        pkg = action("package_show", {"id": slug})
        res = pkg.get("resources", [])
        ds = any(r.get("datastore_active") for r in res)
        geo = any(r.get("is_geospatial") for r in res)
        if ds:
            break
        time.sleep(delay)
    return ds, geo


def main():
    print("== Test multi-format, org '%s' (site-user=%s) ==" % (ORG, _su))
    results = []
    for slug, title, fab, fname, fmt, want_geo in JEUX:
        try:
            tmp = os.path.join(tempfile.gettempdir(), fname)
            fab(tmp)
            pkg_id = ensure_dataset(slug, title, fmt)
            upload(pkg_id, fname, fmt, tmp)
            ds, geo = poll(slug)
            ok = ds and (geo or not want_geo)
            results.append((fmt, slug, ok, ds, geo))
            print("  [%-22s] %-9s datastore=%s geo=%s -> %s" % (slug, fmt, ds, geo, "OK" if ok else "KO"))
        except Exception as e:
            import traceback
            results.append((fmt, slug, False, False, False))
            print("  [%-22s] %-9s ECHEC: %s" % (slug, fmt, str(e)[:160]))
            traceback.print_exc()
        model.Session.remove()
    ok_n = sum(1 for r in results if r[2])
    print("\n== Récapitulatif : %d/%d OK ==" % (ok_n, len(results)))
    for fmt, slug, ok, ds, geo in results:
        print("  %-9s %-22s -> %s" % (fmt, slug, "OK" if ok else "KO"))


if __name__ == "__main__":
    with _flask.test_request_context():
        main()
