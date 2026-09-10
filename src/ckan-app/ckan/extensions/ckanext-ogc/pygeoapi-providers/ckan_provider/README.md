# CKAN Provider for pygeoapi

Ce provider permet d'exposer les datasets géospatiaux de CKAN via pygeoapi avec support complet des standards OGC.

## Structure nettoyée

### Fichiers principaux

- **`provider.py`** - Provider principal avec classes CKANProvider, WFSProvider, WMSProvider, WMTSProvider
- **`ckan_sync.py`** - Synchronisation des datasets CKAN vers pygeoapi
- **`restore_ogc_links.py`** - Restauration et configuration des liens OGC
- **`sync_tool.py`** - Outil de synchronisation unifié avec interface CLI

### Fichiers supprimés (redondants)

- `ogc_providers.py` (intégré dans provider.py)
- `fix_config_simple.py` (remplacé par restore_ogc_links.py)
- `clean_and_fix_config.py` (remplacé par restore_ogc_links.py)
- `fix_config.py` (remplacé par restore_ogc_links.py)
- `add_ogc_links.py` (intégré dans restore_ogc_links.py)
- `update_all_collections.py` (intégré dans restore_ogc_links.py)
- `force_update_links.py` (intégré dans restore_ogc_links.py)
- `diagnostic.py` (intégré dans sync_tool.py)

## Utilisation

### Synchronisation avec sync_tool

```bash
# Synchroniser tous les datasets
python3 /srv/app/pygeoapi-providers/ckan_provider/sync_tool.py --action sync-all

# Synchroniser un dataset spécifique
python3 /srv/app/pygeoapi-providers/ckan_provider/sync_tool.py --action sync-dataset --dataset-id "mon-dataset"

# Vérifier le statut
python3 /srv/app/pygeoapi-providers/ckan_provider/sync_tool.py --action status
```

## Services OGC disponibles

### GeoJSON (Fonctionnel)
- **Endpoint**: `/collections/{collection}/items`
- **Format**: GeoJSON standard
- **Fonctionnalités**: Pagination, filtres, recherche

### WFS (Fonctionnel - Placeholder)
- **Endpoint**: `/collections/{collection}/wfs`
- **Format**: XML WFS 2.0.0
- **Fonctionnalités**: GetCapabilities, DescribeFeatureType, GetFeature
- **Note**: Retourne des placeholders XML fonctionnels

### WMS (Fonctionnel - Placeholder)
- **Endpoint**: `/collections/{collection}/wms`
- **Format**: SVG placeholder
- **Fonctionnalités**: GetCapabilities, GetMap
- **Note**: Retourne des images SVG placeholder

### WMTS (Fonctionnel - Placeholder)
- **Endpoint**: `/collections/{collection}/wmts`
- **Format**: SVG placeholder
- **Fonctionnalités**: GetCapabilities, GetTile
- **Note**: Retourne des tuiles SVG placeholder

## Configuration

### Provider CKAN

```yaml
providers:
  - name: ckan_provider.provider.CKANProvider
    type: feature
    data: ckan-dataset
    options:
      ckan_url: http://localhost:5000
      api_key: ckan-local-dev-apikey
      dataset_id: mon-dataset
      max_record_count: 1000
```

### Transformation de coordonnées

Le provider détecte automatiquement et transforme :
- **Lambert-93 (EPSG:2154)** → **WGS84 (EPSG:4326)**
- **WGS84 (EPSG:4326)** → **Conservé tel quel**

### Champs géométriques supportés

1. **`geo_shape`** (prioritaire) - GeoJSON geometry
2. **`geom`** - WKT geometry
3. **`geometry`** - WKT geometry
4. **`geometry_coordinates` + `geometry_type`** - Format CKAN

## Dépendances

### Requises
- `pygeoapi>=0.18.0`
- `requests`
- `pyyaml`
- `pyproj` (pour transformation de coordonnées)

### Cartographie
- `mapnik>=4.0.0` (pour rendu cartographique WMS/WMTS)
- `pillow>=8.0.0` (pour traitement d'images)
- `numpy>=1.20.0` (pour opérations numériques)

### Optionnelles
- `geopandas` (pour traitement avancé)
- `pandas` (pour CSV)

## Diagnostic

### Vérifier la connectivité

```bash
# Vérifier CKAN
curl http://localhost:5000/api/action/site_read

# Vérifier pygeoapi
curl http://localhost:5001/collections
```

### Logs détaillés

Le provider utilise des logs avec emojis pour faciliter le diagnostic :
- Initialisation
- Recherche/Query
- Données
- Transformation
- Succès
- Erreur
- Avertissement

## Améliorations futures

### Cartographie avec Mapnik

Mapnik est intégré au container via `python3-mapnik` pour le rendu cartographique :

```bash
# Test de l'installation Mapnik
docker-compose exec ckan bash -c "python3 /srv/app/test-mapnik.py"

# Vérifier la version
docker-compose exec ckan bash -c "python3 -c 'import mapnik; print(mapnik.__version__)'"

# Alternative si mapnik n'est pas disponible
docker-compose exec ckan bash -c "python3 -c 'import mapnik; print(\"Mapnik OK\")' 2>/dev/null || echo \"Mapnik not available, using SVG fallback\""
```

**Fonctionnalités disponibles :**
- **WMS** : Cartes PNG haute qualité avec symbolisation intelligente
- **WMTS** : Tuiles PNG pour applications web
- **Symbolisation automatique** : Points, polygones, lignes avec couleurs adaptées
- **Zoom automatique** : Se centre sur les données

### Extensions pygeoapi

```bash
# Pour support Shapefile/GeoPackage
pip install fiona

# Pour support raster
pip install rasterio
```

## Checklist de déploiement

- [ ] CKAN accessible sur `http://localhost:5000`
- [ ] pygeoapi configuré sur `http://localhost:5001`
- [ ] Datasets géospatiaux dans CKAN
- [ ] Synchronisation exécutée
- [ ] OGC links restaurés
- [ ] Test des endpoints

## 🆘 Dépannage

### Erreurs communes

1. **"CKAN not accessible"**
   - Vérifier que CKAN est démarré
   - Vérifier l'URL et l'API key

2. **"No geospatial datasets found"**
   - Vérifier que les datasets ont des ressources géospatiales
   - Vérifier que le datastore est activé pour les CSV

3. **"CRS error"**
   - Vérifier que pyproj est installé
   - Vérifier les coordonnées des données

4. **"Geometry parsing error"**
   - Vérifier le format des géométries dans CKAN
   - Vérifier les champs `geo_shape`, `geom`, `geometry`

### Commandes de diagnostic

```bash
# Vérifier les fichiers
ls -la /srv/app/pygeoapi-providers/ckan_provider/

# Vérifier les logs pygeoapi
tail -f /var/log/pygeoapi.log

# Tester un endpoint
curl "http://localhost:5001/collections/testlmo_avec_records_count/items?limit=1"
```

