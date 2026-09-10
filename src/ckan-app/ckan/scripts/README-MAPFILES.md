# Synchronisation des Mapfiles MapServer

Ce document explique comment synchroniser et régénérer les mapfiles MapServer pour les datasets géospatiaux CKAN.

## Script de synchronisation

Le script `sync-mapfiles.sh` permet de générer ou régénérer les mapfiles MapServer.

### Utilisation

```bash
# Générer tous les mapfiles
docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh

# Générer le mapfile pour un dataset spécifique
docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh testlmocsv

# Forcer la régénération de tous les mapfiles (écrase les existants)
docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh --force

# Forcer la régénération d'un dataset spécifique
docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh testlmocsv --force

# Afficher l'aide
docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh --help
```

### Génération automatique

Les mapfiles sont générés automatiquement lors de :

1. **Création d'un dataset** (`after_create` hook)
2. **Modification d'un dataset** (`after_update` hook)
3. **Création d'une ressource** (`after_resource_create` hook)
4. **Modification d'une ressource** (`after_resource_update` hook)

### Logs détaillés

Les logs de génération incluent :

- **Début de génération** : Informations sur le dataset (nom, ID, titre)
- **Source de données** : Type (datagis, datastore, OGR) et détails
- **Création** : Étape de création du contenu du mapfile
- **Sauvegarde** : Chemin du fichier mapfile créé
- **Succès** : Confirmation avec le chemin complet du fichier
- **Erreurs** : Détails complets en cas d'échec

### Vérification

Pour vérifier qu'un mapfile a été généré :

```bash
# Lister tous les mapfiles
docker compose exec mapserver ls -lh /mapserver/mapfiles/

# Vérifier un mapfile spécifique
docker compose exec mapserver cat /mapserver/mapfiles/testlmocsv.map
```

### Dépannage

#### Le mapfile n'est pas généré

1. **Vérifier que PostGIS est activé dans datastore** :
   ```bash
   docker compose exec db psql -U ckan -d datastore -c "SELECT PostGIS_version();"
   ```

2. **Vérifier que le dataset est détecté comme géospatial** :
   - Le dataset doit avoir une ressource avec `datastore_active=True` ET une colonne géométrie
   - OU une ressource géospatiale (GeoJSON, SHP, GPKG, etc.)

3. **Vérifier les logs CKAN** :
   ```bash
   docker compose logs ckan | grep -i "mapfile\|géométrie\|geometry"
   ```

4. **Forcer la régénération manuellement** :
   ```bash
   docker compose exec ckan /srv/app/scripts/sync-mapfiles.sh <dataset_name> --force
   ```

#### La colonne geometry n'existe pas

Le script peut créer automatiquement la colonne `geometry` PostGIS depuis :
- Colonnes GeoJSON (`st_asgeojson`, `geo_point_2d`, `geom`)
- Colonnes WKT (`geom_wkt`, `geometry_wkt`)
- Colonnes lat/lon (`latitude`, `longitude`)

Pour activer cette fonctionnalité, utilisez `--auto-create-geometry` (activé par défaut).

#### Erreur "PostGIS function does not exist"

PostGIS n'est pas activé dans la base `datastore`. Activez-le :

```bash
docker compose exec db psql -U postgres -d datastore << 'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
SQL
```

### Script Python direct

Vous pouvez aussi utiliser directement le script Python :

```bash
docker compose exec ckan python3 /usr/local/bin/generate-mapfile.py \
  --dataset testlmocsv \
  --ckan-url http://ckan:5000 \
  --ckan-api-key <VOTRE_API_KEY> \
  --mapfiles-dir /mapserver/mapfiles \
  --postgis-host db \
  --postgis-port 5432 \
  --postgis-db datastore \
  --postgis-user ckan \
  --postgis-password <MOT_DE_PASSE> \
  --auto-create-geometry
```

### Variables d'environnement

Le script utilise les variables d'environnement suivantes (avec valeurs par défaut) :

- `CKAN_URL` : `http://ckan:5000`
- `CKAN_API_KEY` : (vide, à définir si nécessaire)
- `POSTGRES_HOST` : `db`
- `POSTGRES_PORT` : `5432`
- `POSTGRES_DB` : `datastore`
- `POSTGRES_USER` : `ckan`
- `POSTGRES_PASSWORD` : `ckan`
- `MAPFILES_DIR` : `/mapserver/mapfiles`











