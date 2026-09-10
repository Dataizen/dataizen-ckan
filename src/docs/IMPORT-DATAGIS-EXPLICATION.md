# Import des fichiers géospatiaux dans datagis

## Vue d'ensemble

Le système utilise **3 méthodes différentes** pour gérer les données géospatiales, selon leur origine et leur format :

### 1. **Datastore** (base `datastore`)
- **Contenu** : Données tabulaires (CSV, Excel) avec colonnes géométriques
- **Import** : Automatique via **XLoader** lors de l'upload
- **Usage** : MapServer se connecte directement à PostGIS
- **Tables** : `_table_{resource_id}` ou `{resource_id}`
- **Colonne géométrie** : `geometry`, `geom`, `the_geom`
- **SRID** : 4326 (WGS84) ou 2154 (Lambert-93)

### 2. **Datagis** (base `datagis`)
- **Contenu** : Fichiers géospatiaux bruts (SHP, GeoJSON, GPKG, KML)
- **Import** : Automatique via **`import-geospatial-to-datagis.py`**
- **Usage** : MapServer se connecte directement à PostGIS
- **Tables** : `res_{resource_id_clean}` (basé sur l'ID complet de la ressource)
- **Colonne géométrie** : `the_geom`
- **SRID** : 4171 (RGF93), 4326, 2154 (selon le fichier source)

### 3. **Fichiers bruts** (via OGR/GDAL)
- **Contenu** : Fichiers SHP, GeoJSON, GPKG, KML stockés dans `ckan_storage/resources/`
- **Import** : Aucun import, utilisation directe
- **Usage** : MapServer lit directement via OGR/GDAL (support `/vsizip/` pour ZIP)
- **Priorité** : Utilisé uniquement si pas de datastore ni datagis

---

## Processus d'import dans datagis

### Quand un fichier est importé dans datagis ?

**Condition principale** : Le fichier géospatial **N'EST PAS** déjà dans le datastore.

```python
# Ligne 95-96 de import-geospatial-to-datagis.py
if resource.get('datastore_active'):
    continue  # Ignorer les ressources déjà dans datastore
```

**Formats supportés** :
- SHP / SHAPEFILE (ZIP ou fichiers séparés)
- GeoJSON
- GPKG / GEOPACKAGE
- KML / KMZ

### Comment ça fonctionne ?

1. **Détection automatique** :
   - Lors de la création d'une ressource (`after_resource_create` dans `plugin.py`)
   - Le plugin détecte si c'est un format géospatial
   - Si `datastore_active = False`, déclenche l'import dans datagis

2. **Import via ogr2ogr** :
   ```bash
   ogr2ogr -f PostgreSQL \
     PG:host=db port=5432 dbname=datagis user=ckan password=... \
     /chemin/vers/fichier.shp \
     -nln res_{resource_id} \
     -lco GEOMETRY_NAME=the_geom \
     -lco FID=_id \
     -overwrite \
     -nlt PROMOTE_TO_MULTI
   ```

3. **Nom de table** :
   - Format : `res_{resource_id_clean}`
   - Basé sur l'ID complet de la ressource CKAN (UUID)
   - Nettoyé pour être valide PostgreSQL (remplacement des tirets par underscores)
   - Limite : 63 caractères (limite PostgreSQL)

4. **Vérification d'existence** :
   - Avant import, vérifie si la table existe déjà
   - Si oui, skip l'import (évite les doublons)

---

## Pourquoi datagis et pas datastore ?

### Raisons de séparation

1. **Formats différents** :
   - **Datastore** : Optimisé pour CSV/Excel avec détection automatique de colonnes
   - **Datagis** : Optimisé pour formats géospatiaux natifs (SHP, GPKG)

2. **Outils différents** :
   - **Datastore** : XLoader (spécialisé CSV/Excel)
   - **Datagis** : ogr2ogr (spécialisé formats géospatiaux)

3. **Gestion des géométries** :
   - **Datastore** : Colonnes géométriques dans des tables tabulaires
   - **Datagis** : Tables PostGIS natives avec `the_geom`

4. **Performance** :
   - **Datagis** : Meilleure performance pour requêtes spatiales complexes
   - **Datastore** : Optimisé pour requêtes tabulaires simples

---

## Pourquoi ne pas utiliser uniquement les fichiers bruts ?

### Avantages de l'import dans datagis

1. **Performance** :
   - Requêtes spatiales beaucoup plus rapides (index PostGIS)
   - Pas besoin de lire le fichier à chaque requête

2. **Fonctionnalités** :
   - Requêtes SQL spatiales complexes
   - Agrégations spatiales
   - Jointures entre tables

3. **Fiabilité** :
   - Validation des données à l'import
   - Gestion des erreurs de format
   - Cohérence des données

4. **Compatibilité** :
   - MapServer peut se connecter directement à PostGIS
   - Pas besoin de passer par OGR à chaque fois

### Inconvénients des fichiers bruts uniquement

1. **Performance** :
   - Lecture du fichier à chaque requête
   - Pas d'index spatial
   - Parsing répété

2. **Limitations** :
   - Pas de requêtes SQL complexes
   - Pas de jointures entre fichiers
   - Dépendance à la disponibilité du fichier

---

## Flux complet

```
┌─────────────────────────────────────────────────────────────┐
│  Upload d'une ressource géospatiale dans CKAN               │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │  Format détecté ?             │
        │  (SHP, GPKG, GeoJSON, etc.)   │
        └───────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
        ▼                               ▼
┌───────────────┐              ┌───────────────┐
│ datastore_    │              │ datastore_    │
│ active = True │              │ active = False│
└───────────────┘              └───────────────┘
        │                               │
        │                               │
        ▼                               ▼
┌───────────────┐              ┌───────────────┐
│ Import dans   │              │ Import dans   │
│ datastore     │              │ datagis       │
│ (via XLoader) │              │ (via ogr2ogr) │
└───────────────┘              └───────────────┘
        │                               │
        └───────────────┬───────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │  Génération du mapfile        │
        │  (priorité: datagis > datastore│
        │   > fichiers bruts)            │
        └───────────────────────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │  MapServer utilise la source  │
        │  la plus performante          │
        └───────────────────────────────┘
```

---

## Configuration

### Activer l'import automatique dans datagis

L'import est déclenché automatiquement lors de la création d'une ressource géospatiale si :
- Le format est géospatial (SHP, GPKG, GeoJSON, etc.)
- `datastore_active = False`

### Import manuel

```bash
# Importer une ressource spécifique
python3 import-geospatial-to-datagis.py --resource-id {resource_id}

# Importer toutes les ressources géospatiales
python3 import-geospatial-to-datagis.py
```

### Créer la base datagis

```sql
-- Se connecter à PostgreSQL
docker exec -it db psql -U ckan

-- Créer la base datagis
CREATE DATABASE datagis OWNER ckan ENCODING 'utf-8';

-- Activer PostGIS
\c datagis;
CREATE EXTENSION IF NOT EXISTS postgis;
```

---

## Résumé

**Les shapefiles et GPKG sont importés dans datagis** :
- Pour améliorer les performances (index PostGIS)
- Pour permettre des requêtes SQL spatiales complexes
- Pour une meilleure intégration avec MapServer
- Uniquement si `datastore_active = False`

**Ils ne sont PAS importés dans datastore** :
- Datastore est optimisé pour CSV/Excel
- Les formats géospatiaux nécessitent ogr2ogr (pas XLoader)
- Séparation des responsabilités (datastore = tabulaire, datagis = géospatial)

**Les fichiers bruts restent disponibles** :
- Comme fallback si datagis/datastore ne sont pas disponibles
- Pour utilisation directe via OGR/GDAL
- Stockés dans `ckan_storage/resources/`
