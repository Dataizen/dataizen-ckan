# tests-e2e — Limitations connues

## Bug #4 : hook `after_resource_create` synchrone trop lent

**Statut** : non corrigé (volontairement à part)

Le plugin OGC enchaîne synchroniquement dans `after_resource_create` :

1. import géospatial vers PostGIS / datagis
2. génération du mapfile MapServer
3. synchronisation pygeoapi
4. synchronisation CSW

Sur les ressources géospatiales (GeoJSON / Shapefile), le total peut
dépasser le `harakiri` uwsgi (50 s) qui tue alors le worker. La
ressource est déjà commit en base avant que le hook ne tourne, mais
le client reçoit une `RemoteDisconnected`.

### Mitigations mises en place

- `tests-e2e/conftest.py` : la fixture `upload_resource` attrape
  `ConnectionError` / `ReadTimeout` et récupère la ressource via
  `package_show` (polling). Le test peut alors continuer.
- La session HTTP a un `Retry` configuré sur (502, 503, 504) avec
  backoff, pour absorber les workers en train de respawn.
- `tests-e2e/test_mapserver_serves.py` : marqué `xfail(strict=False)` car
  ces tests dépendent du mapfile généré par le hook OGC. Le mapfile est
  bien produit (mais après ~5-7 minutes en local, contre 1-2 min en
  qualif), ce qui dépasse même un timeout de 360s. Quand le bug #4 sera
  corrigé (hook RQ-isé), ces tests passeront naturellement et le `xfail`
  pourra être retiré.

### Vraie correction (à faire)

Refactorer `_generate_mapfile_async`, `_sync_csw_async` et l'import
datagis pour qu'ils tournent vraiment en arrière-plan
(threading.Thread déjà partiellement utilisé, mais l'appel HTTP côté
hook reste bloquant). Idéalement les enqueuer dans la queue RQ que
gère xloader.

Tant que ce n'est pas fait, le boot est plus long que nécessaire et
les workers sont régulièrement tués par harakiri sous charge.

## Bug #4bis : Shapefile zip non servi par MapServer — CORRIGÉ

**Statut** : corrigé dans le commit `383dd1f`.

Symptôme historique : pour une ressource ``.zip`` de format ``SHP``, le
mapfile contenait ``CONNECTION "/var/lib/ckan/resources/<id3>/<id3>/<rest>"``
au lieu de ``CONNECTION "/vsizip/{<path>}/<file.shp>"``, ce qui faisait
échouer MapServer en ``msOGRFileOpen(): File not found or unsupported
format``.

Cause : ``_prepare_ogr_for_mapserver`` ne détectait pas le format ZIP
quand le fichier source était stocké sans extension (cas CKAN par
défaut : nom du fichier = id). Le code essayait de copier le zip vers
``/mapserver/data/ogr/`` mais cet emplacement n'est pas un volume
partagé avec MapServer, le fallback retombait sur le path direct.

Fix : détecter le magic byte ``PK\x03\x04`` au début du fichier et
retourner directement ``/vsizip/{<path>}/<file.shp>`` (la syntaxe avec
accolades de GDAL >= 3.3 contourne le besoin d'extension ``.zip``).

Le ``xfail`` sur ``test_shapefile_is_served_by_mapserver_wms`` peut
être retiré dès que bug #4 (hook synchrone, qui empêche le polling
mapfile dans les temps) est lui aussi corrigé.

## Bug #5 (mineur) : pycsw warning remonté comme erreur

`Erreur synchronisation CSW pour dataset ...` dans les logs avec en
fait juste un `UserWarning` de `pkg_resources` provenant de pycsw. Le
plugin OGC capture stderr et le loggue comme ERROR. À filtrer.

## Bug #6 (mineur) : datagis non créée hors --init

La création de `datagis` se fait dans
`postgresql/docker-entrypoint-initdb.d/15_create_datagis.sql`, qui
n'est exécuté qu'au premier boot du volume `pg_data`. Si la base
existait déjà avant l'ajout de ce script (ancienne stack), il faut
un `--init` (wipe pg_data) ou créer la base manuellement.
