# Base datagis et accès WMS

## Base datagis

### Utilisateur de connexion

Par défaut, la connexion à datagis utilise :
- **Utilisateur** : `ckan` (variable `POSTGRES_USER`)
- **Mot de passe** : valeur de `POSTGRES_PASSWORD`
- **Base de données** : `datagis` (variable `DATAGIS_DB`)

Ces valeurs peuvent être configurées via les variables d'environnement :
- `POSTGRES_USER` (défaut: `ckan`)
- `POSTGRES_PASSWORD` (défaut: depuis `.env`)
- `DATAGIS_DB` (défaut: `datagis`)

### Gestion des erreurs si datagis n'existe pas

**Le système ne plante PAS si datagis n'existe pas.** 

Lors de l'import d'un ZIP contenant des shapefiles :
1. Le script `import-geospatial-to-datagis.py` tente de se connecter à datagis
2. Si la base n'existe pas, une erreur est loggée avec des instructions pour la créer
3. Le traitement continue normalement (la ressource est créée dans CKAN)
4. Seul l'import dans datagis échoue, mais ce n'est pas bloquant

### Créer la base datagis

Si vous voulez utiliser datagis pour stocker les données géospatiales :

```bash
# 1. Se connecter à PostgreSQL
docker exec -it db psql -U ckan

# 2. Créer la base datagis
CREATE DATABASE datagis OWNER ckan ENCODING 'utf-8';

# 3. Activer PostGIS
\c datagis
CREATE EXTENSION IF NOT EXISTS postgis;

# 4. Vérifier que PostGIS est activé
SELECT PostGIS_version();
```

### Variables d'environnement pour datagis

Dans votre `.env` ou `docker-compose.yml`, vous pouvez configurer :

```bash
DATAGIS_DB=datagis
POSTGRES_USER=ckan
POSTGRES_PASSWORD=votre_mot_de_passe
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

## Accès aux fonctions WMS/WFS

### Endpoints disponibles

Les endpoints WMS/WFS sont enregistrés via les blueprints Flask :

- **WMS** : `/wms` (proxy vers MapServer)
- **WFS** : `/wfs` (proxy vers MapServer)
- **OGC API** : `/maps` (pygeoapi)

### Vérifier que les blueprints sont enregistrés

Dans les logs CKAN au démarrage, vous devriez voir :

```
Enregistrement de X blueprints OGC:
   - ogc (url_prefix: /maps)
   - wms (url_prefix: /wms)
   - wfs (url_prefix: /wfs)
```

### Tester l'accès WMS

1. **Test direct** :
   ```bash
   curl "http://localhost:8080/wms?map=/mapserver/mapfiles/dataset-name.map&SERVICE=WMS&REQUEST=GetCapabilities"
   ```

2. **Test avec organisation** :
   ```bash
   curl "http://localhost:8080/wms/{org_id}?SERVICE=WMS&REQUEST=GetCapabilities"
   ```

3. **Test GetMap** :
   ```bash
   curl "http://localhost:8080/wms?map=/mapserver/mapfiles/dataset-name.map&SERVICE=WMS&REQUEST=GetMap&LAYERS=dataset-name&CRS=EPSG:3857&BBOX=-20037508.34,-20037508.34,20037508.34,20037508.34&WIDTH=800&HEIGHT=600&FORMAT=image/png"
   ```

### Problèmes courants

1. **404 Not Found** :
   - Vérifier que le plugin `ogc` est dans `ckan.plugins` dans `ckan.ini`
   - Vérifier les logs au démarrage pour voir si les blueprints sont enregistrés

2. **502 Bad Gateway** :
   - Vérifier que MapServer est accessible : `docker compose ps mapserver`
   - Vérifier la variable `MAPSERVER_URL` (défaut: `http://mapserver:80`)

3. **Mapfile non trouvé** :
   - Vérifier que le mapfile existe : `docker compose exec ckan ls -la /mapserver/mapfiles/`
   - Générer le mapfile si nécessaire : `docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh dataset-name`

### Configuration MapServer

Dans `docker-compose.yml`, le service MapServer doit être configuré :

```yaml
mapserver:
  container_name: mapserver
  image: registry.gitlab.com/xpoxpo/dataizen/mapserver:${TAG:-latest}
  volumes:
    - ./mapserver_storage/mapfiles:/mapserver/mapfiles
```

Et dans CKAN, la variable d'environnement :

```bash
MAPSERVER_URL=http://mapserver:80
```

## Résumé

- **datagis est optionnel** : le système fonctionne sans, mais les shapefiles ne seront pas importés dans PostGIS
- **Utilisateur par défaut** : `ckan` (configurable via `POSTGRES_USER`)
- **WMS/WFS accessibles** via `/wms` et `/wfs` si les blueprints sont enregistrés
- **Vérifier les logs** au démarrage pour confirmer l'enregistrement des blueprints


