# Processus de dépôt de données via API

## Vue d'ensemble

Quand une ressource est créée via l'API CKAN, plusieurs extensions interviennent dans un ordre spécifique pour traiter la ressource et la charger dans le datastore.

## Ordre d'exécution des hooks

### 1. `before_resource_create` (IResourceController)

**Extensions impliquées :**
- `ckanext-dataload-router` (DataloadRouter)
- `ckanext-ogc` (OGCPlugin) - si présent

**Actions de `ckanext-dataload-router` :**
1. **Détection du format** : Détecte automatiquement le format depuis `mimetype`, `url` ou `name` si non défini
2. **Définition de `resource_type`** : Si upload, définit `resource_type='file'`
3. **Configuration de `xloader_skip`** :
   - **CSV upload** : `xloader_skip=False` (pour permettre le chargement dans le datastore)
   - **CSV URL externe** : `xloader_skip=True` (xloader ne peut pas charger les URLs externes depuis le conteneur)
   - **Autres formats** (ZIP, SHP, GeoJSON, etc.) : `xloader_skip=True` (pas de traitement par xloader)

**Code clé :**
```python
# Ligne 267-336 dans ckanext-dataload-router/__init__.py
if format_ not in ['csv']:
    resource['xloader_skip'] = True
else:
    if has_upload or url_type == 'upload':
        resource['xloader_skip'] = False  # CSV upload → xloader activé
    else:
        resource['xloader_skip'] = True   # CSV URL externe → xloader désactivé
```

### 2. Création de la ressource (CKAN core)

CKAN crée la ressource dans la base de données avec les métadonnées définies dans `before_resource_create`.

**Important :** Si `xloader_skip=False`, CKAN/xloader peut automatiquement soumettre un job à la queue RQ **AVANT** que `after_resource_create` ne soit appelé.

### 3. `after_resource_create` (IResourceController)

**Extensions impliquées (dans l'ordre de chargement des plugins) :**
- `ckanext-dataload-router` (DataloadRouter)
- `ckanext-ogc` (OGCPlugin)
- `ckanext-harvest-xloader` (HarvestXloaderPlugin) - seulement pour datasets harvestés

#### 3.1. `ckanext-dataload-router.after_resource_create`

**Actions :**
1. **Ressources JSON/GeoJSON** :
   - Définit `xloader_skip=True` immédiatement (pour éviter que xloader ne traite la ressource JSON)
   - Télécharge le JSON
   - Convertit en CSV
   - Crée une nouvelle ressource CSV avec `xloader_skip=False`
   - Soumet explicitement à xloader via `xloader_submit`

2. **Ressources CSV avec URL externe** :
   - Si `xloader_skip=True` ET URL externe détectée :
     - Télécharge le CSV depuis l'URL externe
     - Crée une nouvelle ressource uploadée avec `xloader_skip=False`
     - Le xloader est automatiquement soumis lors de la création (car `xloader_skip=False`)

3. **Ressources CSV uploadées** :
   - Si `xloader_skip=False` (défini dans `before_resource_create`) :
     - Le xloader est automatiquement soumis par CKAN/xloader lors de la création
     - Pas d'action supplémentaire nécessaire

4. **Ajout de colonne `the_geom`** : Si CSV avec colonnes lat/lon, ajoute automatiquement `the_geom`

**Code clé :**
```python
# Ligne 338-471 dans ckanext-dataload-router/__init__.py
def after_resource_create(self, context, resource):
    # JSON/GeoJSON → conversion en CSV
    if format in ['json', 'geojson']:
        _process_json_resource(resource)
    
    # CSV URL externe → création ressource uploadée dérivée
    if format == 'csv' and resource.get('xloader_skip') and is_external_url:
        _process_external_csv_resource(resource)
```

#### 3.2. `ckanext-ogc.after_resource_create`

**Actions :**
- Synchronisation avec pygeoapi (si ressource géospatiale)
- Génération de mapfile (si ressource géospatiale)

#### 3.3. `ckanext-harvest-xloader.after_create` (si dataset harvesté)

**Actions :**
- Nettoie les tags du dataset
- Soumet toutes les ressources supportées à xloader (si pas déjà fait)

**Code clé :**
```python
# Ligne 192-238 dans ckanext-harvest-xloader/plugin.py
def _submit_to_xloader(self, context, package_id):
    for resource in resources:
        if not resource.get('xloader_skip', False):
            toolkit.get_action('xloader_submit')(context, {
                'resource_id': resource_id,
                'ignore_hash': True
            })
```

## Soumission à xloader

### Mécanisme automatique (CKAN/xloader)

Quand une ressource est créée avec `xloader_skip=False`, CKAN/xloader peut automatiquement soumettre un job à la queue RQ **pendant** la création de la ressource, avant que `after_resource_create` ne soit appelé.

**Problème potentiel :** Si `after_resource_create` modifie la ressource (par exemple, crée une nouvelle ressource dérivée), le job xloader peut être soumis pour la mauvaise ressource.

### Mécanisme explicite (`xloader_submit`)

Les extensions peuvent soumettre explicitement un job via :
```python
toolkit.get_action('xloader_submit')(context, {
    'resource_id': resource_id,
    'ignore_hash': True  # Optionnel : forcer même si hash identique
})
```

## Traitement par le worker RQ

### Configuration du worker

**Fichier :** `ckan-app/ckan/supervisor.d/xloader.conf`

**Commande :**
```bash
ckan jobs worker default
```

**Important :** CKAN préfixe automatiquement les queues avec `ckan:<site_id>:` pour éviter les conflits entre instances. Quand on lance `ckan jobs worker default`, CKAN écoute automatiquement la queue RQ préfixée `rq:queue:ckan:<site_id>:default`. Si `ckan.site_id=default`, la queue réelle sera `ckan:default:default`.

**Ne pas mélanger :**
- Utiliser `ckan jobs worker default` (CKAN gère le préfixage automatiquement)
- Ne pas utiliser `ckan jobs worker ckan:default:default` (créerait des queues doubles avec `:intermediate:intermediate`)

Le worker écoute la queue préfixée par CKAN dans Redis et traite les jobs de type `xloader_to_datastore`.

### Processus de traitement

1. **Récupération du job** : Le worker RQ récupère le job depuis Redis
2. **Appel de `xloader_data_into_datastore`** : Fonction principale de traitement
3. **Téléchargement de la ressource** : 
   - Appelle `_download_resource_data` qui télécharge le fichier depuis l'URL de la ressource
   - Utilise `get_resource_and_dataset` avec `ignore_auth=True` (patché par `15-patch-xloader-permissions.sh`)
4. **Chargement dans le datastore** : Utilise `ckanext.datastore.backend.postgres` pour charger les données
5. **Mise à jour du statut** : Appelle `xloader_hook` pour mettre à jour `task_status` dans `datapusher_jobs`

## Problèmes potentiels

### 1. Crash rapide du worker

**Causes possibles :**
- **Erreur lors du téléchargement** : 403 Forbidden (problème d'autorisation)
- **Erreur lors du chargement** : Problème de connexion au datastore
- **Erreur de format** : Fichier CSV invalide ou corrompu
- **Timeout** : Fichier trop volumineux ou connexion lente

**Diagnostic :**
```bash
# Vérifier les logs du worker
tail -f /dev/stdout | grep -E "\[XLOADER\]|xloader|datastore"

# Vérifier les jobs dans Redis
python3 << EOF
import redis
r = redis.Redis(host='redis', port=6379, db=0)
queues = r.keys('rq:queue:*')
for queue in queues:
    jobs = r.lrange(queue, 0, -1)
    print(f"{queue}: {len(jobs)} jobs")
EOF

# Vérifier les task_status dans datapusher_jobs
psql -h db -U datapusher -d datapusher_jobs -c "SELECT id, entity_id, state, error, last_updated FROM task_status ORDER BY last_updated DESC LIMIT 10;"
```

### 2. Job soumis mais non traité

**Causes possibles :**
- Worker non démarré ou crashé
- Queue incorrecte (job dans `ckan:default:default` mais worker écoute `default`)
- Redis inaccessible

**Diagnostic :**
```bash
# Vérifier que le worker est actif
ps aux | grep "jobs worker"

# Vérifier supervisor
supervisorctl status xloader

# Vérifier Redis
redis-cli -h redis ping
```

### 3. Ressource reste en "datastore en attente"

**Causes possibles :**
- Job soumis mais worker crash immédiatement
- Job dans la queue mais worker ne le traite pas
- Erreur silencieuse dans le traitement

**Diagnostic :**
```bash
# Vérifier le task_status
ckan -c /srv/app/ckan.ini datastore status <resource_id>

# Vérifier les logs xloader
tail -f /dev/stdout | grep -E "xloader.*<resource_id>"
```

## Flux complet pour un CSV uploadé via API

1. **API call** : `POST /api/action/resource_create` avec `upload=<file>`, `format=CSV`
2. **before_resource_create (DLR)** : Détecte CSV, définit `xloader_skip=False`
3. **CKAN crée la ressource** : Enregistre dans la base avec `xloader_skip=False`
4. **xloader soumet automatiquement** : Job ajouté à la queue `default` dans Redis (CKAN le préfixe automatiquement en `ckan:<site_id>:default`)
5. **after_resource_create (DLR)** : Aucune action (CSV upload, pas d'URL externe)
6. **Worker RQ traite le job** :
   - Récupère le job depuis Redis
   - Télécharge le fichier depuis `/var/lib/ckan/resources/...`
   - Charge dans le datastore PostgreSQL
   - Met à jour `task_status` dans `datapusher_jobs`
   - Met à jour `datastore_active=True` sur la ressource

## Flux complet pour un CSV avec URL externe via API

1. **API call** : `POST /api/action/resource_create` avec `url=<external_url>`, `format=CSV`
2. **before_resource_create (DLR)** : Détecte CSV URL externe, définit `xloader_skip=True`
3. **CKAN crée la ressource** : Enregistre dans la base avec `xloader_skip=True`
4. **xloader ne soumet pas** : Pas de job car `xloader_skip=True`
5. **after_resource_create (DLR)** :
   - Détecte CSV URL externe avec `xloader_skip=True`
   - Télécharge le CSV depuis l'URL externe
   - Crée une nouvelle ressource uploadée avec `xloader_skip=False`
   - Le xloader soumet automatiquement un job pour la nouvelle ressource
6. **Worker RQ traite le job** : Comme pour un CSV uploadé

## Points d'attention

1. **Ordre des hooks** : `after_resource_create` est appelé **après** que xloader ait pu soumettre un job automatiquement
2. **Contexte `ignore_auth`** : Les extensions utilisent `ignore_auth=True` pour éviter les problèmes de permissions
3. **Queue RQ** : Le worker doit utiliser `ckan jobs worker default` (sans préfixe). CKAN préfixe automatiquement avec `ckan:<site_id>:` pour éviter les conflits entre instances. Si `ckan.site_id=default`, la queue réelle sera `ckan:default:default`.
4. **Base `datapusher_jobs`** : Utilisée pour stocker les `task_status`, pas pour la queue des jobs (qui est dans Redis)

