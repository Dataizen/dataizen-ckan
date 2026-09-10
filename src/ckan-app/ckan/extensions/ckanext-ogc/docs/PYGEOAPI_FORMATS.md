# Formats de sortie disponibles dans pygeoapi

Pygeoapi (version 0.21.0) supporte plusieurs formats de sortie conformes aux standards OGC :

## Formats disponibles (confirmés)

### 1. **GeoJSON** (par défaut)
- **URL**: `?f=json` ou sans paramètre
- **MIME Type**: `application/json`
- **Description**: Format JSON standard pour les données géospatiales
- **Exemple**: `http://localhost:5001/collections/{collection}/items?f=json`
- **Limite**: Aucune limite artificielle (10 000 000 configuré)

### 2. **JSON-LD (RDF)**
- **URL**: `?f=jsonld`
- **MIME Type**: `application/ld+json`
- **Description**: Format RDF en JSON-LD pour l'interopérabilité sémantique
- **Exemple**: `http://localhost:5001/collections/{collection}/items?f=jsonld`
- **Limite**: Aucune limite artificielle (10 000 000 configuré)

### 3. **HTML**
- **URL**: `?f=html`
- **MIME Type**: `text/html`
- **Description**: Interface web interactive pour visualiser les données
- **Exemple**: `http://localhost:5001/collections/{collection}/items?f=html`
- **Limite**: Aucune limite artificielle (10 000 000 configuré)

## Formats potentiellement disponibles (selon configuration)

### 4. **CSV**
- **URL**: `?f=csv`
- **MIME Type**: `text/csv`
- **Description**: Format CSV pour l'export tabulaire
- **Note**: Peut nécessiter une configuration supplémentaire dans pygeoapi

### 5. **GML**
- **URL**: `?f=gml`
- **MIME Type**: `application/gml+xml`
- **Description**: Geography Markup Language (OGC standard)
- **Note**: Peut nécessiter une configuration supplémentaire dans pygeoapi

### 6. **Shapefile (ZIP)**
- **URL**: `?f=shp`
- **MIME Type**: `application/zip`
- **Description**: Export au format Shapefile compressé
- **Note**: Peut nécessiter une configuration supplémentaire dans pygeoapi

### 7. **GeoPackage**
- **URL**: `?f=gpkg`
- **MIME Type**: `application/geopackage+sqlite3`
- **Description**: Format GeoPackage (SQLite avec extension spatiale)
- **Note**: Peut nécessiter une configuration supplémentaire dans pygeoapi

## Configuration des limites

Toutes les limites ont été configurées à **10 000 000** pour permettre l'accès à toutes les données disponibles sans restriction artificielle. Les seules limites sont :
- La quantité réelle de données disponibles dans CKAN
- Les limites de mémoire/performance du serveur

### Fichiers modifiés :
- `pygeoapi/local.config.yml` : Limites serveur et collections
- `ckan_provider/provider.py` : Limites par défaut du provider
- `ckan_provider/ckan_sync.py` : Limites dans la génération de config
- `ckanext/ogc/plugin.py` : Limites dans la synchronisation automatique

## Endpoints disponibles

### API principale
- **Landing Page**: `http://localhost:5001/` (JSON, JSON-LD, HTML)
- **Collections**: `http://localhost:5001/collections` (liste toutes les collections)
- **Collection spécifique**: `http://localhost:5001/collections/{collection_name}`
- **Items d'une collection**: `http://localhost:5001/collections/{collection_name}/items`
- **Item spécifique**: `http://localhost:5001/collections/{collection_name}/items/{item_id}`

### Services OGC
- **WFS GetCapabilities**: `http://localhost:5001/collections/{collection_name}/wfs?service=WFS&version=2.0.0&request=GetCapabilities`
- **WMS GetCapabilities**: `http://localhost:5001/collections/{collection_name}/wms?service=WMS&version=1.3.0&request=GetCapabilities`
- **WMTS GetCapabilities**: `http://localhost:5001/collections/{collection_name}/wmts?service=WMTS&version=1.0.0&request=GetCapabilities`

## Exemples d'utilisation

```bash
# GeoJSON (par défaut)
curl "http://localhost:5001/collections/{collection}/items?f=json"
curl "http://localhost:5001/collections/{collection}/items"  # Même chose

# JSON-LD (RDF)
curl "http://localhost:5001/collections/{collection}/items?f=jsonld"

# HTML (visualisation)
curl "http://localhost:5001/collections/{collection}/items?f=html"

# Toutes les données (pas de limite)
curl "http://localhost:5001/collections/{collection}/items?f=json&limit=10000000"
```

## Paramètres de requête

- `f`: Format de sortie (`json`, `jsonld`, `html`)
- `limit`: Nombre d'items à retourner (par défaut: toutes les données disponibles jusqu'à 10M)
- `startindex`: Index de départ pour la pagination
- `bbox`: Filtre par bounding box (`bbox=minx,miny,maxx,maxy`)
- `q`: Recherche textuelle
- `datetime`: Filtre temporel

## Notes importantes

1. **Limites supprimées** : Toutes les limites artificielles ont été supprimées. Seules les limites réelles des données s'appliquent.

2. **Formats supplémentaires** : Certains formats (CSV, GML, Shapefile, GeoPackage) peuvent nécessiter des plugins ou configurations supplémentaires dans pygeoapi. Les formats JSON, JSON-LD et HTML sont garantis de fonctionner.

3. **Performance** : Avec des limites très élevées, assurez-vous que le serveur a suffisamment de mémoire pour gérer de grandes quantités de données.

