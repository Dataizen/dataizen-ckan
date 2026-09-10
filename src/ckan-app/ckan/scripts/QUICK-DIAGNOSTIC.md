# Diagnostic rapide : Pourquoi le mapfile n'est pas généré ?

## Pour le dataset `testlmocsv`

### Étape 1 : Diagnostic complet

```bash
# Diagnostic détaillé (sans génération)
docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh testlmocsv

# Diagnostic + génération automatique
docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh testlmocsv --fix

# Si PostGIS n'est pas activé, l'activer automatiquement
docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh testlmocsv --enable-postgis --fix
```

### Étape 2 : Vérifier les logs CKAN

Les logs détaillés montrent maintenant toutes les étapes :

```bash
# Voir les logs de génération de mapfiles
docker compose logs ckan | grep -i "mapfile\|géométrie\|geometry\|testlmocsv" | tail -50

# Suivre en temps réel
docker compose logs -f ckan | grep -i "mapfile\|géométrie\|geometry"
```

### Étape 3 : Vérifier manuellement

```bash
# 1. Vérifier PostGIS
docker compose exec db psql -U ckan -d datastore -c "SELECT PostGIS_version();"

# 2. Vérifier les colonnes de la ressource
docker compose exec ckan python3 << 'PYTHON'
import requests
import os

CKAN_URL = os.getenv('CKAN_URL', 'http://ckan:5000')
resource_id = 'c19fa9f3-d125-40c3-ac1d-d76eb119660c'

url = f"{CKAN_URL}/api/action/datastore_search"
params = {'resource_id': resource_id, 'limit': 0}
response = requests.get(url, params=params)
data = response.json()

if data.get('success'):
    fields = data.get('result', {}).get('fields', [])
    print("Colonnes trouvées:")
    for field in fields:
        print(f"  - {field.get('id')}: {field.get('type')}")
        
    # Vérifier les colonnes géométriques
    geom_fields = [f for f in fields if any(kw in f.get('id', '').lower() 
                   for kw in ['geometry', 'geom', 'geo', 'st_asgeojson', 'geo_point'])]
    if geom_fields:
        print("\nColonnes géométriques trouvées:")
        for field in geom_fields:
            print(f"  - {field.get('id')}: {field.get('type')}")
    else:
        print("\nAucune colonne géométrique trouvée")
PYTHON

# 3. Vérifier si le mapfile existe
docker compose exec mapserver ls -lh /mapserver/mapfiles/testlmocsv.map 2>/dev/null || echo "Mapfile non trouvé"
```

### Étape 4 : Générer manuellement avec logs détaillés

```bash
# Générer avec logs détaillés
docker compose exec ckan python3 /usr/local/bin/generate-mapfile.py \
  --dataset testlmocsv \
  --ckan-url http://ckan:5000 \
  --mapfiles-dir /mapserver/mapfiles \
  --postgis-host db \
  --postgis-port 5432 \
  --postgis-db datastore \
  --postgis-user ckan \
  --postgis-password ckan \
  --auto-create-geometry
```

## Causes possibles

### 1. PostGIS non activé dans datastore

**Symptôme** : Erreur `function postgis_version() does not exist`

**Solution** :
```bash
docker compose exec db psql -U postgres -d datastore << 'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
SQL
```

### 2. Colonne geometry manquante

**Symptôme** : Colonnes `st_asgeojson` et `geo_point_2d` existent mais pas `geometry`

**Solution** : Le script devrait créer automatiquement la colonne `geometry` depuis les colonnes GeoJSON. Vérifiez les logs pour voir si la création a échoué.

### 3. Dataset non détecté comme géospatial

**Symptôme** : Le hook `after_create` ou `after_update` ne détecte pas le dataset comme géospatial

**Solution** : Vérifiez que :
- La ressource a `datastore_active=True`
- La ressource contient des colonnes géométriques (détectées par `_has_geometry_column`)

### 4. Erreur lors de la génération

**Symptôme** : Le script s'exécute mais échoue silencieusement

**Solution** : Utilisez le script de diagnostic pour voir les logs détaillés :
```bash
docker compose exec ckan /srv/app/scripts/diagnose-mapfile.sh testlmocsv --fix
```

## Vérification finale

Après génération, vérifiez :

```bash
# 1. Le mapfile existe
docker compose exec mapserver test -f /mapserver/mapfiles/testlmocsv.map && echo "Mapfile existe" || echo "Mapfile manquant"

# 2. Le contenu du mapfile
docker compose exec mapserver head -20 /mapserver/mapfiles/testlmocsv.map

# 3. Test WMS GetCapabilities
curl "http://mapserver.qualif-data.example.org/wms?map=/mapserver/mapfiles/testlmocsv.map&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities" | head -20
```

## Logs à surveiller

Les nouveaux logs détaillés affichent :

- `DÉBUT GÉNÉRATION MAPFILE` : Début de la génération
- `Informations datastore:` : Détails de la table et colonnes
- `Création du contenu du mapfile...` : Étape de création
- `Sauvegarde du mapfile:` : Chemin de sauvegarde
- `MAPFILE GÉNÉRÉ AVEC SUCCÈS` : Confirmation
- `ERREUR LORS DE LA GÉNÉRATION` : Erreur avec détails

Cherchez ces marqueurs dans les logs pour comprendre exactement où le processus échoue.











