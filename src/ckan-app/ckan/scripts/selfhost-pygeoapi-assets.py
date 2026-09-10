#!/usr/bin/env python3
"""Auto-héberge les assets externes des pages HTML de pygeoapi (RGESN : pas de CDN).

Télécharge Leaflet, markercluster, VectorGrid, Bootstrap, tablesort, c3/d3, covjson…
dans le dossier `static/lib/` de pygeoapi, puis réécrit les templates pour pointer vers
`{{ config['server']['url'] }}/static/lib/<fichier>` au lieu des URL unpkg/cloudflare/jsdelivr.

Résilient : un asset qui ne se télécharge pas est laissé sur son CDN (pas de page cassée) ;
le build n'échoue pas. Exécuté une fois à la construction de l'image CKAN (le conteneur
pygeoapi utilise cette image)."""
import os
import re
import sys
import urllib.request

import pygeoapi

PKG = os.path.dirname(pygeoapi.__file__)
STATIC_LIB = os.path.join(PKG, "static", "lib")
TEMPLATES = os.path.join(PKG, "templates")

# CDN -> nom de fichier local (plusieurs URL peuvent viser le même fichier).
ASSETS = {
    "https://unpkg.com/bootstrap@5.1.3/dist/css/bootstrap.min.css": "bootstrap.min.css",
    "https://unpkg.com/leaflet@1.3.1/dist/leaflet.css": "leaflet.css",
    "https://unpkg.com/leaflet@1.3.1/dist/leaflet.js": "leaflet.js",
    "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.3.1/leaflet.js": "leaflet.js",
    "https://unpkg.com/leaflet.markercluster/dist/MarkerCluster.css": "MarkerCluster.css",
    "https://unpkg.com/leaflet.markercluster/dist/MarkerCluster.Default.css": "MarkerCluster.Default.css",
    "https://unpkg.com/leaflet.markercluster/dist/leaflet.markercluster-src.js": "leaflet.markercluster-src.js",
    "https://unpkg.com/leaflet.vectorgrid@latest/dist/Leaflet.VectorGrid.bundled.js": "Leaflet.VectorGrid.bundled.js",
    "https://cdn.jsdelivr.net/npm/c3@0.7.20/c3.css": "c3.css",
    "https://cdn.jsdelivr.net/npm/c3@0.7.20/c3.js": "c3.js",
    "https://cdn.jsdelivr.net/npm/d3@5.16.0/dist/d3.js": "d3.js",
    "https://unpkg.com/tablesort@5.3.0/dist/tablesort.min.js": "tablesort.min.js",
    "https://unpkg.com/leaflet-coverage@0.7/leaflet-coverage.css": "leaflet-coverage.css",
    "https://unpkg.com/leaflet-coverage@0.7/leaflet-coverage.min.js": "leaflet-coverage.min.js",
    "https://unpkg.com/covjson-reader@0.16/covjson-reader.src.js": "covjson-reader.src.js",
    "https://unpkg.com/covutils@0.6/covutils.min.js": "covutils.min.js",
    # page /openapi (documentation interactive) + polyfill
    "https://unpkg.com/swagger-ui-dist/swagger-ui.css": "swagger-ui.css",
    "https://unpkg.com/swagger-ui-dist/swagger-ui-bundle.js": "swagger-ui-bundle.js",
    "https://unpkg.com/swagger-ui-dist/swagger-ui-standalone-preset.js": "swagger-ui-standalone-preset.js",
    "https://unpkg.com/swagger-ui-dist/favicon-16x16.png": "swagger-favicon-16x16.png",
    "https://unpkg.com/swagger-ui-dist/favicon-32x32.png": "swagger-favicon-32x32.png",
    "https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js": "redoc.standalone.js",
    "https://cdnjs.cloudflare.com/ajax/libs/html5shiv/3.7.3/html5shiv.js": "html5shiv.js",
}
# URL référencées par les templates mais qu'on ne télécharge pas sous ce libellé
# (ex. tag `redoc@next` retiré de jsdelivr) : on les fait pointer vers le fichier local
# déjà rapatrié sous un autre nom.
ALIASES = {
    "https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js": "redoc.standalone.js",
}
LOCAL = "{{ config['server']['url'] }}/static/lib/"
# CSS Google Fonts référencée dans les templates (à rapatrier avec ses woff2).
GOOGLE_FONTS = "https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0 Safari/537.36")  # UA moderne -> woff2 (sinon Google renvoie du ttf)


def _fetch(url, ua="dtz-build"):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def selfhost_fonts():
    """Rapatrie la CSS Google Fonts + ses woff2 en local, et renvoie le mapping
    {url d'origine -> chemin local} pour la réécriture des templates. Best-effort."""
    try:
        css = _fetch(GOOGLE_FONTS, ua=_UA).decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"  fonts: CSS indisponible ({e}), laissée sur CDN", file=sys.stderr)
        return {}
    fonts_dir = os.path.join(STATIC_LIB, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    for i, url in enumerate(re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css)):
        name = f"font{i}.woff2"
        try:
            with open(os.path.join(fonts_dir, name), "wb") as f:
                f.write(_fetch(url, ua=_UA))
            # fonts.css est servie en statique (pas de rendu Jinja) : chemin RELATIF au
            # fichier (/static/lib/fonts.css -> fonts/<name>), surtout pas le placeholder
            # `{{ config['server']['url'] }}` de LOCAL qui resterait littéral dans le CSS.
            css = css.replace(url, "fonts/" + name)
        except Exception:  # noqa: BLE001
            pass
    with open(os.path.join(STATIC_LIB, "fonts.css"), "w", encoding="utf-8") as f:
        f.write(css)
    print("  fonts: Google Fonts rapatriées en local (fonts.css + woff2)")
    return {GOOGLE_FONTS: LOCAL + "fonts.css"}


def main():
    os.makedirs(STATIC_LIB, exist_ok=True)
    ok = {}
    for url, name in ASSETS.items():
        dest = os.path.join(STATIC_LIB, name)
        if os.path.exists(dest):
            ok[url] = name
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dtz-build"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            if data:
                with open(dest, "wb") as f:
                    f.write(data)
                ok[url] = name
                print(f"  ok {name} ({len(data)} o)")
        except Exception as e:  # noqa: BLE001
            print(f"  ÉCHEC {url} : {e} (laissé sur CDN)", file=sys.stderr)

    # Alias : URL de template pointant vers un fichier déjà rapatrié sous un autre nom.
    downloaded = set(ok.values())
    for url, name in ALIASES.items():
        if name in downloaded:
            ok[url] = name

    # Google Fonts : CSS + woff2 rapatriés, ajoutés au mapping de réécriture.
    ok.update(selfhost_fonts())

    # Réécriture des templates : remplacer chaque URL téléchargée par le chemin local.
    n = 0
    for root, _, files in os.walk(TEMPLATES):
        for fn in files:
            if not fn.endswith((".html", ".j2")):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            new = txt
            for url, name in ok.items():
                new = new.replace(url, LOCAL + name)
            if new != txt:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(new)
                n += 1
    print(f"assets auto-hébergés : {len(ok)}/{len(ASSETS)} · templates réécrits : {n}")


if __name__ == "__main__":
    main()
