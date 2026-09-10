# Architecture géospatiale complète - CKAN + MapServer + pygeoapi + datagis

## Vue d'ensemble

Cette architecture combine **CKAN**, **PostGIS**, **MapServer**, **pygeoapi** et **la base datagis IDEO** pour fournir des services géospatiaux complets et performants.

### Composants principaux

- **CKAN** : Catalogue de données et gestionnaire des métadonnées
- **PostgreSQL/PostGIS** : 
  - Base `datastore` : Données tabulaires avec géométries PostGIS
  - Base `datagis` : Complément pour fichiers géospatiaux non-datastore
- **MapServer** : Services WMS/WFS traditionnels (compatibles QGIS/ArcGIS)
- **pygeoapi** : Services OGC API modernes (OGC API Features/Maps/Tiles)
- **Docker** : Orchestration via `docker-compose.yml`

---

## Sources de données géospatiales

### 1. PostGIS datastore (base `datastore`)

**Contenu** :
- Données tabulaires (CSV, Excel) importées dans CKAN
- Colonnes géométries PostGIS créées automatiquement ou manuellement
- Tables nommées : `_table_{resource_id}` ou `{resource_id}`

**Caractéristiques** :
- Colonnes géométrie : `geometry`, `geom`, `the_geom`
- SRID principal : 4326 (WGS84) ou 2154 (Lambert-93)
- Import automatique via XLoader CKAN
- Accessible via CKAN API (`/api/action/datastore_search`)

**Usage** :
- pygeoapi (via CKAN API)
- MapServer (connexion directe PostGIS)
- CKAN datastore API

### 2. Base datagis (base `datagis`)

**Contenu** :
- Fichiers géospatiaux non-datastore (SHP, GeoJSON, GPKG, KML, KMZ)
- Import automatique depuis `ckan_storage/resources/`
- Tables nommées : `{dataset_name}_{hash}` (format aligné avec l'existant IDEO)

**Caractéristiques** :
- Colonne géométrie : `the_geom` (standard datagis)
- SRID principal : 4171 (RGF93), parfois 4326 ou 2154
- Import via `ogr2ogr` (script `import-geospatial-to-datagis.py`)
- ~1057 tables avec géométries (IDEO)

**Usage** :
- MapServer (connexion directe PostGIS)


**Différence avec datastore** :
| Aspect | datastore | datagis |
|--------|-----------|---------|
| **Contenu** | Données tabulaires (CSV, Excel) avec géométries | Fichiers géospatiaux bruts (SHP, GeoJSON, etc.) |
| **Import** | Automatique via XLoader | Automatique via `import-geospatial-to-datagis.py` |
| **Tables** | `_table_{resource_id}` ou `{resource_id}` | `{dataset_name}_{hash}` |
| **Colonne géométrie** | `geometry`, `geom`, `the_geom` | `the_geom` |
| **SRID** | 4326 (WGS84) ou 2154 (Lambert-93) | 4171 (RGF93), 4326, 2154 |

### 3. Fichiers géospatiaux (via OGR/GDAL)

**Contenu** :
- Fichiers SHP, GeoJSON, GPKG, KML stockés dans `ckan_storage/resources/`
- Format : Fichiers bruts ou ZIP (support `/vsizip/`)

**Usage** :
- MapServer (lecture directe via OGR/GDAL)
- Priorité 3 (si pas de datastore ni datagis)

---

## Flux de synchronisation

### Au démarrage de CKAN

**Ordre d'exécution** :

1. **Synchronisation pygeoapi** (`_schedule_delayed_startup`)
   - Attente que CKAN soit prêt (API + recherche)
   - Appel de `CKANSync.sync()` pour synchroniser tous les datasets
   - Mise à jour de `/srv/app/pygeoapi/local.config.yml`
   - **Source** : Uniquement datastore (via CKAN API)

2. **Import dans datagis** (`_import_geospatial_to_datagis`) - Optionnel s'active uniquement si `--todatagis` ajouté à start-local.sh
   - Appel de `import-geospatial-to-datagis.py`
   - Import de tous les fichiers géospatiaux non-datastore dans `datagis`
   - Création des tables avec nom `{dataset_name}_{hash}`
   - **Source** : Fichiers SHP, GeoJSON, GPKG, KML dans `ckan_storage`

3. **Génération des mapfiles** (`_generate_all_mapfiles_at_startup`)
   - Appel de `generate-mapfile.py --use-datagis`
   - Génération de tous les mapfiles (datastore + datagis + fichiers)
   - **Priorité** : Datagis → Datastore → Fichiers OGR

### Lors de la création/modification d'un dataset

**Hooks déclenchés** :
- `after_create` : Après création d'un dataset
- `after_update` : Après modification d'un dataset
- `after_resource_create` : Après création d'une ressource
- `after_resource_update` : Après modification d'une ressource
- `after_delete` : Après suppression d'un dataset
- `after_resource_delete` : Après suppression d'une ressource

**Actions automatiques** :

1. **Synchronisation pygeoapi** (`_sync_dataset_async`)
   - Appel de `CKANSync.sync_dataset(dataset_name)`
   - Mise à jour de la collection pygeoapi correspondante
   - **Source** : Datastore uniquement

2. **Import dans datagis** (`_import_resource_to_datagis_async`)
   - Si ressource géospatiale (SHP, GeoJSON, GPKG, etc.) **ET** non-datastore
   - Import automatique dans `datagis` via `ogr2ogr`
   - Création de la table `{dataset_name}_{hash}`

3. **Génération mapfile** (`_generate_mapfile_async`)
   - Appel de `generate-mapfile.py --dataset {dataset_name} --use-datagis`
   - **Priorité de recherche** :
     1. **Datagis** : Table correspondant au nom du dataset (si `--use-datagis`)
     2. **Datastore** : Ressource avec `datastore_active=True` et colonne géométrie
     3. **Fichiers OGR** : Fichiers SHP/GeoJSON/GPKG dans `ckan_storage` (via OGR/GDAL)

### Scripts de synchronisation

**Via `start-local.sh`** :
```bash
# Synchronisation complète (optionnel avec --todatagis)
./start-local.sh --todatagis

# Sans import vers datagis (par défaut)
./start-local.sh
```

**Manuel** :
```bash
# Synchronisation pygeoapi
docker exec ckan python3 /srv/app/ckanext-ogc/pygeoapi-providers/ckan_provider/ckan_sync.py \
  --ckan-url http://localhost:5000 \
  --pygeoapi-config /srv/app/pygeoapi/local.config.yml

# Import datagis
docker exec ckan python3 /usr/local/bin/import-geospatial-to-datagis.py

# Génération mapfiles
docker exec ckan python3 /usr/local/bin/generate-mapfile.py --use-datagis
```

---

## Génération des mapfiles MapServer

### Stockage des mapfiles

**Emplacement** :
- **Local** : `./mapserver_storage/mapfiles/` (bind mount)
- **Conteneur** : `/mapserver/mapfiles/` (monté depuis `mapserver_storage/mapfiles/`)
- **Logs** : `./mapserver_storage/logs/` (bind mount vers `/mapserver/logs/`)

**Format** :
- Fichiers texte `.map` (fichiers plats, pas de BDD)
- Un mapfile par dataset CKAN
- Génération automatique via hooks CKAN

### Priorité de recherche des données

Le script `generate-mapfile.py` cherche les données géospatiales dans cet ordre :

1. **Datagis** (priorité 1) 
   - Recherche par nom de dataset (correspondance partielle)
   - Table avec colonne géométrie dans `geometry_columns`
   - Base : `datagis`
   - Colonne : `the_geom`
   - **Suffixe dans titre** : `(datagis)`

2. **Datastore** (priorité 2)
   - Ressource avec `datastore_active=True`
   - Colonne géométrie PostGIS détectée
   - Table : `_table_{resource_id}` ou `{resource_id}`
   - Base : `datastore`
   - Colonne : `geometry`, `geom`, `the_geom`
   - **Suffixe dans titre** : `(datastore)`

3. **Fichiers OGR** (priorité 3)
   - Fichiers SHP, GeoJSON, GPKG, KML dans `ckan_storage/resources/`
   - Lecture directe via OGR/GDAL
   - Support des ZIP avec `/vsizip/`
   - **Suffixe dans titre** : `(ogr)`

### Format d'un mapfile

```mapfile
MAP
    NAME "dataset-name"
    STATUS ON
    SIZE 800 600
    IMAGETYPE PNG24
    EXTENT -180 -90 180 90
    UNITS DD
    
    CONFIG "MS_ERRORFILE" "/mapserver/logs/dataset-name_error.log"
    
    PROJECTION
        "init=epsg:4326"
    END
    
    WEB
        METADATA
            "wms_title" "Titre du dataset (datagis)"
            "wfs_title" "Titre du dataset (datagis)"
            "wms_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
            "wfs_srs" "EPSG:4326 EPSG:3857 EPSG:2154"
        END
    END
    
    LAYER
        NAME "dataset-name"
        TYPE POINT|LINE|POLYGON
        STATUS ON
        
        CONNECTIONTYPE POSTGIS
        CONNECTION "host=db port=5432 dbname=datagis user=ckan password=ckan"
        DATA "the_geom FROM table_name USING UNIQUE _id USING SRID=4171"
        
        PROJECTION
            "init=epsg:4171"
        END
        
        CLASS
            NAME "default"
            STYLE
                COLOR 255 0 0
                OUTLINECOLOR 0 0 0
                WIDTH 1
            END
        END
    END
END
```

### Détection automatique

Le script `generate-mapfile.py` détecte automatiquement :
- **Type de géométrie** : POINT, LINE, POLYGON (via `ST_GeometryType`)
- **Colonne ID unique** : `_id`, `fid`, `id`, `gid`, `oid`, ou clé primaire
- **SRID** : Depuis `geometry_columns` ou depuis les données
- **BBOX** : Calculé depuis `ST_Extent` (transformé en EPSG:4326 si nécessaire)

---

## Services exposés

### 1. WMS (Web Map Service) - MapServer

**URL** :
- Via proxy CKAN : `http://localhost:8080/wms/{org_id}?SERVICE=WMS&REQUEST=GetCapabilities`
- Direct MapServer : `http://localhost:8081/wms?map=/mapserver/mapfiles/{dataset_name}.map&SERVICE=WMS&REQUEST=GetCapabilities`

**Flux** :
```
Client (QGIS)
  ↓
CKAN proxy (/wms/{org_id})
  ↓
MapServer: /wms?map=/mapserver/mapfiles/{dataset_name}.map
  ↓
MapServer lit le mapfile
  ↓
PostGIS (datastore ou datagis): SELECT geometry FROM table WHERE ...
  ↓
MapServer génère l'image PNG
  ↓
Retour: PNG
```

**Caractéristiques** :
- Compatible WMS 1.1.1 et 1.3.0
- Connexion directe à PostGIS (pas via CKAN)
- Nécessite une colonne géométrie PostGIS
- Génération automatique des mapfiles via hooks CKAN

### 2. WFS (Web Feature Service) - MapServer

**URL** :
- Via proxy CKAN : `http://localhost:8080/maps/{org_id}?SERVICE=WFS&REQUEST=GetCapabilities`
- Direct MapServer : `http://localhost:8081/wfs?map=/mapserver/mapfiles/{dataset_name}.map&SERVICE=WFS&REQUEST=GetCapabilities`

**Flux** :
```
Client (QGIS)
  ↓
CKAN proxy (/maps/{org_id})
  ↓
MapServer: /wfs?map=/mapserver/mapfiles/{dataset_name}.map
  ↓
MapServer lit le mapfile
  ↓
PostGIS (datastore ou datagis): SELECT * FROM table WHERE ...
  ↓
MapServer génère le GML
  ↓
Retour: GML (application/gml+xml; version=3.2)
```

**Caractéristiques** :
- Compatible WFS 2.0.0
- Format de sortie : GML (application/gml+xml; version=3.2)
- Toutes les requêtes WFS sont routées vers MapServer (pas pygeoapi)
- BBOX réel calculé depuis PostGIS pour chaque dataset

### 3. OGC API Features - pygeoapi

**URL** :
- Direct pygeoapi : `http://localhost:5001/collections/{collection_id}/items`
- Via proxy CKAN : `http://localhost:8080/maps/{org_id}/collections/{collection_id}/items`

**Flux** :
```
Client (Web/API)
  ↓
pygeoapi (CKANProvider)
  ↓
CKAN API: /api/action/datastore_search?resource_id={resource_id}
  ↓
PostGIS datastore: SELECT * FROM _table_{resource_id}
  ↓
Retour: GeoJSON features
```

**Caractéristiques** :
- pygeoapi se connecte à **CKAN API** (pas directement à PostGIS)
- Utilise `CKANProvider` qui appelle `/api/action/datastore_search`
- Fonctionne même sans colonne géométrie (retourne des données tabulaires)
- Synchronisation automatique via hooks CKAN
- **Source** : Uniquement datastore (pas datagis)

### 4. OGC API Features via MapServer (pour datagis) (TOCHECK)

**URL** :
- Via proxy CKAN : `http://localhost:8080/maps/{org_id}/collections/{collection_id}/items`

**Flux** :
```
Client (Web/API)
  ↓
CKAN proxy (/maps/{org_id}/collections/{collection_id}/items)
  ↓
Vérification: mapfile existe ?
  ↓
Si oui → MapServer WFS avec OUTPUTFORMAT=application/json
  ↓
Si non → pygeoapi (fallback)
```

**Caractéristiques** :
- Pour datasets géospatiaux : utilise MapServer WFS avec GeoJSON
- Pour datasets non-géospatiaux : fallback vers pygeoapi
- Permet d'accéder aux données `datagis` via OGC API Features

---

## Structure des répertoires

```
dataizen-ckan/
├── ckan_storage/              # Données CKAN (bind mount)
│   ├── resources/             # Fichiers de ressources
│   └── storage/               # Uploads CKAN
├── pg_data/                   # Données PostgreSQL (bind mount)
├── solr_data/                 # Index Solr (bind mount)
├── mapserver_storage/         # Stockage MapServer (bind mount)
│   ├── mapfiles/              # Fichiers .map 
│   └── logs/                  # Logs MapServer
├── ckan-app/
│   └── ckan/
│       └── extensions/
│           └── ckanext-ogc/   # Extension OGC
│               ├── ckanext/
│               │   └── ogc/
│               │       ├── plugin.py      # Hooks CKAN
│               │       └── views.py       # Routes WMS/WFS/OGC API
│               └── scripts/
│                   ├── generate-mapfile.py              # Génération mapfiles
│                   └── import-geospatial-to-datagis.py  # Import datagis
└── docker-compose.yml         # Configuration Docker
```

---

## Configuration Docker

### Services

**CKAN** (`ckan`) :
- Port : 5000 (interne)
- Volumes :
  - `./ckan_storage:/ckan_storage`
  - Scripts OGC montés en lecture seule

**PostgreSQL** (`db`) :
- Port : 5432 (interne)
- Bases de données :
  - `ckan` : Métadonnées CKAN
  - `datastore` : Données tabulaires avec géométries
  - `datagis` : Fichiers géospatiaux non-datastore

**MapServer** (`mapserver`) :
- Port : 8081 (exposé)
- Volumes :
  - `./mapserver_storage/mapfiles:/mapserver/mapfiles`
  - `./mapserver_storage/logs:/mapserver/logs`
  - `./ckan_storage:/ckan_storage:ro` (lecture fichiers OGR)

**pygeoapi** (`pygeoapi`) :
- Port : 5001 (interne)
- Config : `/srv/app/pygeoapi/local.config.yml`

### Variables d'environnement

```bash
# Bases de données
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=datastore
POSTGRES_USER=ckan
POSTGRES_PASSWORD=ckan
DATAGIS_DB=datagis

# CKAN
CKAN_URL=http://localhost:5000
CKAN_API_KEY=...

# Mapfiles
MAPFILES_DIR=/mapserver/mapfiles
```

---

## Commandes utiles

### Vérifier la synchronisation

```bash
# Lister les mapfiles générés
ls -lh mapserver_storage/mapfiles/*.map

# Compter les mapfiles
find mapserver_storage/mapfiles -name "*.map" | wc -l

# Vérifier les collections pygeoapi
curl http://localhost:5001/collections | jq '.collections[].id'

# Vérifier les tables datagis
docker exec db psql -U ckan -d datagis -c "
    SELECT f_table_name, f_geometry_column, srid
    FROM geometry_columns
    WHERE f_table_schema = 'public'
    ORDER BY f_table_name;
"
```

### Générer un mapfile manuellement

```bash
# Pour un dataset spécifique
docker exec ckan python3 /usr/local/bin/generate-mapfile.py \
  --dataset nom-dataset \
  --use-datagis

# Pour tous les datasets
docker exec ckan python3 /usr/local/bin/generate-mapfile.py \
  --use-datagis
```

### Importer dans datagis

```bash
# Import manuel de toutes les ressources géospatiaux
docker exec ckan python3 /usr/local/bin/import-geospatial-to-datagis.py
```

### Synchroniser pygeoapi

```bash
# Synchronisation manuelle
docker exec ckan python3 /srv/app/ckanext-ogc/pygeoapi-providers/ckan_provider/ckan_sync.py \
  --ckan-url http://localhost:5000 \
  --pygeoapi-config /srv/app/pygeoapi/local.config.yml
```

### Tester les services

```bash
# WMS GetCapabilities (via proxy CKAN)
curl "http://localhost:8080/wms/{org_id}?SERVICE=WMS&REQUEST=GetCapabilities"

# WFS GetCapabilities (via proxy CKAN)
curl "http://localhost:8080/maps/{org_id}?SERVICE=WFS&REQUEST=GetCapabilities"

# OGC API Features (via pygeoapi)
curl "http://localhost:5001/collections/{collection_id}/items?limit=10"
```

---

## Points importants

### 1. Priorité des sources de données

- **Datagis** est prioritaire sur **datastore** pour la génération des mapfiles
- **pygeoapi** ne lit que depuis **datastore** (pas datagis)
- **MapServer** peut lire depuis les deux bases selon le mapfile

### 2. Correspondance dataset ↔ table datagis

- La correspondance se fait par nom (avec correspondance partielle)
- Les tables datagis ont souvent des hashs dans leur nom (ex: `_110_communes_adherentes_f98c07e`)
- Le script `generate-mapfile.py` cherche par mots-clés du nom du dataset

### 3. SRID différents

- **datastore** : Principalement 4326 (WGS84) ou 2154 (Lambert-93)
- **datagis** : Principalement 4171 (RGF93), parfois 4326 ou 2154
- Les mapfiles utilisent le SRID de la source, avec transformation automatique pour le BBOX en GetCapabilities

### 4. Synchronisation automatique

- **pygeoapi** : Synchronisé automatiquement via hooks CKAN (datastore uniquement)
- **datagis** : Import automatique via hooks CKAN (si ressource géospatiale non-datastore)
- **mapfiles** : Génération automatique via hooks CKAN (toutes les sources)

### 5. Stockage persistant

- Tous les répertoires de données sont en bind mount local :
  - `ckan_storage/` : Données CKAN
  - `pg_data/` : Données PostgreSQL
  - `solr_data/` : Index Solr
  - `mapserver_storage/` : Mapfiles et logs MapServer

---

##  Améliorations futures

- Provider pygeoapi pour datagis (actuellement pygeoapi ne lit que datastore)
- Correspondance automatique dataset ↔ table datagis (via métadonnées CKAN)
- Conversion automatique SRID dans les mapfiles (4171 → 4326 pour compatibilité)
- Synchronisation bidirectionnelle datagis ↔ CKAN
- Interface web pour gérer datagis
- Support WMTS avec MapCache (cache pour WMS/WFS)
- Support SLD/SE pour le stylage avancé dans MapServer
- Pygeoapi synchronise datagis ?

---

## Résumé des services

| Service | Provider | Source | Format | Nécessite géométrie |
|---------|----------|--------|--------|---------------------|
| **WMS** | MapServer | datastore/datagis/fichiers | PNG/JPEG | Oui |
| **WFS** | MapServer | datastore/datagis/fichiers | GML | Oui |
| **OGC API Features** | pygeoapi | datastore | GeoJSON | Non (mais préférable) |
| **OGC API Features (datagis)** | MapServer | datagis | GeoJSON | Oui |

**Tous les services utilisent PostGIS comme source de données géospatiales** (via datastore, datagis, ou fichiers OGR).


