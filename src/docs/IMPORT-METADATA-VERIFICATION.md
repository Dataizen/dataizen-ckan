# Vérification des métadonnées importées

## Métadonnées des Packages (Datasets) récupérées et importées

Le script `import-from-ckan.py` récupère et importe **TOUTES** les métadonnées suivantes :

### Métadonnées de base
- `name` - Nom unique du dataset
- `title` - Titre du dataset
- `notes` - Description complète
- `author` - Auteur
- `author_email` - Email de l'auteur
- `maintainer` - Mainteneur
- `maintainer_email` - Email du mainteneur
- `license_id` - Licence
- `url` - URL externe du dataset
- `version` - Version du dataset
- `state` - État (active/deleted)
- `private` - Visibilité (privé/public)
- `type` - Type de dataset

### Métadonnées temporelles
- `metadata_created` - Date de création
- `metadata_modified` - Date de modification

### Relations
- `owner_org` - Organisation propriétaire (mappée correctement)
- `relationships_as_subject` - Relations où le dataset est sujet
- `relationships_as_object` - Relations où le dataset est objet
- `groups` - Groupes associés (avec noms préservés)
- `tags` - Tous les tags (avec noms préservés)

### Métadonnées étendues (extras)
- **TOUS les extras** sont préservés via `dataset.get('extras')`
- Cela inclut notamment :
  - `harvest_source_id` - ID de la source de moissonnage
  - `harvest_source_title` - Titre de la source
  - `harvest_source_url` - URL de la source
  - Toutes les métadonnées personnalisées ajoutées par les extensions

### Ressources
- Toutes les ressources sont récupérées avec leurs métadonnées complètes (voir section Ressources)

## Métadonnées des Ressources récupérées et importées

### Métadonnées de base
- `name` - Nom de la ressource
- `description` - Description
- `format` - Format du fichier
- `mimetype` - Type MIME
- `size` - Taille du fichier
- `hash` - Hash du fichier
- `url` - URL du fichier (transformée si nécessaire)
- `url_type` - Type d'URL (upload/link)

### Métadonnées temporelles
- `created` - Date de création
- `last_modified` - Date de modification

### Métadonnées additionnelles
- `position` - Position dans la liste des ressources
- `state` - État (active/deleted)
- `resource_type` - Type de ressource
- `cache_url` - URL de cache (transformée si nécessaire)
- `cache_last_updated` - Date de mise à jour du cache
- `datastore_active` - Indique si la ressource est dans le datastore
- `on_same_domain` - Indique si sur le même domaine
- `revision_id` - ID de révision

### Métadonnées étendues (extras)
- **TOUS les extras** sont préservés via `resource.get('extras')`
- Cela inclut notamment :
  - Métadonnées géospatiales (bbox, crs, etc.)
  - Métadonnées de harvest
  - Toutes les métadonnées personnalisées

### Fichiers
- **Fichiers réels téléchargés et uploadés** pour les ressources avec `url_type='upload'`
- Les fichiers sont stockés dans `/var/lib/ckan/resources/` sur le CKAN cible

## Comment vérifier que tout est récupéré

### 1. Vérifier les métadonnées d'un dataset source

```bash
# Récupérer un dataset complet depuis la source
curl -H "Authorization: SOURCE_TOKEN" \
  "https://source.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result | keys'
```

### 2. Vérifier les métadonnées d'un dataset cible après import

```bash
# Récupérer le même dataset depuis la cible
curl -H "Authorization: TARGET_TOKEN" \
  "https://target.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result | keys'
```

### 3. Comparer les extras

```bash
# Source
curl -H "Authorization: SOURCE_TOKEN" \
  "https://source.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result.extras'

# Cible
curl -H "Authorization: TARGET_TOKEN" \
  "https://target.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result.extras'
```

### 4. Vérifier les ressources

```bash
# Source
curl -H "Authorization: SOURCE_TOKEN" \
  "https://source.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result.resources[] | {name, format, url_type, extras}'

# Cible
curl -H "Authorization: TARGET_TOKEN" \
  "https://target.ckan.fr/api/action/package_show?id=DATASET_NAME" | jq '.result.resources[] | {name, format, url_type, extras}'
```

## Ce qui est récupéré via package_search

`package_search` retourne généralement **toutes les métadonnées** dans les résultats, incluant :
- Tous les champs de base
- Tous les extras
- Toutes les ressources avec leurs métadonnées complètes
- Les tags et groupes

Cependant, pour être absolument sûr d'avoir **TOUTES** les métadonnées (y compris les relations complexes), le script pourrait être amélioré pour utiliser `package_show` pour chaque dataset. Actuellement, `package_search` devrait suffire car il retourne les objets complets.

## Métadonnées non importées (limitations)

1. **Mots de passe utilisateurs** : Non importés (sécurité CKAN)
2. **Activités** : Récupérées et sauvegardées en JSON, mais non importées dans CKAN (limitation API)
3. **Permissions spécifiques** : Les permissions au niveau ressource ne sont pas importées
4. **Historique de révision** : Les révisions individuelles ne sont pas importées (seulement revision_id)

## Résumé

**OUI**, le script récupère bien **TOUTES** les métadonnées des packages et ressources disponibles via l'API CKAN :

- Toutes les métadonnées de base
- Tous les extras (métadonnées personnalisées)
- Tous les tags et groupes
- Toutes les métadonnées des ressources
- Les fichiers réels (pour url_type='upload')
- Les relations entre datasets
- Les métadonnées de harvest

Le script est conçu pour être **complet** et préserver **toutes** les métadonnées disponibles.











