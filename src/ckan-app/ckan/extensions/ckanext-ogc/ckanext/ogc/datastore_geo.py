# -*- coding: utf-8 -*-
"""Géo « en colonne » du datastore -> colonne géométrie PostGIS `_geom` + index GIST.

Un jeu tabulaire (CSV/xlsx chargé au datastore) peut porter la géo dans une COLONNE :
géométrie WKT ou GeoJSON (ex. « Geo Shape ») et/ou point « lat, lon » (ex. « Geo Point »).
Ce module détecte ces colonnes (par NOM + échantillon de CONTENU) et construit une vraie
colonne géométrie PostGIS `_geom` (+ index spatial GIST). Une fois `_geom` présente, la
génération de mapfile existante (generate-mapfile.py) la reconnaît et sert la couche en
WMS/WFS MapServer : le rendu est côté serveur, filtré par l'emprise visible (jamais tous
les tracés côté client).

La géométrisation est faite EN BASE (PostGIS, pas de mémoire CKAN), par BATCHES qui
commitent (résumable, borné) et TOLÈRE les valeurs invalides (savepoint par batch, repli
ligne à ligne sur un batch fautif). Pensé pour tourner en JOB RQ (plusieurs minutes sur
des millions de lignes).

État exposé sur la ressource via des extras (pour l'indicateur « traitement en cours ») :
`dtz_geo_status` = detecting|geometrizing|indexing|mapfile|ready|none|error, avec
`dtz_geo_done`/`dtz_geo_total` pour la progression.
"""
import json
import logging
import os
import re

log = logging.getLogger(__name__)

GEOM_COL = "geometry"              # colonne géométrie PostGIS construite (nom attendu par
                                   # l'engine mapfile existant : generate-mapfile.py la détecte
                                   # via _check_geometry_column_exists et sert la couche PostGIS)
BATCH = 100000                     # lignes par batch (commit à chaque batch)

# Détection par nom (normalisé : espaces/underscores/casse ignorés).
POINT_NAMES = {"geopoint", "geopoint2d", "point", "coordonnees", "coordinates", "position", "latlon", "latlong"}
GEOM_NAMES = {"geoshape", "geoshape2d", "geometry", "geometrie", "geom", "thegeom", "wkt", "geojson", "shape", "contour"}
LAT_NAMES = {"latitude", "lat", "ylat"}
LON_NAMES = {"longitude", "lon", "lng", "long", "xlon"}

_WKT_RE = re.compile(r"^\s*(SRID=\d+\s*;)?\s*(MULTI)?(POINT|LINESTRING|POLYGON|GEOMETRYCOLLECTION)\s*[(Z]", re.I)
_LATLON_RE = re.compile(r"^\s*-?\d{1,3}(?:\.\d+)?\s*,\s*-?\d{1,3}(?:\.\d+)?\s*$")


def _norm(name):
    return re.sub(r"[\s_\-]+", "", str(name or "").strip().lower())


def _looks_geometry(v):
    s = "" if v is None else str(v).strip()
    if _WKT_RE.match(s):
        return "wkt"
    if s.startswith("{") and ('"coordinates"' in s or '"type"' in s):
        return "geojson"
    return None


def _looks_latlon(v):
    return bool(_LATLON_RE.match(str("" if v is None else v)))


def detect_geo(fields, sample_records):
    """fields: liste de {id}. sample_records: quelques lignes du datastore.
    Retourne {kind, col, lat_col, lon_col} ou None. kind ∈ geojson|wkt|point|latlon."""
    ids = [f.get("id") for f in (fields or []) if f.get("id") and f.get("id") != "_id" and f.get("id") != GEOM_COL]
    recs = (sample_records or [])[:5]

    def sample(col):
        for r in recs:
            v = r.get(col)
            if v not in (None, ""):
                return v
        return None

    # 1. Colonne géométrie (WKT/GeoJSON) : contenu prioritaire, sinon nom.
    for c in ids:
        k = _looks_geometry(sample(c))
        if k:
            return {"kind": k, "col": c}
    for c in ids:
        if _norm(c) in GEOM_NAMES:
            k = _looks_geometry(sample(c)) or "geojson"
            return {"kind": k, "col": c}

    # 2. Colonne point « lat, lon » : contenu, sinon nom.
    for c in ids:
        if _looks_latlon(sample(c)):
            return {"kind": "point", "col": c}
    for c in ids:
        if _norm(c) in POINT_NAMES and _looks_latlon(sample(c)):
            return {"kind": "point", "col": c}

    # 3. Paire lat/lon séparée.
    lat = next((c for c in ids if _norm(c) in LAT_NAMES), None)
    lon = next((c for c in ids if _norm(c) in LON_NAMES), None)
    if lat and lon:
        return {"kind": "latlon", "lat_col": lat, "lon_col": lon}
    return None


def _geom_expr(kind, col=None, lat_col=None, lon_col=None):
    """Expression SQL qui construit la géométrie 4326 depuis la/les colonne(s) source."""
    q = lambda c: '"' + c.replace('"', '""') + '"'
    if kind == "geojson":
        return "ST_SetSRID(ST_GeomFromGeoJSON({}),4326)".format(q(col))
    if kind == "wkt":
        return "ST_SetSRID(ST_GeomFromText({}),4326)".format(q(col))
    if kind == "point":
        # « lat, lon » -> point(lon, lat)
        c = q(col)
        return ("ST_SetSRID(ST_MakePoint("
                "split_part({c},',',2)::float8, split_part({c},',',1)::float8),4326)").format(c=c)
    if kind == "latlon":
        return "ST_SetSRID(ST_MakePoint({}::float8,{}::float8),4326)".format(q(lon_col), q(lat_col))
    raise ValueError("kind inconnu: %s" % kind)


def _connect():
    """Connexion écriture à la base datastore (mêmes creds que le plugin ogc)."""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("POSTGIS_HOST", "db"),
        port=int(os.getenv("POSTGIS_PORT", "5432")),
        dbname=os.getenv("POSTGIS_DB", "datastore"),
        user=os.getenv("POSTGIS_USER", "ckan"),
        password=os.getenv("POSTGRES_PASSWORD", "ckan"),
    )


def geometrize(resource_id, detect, progress=None):
    """Construit `_geom` pour la ressource, par batches robustes. `detect` = sortie de
    detect_geo(). `progress(done, total)` optionnel. Retourne (done, total, geom_type)."""
    kind = detect["kind"]
    expr = _geom_expr(kind, detect.get("col"), detect.get("lat_col"), detect.get("lon_col"))
    tbl = '"' + resource_id.replace('"', '""') + '"'
    # source non nulle (pour compter/where) : la colonne géo, ou la colonne lat pour latlon.
    src = detect.get("col") or detect.get("lat_col")
    srcq = '"' + src.replace('"', '""') + '"'

    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} geometry(Geometry,4326);".format(tbl, GEOM_COL))
        conn.commit()
        cur.execute("SELECT coalesce(max(_id),0) FROM {};".format(tbl))
        maxid = cur.fetchone()[0] or 0
        done = 0
        b = 0
        while b < maxid:
            hi = b + BATCH
            where = "_id > %s AND _id <= %s AND {g} IS NULL AND {s} IS NOT NULL AND {s}::text <> ''".format(g=GEOM_COL, s=srcq)
            upd = "UPDATE {t} SET {g} = {e} WHERE {w}".format(t=tbl, g=GEOM_COL, e=expr, w=where)
            try:
                cur.execute("SAVEPOINT b;")
                cur.execute(upd, (b, hi))
                cur.execute("RELEASE SAVEPOINT b;")
                conn.commit()
            except Exception as e:
                # Batch fautif (valeur non parsable) : repli ligne à ligne, on saute les mauvaises.
                conn.rollback()
                log.warning("[geo] batch %s-%s fautif (%s), repli ligne à ligne", b, hi, e)
                cur.execute("SELECT _id, {s}::text FROM {t} WHERE _id > %s AND _id <= %s AND {g} IS NULL AND {s} IS NOT NULL".format(s=srcq, t=tbl, g=GEOM_COL), (b, hi))
                rows = cur.fetchall()
                one = "UPDATE {t} SET {g} = {e} WHERE _id = %s".format(t=tbl, g=GEOM_COL, e=expr.replace(srcq, "%s"))
                for rid, val in rows:
                    try:
                        cur.execute("SAVEPOINT r;")
                        # ré-injecter la valeur : expr référence la colonne, on repasse par la table
                        cur.execute("UPDATE {t} SET {g} = {e} WHERE _id = %s".format(t=tbl, g=GEOM_COL, e=expr), (rid,))
                        cur.execute("RELEASE SAVEPOINT r;")
                    except Exception:
                        cur.execute("ROLLBACK TO SAVEPOINT r;")
                conn.commit()
            b = hi
            if progress:
                try:
                    progress(min(b, maxid), maxid)
                except Exception:
                    pass
        cur.execute("SELECT count({}) FROM {};".format(GEOM_COL, tbl))
        done = cur.fetchone()[0] or 0
        # Index spatial GIST (idempotent) + ANALYZE.
        idx = "geom_gix_" + re.sub(r"[^a-z0-9]", "", resource_id.lower())[:24]
        cur.execute('CREATE INDEX IF NOT EXISTS "{}" ON {} USING GIST ({});'.format(idx, tbl, GEOM_COL))
        conn.commit()
        cur.execute("ANALYZE {};".format(tbl))
        conn.commit()
        cur.execute("SELECT ST_GeometryType({}) FROM {} WHERE {} IS NOT NULL LIMIT 1;".format(GEOM_COL, tbl, GEOM_COL))
        row = cur.fetchone()
        geom_type = row[0] if row else None
        log.info("[geo] ressource %s : _geom peuplée (%s/%s), type %s", resource_id, done, maxid, geom_type)
        return done, maxid, geom_type
    finally:
        cur.close()
        conn.close()
