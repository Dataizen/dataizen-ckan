# dataizen-ckan

Image CKAN de la plateforme **Dataizen** : l'image officielle `ckan/ckan-base:2.11`
enrichie de nos extensions (catalogue de données ouvertes, services géospatiaux OGC,
SSO, harmonisation), plus le Dockerfile, la configuration et les scripts de build.

Ce dépôt ne contient pas le code source de CKAN lui-même : CKAN est installé par pip
dans l'image de base. On y trouve le Dockerfile, nos extensions, des scripts d'entrée
et de la configuration.

## Extensions maison

`ckanext-ogc` (WMS/WFS MapServer, pygeoapi, mapfiles, import géospatial datagis,
choroplèthe), `ckanext-dataload-router` (ingestion CSV/GeoJSON/xlsx vers le datastore,
colonne `the_geom`), `ckanext-keycloak` (SSO OIDC), `ckanext-admin-tools`,
`ckanext-custom-licenses`, `ckanext-dcat-patch`, `ckanext-harvest-xloader`,
`ckanext-rag` (fraîcheur de l'index RAG), `ckanext-dolfin` (profil DOLFIN).

## Build

Build natif linux/amd64 (buildx), publication vers le registre de la plateforme :

```bash
cd src
./build-push.sh build-push ckan
```

Les tags d'image sont figés (jamais `latest`), au format `dataizen-ckan:2.11-dtz.<n>`.

## Tests

Suite end-to-end pytest (`src/tests-e2e/`) qui pousse de vrais jeux (CSV, GeoJSON,
shapefile) dans une instance CKAN et vérifie l'ingestion datastore, la reconnaissance
géo, les services OGC et le rendu MapServer :

```bash
cd src
pip install -r tests-e2e/requirements.txt
CKAN_URL=<url> CKAN_API_KEY=<token-sysadmin> pytest tests-e2e/ -v
```

## Licence

CKAN est distribué sous **GNU Affero General Public License v3.0** (AGPL-3.0). Nos
extensions importent des internals de CKAN : elles en sont dérivées et sont donc, elles
aussi, sous **AGPL-3.0** (chaque extension porte son fichier `LICENSE` ; texte complet
de CKAN dans `src/ckan-app/LICENSE.txt`).

L'AGPL impose (article 13) que tout utilisateur qui interagit avec le logiciel modifié
à travers le réseau puisse obtenir le code source correspondant : ce dépôt public en est
l'offre de source. Voir [THIRD_PARTY.md](THIRD_PARTY.md) pour l'inventaire des composants
et de leurs licences.

## Provenance

Ce fork descend d'une image CKAN développée pour un observatoire régional (projet
DataBFC). Il a depuis divergé et évolue de façon autonome pour Dataizen.
