# CKAN Dataizen

Catalogue de données ouvertes de la région Bourgogne-Franche-Comté, construit autour de CKAN 2.11 avec services OGC (WMS, WFS, OGC API) via MapServer et pygeoapi, authentification SSO Keycloak et chaîne de chargement XLoader.

## Démarrage rapide en local

1. Récupérer ou créer un fichier `.env` à partir du modèle :
   ```bash
   cp .env.example .env
   # éditer .env et remplacer toutes les valeurs CHANGE_ME_*
   ```

2. Démarrer la stack (premier lancement, init des bases) :
   ```bash
   ./start-local.sh --init
   ```

3. Pour redémarrer sans toucher aux données (après modifications du code) :
   ```bash
   ./start-local.sh
   ```

4. Pour importer un dump existant (CKAN + datastore) :
   ```bash
   ./start-local.sh --import-ckan dumps/ckan_20250124.sql --import-datastore dumps/datastore_20250124.sql
   ```

## Avertissement sécurité

Le fichier `.env.example` contient des placeholders `CHANGE_ME_*` qu'il faut impérativement remplacer avant tout déploiement. La clé API par défaut `ckan-local-dev-apikey` est utilisée par `start-local.sh` pour configurer l'utilisateur `ckan_admin` en environnement local : elle est référencée par les scripts de dev (sync-ogc, scripts d'import, etc.). En production, il faut un vrai token et il faut surcharger `CKAN_API_KEY` côté configmap, sans réutiliser cette valeur dans la base CKAN.

Les anciennes versions de ce dépôt ont pu contenir de vraies valeurs de qualification dans `.env.example` (KEYCLOAK_PASSWORD, CKAN_OIDC_CLIENT_SECRET, SECRET_KEY datapusher). Ces valeurs sont à considérer compromises : faire tourner les secrets correspondants côté Keycloak et datapusher avant la mise en service.

## Compilation pour amd64 sur macOS ARM

Si vous êtes sur macOS avec un processeur ARM (M1/M2/M3/M4), les images doivent être compilées en `linux/amd64` pour rester compatibles avec l'hôte Ubuntu de production.

```bash
# Configurer buildx une fois
./setup-buildx.sh

# Ou manuellement
docker buildx create --name amd64-builder --driver docker-container --platform linux/amd64 --use
docker buildx inspect --bootstrap
```

Les variables `DOCKER_BUILDKIT=1` et `COMPOSE_DOCKER_CLI_BUILD=1` sont positionnées automatiquement par `start-local.sh`.

## Déploiement en production

Méthode recommandée, via le wrapper qui configure buildx :

```bash
./build-push.sh build-push ckan
# ou séparément
./build-push.sh build ckan
./build-push.sh push ckan
```

Méthode manuelle :

```bash
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
docker compose build --platform linux/amd64 ckan
docker compose push
```

Penser à mettre à jour les configmaps avant de pousser une nouvelle image sur qualif ou prod.

## Fonctionnement

Un jeu de données poussé via l'API CKAN (par exemple depuis un CMS ou une API tierce) est traité par le plugin `dataload_router`. Selon le type de données, il est converti en CSV ou GeoJSON, puis transmis à XLoader qui pousse les lignes dans le datastore PostgreSQL avec leurs métadonnées. Le dataset devient alors visible dans le catalogue.

Pour les ressources géospatiales, le plugin `ogc` synchronise les datasets vers pygeoapi (OGC API Features) et MapServer (WMS/WFS) à la création / modification.

Si le catalogue semble vide après import :

```bash
docker compose exec ckan ckan -c /srv/app/ckan.ini search-index rebuild
```

## Exemples API

L'API est documentée sur https://docs.ckan.org/en/2.11/api/. Quelques appels utiles, avec la clé `ckan_admin` de dev local :

Création d'une ressource :
```bash
curl -X POST "http://localhost:8080/api/action/resource_create" \
  -H "Authorization: ckan-local-dev-apikey" \
  -F "package_id=testxloader" \
  -F "name=test-ok-geo.csv" \
  -F "format=csv" \
  -F "upload=@test-ok-geo.csv"
```

Listing :
```bash
curl -s -H "Authorization: ckan-local-dev-apikey" \
  http://localhost:8080/api/3/action/organization_list

curl -s -H "Authorization: ckan-local-dev-apikey" \
  http://localhost:8080/api/3/action/package_list
```

Détails d'un dataset :
```bash
curl -H "Authorization: ckan-local-dev-apikey" \
  "http://localhost:8080/api/3/action/package_show?id=ensemble-des-etablissements-du-repertoire-sirene-geolocalises"
```

## Plugins CKAN

Cette image embarque les extensions suivantes :

- **Visualisation**
  - `image_view`, `text_view`, `datatables_view`
  - `geo_view`, `geojson_view`, `wmts_view`, `shp_view` (ckanext-geoview)
  - `spatial_metadata`, `spatial_query` (ckanext-spatial)

- **Moissonnage**
  - `harvest`, `ckan_harvester` (ckanext-harvest)
  - `dcat`, `dcat_json_harvester`, `dcat_rdf_harvester` (ckanext-dcat)

- **Chargement de données**
  - `xloader` (https://github.com/ckan/ckanext-xloader)
  - `harvest_xloader` : code inclus, branche XLoader sur les ressources moissonnées
  - `dataload_router` : code inclus, route les fichiers entrants vers datapusher/xloader avec conversion CSV/GeoJSON

- **OGC**
  - `ogc` : synchronisation automatique des datasets géospatiaux vers pygeoapi et génération des mapfiles MapServer

- **Authentification**
  - `keycloak` : authentification SSO via Keycloak (fork keitaroinc inclus, non maintenu en interne)

- **Personnalisation**
  - `admin_tools` : actions d'administration custom (sync mapfiles, etc.)
  - `custom_licenses` : licences open data françaises (LOV2, ODBL, etc.)
  - `dcat_patch` : ajustements DCAT pour le profil français

- **Autres**
  - `datastore` : tables tabulaires PostgreSQL
  - `activity` : flux d'activité
  - `envvars` : injection de variables d'environnement depuis configmap (prod)

## Structure du projet

```
dataizen-ckan/
├── .env.example              modèle de configuration
├── docker-compose.yml        stack Docker (ckan, db, solr, redis, pygeoapi, datapusher)
├── start-local.sh            démarrage local
├── build-push.sh             wrapper build & push prod
├── setup-buildx.sh           configuration buildx amd64 (macOS ARM)
├── sync-ogc.sh               synchronisation manuelle CKAN -> pygeoapi
├── import-from-ckan.py       import d'un CKAN source vers un CKAN cible (datasets + métadonnées)
├── find-dataset-id.sh        utilitaire de lookup
├── find-resource-id.sh       utilitaire de lookup
├── ckan-app/ckan/            image CKAN
│   ├── Dockerfile
│   ├── docker-entrypoint.d/  scripts d'initialisation (patches, config)
│   ├── extensions/           extensions CKAN
│   │   ├── ckanext-admin-tools/
│   │   ├── ckanext-custom-licenses/
│   │   ├── ckanext-dataload-router/
│   │   ├── ckanext-dcat-patch/
│   │   ├── ckanext-harvest-xloader/
│   │   ├── ckanext-keycloak/
│   │   └── ckanext-ogc/      synchro pygeoapi + génération mapfiles
│   ├── patches/              patches appliqués sur pygeoapi en build
│   ├── scripts/              scripts d'admin embarqués dans l'image
│   ├── supervisor.d/         conf supervisor (worker xloader, harvester, cron)
│   └── docs/                 docs spécifiques au build CKAN
├── mapserver/                image MapServer
├── postgresql/               init scripts PostgreSQL
├── pygeoapi_storage/         configuration et données runtime pygeoapi (ignoré git)
├── docs/                     documentation projet (architecture, moissonnage, import, workers)
├── scripts/                  utilitaires côté hôte (sync mapserver, monitoring xloader, tests)
└── dumps/                    dumps SQL pour imports (ignoré git sauf dumps/ckan.dump)
```

## Services exposés (en local)

| Service | URL | Description |
|---------|-----|-------------|
| **CKAN** | http://localhost:8080 | Interface principale |
| **pygeoapi** | http://localhost:5001 | OGC API Features |
| **PostgreSQL** | localhost:5432 | Bases `ckan` et `datastore` |
| **Solr** | http://localhost:8983 | Index de recherche |
| **Redis** | localhost:6379 | Queue + cache |
| **Datapusher+** | http://localhost:8800 | Chargement de ressources tabulaires |
| **MapServer** | http://localhost:8081 | WMS / WFS |

## Commandes utiles

Gestion des services :
```bash
docker compose logs -f                  # suivre les logs
docker compose ps                       # statut
docker compose restart ckan             # redémarrer un service
docker compose down                     # tout arrêter
```

Accès aux conteneurs :
```bash
docker compose exec ckan bash
docker compose exec db psql -U ckan -d ckan
docker compose logs -f ckan
```

Maintenance :
```bash
# Reconstruire l'index Solr
docker compose exec ckan ckan -c /srv/app/ckan.ini search-index rebuild

# Synchroniser les datasets géospatiaux avec pygeoapi
./sync-ogc.sh

# État des jobs XLoader
docker compose exec db psql -U datapusher -d datapusher_jobs \
  -c "SELECT * FROM jobs WHERE job_type = 'xloader' ORDER BY requested_timestamp DESC LIMIT 10;"
```

## Variables d'environnement principales en production

```bash
# URLs publiques
CKAN_SITE_URL=https://data.core.dataizen.eu
PYGEOAPI_URL=https://geo.core.dataizen.eu

# Authentification CKAN
CKAN_API_KEY=<token-de-prod-distinct-de-la-cle-dev>

# Keycloak
KEYCLOAK_ISSUER_URL=https://auth.core.dataizen.eu/realms/dataizen
CKAN_OIDC_CLIENT_ID=<client-id>
CKAN_OIDC_CLIENT_SECRET=<secret>
CKAN_OIDC_REDIRECT_URI=https://data.core.dataizen.eu/user/sso_login

# Base de données
CKAN_SQLALCHEMY_URL=postgresql://ckan:<password>@db/ckan
CKAN_DATAPUSHER_URL=http://datapusher:8800
```

## Documentation

Documentation détaillée dans [`docs/`](docs/README.md) : architecture géospatiale, moissonnage CSW, chaîne XLoader, intégration pygeoapi, configuration Kubernetes.

## Licences

Référence des licences open data : https://opendefinition.org/licenses/
