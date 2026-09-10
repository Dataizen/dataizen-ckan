# Moissonnage CKAN - Guide complet

## Vue d'ensemble

Le moissonnage CKAN permet de récupérer les datasets d'un CKAN source vers un CKAN cible. Le harvester CKAN (`ckan_harvester`) utilise l'API CKAN pour récupérer les métadonnées et les données.

**URL du CKAN source** : `https://ckan2.qualif-data.example.org`

---

## Options de filtrage disponibles

### 1. Filtrage via l'API CKAN (`package_search`)

Le harvester CKAN utilise `package_search` avec des filtres Solr (`fq`) pour filtrer les datasets à la source.

#### Configuration dans l'interface CKAN

Lors de la création/modification d'une source de moissonnage CKAN, vous pouvez configurer des filtres dans le champ **"Configuration"** (JSON) :

```json
{
  "default_tags": ["harvested", "ckan"],
  "default_groups": ["geodata"],
  "default_extras": {
    "harvest_catalogue_name": "Dataizen"
  },
  "override_extras": {
    "source": "CKAN"
  },
  "fq": "organization:mednumbfc",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "none"
}
```

---

## Paramètres de configuration disponibles

### Paramètres de filtrage (`fq`)

Le paramètre `fq` (filter query) utilise la syntaxe Solr pour filtrer les datasets :

#### a) Filtrer par organisation

```json
{
  "fq": "organization:mednumbfc"
}
```

Pour plusieurs organisations :

```json
{
  "fq": "organization:(mednumbfc OR autre-org)"
}
```

#### b) Filtrer par groupe

```json
{
  "fq": "groups:geodata"
}
```

#### c) Filtrer par tags

```json
{
  "fq": "tags:environnement"
}
```

Pour plusieurs tags (ET) :

```json
{
  "fq": "tags:environnement AND tags:eau"
}
```

Pour plusieurs tags (OU) :

```json
{
  "fq": "tags:(environnement OR transport)"
}
```

#### d) Filtrer par type de ressource

```json
{
  "fq": "res_format:CSV"
}
```

Pour plusieurs formats :

```json
{
  "fq": "res_format:(CSV OR GeoJSON OR SHP)"
}
```

#### e) Filtrer par date de modification

```json
{
  "fq": "metadata_modified:[2024-01-01T00:00:00Z TO *]"
}
```

#### f) Filtrer par état (actif uniquement)

```json
{
  "fq": "state:active"
}
```

#### g) Filtrer par étendue géographique (si spatial activé)

```json
{
  "fq": "spatial_geom:\"Intersects(POLYGON((2.0 46.0, 7.5 46.0, 7.5 48.5, 2.0 48.5, 2.0 46.0)))\""
}
```

#### h) Filtrer par extras

```json
{
  "fq": "extras_ogc_source:datastore"
}
```

#### i) Filtres combinés (AND)

```json
{
  "fq": "organization:mednumbfc AND tags:environnement AND res_format:CSV"
}
```

### Paramètres de comportement

#### `read_only`
- **Type** : `boolean`
- **Défaut** : `true`
- **Description** : Si `true`, les datasets moissonnés ne peuvent pas être modifiés dans le CKAN cible

```json
{
  "read_only": true
}
```

#### `remote_groups`
- **Type** : `string` (`"none"`, `"only_local"`, `"create"`)
- **Défaut** : `"none"`
- **Description** : Comment gérer les groupes du CKAN source
  - `"none"` : Ne pas importer les groupes
  - `"only_local"` : Utiliser uniquement les groupes locaux existants
  - `"create"` : Créer les groupes s'ils n'existent pas

```json
{
  "remote_groups": "none"
}
```

#### `remote_orgs`
- **Type** : `string` (`"none"`, `"only_local"`, `"create"`)
- **Défaut** : `"none"`
- **Description** : Comment gérer les organisations du CKAN source
  - `"none"` : Ne pas importer les organisations
  - `"only_local"` : Utiliser uniquement les organisations locales existantes
  - `"create"` : Créer les organisations s'ils n'existent pas

```json
{
  "remote_orgs": "only_local"
}
```

#### `default_tags`
- **Type** : `array` de `string`
- **Description** : Tags à ajouter automatiquement à tous les datasets moissonnés

```json
{
  "default_tags": ["harvested", "ckan", "dataizen"]
}
```

#### `default_groups`
- **Type** : `array` de `string`
- **Description** : Groupes à ajouter automatiquement à tous les datasets moissonnés

```json
{
  "default_groups": ["geodata"]
}
```

#### `default_extras`
- **Type** : `object` (clé-valeur)
- **Description** : Extras à ajouter automatiquement à tous les datasets moissonnés

```json
{
  "default_extras": {
    "harvest_catalogue_name": "Dataizen",
    "harvest_source_url": "https://ckan2.qualif-data.example.org"
  }
}
```

#### `override_extras`
- **Type** : `object` (clé-valeur)
- **Description** : Extras à écraser dans les datasets moissonnés

```json
{
  "override_extras": {
    "source": "CKAN",
    "harvested_from": "Dataizen"
  }
}
```

---

## Configuration pratique

### Via l'interface web CKAN

1. **Accéder à la gestion des sources de moissonnage** :
   - Menu Admin → Sources de moissonnage
   - Ou directement : `/harvest`

2. **Créer une nouvelle source CKAN** :
   - Cliquer sur "Nouvelle source"
   - Type : **CKAN**
   - URL : `https://ckan2.qualif-data.example.org`
   - **Configuration** : JSON avec les filtres (voir exemples ci-dessous)

### Via l'API CKAN

#### Créer une source CKAN avec filtres

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://ckan2.qualif-data.example.org",
    "type": "ckan",
    "title": "Dataizen - Moissonnage CKAN",
    "config": {
      "default_tags": ["harvested", "ckan", "dataizen"],
      "default_groups": ["geodata"],
      "fq": "organization:mednumbfc AND state:active",
      "read_only": true,
      "remote_groups": "none",
      "remote_orgs": "only_local"
    }
  }'
```

#### Modifier une source existante

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_update" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "source-id",
    "config": {
      "fq": "organization:mednumbfc AND tags:environnement"
    }
  }'
```

---

## Exemples de configurations complètes

### Exemple 1 : Moissonner toutes les organisations

```json
{
  "default_tags": ["harvested", "ckan", "dataizen"],
  "default_groups": ["geodata"],
  "fq": "state:active",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "create"
}
```

### Exemple 2 : Moissonner une organisation spécifique

```json
{
  "default_tags": ["harvested", "ckan", "mednumbfc"],
  "fq": "organization:mednumbfc AND state:active",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

### Exemple 3 : Moissonner uniquement les datasets géospatiaux

```json
{
  "default_tags": ["harvested", "ckan", "geospatial"],
  "default_groups": ["geodata"],
  "fq": "state:active AND (res_format:(SHP OR GeoJSON OR GPKG) OR extras_ogc_source:*)",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

### Exemple 4 : Moissonner par tags et format

```json
{
  "default_tags": ["harvested", "ckan"],
  "fq": "state:active AND tags:environnement AND res_format:CSV",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

### Exemple 5 : Moissonner les datasets récents

```json
{
  "default_tags": ["harvested", "ckan"],
  "fq": "state:active AND metadata_modified:[2024-01-01T00:00:00Z TO *]",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

### Exemple 6 : Moissonner avec filtres combinés complexes

```json
{
  "default_tags": ["harvested", "ckan", "dataizen"],
  "default_groups": ["geodata"],
  "default_extras": {
    "harvest_catalogue_name": "Dataizen",
    "harvest_source_url": "https://ckan2.qualif-data.example.org"
  },
  "override_extras": {
    "source": "CKAN"
  },
  "fq": "state:active AND (organization:mednumbfc OR organization:autre-org) AND (tags:environnement OR tags:eau) AND res_format:(CSV OR GeoJSON OR SHP)",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

---

## Cas d'usage courants

### 1. Moissonner toutes les données publiques

```json
{
  "fq": "state:active",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "create"
}
```

### 2. Moissonner une organisation spécifique

```json
{
  "fq": "organization:mednumbfc AND state:active",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

### 3. Moissonner uniquement les données géospatiales

```json
{
  "fq": "state:active AND (res_format:(SHP OR GeoJSON OR GPKG OR KML) OR extras_ogc_source:*)",
  "default_groups": ["geodata"],
  "read_only": true
}
```

### 4. Moissonner par thème (tags)

```json
{
  "fq": "state:active AND tags:environnement",
  "default_tags": ["harvested", "environnement"],
  "read_only": true
}
```

### 5. Exclure certains types de données

```json
{
  "fq": "state:active AND NOT res_format:PDF AND NOT res_format:DOCX",
  "read_only": true
}
```

---

## Syntaxe Solr pour `fq`

### Opérateurs logiques

- **ET** : `AND` ou `+`
- **OU** : `OR`
- **SAUF** : `NOT` ou `-`

### Exemples de syntaxe

```json
{
  "fq": "organization:mednumbfc AND tags:environnement"
}
```

```json
{
  "fq": "tags:(environnement OR eau OR transport)"
}
```

```json
{
  "fq": "state:active AND NOT tags:test"
}
```

### Plages de dates

```json
{
  "fq": "metadata_modified:[2024-01-01T00:00:00Z TO 2024-12-31T23:59:59Z]"
}
```

```json
{
  "fq": "metadata_modified:[2024-01-01T00:00:00Z TO *]"
}
```

### Recherche de texte (wildcards)

```json
{
  "fq": "title:*environnement*"
}
```

---

## Lancer le moissonnage

### Via l'interface web

1. Aller sur la page de la source de moissonnage
2. Cliquer sur "Moissonner maintenant"

### Via la ligne de commande

```bash
# Moissonner une source spécifique
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester run <source_id>

# Moissonner toutes les sources actives
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester run
```

### Via l'API CKAN

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_job_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "source-id"
  }'
```

---

## Vérifier le statut du moissonnage

### Lister les sources

```bash
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester sources
```

### Vérifier les jobs

```bash
docker compose exec ckan ckan -c /srv/app/ckan.ini harvester jobs
```

### Via l'API

```bash
# Lister les sources
curl "http://localhost:5000/api/3/action/harvest_source_list" \
  -H "Authorization: YOUR_API_KEY"

# Vérifier une source spécifique
curl "http://localhost:5000/api/3/action/harvest_source_show?id=source-id" \
  -H "Authorization: YOUR_API_KEY"
```

---

## Notes importantes

1. **Authentification** : Si le CKAN source nécessite une authentification, vous devez fournir une clé API dans la configuration :

```json
{
  "api_key": "YOUR_SOURCE_API_KEY",
  "fq": "state:active"
}
```

2. **Performance** : Les filtres complexes peuvent ralentir le moissonnage. Testez d'abord avec des filtres simples.

3. **Ressources** : Par défaut, le harvester CKAN ne télécharge pas les fichiers des ressources. Seules les métadonnées sont importées.

4. **Organisations et groupes** : Configurez `remote_orgs` et `remote_groups` selon vos besoins :
   - `"create"` : Crée automatiquement les organisations/groupes
   - `"only_local"` : Utilise uniquement ceux qui existent déjà
   - `"none"` : N'importe pas les organisations/groupes

5. **Mise à jour** : Après modification des filtres, relancez le moissonnage pour appliquer les nouveaux critères.

---

## Débogage

### Vérifier la configuration d'une source

```bash
curl "http://localhost:5000/api/3/action/harvest_source_show?id=source-id" \
  -H "Authorization: YOUR_API_KEY" | jq '.result.config'
```

### Tester les filtres directement sur le CKAN source

```bash
# Tester package_search avec les mêmes filtres
curl "https://ckan2.qualif-data.example.org/api/3/action/package_search?fq=organization:mednumbfc&rows=10" \
  -H "Authorization: YOUR_API_KEY" | jq '.result.count'
```

### Vérifier les logs de moissonnage

```bash
docker compose logs ckan | grep -i "harvest" | tail -50
```

---

## Références

- **Documentation CKAN Harvest** : https://docs.ckan.org/projects/ckanext-harvest/
- **Syntaxe Solr** : https://solr.apache.org/guide/solr/latest/query-guide/standard-query-parser.html
- **API CKAN package_search** : https://docs.ckan.org/en/latest/api/index.html#ckan.logic.action.get.package_search

---

## Exemple complet pour Dataizen

Configuration recommandée pour moissonner `https://ckan2.qualif-data.example.org` :

```json
{
  "default_tags": ["harvested", "ckan", "dataizen"],
  "default_groups": ["geodata"],
  "default_extras": {
    "harvest_catalogue_name": "Dataizen",
    "harvest_source_url": "https://ckan2.qualif-data.example.org"
  },
  "override_extras": {
    "source": "CKAN"
  },
  "fq": "state:active",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```

Pour moissonner uniquement une organisation spécifique :

```json
{
  "default_tags": ["harvested", "ckan", "mednumbfc"],
  "fq": "state:active AND organization:mednumbfc",
  "read_only": true,
  "remote_groups": "none",
  "remote_orgs": "only_local"
}
```
