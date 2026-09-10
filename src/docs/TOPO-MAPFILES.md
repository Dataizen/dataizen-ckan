# Topo sur les Mapfiles MapServer

## Vue d'ensemble

Les mapfiles MapServer sont des fichiers de configuration qui définissent comment MapServer doit servir les données géospatiales via WMS/WFS. Dans notre architecture, **un mapfile est généré par dataset CKAN** (pas par ressource individuelle).

---

## Génération des mapfiles

### 1. Au démarrage de CKAN

**Fonction** : `_generate_all_mapfiles_at_startup()` dans `plugin.py`

**Quand** : Après la synchronisation pygeoapi et l'import datagis

**Comment** :
```python
# Appel du script en ligne de commande
subprocess.run([
    'python3', '/srv/app/ckanext-ogc/scripts/generate-mapfile.py',
    '--ckan-url', self.ckan_url,
    '--ckan-api-key', self.ckan_api_key,
    '--use-datagis'  # Active la recherche dans datagis
], timeout=600)
```

**Résultat** : Génère tous les mapfiles pour tous les datasets géospatiaux

**Emplacement** : `/mapserver/mapfiles/{dataset_name}.map`

---

### 2. À la demande (hooks CKAN)

**Hooks déclenchés** :
- `after_create` : Après création d'un dataset
- `after_update` : Après modification d'un dataset  
- `after_resource_create` : Après création d'une ressource
- `after_resource_update` : Après modification d'une ressource

**Fonction** : `_generate_mapfile_async()` dans `plugin.py`

**Comment** :
```python
# Import direct de MapfileGenerator (plus rapide)
generator = MapfileGenerator(
    ckan_url=self.ckan_url,
    ckan_api_key=self.ckan_api_key,
    auto_create_geometry=True,  # Crée automatiquement les colonnes geometry PostGIS
    use_datagis=True,  # Active la recherche dans datagis
    ckan_storage_path=ckan_storage_path
)
generator.generate_mapfile(dataset)
```

**Résultat** : Génère/régénère le mapfile pour le dataset concerné

---

## Priorité de recherche des données

Le script `generate-mapfile.py` cherche les données dans cet ordre :

### PRIORITÉ 1 : Datastore
- Tables nommées `{resource_id}` dans la base `ckan`
- Colonnes géométrie : `geometry`, `geom`, `the_geom`
- SRID : 4326 (WGS84) ou 2154 (Lambert-93)

### PRIORITÉ 2 : Datagis
- **Nouveau format (utilisé maintenant)** : `res_{resource_id}`
  - Exemple : `res_abc123_def456_ghi789`
  - Format garanti unique et sans ambiguïté
  - Utilise `_find_table_in_datagis_by_name()` avec recherche exacte
  
- **Ancien format (compatibilité)** : `{clean_name}_{hash}`
  - Exemple : `communes_paris_a1b2c3d4`
  - Recherche exacte uniquement (recherche approximative désactivée)
  - **À supprimer progressivement**

- Colonne géométrie : `the_geom`
- SRID : Variable (4171, 4326, 2154, etc.)

### PRIORITÉ 3 : Fichiers OGR (local ou distant)
- Fichiers SHP, GeoJSON, GPKG, KML dans `/ckan_storage/resources/`
- Lecture directe via OGR/GDAL (pas d'import dans PostGIS)
- Support des ZIP via `/vsizip/`

---

## Structure des tables datagis

### Format actuel (nouveau)

**Nom de table** : `res_{resource_id}`

**Exemple** :
- Resource ID : `abc123-def456-ghi789`
- Table : `res_abc123_def456_ghi789`

**Avantages** :
- Unique et sans ambiguïté
- Correspondance directe ressource → table
- Pas de collision possible

**Génération** : Dans `import-geospatial-to-datagis.py`, ligne 2913 :
```python
resource_id_clean = resource_id.replace('-', '_')
expected_table_name = f"res_{resource_id_clean}"
```

### Format ancien (à supprimer)

**Nom de table** : `{clean_name}_{hash}`

**Exemple** :
- Nom ressource : `communes-paris.shp`
- Table : `communes_paris_a1b2c3d4`

**Problèmes** :
- Risque de collision (même nom = même hash)
- Pas de correspondance directe avec resource_id
- Recherche approximative désactivée (seule recherche exacte)

**Migration** : Les anciennes tables sont encore supportées pour compatibilité, mais ne sont plus créées.

---

## WMS au niveau organisation

### Architecture

**Un mapfile par dataset** (pas par ressource)

Chaque dataset géospatial a son propre mapfile :
- Chemin : `/mapserver/mapfiles/{dataset_name}.map`
- Contenu : Peut contenir plusieurs layers si le dataset a plusieurs ressources

### WMS GetCapabilities au niveau org

**Endpoint** : `/wms/{org_id}?SERVICE=WMS&REQUEST=GetCapabilities`

**Fonction** : `_generate_wms_capabilities()` dans `views.py`

**Processus** :

1. **Récupération des datasets** (`_get_org_collections()`)
   - Liste tous les datasets de l'organisation
   - Vérifie quels datasets ont un mapfile valide
   - Filtre les datasets géospatiaux

2. **Pour chaque dataset** :
   - Vérifie l'existence du mapfile : `/mapserver/mapfiles/{dataset_id}.map`
   - Interroge MapServer pour obtenir les layers individuels :
     ```python
     mapserver_params = {
         'map': f'/mapserver/mapfiles/{collection_id}.map',
         'SERVICE': 'WMS',
         'VERSION': '1.3.0',
         'REQUEST': 'GetCapabilities'
     }
     mapserver_response = requests.get(f"{mapserver_url}/wms", params=mapserver_params)
     ```
   - Parse le XML pour extraire tous les layers du mapfile
   - Ajoute chaque layer au GetCapabilities de l'organisation

3. **Structure XML générée** :
   ```xml
   <WMS_Capabilities>
     <Layer>
       <Title>Layers for {org_id}</Title>
       <!-- Layer pour dataset 1 -->
       <Layer>
         <Name>{dataset1_id}</Name>
         <Title>{dataset1_title}</Title>
         <!-- Layers individuels des ressources -->
         <Layer>
           <Name>{resource1_layer}</Name>
         </Layer>
         <Layer>
           <Name>{resource2_layer}</Name>
         </Layer>
       </Layer>
       <!-- Layer pour dataset 2 -->
       <Layer>
         <Name>{dataset2_id}</Name>
         ...
       </Layer>
     </Layer>
   </WMS_Capabilities>
   ```

### WMS GetMap au niveau org

**Endpoint** : `/wms/{org_id}?SERVICE=WMS&REQUEST=GetMap&LAYERS={dataset_id}`

**Fonction** : `wms_proxy()` dans `views.py`

**Processus** :
1. Reçoit la requête avec le paramètre `LAYERS={dataset_id}`
2. Redirige vers MapServer avec le mapfile correspondant :
   ```python
   mapfile_path = f"/mapserver/mapfiles/{dataset_id}.map"
   # Proxy vers MapServer
   ```
3. MapServer génère l'image depuis le mapfile

---

## Structure des mapfiles

### Un mapfile = Un dataset

**Nom** : `{dataset_name}.map`

**Contenu** :
- **MAP** : Configuration globale (projection, extent, etc.)
- **WEB** : Métadonnées WMS (title, onlineresource, etc.)
- **LAYER** : Une couche par ressource géospatiale du dataset
  - Si le dataset a 3 ressources géospatiales → 3 layers dans le mapfile
  - Chaque layer pointe vers :
    - Une table datagis (`res_{resource_id}`)
    - OU une table datastore (`{resource_id}`)
    - OU un fichier OGR

### Exemple de structure

```
Dataset: "communes-paris"
├── Ressource 1: communes.shp → Table datagis: res_abc123_def456
├── Ressource 2: arrondissements.geojson → Table datagis: res_ghi789_jkl012
└── Ressource 3: quartiers.gpkg → Table datagis: res_mno345_pqr678

Mapfile: communes-paris.map
├── LAYER "communes-paris-res_abc123_def456"
│   └── CONNECTIONTYPE postgis
│       └── CONNECTION "host=db dbname=datagis ..."
│       └── DATA "the_geom FROM res_abc123_def456"
├── LAYER "communes-paris-res_ghi789_jkl012"
│   └── CONNECTIONTYPE postgis
│       └── DATA "the_geom FROM res_ghi789_jkl012"
└── LAYER "communes-paris-res_mno345_pqr678"
    └── CONNECTIONTYPE postgis
        └── DATA "the_geom FROM res_mno345_pqr678"
```

---

## Flux complet

### Scénario : Création d'une ressource géospatiale

1. **Upload de la ressource** → `after_resource_create` hook
2. **Import dans datagis** (si non-datastore)
   - Table créée : `res_{resource_id}`
   - Import via `ogr2ogr`
3. **Génération du mapfile** (si dataset géospatial)
   - Recherche de la table `res_{resource_id}` dans datagis
   - Création/régénération du mapfile `{dataset_name}.map`
   - Ajout d'un nouveau LAYER dans le mapfile pour cette ressource

### Scénario : WMS GetCapabilities pour une organisation

1. **Requête** : `/wms/{org_id}?SERVICE=WMS&REQUEST=GetCapabilities`
2. **Récupération** : Tous les datasets de l'organisation
3. **Filtrage** : Datasets avec mapfile valide
4. **Pour chaque dataset** :
   - Interrogation MapServer pour obtenir les layers du mapfile
   - Extraction des layers individuels (une par ressource)
5. **Génération XML** : GetCapabilities avec tous les layers de tous les datasets

---

## Points d'attention

### 1. Tables datagis anciennes

**Problème** : Les anciennes tables au format `{clean_name}_{hash}` existent encore

**Action** : 
- Ne plus créer de nouvelles tables avec ce format
- Supprimer progressivement les anciennes tables
- Utiliser uniquement `res_{resource_id}` pour les nouvelles ressources

### 2. Un mapfile par dataset

**Implication** : Si un dataset a plusieurs ressources géospatiales, elles sont toutes dans le même mapfile

**Avantage** :
- Un seul fichier à gérer par dataset
- GetCapabilities plus simple (un seul mapfile à interroger)

**Inconvénient** :
- Si une ressource change, tout le mapfile est régénéré
- Le mapfile peut devenir volumineux avec beaucoup de ressources

### 3. WMS au niveau org = Agrégation

**Architecture** : Le WMS org n'est **pas** un mapfile unique, mais une **agrégation** de plusieurs mapfiles

**Processus** :
- Chaque dataset a son mapfile
- Le WMS org interroge tous les mapfiles
- Génère un GetCapabilities unifié

**Performance** :
- GetCapabilities peut être lent si beaucoup de datasets (interroge tous les mapfiles)
- GetMap est rapide (un seul mapfile par requête)

---

## Résumé

| Aspect | Détails |
|--------|---------|
| **Génération** | Au démarrage (tous) + Hooks (à la demande) |
| **Format tables datagis** | `res_{resource_id}` (nouveau) + `{name}_{hash}` (ancien, compatibilité) |
| **Un mapfile =** | Un dataset (peut contenir plusieurs layers = plusieurs ressources) |
| **WMS org** | Agrégation de tous les mapfiles des datasets de l'org |
| **Structure** | Un mapfile par dataset, plusieurs layers par mapfile (une par ressource) |
| **Emplacement** | `/mapserver/mapfiles/{dataset_name}.map` |

---

## Actions recommandées

1. **Nettoyage des anciennes tables** :
   - Identifier les tables au format `{name}_{hash}` (sans `res_` au début)
   - Vérifier qu'elles ne sont plus utilisées
   - Les supprimer progressivement
   
   **Script de nettoyage** : `cleanup-old-datagis-tables.sh`
   ```bash
   # Mode simulation (affiche ce qui serait supprimé)
   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh --dry-run
   
   # Suppression avec confirmation
   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh
   
   # Suppression sans confirmation (--force)
   docker exec -it ckan bash /srv/app/scripts/cleanup-old-datagis-tables.sh --force
   ```
   
   Le script :
   - Liste toutes les tables géospatiales dans datagis
   - Identifie celles qui ne commencent pas par `res_` (ancien format)
   - Affiche un résumé avec statistiques
   - Demande confirmation avant suppression
   - Supprime les tables et leurs métadonnées (`geometry_columns`, `datagis_import_metadata`)

2. **Vérification** :
   - S'assurer que toutes les nouvelles ressources utilisent `res_{resource_id}`
   - Vérifier que `_find_table_in_datagis()` ne fait plus de recherche approximative
   
   **Vérifier les tables restantes** :
   ```bash
   docker exec db psql -U ckan -d datagis -c "
       SELECT f_table_name, f_geometry_column, srid
       FROM geometry_columns
       WHERE f_table_schema = 'public'
       ORDER BY f_table_name;
   "
   ```

3. **Optimisation WMS org** :
   - Mettre en cache le GetCapabilities si possible
   - Limiter le nombre de datasets interrogés si trop volumineux
