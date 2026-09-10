# Types de moissonnage disponibles dans CKAN

## Vue d'ensemble

Votre installation CKAN supporte **plusieurs types de harvesters** pour moissonner des données depuis différentes sources, ainsi que des moyens d'**exposer vos données** pour être moissonnées par d'autres systèmes.

---

## Types de harvesters disponibles

D'après votre configuration, les harvesters suivants sont installés :

### 1. **CKAN Harvester** (`ckan_harvester`)

**Type** : `ckan`  
**Usage** : Moissonner les datasets d'un autre CKAN

**Configuration** :
- **URL** : URL du CKAN source (ex: `https://ckan2.qualif-data.example.org`)
- **Filtrage** : Via `fq` (syntaxe Solr)
- **Documentation** : Voir `docs/MOISSONNAGE-CKAN.md`

**Exemple** :
```json
{
  "url": "https://ckan2.qualif-data.example.org",
  "type": "ckan",
  "config": {
    "fq": "state:active",
    "read_only": true
  }
}
```

### 2. **CSW Harvester** (`csw_harvester`)

**Type** : `csw`  
**Usage** : Moissonner les métadonnées depuis un serveur CSW (Catalogue Service for the Web)

**Configuration** :
- **URL** : URL du serveur CSW (ex: `https://catalogue.example.com/csw`)
- **Filtrage** : Via `cql` (Common Query Language) ou `constraints` (OGC Filter)
- **Documentation** : Voir `docs/FILTRAGE-MOISSONNAGE-CSW.md`

**Exemple** :
```json
{
  "url": "https://catalogue.example.com/csw",
  "type": "csw",
  "config": {
    "cql": "dc:type = 'dataset'",
    "validator_profiles": ["iso19139"]
  }
}
```

### 3. **DCAT JSON Harvester** (`dcat_json_harvester`)

**Type** : `dcat_json`  
**Usage** : Moissonner les métadonnées depuis un catalogue DCAT en JSON

**Configuration** :
- **URL** : URL du catalogue DCAT JSON (ex: `https://data.example.com/catalog.json`)
- **Format** : JSON-LD conforme au profil DCAT

**Exemple** :
```json
{
  "url": "https://data.example.com/catalog.json",
  "type": "dcat_json",
  "config": {
    "default_tags": ["harvested", "dcat"]
  }
}
```

### 4. **DCAT RDF Harvester** (`dcat_rdf_harvester`)

**Type** : `dcat_rdf`  
**Usage** : Moissonner les métadonnées depuis un catalogue DCAT en RDF

**Configuration** :
- **URL** : URL du catalogue DCAT RDF (ex: `https://data.example.com/catalog.rdf`)
- **Format** : RDF/XML ou Turtle conforme au profil DCAT

**Exemple** :
```json
{
  "url": "https://data.example.com/catalog.rdf",
  "type": "dcat_rdf",
  "config": {
    "default_tags": ["harvested", "dcat"]
  }
}
```

---

## Exposer vos données pour être moissonnées

Votre CKAN peut être moissonné par d'autres systèmes via plusieurs protocoles :

### 1. **Exposition via l'API CKAN** (pour moissonnage CKAN)

Votre CKAN expose automatiquement son API pour être moissonné par d'autres CKAN :

**Endpoint** : `https://ckan2.qualif-data.example.org/api/3/action/package_search`

**Exemple de moissonnage depuis un autre CKAN** :
```json
{
  "url": "https://ckan2.qualif-data.example.org",
  "type": "ckan",
  "config": {
    "fq": "state:active"
  }
}
```

### 2. **Exposition via CSW** (Catalogue Service for the Web)

Pour exposer vos données via CSW, vous devez configurer un serveur CSW (comme pycsw) qui interroge votre CKAN.

**Avantages** :
- Compatible avec les outils SIG (QGIS, ArcGIS, etc.)
- Standard OGC pour les métadonnées géospatiales
- Support des filtres CQL et OGC Filter

**Configuration** :
- Installer et configurer pycsw pour interroger votre CKAN
- Exposer l'endpoint CSW (ex: `https://ckan2.qualif-data.example.org/csw`)

**Exemple de moissonnage CSW** :
```json
{
  "url": "https://ckan2.qualif-data.example.org/csw",
  "type": "csw",
  "config": {
    "cql": "dc:type = 'dataset'"
  }
}
```

### 3. **Exposition via DCAT** (Data Catalog Vocabulary)

Votre CKAN peut exposer ses métadonnées au format DCAT pour être moissonné par d'autres catalogues.

**Endpoints DCAT disponibles** :
- **DCAT JSON** : `https://ckan2.qualif-data.example.org/api/3/action/package_search?fq=*:*&rows=1000` (avec format DCAT)
- **DCAT RDF** : `https://ckan2.qualif-data.example.org/catalog.rdf` (si configuré)

**Avantages** :
- Standard W3C pour les catalogues de données
- Compatible avec les portails européens (data.europa.eu)
- Support des métadonnées enrichies (licences, formats, etc.)

**Exemple de moissonnage DCAT** :
```json
{
  "url": "https://ckan2.qualif-data.example.org/catalog.json",
  "type": "dcat_json",
  "config": {}
}
```

### 4. **Exposition via OAI-PMH** (si configuré)

OAI-PMH (Open Archives Initiative Protocol for Metadata Harvesting) est un protocole standard pour le moissonnage de métadonnées.

**Endpoint** : `https://ckan2.qualif-data.example.org/oai` (si extension installée)

**Avantages** :
- Standard largement utilisé dans le domaine académique
- Support des métadonnées Dublin Core
- Compatible avec de nombreux outils de moissonnage

---

## Tableau récapitulatif

| Type | Harvester | Expose | Usage |
|------|-----------|--------|-------|
| **CKAN** | `ckan_harvester` | API CKAN | Moissonner un autre CKAN |
| **CSW** | `csw_harvester` | Nécessite pycsw | Moissonner un serveur CSW |
| **DCAT JSON** | `dcat_json_harvester` | API CKAN (avec DCAT) | Moissonner un catalogue DCAT JSON |
| **DCAT RDF** | `dcat_rdf_harvester` | Nécessite configuration | Moissonner un catalogue DCAT RDF |
| **OAI-PMH** | Non installé | Nécessite extension | Moissonner via OAI-PMH |

---

## Configuration pratique

### Moissonner depuis votre CKAN

Pour moissonner les datasets de `https://ckan2.qualif-data.example.org` :

#### Via CKAN Harvester

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://ckan2.qualif-data.example.org",
    "type": "ckan",
    "title": "Dataizen - Moissonnage CKAN",
    "config": {
      "fq": "state:active",
      "read_only": true
    }
  }'
```

#### Via CSW (si serveur CSW configuré)

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://ckan2.qualif-data.example.org/csw",
    "type": "csw",
    "title": "Dataizen - Moissonnage CSW",
    "config": {
      "cql": "dc:type = '\''dataset'\''",
      "validator_profiles": ["iso19139"]
    }
  }'
```

#### Via DCAT JSON

```bash
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://ckan2.qualif-data.example.org/catalog.json",
    "type": "dcat_json",
    "title": "Dataizen - Moissonnage DCAT JSON",
    "config": {
      "default_tags": ["harvested", "dcat"]
    }
  }'
```

### Exposer votre CKAN pour être moissonné

#### 1. Via l'API CKAN (automatique)

Votre CKAN expose automatiquement son API à :
- `https://ckan2.qualif-data.example.org/api/3/action/package_search`

**Test** :
```bash
curl "https://ckan2.qualif-data.example.org/api/3/action/package_search?rows=10"
```

#### 2. Via CSW (nécessite configuration)

Pour exposer vos données via CSW, vous devez :
1. Installer et configurer pycsw
2. Configurer pycsw pour interroger votre CKAN
3. Exposer l'endpoint CSW

**Documentation** : Voir la configuration pycsw dans votre projet

#### 3. Via DCAT (si extension DCAT activée)

Votre CKAN peut exposer ses métadonnées au format DCAT si l'extension `dcat` est correctement configurée.

**Test DCAT JSON** :
```bash
curl "https://ckan2.qualif-data.example.org/api/3/action/package_search?fq=*:*&rows=10" \
  -H "Accept: application/json"
```

---

## Comparaison des formats

| Format | Avantages | Inconvénients | Usage recommandé |
|--------|-----------|---------------|------------------|
| **CKAN API** | Simple, natif | Spécifique à CKAN | Moissonnage entre CKAN |
| **CSW** | Standard OGC, compatible SIG | Nécessite serveur CSW | Métadonnées géospatiales |
| **DCAT** | Standard W3C, compatible Europe | Nécessite configuration | Catalogues de données |
| **OAI-PMH** | Standard académique | Extension non installée | Métadonnées académiques |

---

## Recommandations

### Pour moissonner des données

1. **Entre CKAN** : Utilisez `ckan_harvester` (le plus simple)
2. **Depuis un serveur CSW** : Utilisez `csw_harvester` (pour métadonnées géospatiales)
3. **Depuis un catalogue DCAT** : Utilisez `dcat_json_harvester` ou `dcat_rdf_harvester`

### Pour exposer vos données

1. **Pour d'autres CKAN** : L'API CKAN est automatiquement disponible
2. **Pour les outils SIG** : Configurez un serveur CSW (pycsw)
3. **Pour les portails européens** : Configurez l'exposition DCAT

---

## Documentation complémentaire

- **Moissonnage CKAN** : `docs/MOISSONNAGE-CKAN.md`
- **Filtrage CSW** : `docs/FILTRAGE-MOISSONNAGE-CSW.md`
- **Documentation CKAN Harvest** : https://docs.ckan.org/projects/ckanext-harvest/
- **Standard CSW** : https://www.ogc.org/standards/cat
- **Standard DCAT** : https://www.w3.org/TR/vocab-dcat/

---

## Exemple complet : Moissonnage multi-format

Vous pouvez configurer plusieurs sources de moissonnage pour récupérer des données depuis différentes sources :

```bash
# Source 1 : Moissonner un autre CKAN
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -d '{
    "url": "https://autre-ckan.example.com",
    "type": "ckan",
    "title": "Autre CKAN",
    "config": {"fq": "state:active"}
  }'

# Source 2 : Moissonner un serveur CSW
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -d '{
    "url": "https://catalogue.example.com/csw",
    "type": "csw",
    "title": "Catalogue CSW",
    "config": {"cql": "dc:type = '\''dataset'\''"}
  }'

# Source 3 : Moissonner un catalogue DCAT
curl -X POST "http://localhost:5000/api/3/action/harvest_source_create" \
  -H "Authorization: YOUR_API_KEY" \
  -d '{
    "url": "https://data.example.com/catalog.json",
    "type": "dcat_json",
    "title": "Catalogue DCAT",
    "config": {}
  }'
```

Toutes ces sources peuvent être moissonnées simultanément pour agréger des données depuis différentes sources.
