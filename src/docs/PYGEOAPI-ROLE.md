# Rôle de pygeoapi dans l'architecture OGC

## Vue d'ensemble

**pygeoapi** est un serveur OGC API Features/Maps/Tiles qui fournit des services OGC modernes (OGC API Features) pour les données géospatiales.

## Rôle actuel

### Ce que pygeoapi fait

1. **OGC API Features pour datastore uniquement**
   - pygeoapi se connecte à CKAN via l'API (`/api/action/datastore_search`)
   - Fournit des collections OGC API Features pour les ressources dans le **datastore**
   - Format de sortie : GeoJSON
   - **Source** : Uniquement les données du datastore (tables `_table_{resource_id}`)

2. **Synchronisation automatique**
   - Synchronisation automatique au démarrage de CKAN
   - Synchronisation lors de la création/modification de datasets
   - Mise à jour de `/srv/app/pygeoapi/local.config.yml`

### Ce que pygeoapi ne fait PAS

1. **Pas d'accès à datagis**
   - pygeoapi ne peut pas accéder directement à la base `datagis`
   - Il n'y a pas de provider pygeoapi pour datagis
   - Les données datagis ne sont **pas** disponibles via pygeoapi

2. **Pas de WMS/WFS**
   - pygeoapi ne fournit pas de services WMS/WFS (standards OGC classiques)
   - WMS/WFS sont fournis par **MapServer** uniquement

## Pourquoi pygeoapi ne fonctionne pas bien pour datagis

### Problème principal

**pygeoapi utilise uniquement le CKANProvider qui appelle l'API CKAN**, et l'API CKAN ne peut accéder qu'au datastore, pas à datagis.

### Architecture actuelle

```
pygeoapi
  ↓
CKANProvider (plugin pygeoapi)
  ↓
CKAN API: /api/action/datastore_search
  ↓
PostgreSQL datastore (tables _table_{resource_id})
```

**datagis n'est pas dans ce flux** car :
- datagis est une base séparée (`datagis` vs `datastore`)
- L'API CKAN ne peut pas interroger datagis directement
- Il n'y a pas de provider pygeoapi pour datagis

### Solution actuelle pour datagis

Pour accéder aux données datagis via OGC API Features, on utilise **MapServer WFS avec GeoJSON** :

```
Client
  ↓
CKAN proxy: /maps/{org_id}/collections/{collection_id}/items
  ↓
Vérification: mapfile existe ?
  ↓
Si oui → MapServer WFS avec OUTPUTFORMAT=application/json
  ↓
PostgreSQL datagis (tables res_{resource_id})
```

## Comparaison des services

| Service | Provider | Source | Format | Disponible pour datagis |
|---------|----------|--------|--------|-------------------------|
| **WMS** | MapServer | datastore/datagis/fichiers | PNG/JPEG | Oui |
| **WFS** | MapServer | datastore/datagis/fichiers | GML | Oui |
| **OGC API Features (datastore)** | pygeoapi | datastore | GeoJSON | Non (datastore uniquement) |
| **OGC API Features (datagis)** | MapServer | datagis | GeoJSON | Oui (via MapServer WFS) |

## Recommandations

### Pour utiliser pygeoapi avec datagis

Il faudrait créer un **provider pygeoapi pour datagis** qui :
1. Se connecte directement à PostgreSQL datagis (pas via CKAN API)
2. Lit les tables `res_{resource_id}` directement
3. Fournit les collections OGC API Features pour datagis

**Cependant**, cette fonctionnalité n'est **pas implémentée actuellement**.

### Alternative actuelle

Utilisez **MapServer WFS avec GeoJSON** pour accéder aux données datagis via OGC API Features :

```
GET /maps/{org_id}/collections/{collection_id}/items?f=json
```

Cette requête :
1. Vérifie si un mapfile existe pour le dataset
2. Si oui, utilise MapServer WFS avec `OUTPUTFORMAT=application/json`
3. Retourne GeoJSON depuis datagis

## Conclusion

**pygeoapi fonctionne bien pour datastore**, mais **ne fonctionne pas pour datagis** car :
- Il n'y a pas de provider pygeoapi pour datagis
- pygeoapi ne peut accéder qu'au datastore via l'API CKAN
- Les données datagis sont accessibles via MapServer WFS (qui fonctionne bien)

**Pour datagis, utilisez MapServer WFS avec GeoJSON** au lieu de pygeoapi.
