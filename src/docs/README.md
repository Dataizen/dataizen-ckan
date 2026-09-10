# Documentation dataizen-ckan

Documentation technique du portail CKAN BFC : architecture, moissonnage,
chargement de données, services OGC et points d'exploitation.

## Architecture

- [ARCHITECTURE.md](ARCHITECTURE.md) : architecture géospatiale complète, CKAN + PostGIS + MapServer + pygeoapi + datagis.
- [ARCHITECTURE-XLOADER-COMPLETE.md](ARCHITECTURE-XLOADER-COMPLETE.md) : flux complet de chargement XLoader vers le datastore.
- [PYGEOAPI-ROLE.md](PYGEOAPI-ROLE.md) : rôle de pygeoapi dans la chaîne.
- [TOPO-MAPFILES.md](TOPO-MAPFILES.md) : organisation et génération des mapfiles MapServer.
- [KUBERNETES-CONFIG.md](KUBERNETES-CONFIG.md) : configuration de déploiement Kubernetes.

## Moissonnage CSW / CKAN

- [MOISSONNAGE-CKAN.md](MOISSONNAGE-CKAN.md) : principes du moissonnage CKAN.
- [TYPES-MOISSONNAGE.md](TYPES-MOISSONNAGE.md) : types de moissonneurs disponibles.
- [MOISSONNAGE-RECURRENT-CRON.md](MOISSONNAGE-RECURRENT-CRON.md) : programmation cron des moissonnages.
- [FILTRAGE-MOISSONNAGE-CSW.md](FILTRAGE-MOISSONNAGE-CSW.md) : filtrage des sources CSW.
- [CSW-VALIDATION-ERRORS.md](CSW-VALIDATION-ERRORS.md) : erreurs de validation CSW et corrections automatiques.

## Import et synchronisation

- [IMPORT-CKAN-SCRIPT.md](IMPORT-CKAN-SCRIPT.md) : utilisation de `import-from-ckan.py`.
- [IMPORT-METADATA-VERIFICATION.md](IMPORT-METADATA-VERIFICATION.md) : métadonnées récupérées par l'import CKAN.
- [IMPORT-DATAGIS-EXPLICATION.md](IMPORT-DATAGIS-EXPLICATION.md) : import géospatial vers la base datagis.
- [IMPORT-DATAGIS-LOGS.md](IMPORT-DATAGIS-LOGS.md) : logs et déclencheurs de l'import datagis.
- [PROCESSUS-DEPOT-DONNEES-API.md](PROCESSUS-DEPOT-DONNEES-API.md) : processus de dépôt de données via API.
- [ADMIN-SYNC-MAPSERVER.md](ADMIN-SYNC-MAPSERVER.md) : action admin `admin_sync_mapserver`.

## Workers et XLoader

- [EXPLICATION-CKAN-JOBS-WORKER.md](EXPLICATION-CKAN-JOBS-WORKER.md) : différence entre `ckan jobs worker` et worker RQ.
- [WORKERS-XLOADER.md](WORKERS-XLOADER.md) : workers XLoader.
- [GUIDE-LOGS-XLOADER.md](GUIDE-LOGS-XLOADER.md) : guide des logs XLoader.
- [IDENTIFIER-REQUETES-LENTES.md](IDENTIFIER-REQUETES-LENTES.md) : identification des requêtes HTTP lentes.

## Références

- [ANALYSE_CKAN_IDEO.md](ANALYSE_CKAN_IDEO.md) : analyse du CKAN IDEO de référence.
- [GUIDE_QGIS.md](GUIDE_QGIS.md) : utilisation depuis QGIS.
- [SUBSCRIPTIONS.md](SUBSCRIPTIONS.md) : système de souscriptions.
