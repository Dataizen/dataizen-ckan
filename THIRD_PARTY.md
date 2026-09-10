# Composants tiers et licences (dataizen-ckan)

Ce dépôt construit une image CKAN dérivée. Il ne contient PAS le code source de CKAN :
c'est l'image officielle `ckan/ckan-base:2.11` (CKAN installé par pip) sur laquelle nous
superposons un Dockerfile, de la configuration, des scripts et nos extensions.

## Socle CKAN

- **CKAN** (`ckan/ckan-base:2.11`) : GNU Affero General Public License v3.0 (AGPL-3.0).
  Texte complet dans `src/ckan-app/LICENSE.txt`.

L'AGPL est un copyleft « réseau » (article 13) : toute personne qui interagit avec le
logiciel modifié à travers le réseau doit pouvoir obtenir le code source correspondant.
C'est pourquoi ce dépôt est public et lié depuis les instances Dataizen.

## Extensions maison (dans ce dépôt)

Toutes sous **AGPL-3.0** (elles importent des internals de CKAN : `ckan.plugins`,
`ckan.model`, `ckanext.datastore`... donc œuvres dérivées de CKAN). Chacune porte son
fichier `LICENSE`.

| Extension | Rôle |
|---|---|
| `ckanext-ogc` | WMS/WFS (MapServer), pygeoapi, génération de mapfiles, import géospatial datagis, choroplèthe |
| `ckanext-dataload-router` | ingestion CSV / GeoJSON / xlsx multi-onglets vers le datastore, colonne `the_geom` |
| `ckanext-keycloak` | SSO OIDC (Keycloak) |
| `ckanext-admin-tools` | tâches et actions d'administration |
| `ckanext-custom-licenses` | liste de licences personnalisée |
| `ckanext-dcat-patch` | ajustements DCAT-AP |
| `ckanext-harvest-xloader` | liaison moissonnage + xloader |
| `ckanext-rag` | fraîcheur de l'index RAG (webhooks vers le service dtz-rag) |
| `ckanext-dolfin` | profil d'harmonisation DOLFIN |

## Extensions amont (installées au build par pip, non redistribuées en source ici)

Tirées de leurs dépôts d'origine, chacune sous sa propre licence (majoritairement
AGPL-3.0 ou MIT) : `ckanext-harvest`, `ckanext-dcat`, `ckanext-geoview`,
`ckanext-spatial`, `ckanext-scheming`, `ckanext-showcase`, `ckanext-xloader`,
`ckanext-composite`.

## Autres briques du déploiement (images séparées, hors de ce dépôt)

MapServer, pygeoapi, GDAL/OGR, PostGIS, Solr, Redis : chacune sous sa licence propre,
tirée de son image officielle.
