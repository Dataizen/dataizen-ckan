# MapServer pour CKAN

Ce répertoire contient la configuration MapServer pour fournir les services WMS/WFS pour les datasets géospatiaux CKAN.

## Architecture

MapServer est déployé dans un conteneur Docker séparé et se connecte à PostGIS (base de données CKAN) pour servir les données géospatiales via les protocoles WMS et WFS.

## Structure

```
mapserver/
├── Dockerfile              # Image Docker MapServer
├── apache-mapserver.conf   # Configuration Apache pour MapServer
├── nginx-reverse-proxy.conf.example  # Exemple de configuration Nginx
└── README.md              # Ce fichier
```

## Déploiement

### 1. Construire l'image

```bash
docker-compose build mapserver
```

### 2. Démarrer le service

```bash
docker-compose up -d mapserver
```

### 3. Générer les mapfiles

Générer tous les mapfiles pour les datasets géospatiaux existants :

```bash
docker exec mapserver python3 /usr/local/bin/generate-mapfile.py
```

Générer pour un dataset spécifique :

```bash
docker exec mapserver python3 /usr/local/bin/generate-mapfile.py --dataset nom-du-dataset
```

## Génération automatique

Les mapfiles sont générés automatiquement via les hooks CKAN :
- Lors de la création d'un dataset géospatial
- Lors de la modification d'un dataset géospatial
- Lors de l'activation du datastore sur une ressource géospatiale

## Configuration

### Variables d'environnement

Le service MapServer utilise les variables d'environnement suivantes (définies dans `.env` ou `docker-compose.yml`) :

- `CKAN_URL` : URL de l'API CKAN (ex: `http://ckan:5000`)
- `CKAN_API_KEY` : Clé API CKAN
- `POSTGIS_HOST` : Host PostGIS (ex: `db`)
- `POSTGIS_PORT` : Port PostGIS (ex: `5432`)
- `POSTGIS_DB` : Base de données PostGIS (généralement `datastore` pour CKAN datastore)
- `POSTGIS_USER` : Utilisateur PostGIS
- `POSTGIS_PASSWORD` : Mot de passe PostGIS
- `MAPFILES_DIR` : Répertoire pour les mapfiles (ex: `/mapserver/mapfiles`)

### Format des mapfiles

Les mapfiles sont générés au format MapServer standard et stockés dans `/mapserver/mapfiles/`.

Chaque dataset géospatial a son propre fichier `.map` nommé selon le nom du dataset CKAN.

## Accès aux services

### WMS

**GetCapabilities :**
```
http://localhost:8081/wms?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WMS&REQUEST=GetCapabilities
```

**GetMap :**
```
http://localhost:8081/wms?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WMS&REQUEST=GetMap&LAYERS=nom-dataset&BBOX=minx,miny,maxx,maxy&WIDTH=800&HEIGHT=600&CRS=EPSG:4326&FORMAT=image/png
```

### WFS

**GetCapabilities :**
```
http://localhost:8081/wfs?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WFS&REQUEST=GetCapabilities
```

**GetFeature :**
```
http://localhost:8081/wfs?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WFS&REQUEST=GetFeature&TYPENAME=nom-dataset&OUTPUTFORMAT=application/json
```

**Note importante** : Toutes les requêtes MapServer nécessitent le paramètre `map` avec le chemin complet du fichier `.map`.

## Reverse Proxy

Pour exposer MapServer via un reverse proxy (Nginx/Traefik), voir `nginx-reverse-proxy.conf.example`.

Les endpoints à exposer sont :
- `/wms` → MapServer (WMS)
- `/wfs` → MapServer (WFS)
- `/wmts` → MapServer (WMTS, si MapCache est configuré)

## Dépannage

### Vérifier que MapServer fonctionne

```bash
docker exec mapserver mapserv -v
```

### Vérifier que mapserv est installé

```bash
docker exec mapserver which mapserv
# ou
docker exec mapserver ls -la /usr/lib/cgi-bin/mapserv
```

### Vérifier les logs Apache

```bash
docker logs mapserver
# ou
docker exec mapserver tail -f /var/log/apache2/error.log
```

### Vérifier qu'un mapfile est valide

```bash
docker exec mapserver mapserv -nh /mapserver/mapfiles/nom-dataset.map
```

### Tester une requête WMS

```bash
# GetCapabilities
curl "http://localhost:8081/wms?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WMS&REQUEST=GetCapabilities"

# GetMap
curl "http://localhost:8081/wms?map=/mapserver/mapfiles/nom-dataset.map&SERVICE=WMS&REQUEST=GetMap&LAYERS=nom-dataset&BBOX=-5,41,10,51&WIDTH=800&HEIGHT=600&CRS=EPSG:4326&FORMAT=image/png" -o test-map.png
```

### Problèmes courants

**Erreur "mapserv not found"** :
- Vérifier que le package `cgi-mapserver` est installé : `docker exec mapserver dpkg -l | grep mapserver`
- Vérifier l'emplacement de mapserv : `docker exec mapserver find /usr -name mapserv`

**Erreur "map file not found"** :
- Vérifier que les mapfiles sont générés : `docker exec mapserver ls -la /mapserver/mapfiles/`
- Vérifier les permissions : `docker exec mapserver ls -la /mapserver/mapfiles/`

**Erreur de connexion PostGIS** :
- Vérifier que la base de données `datastore` existe et que PostGIS est activé
- Vérifier les credentials dans les mapfiles générés

## Notes importantes

1. **Base de données** : MapServer se connecte à la base de données PostGIS `datastore` (pas `ckan`). Assurez-vous que PostGIS est activé sur la base de données `datastore`.

2. **Tables PostGIS** : Les tables PostGIS pour CKAN datastore utilisent le format `_table_{resource_id}`.

3. **Colonnes géométries** : Le script de génération détecte automatiquement les colonnes géométries (geometry, geom, the_geom, etc.).

4. **SRID** : Par défaut, les mapfiles utilisent SRID 4326 (WGS84). Pour les données en Lambert-93 (EPSG:2154), ajuster le SRID dans le mapfile généré.

5. **Performance** : Pour de meilleures performances, considérer l'ajout de MapCache pour le cache WMTS/WMS.

## Références

- [Documentation MapServer](https://mapserver.org/)
- [MapServer Docker](https://hub.docker.com/r/osgeo/mapserver)
- [Architecture géospatiale complète](../ARCHITECTURE_GEOSPATIALE.md)

